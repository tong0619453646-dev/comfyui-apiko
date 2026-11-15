"""Conversions between ComfyUI tensors and the files APIKO sends and receives."""

from __future__ import annotations

import logging
from io import BytesIO

from .client import UPLOAD_MAX_BYTES, ApiError, upload_image

log = logging.getLogger("comfyui-apiko")


def tensor_to_upload(image) -> str:
    """Upload one HxWxC (or 1xHxWxC) float image tensor as PNG, or JPEG when the PNG is over 10 MB."""
    import numpy as np
    from PIL import Image

    if image.dim() == 4:
        image = image[0]
    array = (image.clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)
    if array.shape[-1] == 1:
        array = array[..., 0]
    pil = Image.fromarray(array)
    buf = BytesIO()
    pil.save(buf, format="PNG", optimize=True)
    if buf.tell() <= UPLOAD_MAX_BYTES:
        return upload_image(buf.getvalue(), "image/png")
    buf = BytesIO()
    pil.convert("RGB").save(buf, format="JPEG", quality=95)
    return upload_image(buf.getvalue(), "image/jpeg")


def batch_to_uploads(images, limit: int = 16) -> list[str]:
    """Upload every image of a batch (in order) and return their public URLs."""
    if images is None:
        return []
    if images.shape[0] > limit:
        raise ApiError(f"At most {limit} reference images per request; the batch has {images.shape[0]}.")
    return [tensor_to_upload(images[i]) for i in range(images.shape[0])]


def bytes_to_image(raw: bytes):
    """Decode image bytes to a 1xHxWx3 float tensor."""
    import numpy as np
    import torch
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(BytesIO(raw)) as pil:
            pil = ImageOps.exif_transpose(pil)
            array = np.asarray(pil.convert("RGB"), dtype=np.float32) / 255.0
    except (UnidentifiedImageError, OSError, ValueError):
        raise ApiError("The result is not a readable image.") from None
    return torch.from_numpy(array.copy()).unsqueeze(0)


def images_to_batch(tensors: list):
    """Stack results; images whose size differs from the first are left out (ComfyUI batches need one size)."""
    import torch

    first = tensors[0]
    same = [t for t in tensors if t.shape == first.shape]
    if len(same) < len(tensors):
        log.info(f"[APIKO] {len(tensors) - len(same)} result image(s) have a different size and were left out of the batch")
    return torch.cat(same, dim=0)


def bytes_to_video(raw: bytes):
    """Wrap MP4 bytes as a ComfyUI VIDEO (needs a ComfyUI with native video support, 2025 or later)."""
    try:
        from comfy_api.latest import InputImpl

        return InputImpl.VideoFromFile(BytesIO(raw))
    except ImportError:
        pass
    try:
        from comfy_api.input_impl import VideoFromFile

        return VideoFromFile(BytesIO(raw))
    except ImportError:
        raise ApiError("This ComfyUI has no native VIDEO type; update ComfyUI to use the APIKO Video node.") from None
