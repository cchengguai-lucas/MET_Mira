from __future__ import annotations

import importlib
import os
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient


def test_postgres_lifespan_has_no_bootstrap_side_effects(app, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "is_postgres", lambda: True)
    monkeypatch.setattr(
        main, "init_db", lambda: pytest.fail("PostgreSQL lifespan ran migrations")
    )
    monkeypatch.setattr(main, "seed", lambda: pytest.fail("PostgreSQL lifespan ran seed"))
    with TestClient(app):
        pass


def test_sample_project_seeds_once_when_opted_in(app, admin):
    """Empty projects + seed must not recreate ELEC after the once-only marker exists."""
    from sqlalchemy import func, select

    from backend import config
    from backend.db import session_factory
    from backend.models import Project, User
    from backend.ops import seed
    from backend.services import project_purge

    assert admin.get("/api/projects/ELEC").status_code == 200
    with session_factory()() as db:
        project = db.scalar(select(Project).where(Project.key == "ELEC"))
        assert project is not None
        actor = db.get(User, admin.get("/api/auth/me").json()["user"]["id"])
        project_purge.purge_project(db, project, actor)
        assert db.scalar(select(Project.id).limit(1)) is None

    marker = config.get_settings().data_dir / ".sample_project_seeded"
    assert marker.is_file()

    seed()
    with session_factory()() as db:
        assert db.scalar(select(Project.id).where(Project.key == "ELEC")) is None

    # Without the marker, one opt-in seed recreates the sample; a second call does not.
    marker.unlink()
    seed()
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 1
    seed()
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 1


def test_bootstrap_holds_advisory_lock_on_bootstrap_connection(monkeypatch):
    from backend import ops

    events: list[tuple[str, object]] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def commit(self):
            events.append(("commit", self))

    connection = Connection()

    class Engine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return connection

    @contextmanager
    def lock(conn):
        events.append(("lock", conn))
        yield
        events.append(("unlock", conn))

    monkeypatch.setattr(ops, "_prepare_directories", lambda: None)
    monkeypatch.setattr(ops, "engine", lambda: Engine())
    monkeypatch.setattr(ops, "postgres_advisory_lock", lock)
    monkeypatch.setattr(
        ops,
        "_bootstrap_on_connection",
        lambda conn: events.append(("bootstrap", conn)) or {"removed": 0},
    )

    assert ops.bootstrap() == {"removed": 0}
    assert events[:3] == [
        ("lock", connection),
        ("bootstrap", connection),
        ("unlock", connection),
    ]


def test_attachment_reconcile_skips_fresh_pending_files(app):
    from backend.config import get_settings
    from backend.db import session_factory
    from backend.routers.attachments import reconcile_storage

    with TestClient(app):
        directory = get_settings().attachments_dir
        stale = directory / ".stale.bin.uploading"
        fresh = directory / ".fresh.bin.uploading"
        stale.write_bytes(b"stale")
        fresh.write_bytes(b"fresh")
        grace = get_settings().attachment_reconcile_grace_seconds
        old = time.time() - grace - 10
        os.utime(stale, (old, old))
        with session_factory()() as db:
            result = reconcile_storage(db)

    assert not stale.exists()
    assert fresh.exists()
    assert result["removed"] == 1
    assert result["skipped"] == 1


def test_postgres_pool_settings_are_parameterized(monkeypatch):
    from backend import config, db

    values = {
        "METJIRA_DB_POOL_SIZE": "7",
        "METJIRA_DB_MAX_OVERFLOW": "3",
        "METJIRA_DB_POOL_TIMEOUT": "11",
        "METJIRA_DB_POOL_RECYCLE": "123",
        "METJIRA_DB_POOL_USE_LIFO": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    eng = db._make_engine("postgresql+psycopg://user:pass@localhost/example")
    try:
        assert eng.pool.size() == 7
        assert eng.pool._max_overflow == 3
        assert eng.pool._timeout == 11
        assert eng.pool._recycle == 123
        assert eng.pool._pool.use_lifo is False
    finally:
        eng.dispose()
        config.get_settings.cache_clear()


def test_migration_0032_is_head_and_models_define_indexes():
    from backend import models  # noqa: F401 - register metadata
    from backend.db import Base

    root = Path(__file__).parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "backend" / "migrations"))
    assert ScriptDirectory.from_config(cfg).get_current_head() == "0032"

    expected = {
        "sprints": {"uq_sprints_project_active"},
        "approval_requests": {"uq_approval_requests_issue_target_pending"},
        "notifications": {
            "ix_notifications_user_id_id",
            "ix_notifications_user_unread",
        },
        "activity_logs": {"ix_activity_logs_issue_created"},
        "rate_limit_events": {"ix_rate_limit_events_subject_time"},
        "ai_slot_leases": {"ix_ai_slot_leases_subject_expires"},
        "outbox_events": {
            "ix_outbox_events_claimable",
            "uq_outbox_events_dedupe_key",
        },
    }
    for table_name, names in expected.items():
        actual = {index.name for index in Base.metadata.tables[table_name].indexes}
        assert names <= actual


@pytest.mark.parametrize(
    ("active", "pending", "message"),
    [
        ([{"project_id": 7, "count": 2}], [], "duplicate active sprints"),
        (
            [],
            [{"issue_id": 8, "target_status_id": 9, "count": 2}],
            "duplicate pending approval requests",
        ),
    ],
)
def test_migration_0021_rejects_duplicates(monkeypatch, active, pending, message):
    migration = importlib.import_module(
        "backend.migrations.versions.0021_concurrency_and_queue_indexes"
    )
    rows = iter((active, pending))

    class Result:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self.value

    class Bind:
        def execute(self, _statement):
            return Result(next(rows))

    monkeypatch.setattr(migration.op, "get_bind", lambda: Bind())
    with pytest.raises(RuntimeError, match=message):
        migration._reject_duplicates()
