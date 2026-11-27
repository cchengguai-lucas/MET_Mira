from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_health_checks_database_and_data_dir(app):
    with TestClient(app) as client:
        body = client.get("/api/health").json()
        assert body["ok"] is True
        assert body["checks"] == {"database": "ok", "data_dir": "ok"}
        assert body["env"] == "staging"
        assert "backup_age_hours" not in body


def test_health_fails_when_database_is_down(app, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend import main

    with TestClient(app) as client:
        def broken():
            raise OperationalError("select 1", {}, Exception("down"))

        monkeypatch.setattr(main, "session_factory", lambda: broken)
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["checks"]["database"].startswith("error")


def test_login_failures_are_rate_limited(app, monkeypatch):
    from backend import config

    monkeypatch.setenv("METJIRA_LOGIN_MAX_FAILURES", "3")
    config.get_settings.cache_clear()
    with TestClient(app) as client:
        for _ in range(3):
            wrong = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
            assert wrong.status_code == 401
        blocked = client.post("/api/auth/login",
                              json={"username": "admin", "password": "admin-pass"})
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        other = client.post("/api/auth/login", json={"username": "someone", "password": "x"})
        assert other.status_code == 401, "the lockout is per account, not the whole address"


def test_successful_login_clears_failures(app, monkeypatch):
    from backend import config

    monkeypatch.setenv("METJIRA_LOGIN_MAX_FAILURES", "3")
    config.get_settings.cache_clear()
    with TestClient(app) as client:
        for _ in range(2):
            client.post("/api/auth/login", json={"username": "admin", "password": "x"})
        assert client.post("/api/auth/login",
                           json={"username": "admin", "password": "admin-pass"}).is_success
        for _ in range(2):
            client.post("/api/auth/login", json={"username": "admin", "password": "x"})
        assert client.post("/api/auth/login",
                           json={"username": "admin", "password": "admin-pass"}).is_success


def test_ai_quota_and_concurrency(app, monkeypatch):
    from backend import config
    from backend.services import ratelimit

    monkeypatch.setenv("METJIRA_AI_REQUESTS_PER_MINUTE", "3")
    monkeypatch.setenv("METJIRA_AI_MAX_CONCURRENT", "1")
    config.get_settings.cache_clear()
    with ratelimit.ai_slot(1):
        with pytest.raises(HTTPException) as busy:
            with ratelimit.ai_slot(1):
                pass
        assert busy.value.status_code == 429
        with ratelimit.ai_slot(2):
            pass
    with ratelimit.ai_slot(1):
        pass
    with pytest.raises(HTTPException) as quota:
        ratelimit.take_ai_request(1)
    assert quota.value.status_code == 429 and "Retry-After" in quota.value.headers
