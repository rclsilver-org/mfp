"""Fake scanner for local dev (SCANAPP_FAKE_SCANNER=true): generate realistic,
visually distinct document pages instead of driving the real device — so the
workbench (reorder / interleave / preview / finalize) can be exercised without
putting paper on the glass. Nothing is scanned or printed.
"""
from __future__ import annotations

import io
import random
import time

from PIL import Image, ImageDraw, ImageFont

_TITLES = ["Facture", "Contrat", "Relevé bancaire", "Lettre", "Bulletin de paie",
           "Ordonnance", "Attestation", "Devis", "Note de frais", "Courrier"]


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _page(title: str, batch_label: str, n: int, total: int, color: str) -> bytes:
    W, H = 1240, 1754  # ~A4 @150dpi
    im = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(im)

    # faint big page number watermark
    d.text((W - 380, H - 360), str(n), fill=(232, 232, 232), font=_font(300))

    # header
    d.rectangle([0, 0, W, 160], fill=(226, 232, 244))
    d.text((60, 45), title, fill=(20, 30, 60), font=_font(58))
    d.text((60, 118), f"{batch_label} · page {n}/{total} · {time.strftime('%H:%M:%S')}",
           fill=(90, 90, 90), font=_font(28))

    # body: fake text lines
    y = 230
    for _ in range(30):
        w = random.randint(int(W * 0.35), int(W * 0.85))
        d.rectangle([80, y, 80 + w, y + 16], fill=(208, 208, 208))
        y += 46
        if y > H - 260:
            break

    # footer
    d.line([60, H - 96, W - 60, H - 96], fill=(200, 200, 200), width=2)
    d.text((60, H - 74), f"[FAUX SCAN] {batch_label} — page {n}", fill=(150, 150, 150), font=_font(26))

    if color == "Grayscale8":
        im = im.convert("L").convert("RGB")
    elif color == "BlackAndWhite1":
        im = im.convert("1").convert("RGB")

    out = io.BytesIO()
    im.save(out, format="JPEG", quality=85)
    return out.getvalue()


def generate_pages(source: str, color: str, batch_label: str, count: int | None = None):
    """Yield realistic JPEG pages. Platen -> 1 page, ADF -> a few pages."""
    if count is None:
        count = 1 if source.lower() in ("platen", "flatbed") else random.randint(3, 6)
    title = f"{random.choice(_TITLES)} #{random.randint(1000, 9999)}"
    for n in range(1, count + 1):
        yield _page(title, batch_label, n, count, color)
