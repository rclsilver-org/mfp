"""SQLite (async SQLAlchemy) data model: sessions, batches, pages, scan history."""
from __future__ import annotations

import time
import uuid

from sqlalchemy import ForeignKey, Integer, String, Float, Text, JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", future=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> int:
    return int(time.time())


class Base(DeclarativeBase):
    pass


class ScanSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_username: Mapped[str] = mapped_column(String, index=True)   # target user
    performed_by: Mapped[str] = mapped_column(String)                 # actual actor
    owner_uid_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="draft")
    # once saved, remember where/what so the session can be re-opened & overwritten
    saved_history_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saved_archive_path: Mapped[str | None] = mapped_column(String, nullable=True)
    saved_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)
    expires_at: Mapped[int] = mapped_column(Integer, default=lambda: _now() + settings.session_ttl_hours * 3600)

    batches: Mapped[list["Batch"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    pages: Mapped[list["Page"]] = relationship(back_populates="session", cascade="all, delete-orphan",
                                               order_by="Page.order_index")


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String, default="platen")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)

    session: Mapped[ScanSession] = relationship(back_populates="batches")


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    order_index: Mapped[float] = mapped_column(Float, default=0.0)
    blob_key: Mapped[str] = mapped_column(String)          # original jpeg path (immutable)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    crop: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {x,y,w,h} normalized
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String, default="platen")
    created_at: Mapped[int] = mapped_column(Integer, default=_now)

    session: Mapped[ScanSession] = relationship(back_populates="pages")


class ScanHistory(Base):
    __tablename__ = "scan_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[int] = mapped_column(Integer, default=_now, index=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)  # for re-open/edit
    user: Mapped[str] = mapped_column(String, index=True)      # target user
    performed_by: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    deliveries: Mapped[str] = mapped_column(Text, default="")  # csv of delivery targets
    source: Mapped[str] = mapped_column(String, default="")
    archive_path: Mapped[str | None] = mapped_column(String, nullable=True)  # for later download


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
