from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.services import sla

SHANGHAI = sla.SLA_TZ


def business(**overrides) -> sla.Calendar:
    values = {
        "start": sla._clock("09:00"), "end": sla._clock("18:00"),
        "workdays": frozenset({1, 2, 3, 4, 5}), "holidays": frozenset(),
        "extra_workdays": frozenset(),
    }
    return sla.Calendar(**{**values, **overrides})


def local(*args) -> datetime:
    return datetime(*args, tzinfo=SHANGHAI)


def test_business_calendar_skips_nights_weekends_and_holidays():
    cal = business()
    # Friday 16:00 + 4 working hours: 2 h on Friday, 2 h on Monday.
    assert sla.add_time(local(2026, 10, 16, 16), 4, cal) == local(2026, 10, 19, 11)
    # Before opening counts from 09:00.
    assert sla.add_time(local(2026, 10, 19, 7), 1, cal) == local(2026, 10, 19, 10)
    holiday = business(holidays=frozenset({"2026-10-19"}))
    assert sla.add_time(local(2026, 10, 16, 16), 4, holiday) == local(2026, 10, 20, 11)
    makeup = business(extra_workdays=frozenset({"2026-10-17"}))
    assert sla.add_time(local(2026, 10, 16, 16), 4, makeup) == local(2026, 10, 17, 11)
    assert sla.elapsed(local(2026, 10, 16, 16), local(2026, 10, 19, 11), cal) == pytest.approx(4)
    assert sla.add_time(local(2026, 10, 16, 16), 4, None) == local(2026, 10, 16, 20)


def test_settings_validation(admin):
    bad = [
        {"sla_calendar": "lunar"},
        {"sla_business_hours": {"start": "18:00", "end": "09:00", "workdays": [1]}},
        {"sla_business_hours": {"start": "09:00", "end": "18:00", "workdays": []}},
        {"sla_holidays": ["10/01"]},
        {"sla_paused_statuses": ["不存在"]},
    ]
    for settings in bad:
        response = admin.put("/api/projects/ELEC", json={"settings": settings})
        assert response.status_code == 400, settings


def _status(admin, name: str) -> int:
    statuses = admin.get("/api/projects/ELEC").json()["statuses"]
    return next(s["id"] for s in statuses if s["name"] == name)


def test_reopening_starts_a_new_clock(admin):
    from backend.db import session_factory, utcnow
    from backend.models import Issue

    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "超时后驳回", "priority": "highest", "due_date": "2099-01-01",
    }).json()
    assert issue["sla_state"] == "ok"
    with session_factory()() as db:
        row = db.get(Issue, issue["id"])
        row.sla_started_at = row.created_at = utcnow() - timedelta(days=3)
        row.sla_due_at = utcnow() - timedelta(days=2)
        row.sla_notified_level = 2
        db.commit()
    assert admin.get(f"/api/issues/{issue['key']}").json()["sla_state"] == "breached"

    done = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": _status(admin, "已驳回")})
    assert done.json()["sla_state"] == "completed"
    reopened = admin.patch(f"/api/issues/{issue['key']}",
                           json={"status_id": _status(admin, "待评估")}).json()
    assert reopened["sla_state"] == "ok"
    due = datetime.fromisoformat(reopened["sla_due_at"])
    assert timedelta(hours=23) < due - datetime.now(timezone.utc) <= timedelta(hours=24)
    with session_factory()() as db:
        assert db.get(Issue, issue["id"]).sla_notified_level == 0


def test_paused_status_stops_the_clock(admin):
    from backend.db import session_factory, utcnow
    from backend.models import Issue

    assert admin.put("/api/projects/ELEC", json={
        "settings": {"sla_paused_statuses": ["方案讨论"]},
    }).status_code == 200
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "等客户确认", "priority": "high", "due_date": "2099-01-01",
    }).json()
    original_due = datetime.fromisoformat(issue["sla_due_at"])
    paused = admin.patch(f"/api/issues/{issue['key']}",
                         json={"status_id": _status(admin, "方案讨论")}).json()
    assert paused["sla_state"] == "paused"
    noted = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_note": "等待方案"})
    assert noted.status_code == 200
    submitted = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "submit"})
    assert submitted.status_code == 200
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "electrical_scheme_ok": True, "process_scheme_ok": True,
    }).status_code == 200
    with session_factory()() as db:
        db.get(Issue, issue["id"]).sla_paused_at = utcnow() - timedelta(hours=5)
        db.commit()
    resumed = admin.patch(f"/api/issues/{issue['key']}",
                          json={"status_id": _status(admin, "研发中")}).json()
    assert resumed["sla_state"] == "ok"
    shift = datetime.fromisoformat(resumed["sla_due_at"]) - original_due
    assert timedelta(hours=4, minutes=59) < shift < timedelta(hours=5, minutes=1)


def test_priority_change_keeps_the_original_start(admin):
    from backend.db import session_factory, utcnow
    from backend.models import Issue

    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "升级优先级", "priority": "low", "due_date": "2099-01-01",
    }).json()
    with session_factory()() as db:
        db.get(Issue, issue["id"]).sla_started_at = utcnow() - timedelta(hours=30)
        db.commit()
    raised = admin.patch(f"/api/issues/{issue['key']}", json={"priority": "highest"}).json()
    assert raised["sla_state"] == "breached"
    lowered = admin.patch(f"/api/issues/{issue['key']}", json={"priority": "medium"}).json()
    assert lowered["sla_state"] == "ok"


def test_switching_to_business_hours_moves_open_deadlines(admin):
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "工作时间", "priority": "highest", "due_date": "2099-01-01",
    }).json()
    natural = datetime.fromisoformat(issue["sla_due_at"])
    assert admin.put("/api/projects/ELEC", json={"settings": {
        "sla_calendar": "business",
        "sla_business_hours": {"start": "09:00", "end": "18:00", "workdays": [1, 2, 3, 4, 5]},
    }}).status_code == 200
    moved = datetime.fromisoformat(admin.get(f"/api/issues/{issue['key']}").json()["sla_due_at"])
    assert moved - natural > timedelta(days=1)
