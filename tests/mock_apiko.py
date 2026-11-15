"""A local stand-in for the APIKO API, for tests: models, uploads, image/video tasks, chat, result files."""

from __future__ import annotations

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
KEY = "sk-test"

MODELS = {
    "nano-banana-2.1": {
        "category": "image",
        "endpoints": ["POST /v1/images/generations", "POST /v1/images/edits", "GET /v1/tasks/{id}"],
        "operations": ["generation", "edit"],
        "supported_modes": ["sync", "async"],
        "input_schema": {
            "additionalProperties": False,
            "required": ["model", "prompt"],
            "properties": {
                "model": {"type": "string"}, "prompt": {"type": "string"}, "async": {"type": "boolean"},
                "size": {"type": "string", "enum": ["auto", "1:1", "16:9", "9:16"]},
                "resolution": {"type": "string", "enum": ["1k", "2k", "4k"]},
                "images": {"type": "array", "maxItems": 14},
            },
        },
    },
    "minimax-h3-image": {
        "category": "image",
        "endpoints": ["POST /v1/images/generations", "POST /v1/images/edits"],
        "operations": ["generation", "edit"],
        "supported_modes": ["sync"],
        "input_schema": {
            "additionalProperties": False,
            "required": ["model", "prompt"],
            "properties": {
                "model": {"type": "string"}, "prompt": {"type": "string"},
                "resolution": {"type": "string", "enum": ["1K", "2K", "4K"]},
            },
        },
    },
    "seedance-2.5": {
        "category": "video",
        "endpoints": ["POST /v1/videos"],
        "input_schema": {
            "additionalProperties": False,
            "required": ["model", "prompt"],
            "properties": {
                "model": {"type": "string"}, "prompt": {"type": "string"},
                "duration": {"type": "integer", "minimum": 4, "maximum": 30},
                "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"]},
                "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
                "generate_audio": {"type": "boolean"}, "seed": {"type": "integer"},
                "first_image_url": {"type": "string"}, "last_image_url": {"type": "string"},
                "reference_image_urls": {"type": "array", "maxItems": 30},
                "reference_videos": {"type": "array", "maxItems": 10},
            },
        },
    },
    "wan3.0-image-to-video": {
        "category": "video",
        "endpoints": ["POST /v1/videos"],
        "input_schema": {
            "additionalProperties": False,
            "required": ["model", "prompt", "first_image_url"],
            "properties": {
                "model": {"type": "string"}, "prompt": {"type": "string"},
                "duration": {"type": "integer", "minimum": 2, "maximum": 30},
                "first_image_url": {"type": "string"},
            },
        },
    },
    "gemini-3.5-flash": {},
}


class State:
    def __init__(self):
        self.requests: list[tuple[str, str, object]] = []
        self.tasks: dict[str, dict] = {}
        self.polls_until_done = 2
        self.fail_next = False
        self.base = ""


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, payload, ctype="application/json"):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authed(self):
            if self.headers.get("Authorization") != "Bearer " + KEY:
                self._send(401, {"error": {"message": "invalid key"}})
                return False
            return True

        def do_GET(self):
            state.requests.append(("GET", self.path, None))
            if self.path.startswith("/files/"):
                if not self._authed():
                    return
                if self.path.endswith(".mp4"):
                    mp4 = os.environ.get("APIKO_MOCK_MP4")
                    if mp4:
                        with open(mp4, "rb") as fh:
                            return self._send(200, fh.read(), "video/mp4")
                    return self._send(200, b"\x00\x00\x00\x18ftypmp42fake-video", "video/mp4")
                return self._send(200, PNG_1x1, "image/png")
            if not self._authed():
                return
            if self.path == "/v1/models?expand=parameters":
                return self._send(200, {"data": [{"id": k, "parameters": v or None} for k, v in MODELS.items()]})
            if self.path.startswith("/v1/tasks/"):
                tid = self.path.rsplit("/", 1)[1]
                task = state.tasks.get(tid)
                if not task:
                    return self._send(404, {"error": {"message": "task not found"}})
                task["polls"] += 1
                if task["polls"] < state.polls_until_done:
                    return self._send(200, {"id": tid, "status": "in_progress", "progress": "40%"})
                if task.get("fail"):
                    return self._send(200, {"id": tid, "status": "failed", "fail_reason": "content refused"})
                key = "video_url" if task["kind"] == "video" else "data"
                url = f"{state.base}/files/{tid}." + ("mp4" if task["kind"] == "video" else "png")
                body = {"id": tid, "status": "completed"}
                body[key] = url if key == "video_url" else [{"url": url}]
                return self._send(200, body)
            self._send(404, {"error": {"message": "no route"}})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if not self._authed():
                return
            if self.path == "/v1/uploads/images":
                state.requests.append(("POST", self.path, self.headers.get("Content-Type")))
                n = sum(1 for r in state.requests if r[1] == self.path)
                return self._send(200, {"url": f"https://img.example/upload-{n}.png"})
            body = json.loads(raw or b"{}")
            state.requests.append(("POST", self.path, body))
            if os.environ.get("APIKO_MOCK_LOG"):
                print("POST", self.path, json.dumps(body), flush=True)
            if self.path == "/v1/chat/completions":
                if body.get("max_tokens", 0) < 10:
                    return self._send(200, {"choices": [{"finish_reason": "length", "message": {"content": ""}}]})
                return self._send(200, {"choices": [{"finish_reason": "stop", "message": {"content": "a cinematic prompt"}}]})
            if self.path in ("/v1/images/generations", "/v1/images/edits", "/v1/videos"):
                kind = "video" if self.path == "/v1/videos" else "image"
                if kind == "image" and not body.get("async"):
                    return self._send(200, {"created": 1, "data": [{"url": f"{state.base}/files/sync.png"}]})
                tid = f"task_{len(state.tasks) + 1}"
                state.tasks[tid] = {"kind": kind, "polls": 0, "fail": state.fail_next}
                return self._send(200, {"id": tid, "status": "queued"})
            self._send(404, {"error": {"message": "no route"}})

    return Handler


def start() -> tuple[ThreadingHTTPServer, State]:
    state = State()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


if __name__ == "__main__":
    import sys
    import time

    srv, st = start()
    print(st.base, flush=True)
    if len(sys.argv) > 1:
        st.polls_until_done = int(sys.argv[1])
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
