"""Assemble ordered pages into a PDF and deliver it (archive/home/email/download)."""
from __future__ import annotations

import os
import re
import time
from email.message import EmailMessage

import aiosmtplib
import img2pdf
from sqlalchemy import delete, select

from .config import settings
from . import imaging
from .models import Batch, Page, ScanHistory, ScanSession, async_session


def _safe(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]", "", name or "").strip().replace(" ", "_")
    return name or "scan"


def _chown_best_effort(path: str, uid: int | None, group: str | None, mode: int):
    import grp
    import shutil
    try:
        gid = grp.getgrnam(group).gr_gid if group else -1
    except (KeyError, PermissionError):
        gid = -1
    try:
        os.chown(path, uid if uid is not None else -1, gid)
    except (PermissionError, OSError):
        pass
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def build_pdf(session_id: str, pages: list[Page], out_path: str):
    imgs = []
    for p in pages:
        imgs.append(imaging.render_edited(p.blob_key, p.rotation, p.crop))
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(img2pdf.convert(imgs))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


async def finalize(session_id: str, name: str, deliveries: list[str], overwrite: bool = False) -> dict:
    async with async_session() as db:
        sess = await db.get(ScanSession, session_id)
        if sess is None:
            raise ValueError("session not found")
        pages = (await db.execute(
            select(Page).where(Page.session_id == session_id).order_by(Page.order_index)
        )).scalars().all()
        if not pages:
            raise ValueError("no pages to finalize")

        safe = _safe(name or sess.name or "scan")
        do_overwrite = overwrite and sess.saved_archive_path and sess.saved_history_id

        if do_overwrite:
            dst = sess.saved_archive_path
            filename = sess.saved_filename or os.path.basename(dst)
        else:
            ts = time.strftime("%H-%M-%S", time.localtime())
            day = time.strftime("%Y/%m/%d", time.localtime())
            filename = f"{ts}_{safe}.pdf"
            dst = os.path.join(settings.scan_archive_dir, sess.owner_username, day, filename)

        # build the PDF in the session scratch, then archive it (the canonical copy)
        pdf_path = os.path.join(imaging.scratch_dir(sess.owner_username, session_id), filename)
        build_pdf(session_id, pages, pdf_path)

        results: dict[str, str] = {}
        archive_path = None
        try:
            base = os.path.join(settings.scan_archive_dir, sess.owner_username)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _copy(pdf_path, dst)
            for root, _dirs, _files in os.walk(base):
                _chown_best_effort(root, 0, settings.archive_group, 0o2750)
            _chown_best_effort(dst, 0, settings.archive_group, 0o640)
            archive_path = dst
            results["archive"] = "overwritten" if do_overwrite else "ok"
        except Exception as exc:  # noqa: BLE001
            results["archive"] = f"error: {exc}"

        if "email" in deliveries:
            results["email"] = await _send_email(sess.owner_email, filename, pdf_path)

        src = ",".join(sorted({p.source for p in pages}))
        if do_overwrite:
            hist = await db.get(ScanHistory, sess.saved_history_id)
            if hist is not None:
                hist.ts = int(time.time()); hist.name = safe; hist.pages = len(pages)
                hist.deliveries = ",".join(deliveries); hist.source = src
                hist.archive_path = archive_path; hist.session_id = session_id
            else:
                do_overwrite = False
        if not do_overwrite:
            hist = ScanHistory(
                session_id=session_id, user=sess.owner_username, performed_by=sess.performed_by,
                name=safe, pages=len(pages), deliveries=",".join(deliveries),
                source=src, archive_path=archive_path,
            )
            db.add(hist)
            await db.flush()

        sess.status = "delivered"
        sess.saved_history_id = hist.id
        sess.saved_archive_path = archive_path
        sess.saved_filename = filename
        npages = len(pages)

        # scratch is cleaned on save; the archived PDF becomes the source for a
        # later re-open (pages are re-rasterized from it). Drop the page/batch rows.
        await db.execute(delete(Page).where(Page.session_id == session_id))
        await db.execute(delete(Batch).where(Batch.session_id == session_id))
        await db.commit()
        imaging.cleanup_session(sess.owner_username, session_id)

        return {"id": hist.id, "filename": filename, "pages": npages,
                "results": results, "overwritten": bool(do_overwrite)}


def _copy(src: str, dst: str):
    import shutil
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


async def _send_email(to: str | None, filename: str, pdf_path: str) -> str:
    if not settings.email_enabled:
        return "disabled"
    if not to:
        return "error: no email address"
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = f"Scan: {filename}"
    msg.set_content("Votre document scanné est en pièce jointe.")
    with open(pdf_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=filename)
    try:
        await aiosmtplib.send(msg, hostname=settings.smtp_host, port=settings.smtp_port)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"
