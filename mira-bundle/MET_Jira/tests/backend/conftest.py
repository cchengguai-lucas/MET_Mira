from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("METJIRA_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("METJIRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("METJIRA_PRESENCE_ROOT", str(tmp_path / "presence"))
    monkeypatch.setenv("METJIRA_DEPLOY_ENV", "staging")
    monkeypatch.setenv("METJIRA_SCHEDULER", "false")
    monkeypatch.setenv("METJIRA_LLM_ENABLED", "false")
    monkeypatch.setenv("METJIRA_AUTH_MODE", "local")
    monkeypatch.setenv("METJIRA_ADMIN_PASSWORD", "admin-pass")
    monkeypatch.setenv("METJIRA_SEED_SAMPLE_PROJECT", "true")
    monkeypatch.setenv("ENABLE_LDAP", "false")
    from backend import config, db
    from backend.services import ldap_auth

    # The deployed .env holds live SMTP/WeCom credentials; tests must never send real alerts.
    monkeypatch.setattr(config, "_DOTENV", {})
    for key in list(os.environ):
        if key.startswith(("METJIRA_SMTP_", "METJIRA_WECOM_", "METJIRA_ALERT_")):
            monkeypatch.delenv(key)
    config.get_settings.cache_clear()
    ldap_auth.load_ldap_settings.cache_clear()
    db.configure(os.environ["METJIRA_DATABASE_URL"])
    from backend.main import create_app

    yield create_app()
    config.get_settings.cache_clear()
    ldap_auth.load_ldap_settings.cache_clear()


def login(app, username: str, password: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def admin(app):
    client = login(app, "admin", "admin-pass")
    yield client
    client.__exit__(None, None, None)


@pytest.fixture()
def make_user(app, admin):
    clients: list[TestClient] = []

    def _make(username: str, display_name: str | None = None) -> tuple[TestClient, int]:
        response = admin.post(
            "/api/admin/users",
            json={"username": username, "display_name": display_name or username,
                  "password": "secret-123"},
        )
        assert response.status_code == 200, response.text
        client = login(app, username, "secret-123")
        clients.append(client)
        return client, response.json()["id"]

    yield _make
    for client in clients:
        client.__exit__(None, None, None)
