"""Background maintenance: purge expired sessions (and their scratch)."""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import delete, select

from . import imaging
from .models import Batch, Page, ScanSession, async_session


async def _purge_expired():
    now = int(time.time())
    async with async_session() as db:
        expired = (await db.execute(
            select(ScanSession).where(ScanSession.expires_at < now)
        )).scalars().all()
        for s in expired:
            imaging.cleanup_session(s.owner_username, s.id)
            await db.execute(delete(Page).where(Page.session_id == s.id))
            await db.execute(delete(Batch).where(Batch.session_id == s.id))
            await db.delete(s)
        await db.commit()
        return len(expired)


async def cleanup_loop(interval: int = 3600):
    while True:
        try:
            await _purge_expired()
        except Exception:  # noqa: BLE001 - never let maintenance crash the app
            pass
        await asyncio.sleep(interval)
