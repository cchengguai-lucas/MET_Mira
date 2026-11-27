from __future__ import annotations

import json
import os
from pathlib import Path


def _root() -> Path:
    return Path(os.environ["METJIRA_PRESENCE_ROOT"])


def _events() -> list[dict]:
    lines: list[dict] = []
    for path in sorted((_root() / "events").glob("*.jsonl")):
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return lines


def test_heartbeat_writes_seat_under_deploy_env(admin):
    seat = _root() / "staging" / "user_1.json"
    assert seat.is_file(), "login should open a seat"
    assert json.loads(seat.read_text(encoding="utf-8"))["page"] == "login"

    response = admin.post("/api/presence/heartbeat", json={"page": "Board"})
    assert response.status_code == 200
    data = json.loads(seat.read_text(encoding="utf-8"))
    assert data["page"] == "board"
    assert data["username"] == "admin"

    admin.post("/api/presence/leave")
    assert not seat.exists()


def test_login_logout_and_export_events(admin):
    admin.get("/api/projects/ELEC/export.xlsx")
    admin.post("/api/auth/logout")
    actions = [(e["action"], e["deploy_env"]) for e in _events()]
    assert ("login", "staging") in actions
    assert ("export_excel", "staging") in actions
    assert ("logout", "staging") in actions
    assert not (_root() / "staging" / "user_1.json").exists()


def test_heartbeat_requires_login(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        assert client.post("/api/presence/heartbeat", json={"page": "x"}).status_code == 401
