"""Page image storage + non-destructive edits (Pillow) + PDF re-rasterization.

In-progress page images live under the owner's tree, transient:
    <scan_archive>/<owner>/.scratch/<session>/originals/<page>.jpg
They are removed when the document is saved (finalize) and purged by TTL.
A saved document can still be re-opened by rasterizing its archived PDF.
"""
from __future__ import annotations

import io
import os
import shutil

from PIL import Image

from .config import settings

Image.MAX_IMAGE_PIXELS = None  # scans can be large; we trust our own device


def scratch_dir(owner: str, session_id: str) -> str:
    d = os.path.join(settings.scan_archive_dir, owner, ".scratch", session_id)
    os.makedirs(os.path.join(d, "originals"), exist_ok=True)
    return d


def save_original(owner: str, session_id: str, page_id: str, jpeg: bytes) -> tuple[str, int, int]:
    path = os.path.join(scratch_dir(owner, session_id), "originals", f"{page_id}.jpg")
    with open(path, "wb") as f:
        f.write(jpeg)
    with Image.open(io.BytesIO(jpeg)) as im:
        w, h = im.size
    return path, w, h


def cleanup_session(owner: str, session_id: str):
    shutil.rmtree(os.path.join(settings.scan_archive_dir, owner, ".scratch", session_id),
                  ignore_errors=True)


def pdf_to_jpegs(pdf_path: str, dpi: int = 200):
    """Rasterize each page of a PDF to JPEG bytes (for re-opening a saved doc)."""
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            yield pix.tobytes("jpeg")
    finally:
        doc.close()


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
