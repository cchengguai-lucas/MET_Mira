from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

from ops_console.auth import issue_session, parse_session, verify_password
from ops_console.logs import normalize_path, summarize
from ops_console.presence import event_summary, online
from ops_console.store import Store


def _met_jira_hash(password: str, salt: bytes = b"0123456789abcdef") -> str:
    enc = lambda v: base64.urlsafe_b64encode(v).rstrip(b"=").decode()  # noqa: E731
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2${enc(salt)}${enc(digest)}"


def test_verify_password_matches_met_jira_format():
    stored = _met_jira_hash("MetJira123!")
    assert verify_password("MetJira123!", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", None)
    assert not verify_password("x", "ldap$whatever")


def test_session_roundtrip_and_tamper():
    token = issue_session({"id": 1, "username": "admin"}, "s3cret")
    assert parse_session(token, "s3cret")["username"] == "admin"
    assert parse_session(token, "other") is None
    assert parse_session(token[:-2] + "xx", "s3cret") is None
    assert parse_session(issue_session({"id": 1}, "s3cret", ttl=-5), "s3cret") is None


def _seat(root: Path, env: str, uid: int, last_seen: float, page: str = "board") -> None:
    directory = root / env
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"user_id": uid, "username": f"u{uid}", "label": f"User {uid}",
               "last_seen": last_seen, "page": page}
    (directory / f"user_{uid}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_online_respects_ttl_and_ignores_corrupt_files(tmp_path):
    now = 1_000_000.0
    _seat(tmp_path, "production", 1, now - 10)
    _seat(tmp_path, "production", 2, now - 500)
    (tmp_path / "production" / "user_3.json").write_text("{broken", encoding="utf-8")
    seats = online("production", root=tmp_path, ttl=120, now=now)
    assert [s["user_id"] for s in seats] == [1]
    assert seats[0]["page_label"] == "Board"
    assert (tmp_path / "production" / "user_2.json").exists(), "expired seats are not deleted"
    assert online("staging", root=tmp_path, now=now) == []


def test_store_credits_usage_and_builds_history(tmp_path):
    store = Store(tmp_path / "ops.sqlite3")
    now = time.time()
    seats = [
        {"user_id": 1, "label": "A", "page": "board"},
        {"user_id": 2, "label": "B", "page": "list"},
    ]
    for i in range(4):
        store.record_sample("production", seats, 15, now=now - 60 + i * 15)
    store.record_sample("production", seats[:1], 15, now=now)
    board = store.leaderboard("production", 1)
    assert board[0]["user_id"] == 1 and board[0]["seconds"] == 75
    assert store.daily("production", 1)[0]["users"] == 2
    pages = {p["page"]: p["seconds"] for p in store.page_dwell("production", 1)}
    assert pages == {"board": 75, "list": 60}
    history = store.online_history("production", 1, now=now)
    assert max(v for _, v, _ in history if v is not None) == 2
    seen = {uid for _, _, ids in history for uid in ids}
    assert seen == {1, 2}
    assert all(ids == [] for _, v, ids in history if v is None)
    assert store.recent_labels("production", [2, 1, 9]) == {1: "A", 2: "B"}
    assert store.leaderboard("staging", 1) == []


def test_store_uptime(tmp_path):
    store = Store(tmp_path / "ops.sqlite3")
    now = time.time()
    for i, ok in enumerate([True, True, True, False]):
        store.record_probe("llm", ok, 100 if ok else None, now=now - i * 60)
    up = store.uptime("llm", 1, now=now)
    assert up["samples"] == 4 and up["ratio"] == 0.75 and up["avg_ms"] == 100


def test_events_filtered_by_env(tmp_path):
    from datetime import datetime

    from ops_console.config import TZ

    today = datetime.now(TZ).date()
    events = tmp_path / "events"
    events.mkdir()
    rows = [
        {"ts": time.time(), "deploy_env": "production", "action": "login", "label": "A"},
        {"ts": time.time(), "deploy_env": "staging", "action": "login", "label": "B"},
        {"ts": time.time(), "deploy_env": "production", "action": "export_excel", "label": "A"},
    ]
    (events / f"{today.isoformat()}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8"
    )
    summary = event_summary("production", 7, root=tmp_path, today=today)
    assert summary["totals"] == {"login": 1, "export_excel": 1}


def test_journal_summary_parses_access_log_and_errors():
    now = 1_791_500_000.0
    access = 'python3[1]: INFO:     10.1.2.3:5555 - "{m} {p} HTTP/1.1" {s} OK'
    lines = [
        (now - 100, access.format(m="GET", p="/api/issues/ELEC-12?x=1", s=200)),
        (now - 90, access.format(m="GET", p="/api/issues/ELEC-13", s=200)),
        (now - 80, access.format(m="POST", p="/api/projects/7/boards", s=500)),
        (now - 70, access.format(m="GET", p="/assets/index.js", s=304)),
        (now - 60, access.format(m="GET", p="/api/auth/me", s=401)),
        (now - 50, "python3[1]: ERROR:    Exception in ASGI application"),
        (now - 50, "python3[1]: Traceback (most recent call last):"),
        (now - 40, "systemd[1]: Started met-jira.service - MET Jira web (8800, production)."),
    ]
    data = summarize(lines, 24, now=now)
    assert data["requests"] == 5
    assert data["status"] == {"2xx": 2, "5xx": 1, "3xx": 1, "4xx": 1}
    assert data["error_rate"] == 0.2
    assert ("GET /api/issues/:id", 2) in data["top_endpoints"]
    assert data["error_endpoints"] == [("/api/projects/:id/boards", 1)]
    assert len(data["errors"]) == 1 and "Traceback" in data["errors"][0]["text"]
    assert len(data["restarts"]) == 1
    assert normalize_path("/") == "(pages)"
    assert len(data["series"]) in (24, 25), "hourly buckets for a day"

    recent = summarize(lines, 6, now=now)
    assert recent["requests"] == 5
    assert len(recent["series"]) in (72, 73), "5-minute buckets for the 6-hour view"
