"""Database engine, session factory and declarative base."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
BOOTSTRAP_ADVISORY_LOCK_KEY = 0x4D45544A495241
_FACET_AFFECTING_MODELS = {
    "Issue",
    "IssueMachine",
    "Machine",
    "MachineGroup",
    "Project",
    "ProjectMember",
    "Status",
    "User",
}


@event.listens_for(Session, "before_flush")
def _mark_facet_cache_dirty(session: Session, _flush_context, _instances) -> None:
    changed = (*session.new, *session.dirty, *session.deleted)
    if any(type(row).__name__ in _FACET_AFFECTING_MODELS for row in changed):
        session.info["facet_cache_dirty"] = True


@event.listens_for(Session, "after_commit")
def _invalidate_facet_cache(session: Session) -> None:
    if session.info.pop("facet_cache_dirty", False):
        from .services.search import clear_facet_cache

        clear_facet_cache()
    from .services import live

    live.flush_queued(session)


@event.listens_for(Session, "after_rollback")
def _clear_facet_dirty_flag(session: Session) -> None:
    session.info.pop("facet_cache_dirty", None)
    from .services import live

    live.clear_queued(session)


def _json_dumps(value: object) -> str:
    # Keep CJK readable so label / scope filters can match the stored JSON text.
    return json.dumps(value, ensure_ascii=False)


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(
            url, connect_args={"check_same_thread": False}, json_serializer=_json_dumps
        )

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - sqlite only
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    settings = get_settings()
    connect_args = {
        "application_name": settings.db_application_name,
        "connect_timeout": settings.db_connect_timeout,
        "prepare_threshold": (
            None if settings.db_prepare_threshold < 0 else settings.db_prepare_threshold
        ),
    }
    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_use_lifo=settings.db_pool_use_lifo,
        json_serializer=_json_dumps,
    )


def configure(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    _engine = _make_engine(url or get_settings().database_url)
    from .metrics import instrument_pool

    instrument_pool(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def is_postgres(db: Session | None = None) -> bool:
    bind = db.get_bind() if db is not None else engine()
    return bind.dialect.name == "postgresql"


@contextmanager
def postgres_advisory_lock(
    connection: Connection, key: int = BOOTSTRAP_ADVISORY_LOCK_KEY
) -> Iterator[None]:
    """Serialize process-wide PostgreSQL operations on one checked-out connection."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL advisory locks require a PostgreSQL connection")
    connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
    try:
        yield
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def init_db(connection: Connection | None = None) -> None:
    """Bring the schema up to date: Alembic on PostgreSQL, create_all elsewhere (tests)."""
    from . import models  # noqa: F401 - register tables

    eng = connection.engine if connection is not None else engine()
    if eng.dialect.name != "postgresql":
        Base.metadata.create_all(eng)
        return

    from alembic import command
    from alembic.config import Config

    from .config import ROOT

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    if connection is not None:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        return
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
