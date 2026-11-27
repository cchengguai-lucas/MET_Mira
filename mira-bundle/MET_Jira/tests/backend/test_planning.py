from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from test_health import _create, _days_ago, _mover


def _backdate(key: str, created: int, **fields: list[int]) -> None:
    """Move an issue's creation and its logged changes, per field in order, into the past."""
    from backend.db import session_factory
    from backend.models import ActivityLog, Issue

    with session_factory()() as db:
        issue = db.scalar(select(Issue).where(Issue.key == key))
        issue.created_at = _days_ago(created)
        for field, days in fields.items():
            rows = db.scalars(select(ActivityLog).where(ActivityLog.issue_id == issue.id,
                                                        ActivityLog.field == field)
                              .order_by(ActivityLog.id)).all()
            for row, ago in zip(rows, days, strict=True):
                row.created_at = _days_ago(ago)
        db.commit()


def _backdate_sprint(sprint_id: int, started: int, completed: int | None = None) -> None:
    from backend.db import session_factory
    from backend.models import Sprint

    with session_factory()() as db:
        sprint = db.get(Sprint, sprint_id)
        sprint.started_at = _days_ago(started)
        if completed is not None:
            sprint.completed_at = _days_ago(completed)
        db.commit()


def test_velocity_scope_change_and_burn_up(admin):
    move = _mover(admin)
    sprint = admin.post("/api/projects/ELEC/sprints", json={"name": "S1"}).json()
    a = _create(admin, title="A", sprint_id=sprint["id"], story_points=5)
    b = _create(admin, title="B", sprint_id=sprint["id"], story_points=3)
    c = _create(admin, title="C", sprint_id=sprint["id"], story_points=2)
    d = _create(admin, title="D", story_points=1)
    started = admin.post(f"/api/sprints/{sprint['id']}/start", json={})
    assert started.status_code == 200, started.text
    move(d, sprint_id=sprint["id"])
    admin.patch(f"/api/issues/{c['key']}", json={"sprint_id": None}).raise_for_status()
    move(b, story_points=5)
    move(a, "研发中", "开发完成", "测试中", "测试通过", "已完成")
    admin.post(f"/api/sprints/{sprint['id']}/complete", json={}).raise_for_status()

    _backdate(a["key"], 12, status_id=[8, 7, 6, 4, 3])
    _backdate(b["key"], 12, story_points=[4], sprint_id=[1])
    _backdate(c["key"], 12, sprint_id=[5])
    _backdate(d["key"], 12, sprint_id=[6, 1])
    _backdate_sprint(sprint["id"], started=10, completed=1)

    body = admin.get("/api/projects/ELEC/reports/velocity").json()
    row = body["sprints"][-1]
    assert row["committed"] == {"count": 3, "points": 10}, "points as they were at the start"
    assert row["added"] == {"count": 1, "points": 1}
    assert row["removed"] == {"count": 1, "points": 2}, "moving open work out at completion"
    assert row["scope"] == {"count": 3, "points": 11}
    assert row["completed"] == {"count": 1, "points": 5}
    assert row["committed_completed"] == 1 and row["reestimated"] == 2
    assert body["average"]["points"] == 5 and body["average"]["commitment_rate"] == 33

    detail = admin.get(f"/api/projects/ELEC/reports/sprint?sprint_id={sprint['id']}").json()
    series = detail["series"]
    assert series[0]["date"] == _days_ago(10).date().isoformat()
    assert (series[0]["scope_count"], series[0]["scope_points"]) == (3, 10)
    by_date = {p["date"]: p for p in series}
    assert by_date[_days_ago(6).date().isoformat()]["scope_count"] == 4
    assert by_date[_days_ago(5).date().isoformat()]["scope_count"] == 3
    last = series[-1]
    assert (last["scope_points"], last["done_points"], last["remaining_points"]) == (11, 5, 6)
    assert last["ideal_count"] == 0
    assert [(e["kind"], e["key"]) for e in detail["changes"]] == [
        ("added", d["key"]), ("removed", c["key"])]
    assert [(r["key"], r["old"], r["new"]) for r in detail["reestimates"]] == [(b["key"], 3, 5)]


def test_forecast_replays_daily_throughput(admin):
    move = _mover(admin)
    for ago in range(7):
        issue = move(_create(admin, title=f"完成 {ago}"), "研发中", "开发完成", "测试中",
                      "测试通过", "已完成")
        _backdate(issue["key"], 20, status_id=[ago + 4, ago + 3, ago + 2, ago + 1, ago])
    for n in range(3):
        _create(admin, title=f"剩余 {n}")
    epic = _create(admin, title="大目标", type="epic")
    _create(admin, title="史诗子项", epic_id=epic["id"])

    today = date.today()
    body = admin.get("/api/projects/ELEC/reports/forecast?days=7&scope=open").json()
    assert body["throughput"]["total"] == 7 and body["throughput"]["per_week"] == 7
    assert body["scope"]["remaining"] == 4 and body["options"]["sprint"] is None
    finish = (today + timedelta(days=4)).isoformat()
    assert body["finish"] == {"p50": finish, "p70": finish, "p85": finish, "p95": finish}
    assert body["curve"][-1]["probability"] == 100
    assert body["target"]["date"] == (today + timedelta(days=14)).isoformat()
    assert body["target"]["probability"] == 100
    assert body["target"]["items"] == {"p50": 14, "p85": 14, "p95": 14}

    assert [e["key"] for e in body["options"]["epics"]] == [epic["key"]]
    scoped = admin.get(f"/api/projects/ELEC/reports/forecast?days=7&scope=epic:{epic['id']}"
                       f"&target={(today + timedelta(days=3)).isoformat()}").json()
    assert scoped["scope"]["kind"] == "epic" and scoped["scope"]["remaining"] == 1
    assert scoped["finish"]["p95"] == (today + timedelta(days=1)).isoformat()
    assert scoped["target"]["items"]["p85"] == 3

    past = admin.get("/api/projects/ELEC/reports/forecast?target=2020-01-01")
    assert past.status_code == 400


def test_forecast_needs_recent_throughput(admin):
    _create(admin, title="没人完成")
    body = admin.get("/api/projects/ELEC/reports/forecast?days=14&scope=open").json()
    assert body["scope"]["remaining"] == 1
    assert body["finish"] is None and body["curve"] == []
    assert body["target"]["probability"] is None


def test_load_history_follows_reassignment(admin, make_user):
    _, other = make_user("lin", "林")
    members = admin.post("/api/projects/ELEC/members", json={"user_id": other, "role": "member"})
    assert members.status_code in (200, 409), members.text
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _create(admin, title="换人", assignee_id=me)
    moved = admin.patch(f"/api/issues/{issue['key']}", json={"assignee_id": other})
    assert moved.status_code == 200, moved.text
    _backdate(issue["key"], 10, assignee_id=[3])

    body = admin.get("/api/projects/ELEC/reports/load/history?days=14").json()
    assert len(body["dates"]) == 14
    lines = {s["user"]["id"]: s["values"] for s in body["series"] if s["kind"] == "user"}
    assert lines[me][-5] == 1 and lines[me][-1] == 0
    assert lines[other][-5] == 0 and lines[other][-1] == 1
    assert lines[me][0] == 0, "not created yet two weeks ago"
