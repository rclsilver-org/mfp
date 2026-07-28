"""Single-shared-scanner lock + batch scan orchestration.

The physical scanner is one resource; replicas=1 makes an in-process asyncio lock
the correct, simplest guard. A scan acquires the lock for the whole job.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from .config import settings
from .escl.client import EsclClient
from . import fakescan, imaging
from .models import Batch, Page, ScanSession, async_session

_lock = asyncio.Lock()
_held_by: str | None = None


def held_by() -> str | None:
    return _held_by


def is_busy() -> bool:
    return _lock.locked()


class SessionBusy(Exception):
    pass


async def scan_batch(session_id: str, actor: str, options: dict) -> dict:
    """Acquire the scanner, scan a batch, append pages to the session. Fail fast if busy."""
    global _held_by
    if _lock.locked():
        raise SessionBusy(_held_by or "someone")
    async with _lock:
        _held_by = actor
        try:
            return await _do_scan(session_id, options)
        finally:
            _held_by = None


async def _do_scan(session_id: str, options: dict) -> dict:
    source = options.get("source", "platen")
    color = options.get("color", "RGB24")
    resolution = int(options.get("resolution", 300))
    page_size = options.get("page_size", "A4")

    async with async_session() as db:
        sess = await db.get(ScanSession, session_id)
        if sess is None:
            raise ValueError("session not found")
        # next batch index + starting order_index
        existing = (await db.execute(select(Batch).where(Batch.session_id == session_id))).scalars().all()
        batch = Batch(session_id=session_id, index=len(existing), source=source, options=options)
        db.add(batch)
        await db.flush()

        pages = (await db.execute(select(Page).where(Page.session_id == session_id))).scalars().all()
        order = (max((p.order_index for p in pages), default=0.0)) + 10.0

        async def _pages():
            if settings.fake_scanner:
                import asyncio
                for jpeg in fakescan.generate_pages(source, color, f"Lot {batch.index + 1}"):
                    await asyncio.sleep(0.15)  # simulate scan time
                    yield jpeg
            else:
                async for jpeg in EsclClient().scan(source=source, color=color,
                                                    resolution=resolution, page_size=page_size):
                    yield jpeg

        count = 0
        async for jpeg in _pages():
            page = Page(session_id=session_id, batch_id=batch.id, order_index=order, source=source, blob_key="")
            db.add(page)
            await db.flush()
            path, w, h = imaging.save_original(session_id, page.id, jpeg)
            page.blob_key, page.width, page.height = path, w, h
            order += 10.0
            count += 1
        await db.commit()
        return {"batch_id": batch.id, "pages": count}
