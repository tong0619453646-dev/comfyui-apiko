"""Small APIKO HTTP client: request, upload, model schemas, task polling and downloads.

Standard library only. The API key is read from the APIKO_API_KEY environment variable or from
an `api_key.txt` file next to this package, is sent only to the APIKO API host, and is never
logged, returned or written anywhere. It is deliberately not a node input: ComfyUI stores node
inputs in saved workflows and in the metadata of every PNG it saves.
"""

from __future__ import annotations

import logging

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "comfyui-apiko/0.1.0 (+https://apiko.app/)"
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024
SCHEMA_TTL = 600
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")

log = logging.getLogger("comfyui-apiko")


class ApiError(RuntimeError):
    """An error message that is safe to show in the ComfyUI UI (never contains the key)."""


def api_base() -> str:
    return os.environ.get("APIKO_BASE_URL", "https://api.apiko.app").strip().rstrip("/")


def api_key() -> str:
    base = urllib.parse.urlsplit(api_base())
    if base.scheme != "https" and base.hostname not in ("127.0.0.1", "localhost"):
        raise ApiError(f"Refusing to send the API key to a non-HTTPS base URL: {api_base()}")
    key = os.environ.get("APIKO_API_KEY", "").strip()
    if not key and os.path.isfile(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as fh:
            key = fh.read().strip()
    if not key:
        raise ApiError(
            "No APIKO API key found. Create one at https://apiko.app/dashboard/apikeys, then either set the "
            f"APIKO_API_KEY environment variable before starting ComfyUI or paste the key into {KEY_FILE}."
        )
    return key


def is_api_host(url: str) -> bool:
    return urllib.parse.urlsplit(url).netloc == urllib.parse.urlsplit(api_base()).netloc


def _error_message(raw: str) -> str:
    try:
        err = json.loads(raw).get("error")
        if isinstance(err, dict):
            return err.get("message") or raw
        if isinstance(err, str):
            return err
        return json.loads(raw).get("message") or raw
    except (ValueError, AttributeError):
        return raw


def request(method: str, path: str, body=None, content_type: str = "application/json", timeout: int = 120) -> dict:
    url = path if path.startswith("http") else api_base() + path
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Authorization": "Bearer " + api_key()}
    data = None
    if body is not None:
        data = json.dumps(body).encode() if content_type == "application/json" else body
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    where = urllib.parse.urlsplit(url).path
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
    except urllib.error.HTTPError as err:
        msg = _error_message(err.read().decode(errors="replace"))
        hint = {
            401: " Check the API key in the APIKO console.",
            402: " Your APIKO balance is too low; top up at https://apiko.app/dashboard.",
            404: " The model id is wrong or retired; nothing was billed. See https://apiko.app/models.",
            503: " The model has no available route right now; try again a little later.",
        }.get(err.code, "")
        raise ApiError(f"APIKO HTTP {err.code} from {where}: {msg[:600]}{hint}") from None
    except urllib.error.URLError as err:
        raise ApiError(f"Network error calling APIKO {where}: {err.reason}") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise ApiError(f"APIKO {where} returned a non-JSON response.") from None


def upload_image(raw: bytes, content_type: str) -> str:
    """Upload JPEG/PNG/WebP bytes (free, kept 72 hours) and return the public URL."""
    if len(raw) > UPLOAD_MAX_BYTES:
        raise ApiError(f"Image is {len(raw) / 1048576:.1f} MB; APIKO uploads are limited to 10 MB.")
    res = request("POST", "/v1/uploads/images", raw, content_type=content_type)
    url = res.get("url")
    if not url and isinstance(res.get("data"), dict):
        url = res["data"].get("url")
    if not url:
        raise ApiError("APIKO upload returned no URL.")
    return url


_schema_lock = threading.Lock()
_schema_cache: dict = {"at": 0.0, "models": None}

DONE_OK = {"completed", "succeeded", "success", "done"}
DONE_FAIL = {"failed", "failure", "cancelled", "canceled", "error", "expired"}
USAGE_PAGE = "https://apiko.app/dashboard"


def model_params() -> dict | None:
    """{model id: parameters dict} for every model this key can call; None when the list is unavailable.

    parameters carries input_schema, endpoints, operations and supported_modes (GET /v1/models?expand=parameters).
    """
    with _schema_lock:
        if _schema_cache["models"] is not None and time.time() - _schema_cache["at"] < SCHEMA_TTL:
            return _schema_cache["models"]
    try:
        res = request("GET", "/v1/models?expand=parameters", timeout=30)
    except ApiError as err:
        if "HTTP 401" in str(err) or "HTTP 403" in str(err):
            raise
        log.info(f"[APIKO] model list unavailable, sending fields unchecked: {err}")
        return None
    models = {}
    for item in res.get("data") or []:
        if isinstance(item, dict) and item.get("id"):
            params = item.get("parameters")
            models[item["id"]] = params if isinstance(params, dict) else {}
    with _schema_lock:
        _schema_cache.update(at=time.time(), models=models)
    return models


def check_fields(model: str, schema: dict | None, body: dict) -> dict:
    """Drop fields the model's schema does not list and reject values it would refuse, before any charge."""
    props = (schema or {}).get("properties")
    if not props:
        return body
    out = {}
    for key, val in body.items():
        spec = props.get(key)
        if spec is None:
            log.info(f"[APIKO] {model} does not accept '{key}', not sent")
            continue
        enum = spec.get("enum")
        if enum and val not in enum and isinstance(val, str):
            # Tiers are spelled 4k on some models and 4K on others; send the model's own spelling.
            val = next((e for e in enum if isinstance(e, str) and e.lower() == val.lower()), val)
        if enum and val not in enum:
            raise ApiError(f"{model}: {key}={val!r} is not allowed (nothing was billed). Choose one of: {', '.join(map(str, enum))}.")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            lo, hi = spec.get("minimum"), spec.get("maximum")
            if (lo is not None and val < lo) or (hi is not None and val > hi):
                raise ApiError(f"{model}: {key}={val} is out of range {lo}–{hi} (nothing was billed).")
        if isinstance(val, list) and spec.get("maxItems") and len(val) > spec["maxItems"]:
            raise ApiError(f"{model}: at most {spec['maxItems']} items in {key}, got {len(val)} (nothing was billed).")
        out[key] = val
    for key in schema.get("required") or []:
        if key not in out:
            hint = " Connect a first_frame image." if key == "first_image_url" else ""
            raise ApiError(f"{model} needs '{key}' (nothing was billed).{hint}")
    return out


def plan(kind: str, model: str, body: dict) -> tuple[str, dict, bool]:
    """Return (endpoint path, checked body, async?) for an image or video job."""
    models = model_params()
    params: dict = {}
    if models is not None:
        if model not in models:
            stem = model.split("-")[0].split(".")[0]
            near = [m for m in models if stem and m.startswith(stem)][:6]
            raise ApiError(
                f"Model '{model}' is not available to this API key (nothing was billed)."
                + (f" Similar ids: {', '.join(near)}." if near else "")
                + " Current models: https://apiko.app/models"
            )
        params = models[model] or {}
    endpoints = params.get("endpoints") or []
    if kind == "image":
        edit = bool(body.get("images"))
        if edit and params.get("operations") and "edit" not in params["operations"]:
            raise ApiError(f"{model} cannot edit reference images; disconnect the images input or pick another model.")
        path = "/v1/images/edits" if edit else "/v1/images/generations"
        modes = params.get("supported_modes") or ["async"]
        use_async = "async" in modes
    else:
        path = "/v1/videos"
        use_async = True
    if endpoints and f"POST {path}" not in endpoints:
        raise ApiError(f"{model} does not offer POST {path} (nothing was billed).")
    body = check_fields(model, params.get("input_schema"), body)
    if kind == "image" and use_async:
        body["async"] = True
    return path, body, use_async


def submit(kind: str, model: str, body: dict) -> tuple[str | None, list[str]]:
    """Submit once. Returns (task id, []) for async jobs or (None, result urls) for synchronous ones.

    Never retried: a timeout after the request was sent may still be billed.
    """
    path, payload, use_async = plan(kind, model, {"model": model, **body})
    timeout = 180 if (kind == "video" or use_async) else 600
    try:
        job = request("POST", path, payload, timeout=timeout)
    except ApiError as err:
        if str(err).startswith("Network error") or "HTTP 524" in str(err):
            raise ApiError(
                f"{err}. The job may have been accepted and billed. Do not queue it again: check {USAGE_PAGE} "
                "for a new task and fetch it with resume_task_id."
            ) from None
        raise
    tid = job.get("id") or job.get("task_id")
    if not tid and isinstance(job.get("data"), dict):
        tid = job["data"].get("id") or job["data"].get("task_id")
    if tid and (use_async or str(job.get("status", "")).lower() not in DONE_OK):
        log.info(f"[APIKO] {model}: submitted task {tid}")
        return str(tid), []
    return None, result_urls(job)


def poll(task_id: str, interval: float, timeout: float, on_progress=None, check_interrupt=None) -> dict:
    deadline = time.time() + timeout
    last = None
    while True:
        try:
            task = request("GET", "/v1/tasks/" + urllib.parse.quote(task_id, safe=""), timeout=30)
        except ApiError as err:
            msg = str(err)
            if "HTTP 404" in msg:
                raise ApiError(f"APIKO task {task_id} not found: the id must come from this API key, and old tasks expire.") from None
            transient = msg.startswith("Network error") or any(f"HTTP {c}" in msg for c in (500, 502, 503, 504, 520, 522, 524))
            if transient and time.time() + 5 < deadline:
                log.info(f"[APIKO] task {task_id}: read failed, retrying ({msg[:120]})")
                _wait(5, check_interrupt)
                continue
            raise
        status = str(task.get("status", "")).lower()
        if status != last:
            log.info(f"[APIKO] task {task_id}: {status} {task.get('progress', '')}".rstrip())
            last = status
        if on_progress:
            on_progress(task.get("progress"))
        if status in DONE_OK:
            return task
        if status in DONE_FAIL:
            reason = task.get("fail_reason")
            if not reason and isinstance(task.get("error"), dict):
                reason = task["error"].get("message")
            raise ApiError(f"APIKO task {task_id} {status}; failed tasks are refunded in full. Reason: {reason or 'none given'}")
        if time.time() + interval > deadline:
            raise ApiError(
                f"APIKO task {task_id} is still {status or 'running'} after {int(timeout)} s. Do not queue the same job "
                f"again (that bills a second time): put {task_id} into the node's resume_task_id and queue to fetch it."
            )
        _wait(interval, check_interrupt)


def _wait(seconds: float, check_interrupt=None) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if check_interrupt:
            check_interrupt()
        time.sleep(min(0.5, max(0.0, end - time.time())))


def result_urls(task: dict) -> list[str]:
    urls = []
    data = task.get("data")
    if isinstance(data, list):
        urls = [d["url"] for d in data if isinstance(d, dict) and d.get("url")]
    elif isinstance(data, dict) and data.get("url"):
        urls = [data["url"]]
    if not urls and task.get("video_url"):
        urls = [task["video_url"]]
    if not urls:
        raise ApiError("APIKO task completed but returned no result URL.")
    return urls


def download(url: str) -> bytes:
    if urllib.parse.urlsplit(url).scheme != "https" and not is_api_host(url):
        raise ApiError("Refusing to download a result over plain HTTP.")
    headers = {"User-Agent": USER_AGENT}
    if is_api_host(url):
        headers["Authorization"] = "Bearer " + api_key()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as resp:
                chunks, size = [], 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > DOWNLOAD_MAX_BYTES:
                        raise ApiError("Result file is larger than 512 MB.")
                    chunks.append(chunk)
            if not size:
                raise ApiError("Result download was empty.")
            return b"".join(chunks)
        except (urllib.error.URLError, OSError) as err:
            if attempt == 2:
                raise ApiError(f"Result download failed ({err}). The link stays valid for 24 hours: {url}") from None
            time.sleep(1 + attempt)
    raise ApiError("Result download failed.")
