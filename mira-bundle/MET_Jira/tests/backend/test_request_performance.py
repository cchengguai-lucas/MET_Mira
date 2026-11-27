from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.config import get_settings
from backend.services import llm, search


def test_ai_stream_releases_initial_session_before_llm(admin, monkeypatch):
    monkeypatch.setattr(
        llm, "probe", lambda force=False: {"reachable": True, "tool_mode": "native"}
    )

    def chat(_messages, **_kwargs):
        checked_out = getattr(db.engine().pool, "checkedout", lambda: 0)()
        assert checked_out == 0
        return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr(llm, "chat", chat)
    response = admin.post(
        "/api/agent/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    assert '"type": "done"' in response.text


def test_attachment_disk_write_is_offloaded(admin, monkeypatch):
    issue = admin.post(
        "/api/issues",
        json={"project": "ELEC", "title": "附件线程", "due_date": "2099-12-31"},
    ).json()
    from backend.routers import attachments

    calls = 0
    original = attachments.to_thread.run_sync

    async def tracked(function, *args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(function, *args, **kwargs)

    monkeypatch.setattr(attachments.to_thread, "run_sync", tracked)
    response = admin.post(
        f"/api/issues/{issue['key']}/attachments",
        files={"file": ("proof.txt", io.BytesIO(b"proof"), "text/plain")},
    )
    assert response.status_code == 200
    assert calls >= 2


def test_attachment_db_failure_removes_only_new_file(admin, monkeypatch):
    issue = admin.post(
        "/api/issues",
        json={"project": "ELEC", "title": "附件补偿", "due_date": "2099-12-31"},
    ).json()
    from backend.routers import attachments

    directory = get_settings().attachments_dir
    sentinel = directory / "existing.bin"
    sentinel.write_bytes(b"keep")

    def fail(*_args, **_kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(attachments, "log", fail)
    with pytest.raises(RuntimeError, match="database write failed"):
        admin.post(
            f"/api/issues/{issue['key']}/attachments",
            files={"file": ("new.txt", io.BytesIO(b"new"), "text/plain")},
        )
    assert sentinel.read_bytes() == b"keep"
    assert list(directory.iterdir()) == [sentinel]


def test_facets_cache_is_bounded_and_invalidated(admin, monkeypatch):
    search.clear_facet_cache()
    original = search.search_facets
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(search, "search_facets", counted)
    assert admin.get("/api/search/facets").status_code == 200
    assert admin.get("/api/search/facets").status_code == 200
    assert calls == 1
    admin.post(
        "/api/issues",
        json={"project": "ELEC", "title": "缓存失效", "due_date": "2099-12-31"},
    )
    assert admin.get("/api/search/facets").status_code == 200
    assert calls == 2
    assert len(search._facet_cache) <= search.FACET_CACHE_MAX


def test_notifications_presence_health_and_metrics_acl(app, admin):
    unread = admin.get("/api/notifications/unread-count")
    assert unread.status_code == 200
    assert isinstance(unread.json()["unread"], int)

    assert admin.post("/api/presence/heartbeat", json={"page": "home"}).status_code == 200
    assert admin.get("/api/dashboard/health").status_code == 200

    assert admin.get("/metrics").status_code == 403
    with TestClient(app, client=("127.0.0.1", 41000)) as local:
        metrics = local.get("/metrics")
        assert metrics.status_code == 200
        assert "metjira_http_requests_total" in metrics.text
