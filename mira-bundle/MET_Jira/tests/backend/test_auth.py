from __future__ import annotations

import json
import re
import time

from fastapi.testclient import TestClient

from backend.db import session_factory
from backend.models import User
from backend.security import _decode


def _login_status(app, username: str, password: str) -> int:
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        return response.status_code


def test_login_allowlist_blocks_other_accounts(app, make_user, monkeypatch):
    from backend.config import get_settings

    make_user("alice")
    monkeypatch.setenv("METJIRA_LOGIN_ALLOWLIST", "houcheng.fan,admin")
    get_settings.cache_clear()
    try:
        assert _login_status(app, "alice", "secret-123") == 403
        assert _login_status(app, "admin", "admin-pass") == 200
        assert _login_status(app, "Houcheng.Fan", "nope") == 401
    finally:
        get_settings.cache_clear()


def _remaining(response) -> tuple[int, int]:
    match = re.search(r"Max-Age=(\d+)", response.headers["set-cookie"])
    assert match is not None
    token = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    payload = json.loads(_decode(token.split(".", 1)[0]))
    return int(match.group(1)), int(payload["exp"]) - int(time.time())


def test_remember_keeps_the_session_for_seven_days(app):
    with TestClient(app) as client:
        plain = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin-pass"},
        )
        assert plain.status_code == 200, plain.text
        cookie_ttl, token_ttl = _remaining(plain)
        assert cookie_ttl == 12 * 3600
        assert 12 * 3600 - 30 <= token_ttl <= 12 * 3600

        remembered = client.post("/api/auth/login", json={
            "username": "admin", "password": "admin-pass", "remember": True,
        })
        assert remembered.status_code == 200, remembered.text
        cookie_ttl, token_ttl = _remaining(remembered)
        assert cookie_ttl == 7 * 24 * 3600
        assert 7 * 24 * 3600 - 30 <= token_ttl <= 7 * 24 * 3600
        assert client.get("/api/auth/me").status_code == 200


def test_change_own_password(app, make_user):
    alice, _ = make_user("alice")
    bad = alice.post("/api/auth/me/password",
                     json={"old_password": "wrong", "new_password": "newpass-1"})
    assert bad.status_code == 400
    ok = alice.post("/api/auth/me/password",
                    json={"old_password": "secret-123", "new_password": "newpass-1"})
    assert ok.status_code == 200, ok.text
    assert _login_status(app, "alice", "secret-123") != 200
    assert _login_status(app, "alice", "newpass-1") == 200


def test_admin_reset_password_local_only(app, admin, make_user):
    _, bob_id = make_user("bob")
    response = admin.put(f"/api/admin/users/{bob_id}", json={"password": "reset-456"})
    assert response.status_code == 200, response.text
    assert _login_status(app, "bob", "reset-456") == 200

    with session_factory()() as db:
        ad_user = User(username="carol", display_name="Carol", auth_source="ldap")
        db.add(ad_user)
        db.commit()
        carol_id = ad_user.id
    response = admin.put(f"/api/admin/users/{carol_id}", json={"password": "whatever-1"})
    assert response.status_code == 400

    alice, _ = make_user("alice")
    forbidden = alice.put(f"/api/admin/users/{bob_id}", json={"password": "hijack-1"})
    assert forbidden.status_code == 403


def test_shortcut_preferences_round_trip_and_validation(admin):
    shortcuts = {"ai": "KeyJ", "create": ""}
    saved = admin.put("/api/auth/me/preferences", json={"shortcuts": shortcuts})
    assert saved.status_code == 200, saved.text
    assert admin.get("/api/auth/me").json()["preferences"]["shortcuts"] == shortcuts
    assert admin.put("/api/auth/me/preferences", json={"shortcuts": ["KeyJ"]}).status_code == 400
    assert admin.put("/api/auth/me/preferences",
                     json={"shortcuts": {"ai": "x" * 40}}).status_code == 400
    assert admin.put("/api/auth/me/preferences", json={"evil": True}).status_code == 400


def test_pinned_projects_preferences_round_trip_and_validation(admin):
    saved = admin.put("/api/auth/me/preferences", json={"pinned_projects": ["demo", "DEMO", "abc"]})
    assert saved.status_code == 200, saved.text
    assert admin.get("/api/auth/me").json()["preferences"]["pinned_projects"] == ["DEMO", "ABC"]
    bad_scalar = admin.put("/api/auth/me/preferences", json={"pinned_projects": "DEMO"})
    assert bad_scalar.status_code == 400
    bad_key = admin.put("/api/auth/me/preferences", json={"pinned_projects": ["1BAD"]})
    assert bad_key.status_code == 400
    too_many = admin.put(
        "/api/auth/me/preferences",
        json={"pinned_projects": [f"P{i}" for i in range(41)]},
    )
    assert too_many.status_code == 400
