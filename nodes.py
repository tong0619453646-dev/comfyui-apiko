"""APIKO nodes for ComfyUI: image generation and editing, video generation, and chat."""

from __future__ import annotations

import logging

import json

from . import client
from .client import ApiError
from .media import batch_to_uploads, bytes_to_image, bytes_to_video, images_to_batch, tensor_to_upload

CATEGORY = "APIKO"

# Models listed on https://apiko.app/models (2026-11-15). Any other id this key can call works through custom_model.
IMAGE_MODELS = [
    "nano-banana-2.1",
    "nano-banana-pro",
    "nano-banana-2",
    "gpt-image-2.6-sunburst",
    "gpt-image-2.6-flare",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
    "gpt-image-2",
    "seedream-5.5",
    "doubao-seedream-5-0-pro",
    "minimax-h3-image",
]
VIDEO_MODELS = [
    "seedance-2.5",
    "seedance-2.0",
    "seedance-2.0-fast",
    "seedance-2.0-mini",
    "kling-4.0",
    "wan3.0-text-to-video",
    "wan3.0-image-to-video",
    "wan3.0-reference-video",
    "wan3.0-prime-text-to-video",
    "wan3.0-prime-image-to-video",
    "wan3.0-prime-reference-video",
    "minimax-h3",
    "grok-imagine-video-1.5",
    "gemini-omni-1.1-flash",
]
CHAT_MODELS = [
    "gemini-3.5-flash",
    "gpt-6-astra",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "deepseek-v4-pro",
    "qwen3.8-max",
    "kimi-k3",
    "glm-5.3",
]

DEFAULT = "default"
SIZES = [DEFAULT, "auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "4:5", "5:4", "21:9", "1024x1024"]
IMAGE_RESOLUTIONS = [DEFAULT, "1k", "1.5k", "2k", "4k"]
QUALITIES = [DEFAULT, "low", "medium", "high", "auto"]
VIDEO_RESOLUTIONS = [DEFAULT, "480p", "720p", "768p", "1080p", "2k", "4k"]
ASPECTS = [DEFAULT, "adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"]
AUDIO = [DEFAULT, "on", "off"]

ACCOUNT_HINT = "Needs an APIKO API key (https://apiko.app/): set APIKO_API_KEY or put it in api_key.txt in this node's folder."

log = logging.getLogger("comfyui-apiko")


def _interrupt_check():
    try:
        import comfy.model_management as mm

        return mm.throw_exception_if_processing_interrupted
    except ImportError:
        return None


def _progress_bar():
    try:
        import comfy.utils

        bar = comfy.utils.ProgressBar(100)
    except ImportError:
        return None

    def update(progress):
        try:
            value = float(str(progress).rstrip("%"))
        except (TypeError, ValueError):
            return
        bar.update_absolute(max(0, min(100, int(value))), 100)

    return update


def _extra(extra_json: str) -> dict:
    text = (extra_json or "").strip()
    if not text:
        return {}
    try:
        extra = json.loads(text)
    except ValueError as err:
        raise ApiError(f"extra_json is not valid JSON: {err}") from None
    if not isinstance(extra, dict):
        raise ApiError("extra_json must be a JSON object, e.g. {\"quality\": \"high\"}.")
    for key in ("model", "async"):
        extra.pop(key, None)
    return extra


def _urls(text: str) -> list[str]:
    urls = [line.strip() for line in (text or "").replace(",", "\n").splitlines() if line.strip()]
    for url in urls:
        if not url.startswith("https://"):
            raise ApiError(f"Reference media must be public https:// URLs: {url[:80]}")
    return urls


def _run(kind: str, model: str, body: dict, resume_task_id: str, timeout_s: int) -> tuple[list[bytes], str, str]:
    """Submit (or resume) one job, wait for it, and download every result file."""
    task_id = (resume_task_id or "").strip()
    urls: list[str] = []
    if not task_id:
        task_id, urls = client.submit(kind, model, body)
    if task_id:
        try:
            task = client.poll(
                task_id,
                interval=5 if kind == "image" else 10,
                timeout=timeout_s,
                on_progress=_progress_bar(),
                check_interrupt=_interrupt_check(),
            )
        except ApiError:
            raise
        except Exception:
            log.warning(f"[APIKO] stopped waiting for task {task_id}; it keeps running and is billed. Resume it with resume_task_id={task_id}")
            raise
        urls = client.result_urls(task)
    files = [client.download(u) for u in urls]
    return files, urls[0], task_id or ""


class APIKOImage:
    DESCRIPTION = (
        "Generate an image from text, or edit and combine images when `images` is connected, with Nano Banana, "
        "GPT Image, Seedream and more through one APIKO key. Billed per image to your APIKO balance; failed jobs "
        "are refunded. " + ACCOUNT_HINT
    )
    CATEGORY = CATEGORY
    FUNCTION = "generate"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "url", "task_id")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (IMAGE_MODELS, {"default": "nano-banana-2.1", "tooltip": "Prices and details: https://apiko.app/models"}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "size": (SIZES, {"default": DEFAULT, "tooltip": "Aspect ratio or pixel size; 'default' lets the model decide. Unsupported values are rejected before any charge."}),
                "resolution": (IMAGE_RESOLUTIONS, {"default": DEFAULT, "tooltip": "Output tier on models that have one (Nano Banana, GPT Image, Seedream 5.5). Higher tiers cost more."}),
                "quality": (QUALITIES, {"default": DEFAULT, "tooltip": "GPT Image only."}),
                "variation": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1, "tooltip": "Not sent to the API. ComfyUI reuses the last result while the inputs stay the same; change this to generate (and pay for) a new one."}),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "Reference images to edit or combine (a batch counts as several). Uploaded to APIKO, kept 72 hours."}),
                "custom_model": ("STRING", {"default": "", "tooltip": "Any other image model id from https://apiko.app/models; overrides `model`."}),
                "extra_json": ("STRING", {"multiline": True, "default": "", "tooltip": "Extra request fields as JSON, e.g. {\"background\": \"transparent\"}."}),
                "resume_task_id": ("STRING", {"default": "", "tooltip": "Fetch an earlier task instead of submitting a new one (free)."}),
                "timeout_minutes": ("INT", {"default": 10, "min": 1, "max": 60}),
            },
        }

    def generate(self, model, prompt, size, resolution, quality, variation, images=None, custom_model="",
                 extra_json="", resume_task_id="", timeout_minutes=10):
        model = (custom_model or "").strip() or model
        body: dict = {}
        if not (resume_task_id or "").strip():
            if not prompt.strip():
                raise ApiError("Write a prompt first.")
            body["prompt"] = prompt
            for key, val in (("size", size), ("resolution", resolution), ("quality", quality)):
                if val != DEFAULT:
                    body[key] = val
            if images is not None:
                body["images"] = batch_to_uploads(images)
            body.update(_extra(extra_json))
        files, url, task_id = _run("image", model, body, resume_task_id, timeout_minutes * 60)
        return (images_to_batch([bytes_to_image(f) for f in files]), url, task_id)


