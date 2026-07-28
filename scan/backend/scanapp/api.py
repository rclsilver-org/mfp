"""REST API for the scan app."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, select

from . import history as history_mod
from . import imaging, scanner
from .auth import ldap
from .auth.ldap import LdapUser
from .config import settings
from .deps import current_user, require_printers, resolve_target
from .escl.client import AdfEmptyOrJam, EsclClient, ScannerBusy
from .finalize import finalize as do_finalize
from .models import Batch, Page, ScanHistory, ScanSession, async_session

router = APIRouter(prefix="/api")
_caps_cache: dict = {}


# ---------- identity / scanner ----------
@router.get("/me")
async def me(user: LdapUser = Depends(current_user)):
    return {"username": user.uid, "email": user.mail, "display_name": user.display_name,
            "uid_number": user.uid_number, "can_scan": user.can_scan, "is_admin": user.is_admin}


@router.get("/scanner/capabilities")
async def capabilities(_: LdapUser = Depends(require_printers)):
    if settings.fake_scanner:
        src = {"max_width": 2550, "max_height": 3508,
               "color_modes": ["RGB24", "Grayscale8", "BlackAndWhite1"],
               "resolutions": [150, 300, 600], "formats": ["image/jpeg", "application/pdf"]}
        return {"make_and_model": "Fake Scanner (dev)", "platen": src, "adf": src,
                "adf_duplex": False, "page_sizes": ["A4", "Letter", "Legal"]}
    if "caps" not in _caps_cache:
        _caps_cache["caps"] = await EsclClient().capabilities()
    c = _caps_cache["caps"]
    def src(s):
        return None if s is None else {"max_width": s.max_width, "max_height": s.max_height,
                                       "color_modes": s.color_modes, "resolutions": s.resolutions,
                                       "formats": s.formats}
    return {"make_and_model": c.make_and_model, "platen": src(c.platen),
            "adf": src(c.adf), "adf_duplex": c.adf_duplex,
            "page_sizes": ["A4", "Letter", "Legal"]}


@router.get("/scanner/status")
async def scanner_status(_: LdapUser = Depends(require_printers)):
    if settings.fake_scanner:
        st = {"state": "Idle (faux scanner)", "adf_state": None, "idle": True}
    else:
        try:
            st = await EsclClient().status()
        except Exception as exc:  # noqa: BLE001
            st = {"state": "Unreachable", "error": str(exc), "idle": False}
    st["busy"] = scanner.is_busy()
    st["held_by"] = scanner.held_by()
    return st


# ---------- sessions ----------
class CreateSession(BaseModel):
    name: str = ""
    on_behalf_of: str | None = None


async def _owned_session(sid: str, user: LdapUser) -> ScanSession:
    async with async_session() as db:
        sess = await db.get(ScanSession, sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    if not (user.is_admin or sess.owner_username == user.uid or sess.performed_by == user.uid):
        raise HTTPException(403, "not your session")
    return sess


@router.post("/sessions")
async def create_session(body: CreateSession, actor: LdapUser = Depends(require_printers)):
    target = resolve_target(actor, body.on_behalf_of)
    async with async_session() as db:
        sess = ScanSession(owner_username=target.uid, performed_by=actor.uid,
                           owner_uid_number=target.uid_number, owner_email=target.mail,
                           name=body.name)
        db.add(sess)
        await db.commit()
        return {"id": sess.id, "owner": sess.owner_username, "performed_by": sess.performed_by,
                "name": sess.name, "status": sess.status}


@router.get("/sessions")
async def list_sessions(user: LdapUser = Depends(current_user)):
    async with async_session() as db:
        stmt = select(ScanSession).order_by(ScanSession.updated_at.desc())
        if not user.is_admin:
            stmt = stmt.where(ScanSession.owner_username == user.uid)
        sess = (await db.execute(stmt)).scalars().all()
        return [{"id": s.id, "owner": s.owner_username, "name": s.name, "status": s.status,
                 "updated_at": s.updated_at} for s in sess]


@router.get("/sessions/{sid}")
async def get_session(sid: str, user: LdapUser = Depends(current_user)):
    sess = await _owned_session(sid, user)
    async with async_session() as db:
        pages = (await db.execute(select(Page).where(Page.session_id == sid)
                                  .order_by(Page.order_index))).scalars().all()
        batches = (await db.execute(select(Batch).where(Batch.session_id == sid)
                                    .order_by(Batch.index))).scalars().all()
    counts: dict[str, int] = {}
    for p in pages:
        counts[p.batch_id] = counts.get(p.batch_id, 0) + 1
    return {"id": sess.id, "owner": sess.owner_username, "performed_by": sess.performed_by,
            "name": sess.name, "status": sess.status,
            "saved_history_id": sess.saved_history_id, "saved_filename": sess.saved_filename,
            "batches": [{"id": b.id, "index": b.index, "source": b.source,
                         "count": counts.get(b.id, 0)} for b in batches],
            "pages": [_page_dto(p) for p in pages]}


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, user: LdapUser = Depends(current_user)):
    await _owned_session(sid, user)
    async with async_session() as db:
        await db.execute(delete(ScanSession).where(ScanSession.id == sid))
        await db.commit()
    imaging.cleanup_session(sid)
    return {"ok": True}


# ---------- scanning ----------
class ScanOptions(BaseModel):
    source: str = "platen"
    color: str = "RGB24"
    resolution: int = 300
    page_size: str = "A4"


@router.post("/sessions/{sid}/scan")
async def scan(sid: str, opts: ScanOptions, actor: LdapUser = Depends(require_printers)):
    await _owned_session(sid, actor)
    try:
        res = await scanner.scan_batch(sid, actor.uid, opts.model_dump())
    except scanner.SessionBusy as exc:
        raise HTTPException(409, f"scanner busy (held by {exc})") from exc
    except ScannerBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except AdfEmptyOrJam as exc:
        raise HTTPException(400, str(exc)) from exc
    return res


# ---------- pages ----------
def _page_dto(p: Page) -> dict:
    return {"id": p.id, "batch_id": p.batch_id, "order_index": p.order_index,
            "rotation": p.rotation, "crop": p.crop, "width": p.width, "height": p.height,
            "source": p.source}


async def _page(pid: str, user: LdapUser) -> Page:
    async with async_session() as db:
        p = await db.get(Page, pid)
    if p is None:
        raise HTTPException(404, "page not found")
    await _owned_session(p.session_id, user)
    return p


@router.get("/pages/{pid}/thumb")
async def page_thumb(pid: str, user: LdapUser = Depends(current_user)):
    p = await _page(pid, user)
    data = imaging.thumbnail(p.blob_key, p.rotation, p.crop)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache", "ETag": f'"{pid}-{p.rotation}"'})


@router.get("/pages/{pid}/preview")
async def page_preview(pid: str, user: LdapUser = Depends(current_user)):
    p = await _page(pid, user)
    data = imaging.render_edited(p.blob_key, p.rotation, p.crop)
    return Response(content=data, media_type="image/jpeg")


class PageEdit(BaseModel):
    rotation: int | None = None
    crop: dict | None = None


@router.patch("/pages/{pid}")
async def edit_page(pid: str, body: PageEdit, user: LdapUser = Depends(current_user)):
    await _page(pid, user)
    async with async_session() as db:
        p = await db.get(Page, pid)
        if body.rotation is not None:
            p.rotation = body.rotation % 360
        if body.crop is not None:
            p.crop = body.crop or None
        await db.commit()
        return _page_dto(p)


@router.delete("/pages/{pid}")
async def delete_page(pid: str, user: LdapUser = Depends(current_user)):
    await _page(pid, user)
    async with async_session() as db:
        await db.execute(delete(Page).where(Page.id == pid))
        await db.commit()
    return {"ok": True}


class Reorder(BaseModel):
    order: list[str]


@router.post("/sessions/{sid}/pages/reorder")
async def reorder(sid: str, body: Reorder, user: LdapUser = Depends(current_user)):
    await _owned_session(sid, user)
    async with async_session() as db:
        for i, pid in enumerate(body.order):
            p = await db.get(Page, pid)
            if p and p.session_id == sid:
                p.order_index = float((i + 1) * 10)
        await db.commit()
    return {"ok": True}


@router.post("/sessions/{sid}/batches/{bid}/reverse")
async def reverse_batch(sid: str, bid: str, user: LdapUser = Depends(current_user)):
    """Reverse the order of pages within a batch, in place (other pages untouched)."""
    await _owned_session(sid, user)
    async with async_session() as db:
        pages = (await db.execute(select(Page).where(Page.session_id == sid)
                                  .order_by(Page.order_index))).scalars().all()
        bpages = [p for p in pages if p.batch_id == bid]
        slots = [p.order_index for p in bpages]
        for i, p in enumerate(bpages):
            p.order_index = slots[len(slots) - 1 - i]
        await db.commit()
    return {"ok": True, "reversed": len(bpages)}


class Interleave(BaseModel):
    front_batch: str
    back_batch: str
    back_reversed: bool = True


@router.post("/sessions/{sid}/interleave")
async def interleave(sid: str, body: Interleave, user: LdapUser = Depends(current_user)):
    """Zip a front batch (rectos) with a back batch (versos). The ADF is simplex,
    so versos come out reversed -> back_reversed=True rebuilds the duplex order."""
    await _owned_session(sid, user)
    if body.front_batch == body.back_batch:
        raise HTTPException(400, "front and back batches must differ")
    async with async_session() as db:
        pages = (await db.execute(select(Page).where(Page.session_id == sid)
                                  .order_by(Page.order_index))).scalars().all()
        front = [p for p in pages if p.batch_id == body.front_batch]
        back = [p for p in pages if p.batch_id == body.back_batch]
        if not front or not back:
            raise HTTPException(400, "both batches must have pages")
        if body.back_reversed:
            back = back[::-1]
        interleaved: list[Page] = []
        for i in range(max(len(front), len(back))):
            if i < len(front):
                interleaved.append(front[i])
            if i < len(back):
                interleaved.append(back[i])
        others = [p for p in pages if p.batch_id not in (body.front_batch, body.back_batch)]
        for i, p in enumerate(interleaved + others):
            p.order_index = float((i + 1) * 10)
        await db.commit()
    return {"ok": True, "pages": len(interleaved) + len(others)}


# ---------- finalize ----------
class Finalize(BaseModel):
    name: str = ""
    deliveries: list[str] = []
    overwrite: bool = False


@router.post("/sessions/{sid}/finalize")
async def finalize_session(sid: str, body: Finalize, user: LdapUser = Depends(current_user)):
    await _owned_session(sid, user)
    try:
        return await do_finalize(sid, body.name, body.deliveries, body.overwrite)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sessions/{sid}/download")
async def download(sid: str, user: LdapUser = Depends(current_user)):
    import glob
    import os
    await _owned_session(sid, user)
    out = sorted(glob.glob(os.path.join(settings.scratch_dir, sid, "output", "*.pdf")))
    if not out:
        raise HTTPException(404, "no finalized document")
    path = out[-1]
    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))


# ---------- history / users ----------
@router.get("/history")
async def get_history(user: str | None = None, type: str | None = None,
                      limit: int = Query(500, le=2000), viewer: LdapUser = Depends(current_user)):
    return await history_mod.get_history(viewer, user, type, limit)


async def _owned_history(hid: int, user: LdapUser) -> ScanHistory:
    async with async_session() as db:
        h = await db.get(ScanHistory, hid)
    if h is None:
        raise HTTPException(404, "document not found")
    if not (user.is_admin or h.user == user.uid):
        raise HTTPException(403, "not yours")
    return h


@router.post("/history/{hid}/reopen")
async def reopen_document(hid: int, user: LdapUser = Depends(current_user)):
    h = await _owned_history(hid, user)
    if not h.session_id:
        raise HTTPException(410, "document not editable")
    async with async_session() as db:
        sess = await db.get(ScanSession, h.session_id)
        pages = (await db.execute(select(Page).where(Page.session_id == h.session_id))).scalars().all()
    if sess is None or not pages:
        raise HTTPException(410, "document too old to re-open (session cleaned up)")
    return {"session_id": sess.id, "name": h.name}


@router.post("/documents/{hid}/email")
async def email_document(hid: int, user: LdapUser = Depends(current_user)):
    import os
    from .finalize import _send_email
    h = await _owned_history(hid, user)
    if not h.archive_path or not os.path.isfile(h.archive_path):
        raise HTTPException(410, "file no longer available")
    target = ldap.get_user(h.user)
    to = target.mail if target else None
    res = await _send_email(to, os.path.basename(h.archive_path), h.archive_path)
    if res != "ok":
        raise HTTPException(502, f"email: {res}")
    return {"email": "ok", "to": to}


@router.get("/documents/{hid}/download")
async def download_document(hid: int, user: LdapUser = Depends(current_user)):
    import os
    async with async_session() as db:
        h = await db.get(ScanHistory, hid)
    if h is None or not h.archive_path:
        raise HTTPException(404, "document not found")
    if not (user.is_admin or h.user == user.uid):
        raise HTTPException(403, "not yours")
    if not os.path.isfile(h.archive_path):
        raise HTTPException(410, "file no longer available")
    return FileResponse(h.archive_path, media_type="application/pdf",
                        filename=os.path.basename(h.archive_path))


@router.get("/users")
async def list_users(admin: LdapUser = Depends(current_user)):
    if not admin.is_admin:
        raise HTTPException(403, "admins only")
    import ldap3
    srv = ldap3.Server(settings.ldap_url, get_info=ldap3.NONE)
    conn = ldap3.Connection(srv, auto_bind=True)
    try:
        conn.search(settings.ldap_user_base, "(objectClass=posixAccount)",
                    attributes=["uid", "cn", "displayName"])
        return sorted([{"uid": e["uid"].value,
                        "display_name": (e["displayName"].value if "displayName" in e else None) or (e["cn"].value if "cn" in e else e["uid"].value)}
                       for e in conn.entries], key=lambda u: u["uid"])
    finally:
        conn.unbind()
