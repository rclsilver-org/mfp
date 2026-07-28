"""Unified print + scan history, scoped by the viewer's rights."""
from __future__ import annotations

import os
import sqlite3

from sqlalchemy import select

from .auth.ldap import LdapUser
from .config import settings
from .models import ScanHistory, async_session


async def get_history(viewer: LdapUser, user: str | None, type_filter: str | None,
                      limit: int = 500) -> list[dict]:
    # scoping: non-admins can only see their own
    if not viewer.is_admin:
        target = viewer.uid
    else:
        target = user  # None => everyone

    rows: list[dict] = []

    # --- scans (our DB) ---
    if type_filter in (None, "scan"):
        async with async_session() as db:
            stmt = select(ScanHistory).order_by(ScanHistory.ts.desc()).limit(limit)
            if target:
                stmt = select(ScanHistory).where(ScanHistory.user == target).order_by(ScanHistory.ts.desc()).limit(limit)
            for h in (await db.execute(stmt)).scalars().all():
                rows.append({"id": h.id, "type": "scan", "ts": h.ts, "user": h.user,
                             "performed_by": h.performed_by, "document": h.name,
                             "pages": h.pages, "source_ip": None,
                             "deliveries": h.deliveries,
                             "downloadable": bool(h.archive_path)})

    # --- prints (CUPS proxy sqlite, read-only) ---
    if type_filter in (None, "print") and os.path.exists(settings.print_metrics_db):
        try:
            uri = f"file:{settings.print_metrics_db}?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                q = "SELECT ts,user,host,name,pages FROM history"
                params: tuple = ()
                if target:
                    q += " WHERE user=?"
                    params = (target,)
                q += " ORDER BY ts DESC LIMIT ?"
                params = params + (limit,)
                for ts, u, host, name, pages in con.execute(q, params):
                    rows.append({"id": None, "type": "print", "ts": ts, "user": u,
                                 "performed_by": None, "document": name,
                                 "pages": pages, "source_ip": host, "deliveries": None,
                                 "downloadable": False})
            finally:
                con.close()
        except sqlite3.Error:
            pass

    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit]