class APIKOVideo:
    DESCRIPTION = (
        "Generate a video from text, a first (and last) frame, or reference images and clips with Seedance, Kling, "
        "Wan, MiniMax, Grok Imagine and Veo-class models through one APIKO key. Billed per second of output; failed "
        "jobs are refunded. " + ACCOUNT_HINT
    )
    CATEGORY = CATEGORY
    FUNCTION = "generate"
    RETURN_TYPES = ("VIDEO", "STRING", "STRING")
    RETURN_NAMES = ("video", "url", "task_id")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (VIDEO_MODELS, {"default": "seedance-2.5", "tooltip": "Prices and details: https://apiko.app/models"}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "duration": ("INT", {"default": 5, "min": 0, "max": 30, "tooltip": "Seconds of output (billed per second). 0 = model default."}),
                "resolution": (VIDEO_RESOLUTIONS, {"default": DEFAULT}),
                "aspect_ratio": (ASPECTS, {"default": DEFAULT}),
                "generate_audio": (AUDIO, {"default": DEFAULT, "tooltip": "On models that can add sound (Seedance, Kling 3, Grok Imagine, Veo, Gemini Omni)."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2147483647, "control_after_generate": False, "tooltip": "0 = not sent. Sent only to models that take a seed (Seedance); changing it also makes ComfyUI run the node again."}),
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": "Start frame for image-to-video (required by Wan image-to-video)."}),
                "last_frame": ("IMAGE", {"tooltip": "End frame, on models that support it."}),
                "reference_images": ("IMAGE", {"tooltip": "Reference images (Image 1, Image 2 ... in the prompt) for Seedance, Kling 4.0, Wan reference and MiniMax."}),
                "reference_video_urls": ("STRING", {"multiline": True, "default": "", "tooltip": "Public https:// clips, one per line. Wan bills their length on top of the output."}),
                "reference_audio_urls": ("STRING", {"multiline": True, "default": "", "tooltip": "Public https:// wav or mp3, one per line."}),
                "custom_model": ("STRING", {"default": "", "tooltip": "Any other video model id from https://apiko.app/models; overrides `model`."}),
                "extra_json": ("STRING", {"multiline": True, "default": "", "tooltip": "Extra request fields as JSON."}),
                "resume_task_id": ("STRING", {"default": "", "tooltip": "Fetch an earlier task instead of submitting a new one (free)."}),
                "timeout_minutes": ("INT", {"default": 25, "min": 1, "max": 120}),
            },
        }

    def generate(self, model, prompt, duration, resolution, aspect_ratio, generate_audio, seed, first_frame=None,
                 last_frame=None, reference_images=None, reference_video_urls="", reference_audio_urls="",
                 custom_model="", extra_json="", resume_task_id="", timeout_minutes=25):
        model = (custom_model or "").strip() or model
        body: dict = {}
        if not (resume_task_id or "").strip():
            if not prompt.strip():
                raise ApiError("Write a prompt first.")
            body["prompt"] = prompt
            if duration:
                body["duration"] = duration
            if resolution != DEFAULT:
                body["resolution"] = resolution
            if aspect_ratio != DEFAULT:
                body["aspect_ratio"] = aspect_ratio
            if generate_audio != DEFAULT:
                body["generate_audio"] = generate_audio == "on"
            if seed:
                body["seed"] = seed
            if first_frame is not None:
                body["first_image_url"] = tensor_to_upload(first_frame[0])
            if last_frame is not None:
                body["last_image_url"] = tensor_to_upload(last_frame[0])
            if reference_images is not None:
                body["reference_image_urls"] = batch_to_uploads(reference_images, limit=30)
            if _urls(reference_video_urls):
                body["reference_videos"] = _urls(reference_video_urls)
            if _urls(reference_audio_urls):
                body["reference_audios"] = _urls(reference_audio_urls)
            body.update(_extra(extra_json))
        files, url, task_id = _run("video", model, body, resume_task_id, timeout_minutes * 60)
        return (bytes_to_video(files[0]), url, task_id)


