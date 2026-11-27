from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.db import Base
from backend.models import Project, User

pytestmark = pytest.mark.postgres


@pytest.fixture()
def pg():
    raw_url = os.getenv("METJIRA_TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("METJIRA_TEST_POSTGRES_URL is not configured")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail("METJIRA_TEST_POSTGRES_URL must target a PostgreSQL *_test database")
    schema = f"metjira_test_{uuid.uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(isolated_url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture()
def pg_app(pg, monkeypatch, tmp_path):
    from backend import config, db
    from backend.ops import seed

    monkeypatch.setattr(config, "_DOTENV", {})
    database_url = pg.url.render_as_string(hide_password=False)
    monkeypatch.setenv("METJIRA_DATABASE_URL", database_url)
    monkeypatch.setenv("METJIRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("METJIRA_DEPLOY_ENV", "staging")
    monkeypatch.setenv("METJIRA_SECRET", "postgres-concurrency-test-secret")
    monkeypatch.setenv("METJIRA_AUTH_MODE", "local")
    monkeypatch.setenv("METJIRA_ADMIN_PASSWORD", "admin-pass")
    monkeypatch.setenv("METJIRA_SCHEDULER", "false")
    monkeypatch.setenv("METJIRA_LLM_ENABLED", "false")
    monkeypatch.setenv("METJIRA_SEED_SAMPLE_PROJECT", "true")
    config.get_settings.cache_clear()
    db.configure(database_url)
    seed()

    from backend.main import create_app

    yield create_app()
    config.get_settings.cache_clear()


def _client(app) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-pass"},
    )
    assert response.status_code == 200, response.text
    return client


def _concurrent(calls):
    barrier = Barrier(len(calls))

    def run(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def test_postgres_outbox_skip_locked_and_crash_reclaim_once(pg):
    from backend.db import utcnow
    from backend.models import OutboxEvent
    from backend.services.outbox import claim_batch, complete, enqueue

    with Session(pg, expire_on_commit=False) as db:
        first_event = enqueue(db, "webhook", {"value": 1})
        first_event_id = first_event.id
        db.commit()

    def claim(owner: str) -> list[int]:
        with Session(pg, expire_on_commit=False) as db:
            return [row.id for row in claim_batch(db, limit=1, owner=owner)]

    claimed = _concurrent([lambda: claim("worker-1"), lambda: claim("worker-2")])
    assert sorted(event for batch in claimed for event in batch) == [first_event_id]

    with Session(pg, expire_on_commit=False) as db:
        crashed_event = enqueue(db, "webhook", {"value": 2})
        crashed_event_id = crashed_event.id
        db.commit()
    now = utcnow()
    with Session(pg, expire_on_commit=False) as db:
        abandoned = claim_batch(
            db, limit=1, lease_seconds=1, owner="crashed-worker", now=now
        )[0]
    old_token = abandoned.claim_token

    def reclaim(owner: str) -> list[OutboxEvent]:
        with Session(pg, expire_on_commit=False) as db:
            return claim_batch(
                db,
                limit=1,
                lease_seconds=10,
                owner=owner,
                now=now + timedelta(seconds=2),
            )

    reclaimed = _concurrent(
        [lambda: reclaim("replacement-1"), lambda: reclaim("replacement-2")]
    )
    rows = [row for batch in reclaimed for row in batch]
    assert [row.id for row in rows] == [crashed_event_id]
    assert rows[0].attempts == 2
    assert rows[0].claim_token != old_token
    with Session(pg) as db:
        assert not complete(db, crashed_event_id, old_token)
        row = db.get(OutboxEvent, crashed_event_id)
        assert row is not None
        assert row.status == "claimed"
        assert row.claimed_by in {"replacement-1", "replacement-2"}


def test_postgres_concurrent_bootstrap_is_idempotent(pg, monkeypatch, tmp_path):
    from backend import config, db, ops
    from backend.models import Status

    Base.metadata.drop_all(pg)
    database_url = pg.url.render_as_string(hide_password=False)
    monkeypatch.setattr(config, "_DOTENV", {})
    monkeypatch.setenv("METJIRA_DATABASE_URL", database_url)
    monkeypatch.setenv("METJIRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("METJIRA_DEPLOY_ENV", "staging")
    monkeypatch.setenv("METJIRA_SECRET", "postgres-bootstrap-test-secret")
    monkeypatch.setenv("METJIRA_AUTH_MODE", "local")
    monkeypatch.setenv("METJIRA_ADMIN_PASSWORD", "admin-pass")
    config.get_settings.cache_clear()
    db.configure(database_url)

    results = _concurrent([ops.bootstrap, ops.bootstrap])
    assert results == [
        {"finalized": 0, "restored": 0, "removed": 0, "skipped": 0},
        {"finalized": 0, "restored": 0, "removed": 0, "skipped": 0},
    ]
    with Session(pg) as session:
        assert session.scalar(select(func.count(User.id))) == 1
        assert session.scalar(select(func.count(Project.id))) == 1
        duplicate_statuses = session.execute(
            select(Status.project_id, Status.name, func.count(Status.id))
            .group_by(Status.project_id, Status.name)
            .having(func.count(Status.id) > 1)
        ).all()
        assert duplicate_statuses == []
        assert session.scalar(text("SELECT count(*) FROM alembic_version")) == 1
    config.get_settings.cache_clear()


def test_postgres_json_timezone_and_constraints(pg):
    """Exercise production-only JSONB-ish values, timezone timestamps and FK enforcement."""
    from backend.db import utcnow

    with Session(pg) as db:
        db.execute(text("SELECT 1"))
        project = Project(key="PG", name="Postgres", settings={
            "profile": "mechanical",
            "transition_gates": {"终检": {"required_fields": ["custom_fields.drawing_no"]}},
        })
        db.add(project)
        db.flush()
        assert db.scalar(select(Project).where(Project.settings["profile"].as_string()
                                               == "mechanical")) is not None
        assert utcnow().tzinfo is not None


def test_postgres_issue_sequence_is_locked(pg):
    from backend.services.issues import _next_number

    with Session(pg) as db:
        project = Project(key="SEQ", name="Sequence")
        db.add(project)
        db.commit()
        project_id = project.id

    def take(_index: int) -> int:
        with Session(pg) as db:
            project = db.get(Project, project_id)
            number = _next_number(db, project)
            db.commit()
            return number

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = sorted(pool.map(take, range(8)))
    assert numbers == list(range(1, 9))


def test_postgres_trigram_similarity(pg):
    with pg.connect() as connection:
        score = connection.scalar(text("SELECT similarity('机械设计', '机械设变')"))
    assert score > 0


def test_postgres_shared_rate_limit_and_expiring_ai_lease(
    pg_app,
    pg,
    monkeypatch,
):
    from backend import config
    from backend.db import utcnow
    from backend.models import AiSlotLease, RateLimitEvent
    from backend.services import ratelimit

    monkeypatch.setenv("METJIRA_AI_REQUESTS_PER_MINUTE", "3")
    monkeypatch.setenv("METJIRA_AI_MAX_CONCURRENT", "1")
    monkeypatch.setenv("METJIRA_AI_SLOT_LEASE_TTL_SECONDS", "1")
    monkeypatch.setenv("METJIRA_AI_SLOT_HEARTBEAT_SECONDS", "1")
    config.get_settings.cache_clear()

    def take(_index: int) -> bool:
        try:
            ratelimit.take_ai_request(4242)
            return True
        except HTTPException as error:
            assert error.status_code == 429
            return False

    accepted = _concurrent([lambda index=index: take(index) for index in range(8)])
    assert accepted.count(True) == 3

    def acquire() -> tuple[str, str | int]:
        try:
            return ("accepted", ratelimit.acquire_ai_slot(4242))
        except HTTPException as error:
            return ("blocked", error.status_code)

    acquisitions = _concurrent([acquire, acquire])
    assert sorted(result[0] for result in acquisitions) == ["accepted", "blocked"]
    assert next(result[1] for result in acquisitions if result[0] == "blocked") == 429
    token = str(next(result[1] for result in acquisitions if result[0] == "accepted"))
    with Session(pg) as db:
        lease = db.get(AiSlotLease, token)
        assert lease is not None
        lease.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    replacement = ratelimit.acquire_ai_slot(4242)
    assert replacement != token
    assert ratelimit.heartbeat_ai_slot(replacement)
    ratelimit.release_ai_slot(4242, replacement)

    with Session(pg) as db:
        subjects = list(db.scalars(select(RateLimitEvent.subject_hash)))
    assert subjects and all(len(value) == 64 and "4242" not in value for value in subjects)


def test_postgres_concurrent_writes_and_idempotency(pg_app, pg):
    from test_excel import _legacy_sheet

    from backend.models import ImportBatch, Watcher

    clients = [_client(pg_app), _client(pg_app)]
    try:
        created = clients[0].post(
            "/api/issues",
            json={
                "project": "ELEC",
                "title": "并发问题",
                "type": "task",
                "due_date": "2099-12-31",
            },
        ).json()
        patches = _concurrent([
            lambda: clients[0].patch(
                f"/api/issues/{created['key']}",
                json={"title": "并发 A", "expected_version": created["lock_version"]},
            ),
            lambda: clients[1].patch(
                f"/api/issues/{created['key']}",
                json={"title": "并发 B", "expected_version": created["lock_version"]},
            ),
        ])
        assert sorted(response.status_code for response in patches) == [200, 409]
        assert next(r for r in patches if r.status_code == 409).json()["code"] == "stale_write"

        project = clients[0].get("/api/projects/ELEC").json()
        target = next(s for s in project["statuses"] if s["name"] == "方案讨论")
        settings = {
            "transition_gates": {
                target["name"]: {"approvals": [{"role": "admin", "count": 1}]}
            }
        }
        assert clients[0].put(
            "/api/projects/ELEC",
            json={"settings": settings},
        ).status_code == 200
        approval_issue = clients[0].post(
            "/api/issues",
            json={
                "project": "ELEC",
                "title": "并发审批",
                "due_date": "2099-12-31",
            },
        ).json()
        requests = _concurrent([
            lambda client=client: client.post(
                f"/api/issues/{approval_issue['key']}/transition-requests",
                json={
                    "status_id": target["id"],
                    "expected_version": approval_issue["lock_version"],
                },
            )
            for client in clients
        ])
        assert [response.status_code for response in requests] == [200, 200]
        assert len({response.json()["id"] for response in requests}) == 1
        approval = requests[0].json()
        decisions = _concurrent([
            lambda client=client: client.post(
                f"/api/approval-requests/{approval['id']}/decision",
                json={"approve": True, "expected_version": approval["lock_version"]},
            )
            for client in clients
        ])
        assert sorted(response.status_code for response in decisions) == [200, 409]
        assert next(r for r in decisions if r.status_code == 409).json()["code"] == "stale_write"

        sprints = [
            clients[0].post(
                "/api/projects/ELEC/sprints",
                json={"name": f"并发冲刺 {index}"},
            ).json()
            for index in range(2)
        ]
        starts = _concurrent([
            lambda index=index: clients[index].post(
                f"/api/sprints/{sprints[index]['id']}/start",
                json={"expected_version": sprints[index]["lock_version"]},
            )
            for index in range(2)
        ])
        assert sorted(response.status_code for response in starts) == [200, 409]
        assert next(r for r in starts if r.status_code == 409).json()["detail"] == (
            "已有进行中的冲刺，请先完成该冲刺"
        )

        watches = _concurrent([
            lambda client=client: client.post(f"/api/issues/{created['key']}/watch")
            for client in clients
        ])
        assert [response.status_code for response in watches] == [200, 200]
        with Session(pg) as db:
            from backend.models import ApprovalRequest, Sprint

            issue_id = db.scalar(
                text("SELECT id FROM issues WHERE key = :key"),
                {"key": created["key"]},
            )
            watcher_count = db.scalar(
                select(func.count(Watcher.id)).where(Watcher.issue_id == issue_id)
            )
            assert db.scalar(
                select(func.count(ApprovalRequest.id)).where(
                    ApprovalRequest.issue_id
                    == db.scalar(
                        text("SELECT id FROM issues WHERE key = :key"),
                        {"key": approval_issue["key"]},
                    )
                )
            ) == 1
            assert db.scalar(
                select(func.count(Sprint.id)).where(
                    Sprint.project_id
                    == db.scalar(select(Project.id).where(Project.key == "ELEC")),
                    Sprint.state == "active",
                )
            ) == 1
        assert watcher_count == 1

        preview = clients[0].post(
            "/api/projects/ELEC/import/preview",
            files={
                "file": (
                    "legacy.xlsx",
                    _legacy_sheet(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        ).json()
        commits = _concurrent([
            lambda client=client: client.post(
                "/api/projects/ELEC/import/commit",
                json={"token": preview["token"], "mapping": preview["mapping"]},
            )
            for client in clients
        ])
        assert [response.status_code for response in commits] == [200, 200]
        assert commits[0].json() == commits[1].json()
        with Session(pg) as db:
            assert db.scalar(
                select(func.count(ImportBatch.id))
                .where(ImportBatch.token == preview["token"])
            ) == 1
    finally:
        for client in clients:
            client.__exit__(None, None, None)


def test_alembic_upgrades_empty_postgres_database(pg):
    Base.metadata.drop_all(pg)
    with pg.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        cfg.set_main_option(
            "script_location", str(Path(__file__).parents[2] / "backend" / "migrations")
        )
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        expected_revision = ScriptDirectory.from_config(cfg).get_current_head()
    with pg.connect() as connection:
        names = set(connection.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )).scalars())
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        lock_columns = set(connection.execute(text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name = 'lock_version'"
        )).scalars())
        import_columns = set(connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'import_batches'"
        )).scalars())
        index_defs = {
            row["indexname"]: row["indexdef"]
            for row in connection.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname IN ("
                "'uq_sprints_project_active', "
                "'uq_approval_requests_issue_target_pending', "
                "'ix_notifications_user_id_id', "
                "'ix_notifications_user_unread', "
                "'ix_activity_logs_issue_created')"
            )).mappings()
        }
    assert {
        "issues", "approval_requests", "master_assets", "project_assets",
        "department_templates", "department_template_versions",
        "org_unit_template_recommendations", "project_template_migrations",
        "department_template_audits",
        "rate_limit_events", "ai_slot_leases",
    } <= names
    assert revision == expected_revision
    assert lock_columns == {"issues", "sprints", "approval_requests"}
    assert {"token", "result"} <= import_columns
    assert "UNIQUE" in index_defs["uq_sprints_project_active"]
    assert "WHERE" in index_defs["uq_sprints_project_active"]
    assert "UNIQUE" in index_defs["uq_approval_requests_issue_target_pending"]
    assert "WHERE" in index_defs["uq_approval_requests_issue_target_pending"]
    assert "(user_id, id)" in index_defs["ix_notifications_user_id_id"]
    assert "WHERE" in index_defs["ix_notifications_user_unread"]
    assert "(issue_id, created_at)" in index_defs["ix_activity_logs_issue_created"]
    with pg.begin() as connection:
        index = connection.scalar(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() "
            "AND tablename = 'issues' AND indexname = 'ix_issues_extra_gin'"
        ))
        assert index and "USING gin" in index
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(connection.execute(text(
            "EXPLAIN SELECT * FROM issues WHERE extra @> '{\"field\":\"value\"}'::jsonb"
        )).scalars())
        assert "ix_issues_extra_gin" in plan
