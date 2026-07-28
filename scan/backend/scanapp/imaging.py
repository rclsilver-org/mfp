"""Scratch storage for page images + non-destructive edit application (Pillow)."""
from __future__ import annotations

import io
import os

from PIL import Image

from .config import settings

Image.MAX_IMAGE_PIXELS = None  # scans can be large; we trust our own device


def _sess_dir(session_id: str) -> str:
    d = os.path.join(settings.scratch_dir, session_id)
    os.makedirs(os.path.join(d, "originals"), exist_ok=True)
    os.makedirs(os.path.join(d, "derived"), exist_ok=True)
    return d


def save_original(session_id: str, page_id: str, jpeg: bytes) -> tuple[str, int, int]:
    d = _sess_dir(session_id)
    path = os.path.join(d, "originals", f"{page_id}.jpg")
    with open(path, "wb") as f:
        f.write(jpeg)
    with Image.open(io.BytesIO(jpeg)) as im:
        w, h = im.size
    return path, w, h


def _apply_edits(im: Image.Image, rotation: int, crop: dict | None) -> Image.Image:
    if crop:
        w, h = im.size
        x = int(crop["x"] * w); y = int(crop["y"] * h)
        cw = int(crop["w"] * w); ch = int(crop["h"] * h)
        im = im.crop((x, y, x + cw, y + ch))
    if rotation:
        im = im.rotate(-rotation, expand=True)  # clockwise degrees
    return im


def render_edited(blob_key: str, rotation: int, crop: dict | None) -> bytes:
    with Image.open(blob_key) as im:
        im = im.convert("RGB")
        im = _apply_edits(im, rotation, crop)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=92)
    return out.getvalue()


def thumbnail(blob_key: str, rotation: int, crop: dict | None, max_px: int = 240) -> bytes:
    with Image.open(blob_key) as im:
        im = im.convert("RGB")
        im = _apply_edits(im, rotation, crop)
        im.thumbnail((max_px, max_px))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=80)
    return out.getvalue()


def cleanup_session(session_id: str):
    import shutil
    shutil.rmtree(os.path.join(settings.scratch_dir, session_id), ignore_errors=True)