class APIKOChat:
    DESCRIPTION = (
        "Ask a text model (Gemini, GPT, Claude, DeepSeek, Qwen, Kimi, GLM) through APIKO, for example to write or "
        "translate prompts for the image and video nodes. Billed per token. " + ACCOUNT_HINT
    )
    CATEGORY = CATEGORY
    FUNCTION = "chat"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (CHAT_MODELS, {"default": "gemini-3.5-flash", "tooltip": "Prices and details: https://apiko.app/models"}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "system": ("STRING", {"multiline": True, "default": ""}),
                "max_tokens": ("INT", {"default": 4096, "min": 1, "max": 65536, "tooltip": "Reasoning models spend part of this on thinking before they answer; keep it generous."}),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "Optional image for vision models (first image of the batch)."}),
                "custom_model": ("STRING", {"default": "", "tooltip": "Any other text model id from https://apiko.app/models."}),
            },
        }

    def chat(self, model, prompt, system, max_tokens, image=None, custom_model=""):
        model = (custom_model or "").strip() or model
        if not prompt.strip():
            raise ApiError("Write a prompt first.")
        content: object = prompt
        if image is not None:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": tensor_to_upload(image[0])}},
            ]
        messages = ([{"role": "system", "content": system}] if system.strip() else []) + [{"role": "user", "content": content}]
        res = client.request("POST", "/v1/chat/completions", {"model": model, "messages": messages, "max_tokens": max_tokens}, timeout=300)
        try:
            choice = res["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise ApiError(f"Unexpected chat response: {json.dumps(res)[:300]}") from None
        text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        if choice.get("finish_reason") == "length":
            if not text.strip():
                raise ApiError(f"{model} used all {max_tokens} max_tokens (reasoning models think first) and wrote no answer. Raise max_tokens.")
            log.warning(f"[APIKO] {model} hit max_tokens={max_tokens}; the answer is cut off")
        return (text,)


NODE_CLASS_MAPPINGS = {
    "APIKOImage": APIKOImage,
    "APIKOVideo": APIKOVideo,
    "APIKOChat": APIKOChat,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "APIKOImage": "APIKO Image (Nano Banana, GPT Image, Seedream)",
    "APIKOVideo": "APIKO Video (Seedance, Kling, Wan)",
    "APIKOChat": "APIKO Chat (Gemini, GPT, Claude, DeepSeek)",
}
