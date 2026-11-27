from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

ELEC_ISSUE = {"project": "ELEC", "due_date": "2099-12-31", "type": "bug"}


def _days_ago(days: int) -> datetime:
    return datetime.combine(date.today() - timedelta(days=days), time(12), tzinfo=timezone.utc)


def _about(value: float, days: int) -> bool:
    """Durations up to now depend on the time of day; backdated events sit at noon."""
    return abs(value - days) <= 0.5


def _rewrite_history(key: str, created: int, moves: list[int], legacy: bool = False) -> None:
    """Backdate an issue and its status changes; ``legacy`` drops the logged ids, as in
    rows written before ids were recorded."""
    from backend.db import session_factory
    from backend.models import ActivityLog, Issue

    with session_factory()() as db:
        issue = db.scalar(select(Issue).where(Issue.key == key))
        issue.created_at = _days_ago(created)
        rows = db.scalars(select(ActivityLog).where(ActivityLog.issue_id == issue.id)
                          .order_by(ActivityLog.id)).all()
        changes = [r for r in rows if r.field == "status_id"]
        for row, days in zip(changes, moves, strict=True):
            row.created_at = _days_ago(days)
        if legacy:
            for row in rows:
                row.old_ref = row.new_ref = None
                row.meta = {}
        db.commit()


def _create(admin, **fields) -> dict:
    tester = admin.get("/api/auth/me").json()["user"]["id"]
    response = admin.post("/api/issues", json={**ELEC_ISSUE, "tester_id": tester, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def _mover(admin):
    project = admin.get("/api/projects/ELEC").json()
    ids = {s["name"]: s["id"] for s in project["statuses"]}

    def move(issue: dict, *names: str, **extra) -> dict:
        body: dict = {}
        for name in names:
            payload: dict = {"status_id": ids[name]}
            if name == "重新打开":
                payload["reopen_reason"] = "返工验证"
            response = admin.patch(f"/api/issues/{issue['key']}", json=payload)
            assert response.status_code == 200, response.text
            body = response.json()
        if extra:
            response = admin.patch(f"/api/issues/{issue['key']}", json=extra)
            assert response.status_code == 200, response.text
            body = response.json()
        return body

    return move


def test_health_scores_risks_and_flow(admin):
    move = _mover(admin)
    late = _create(admin, title="逾期", due_date="2020-01-01")
    blocker = _create(admin, title="阻塞者")
    blocked = _create(admin, title="被阻塞")
    link = admin.post(f"/api/issues/{blocker['key']}/links",
                      json={"target_key": blocked["key"], "link_type": "blocks"})
    assert link.status_code == 200, link.text
    move(_create(admin, title="完成"), "研发中", "开发完成", "测试中", "测试通过", "已完成")
    move(_create(admin, title="驳回"), "已驳回")
    _create(admin, title="大目标", type="epic", due_date="2020-01-01")

    body = admin.get("/api/projects/ELEC/reports/health?days=14").json()
    assert [i["key"] for i in body["lists"]["overdue"]] == [late["key"]], "epics are not work"
    assert [i["key"] for i in body["lists"]["blocked"]] == [blocked["key"]]
    assert body["counts"]["open"] == 3 and body["counts"]["total"] == 5
    assert body["flow"]["completed"] == 1 and body["flow"]["rejected"] == 1
    week = body["throughput"][-1]
    assert len(body["throughput"]) == 2
    assert (week["created"], week["completed"], week["rejected"], week["open"]) == (5, 1, 1, 3)
    assert body["score"] < 100 and body["level"] in {"good", "attention", "risk"}
    assert "overdue" in {s["key"] for s in body["signals"]}

    projects = admin.get("/api/dashboard/health").json()["projects"]
    elec = next(p for p in projects if p["key"] == "ELEC")
    assert elec["score"] == body["score"] and "lists" not in elec


def test_quality_follows_the_window(admin):
    move = _mover(admin)
    failing = move(
        _create(admin, title="未通过"), "研发中", "开发完成", "测试中", test_passed=False,
    )
    assert failing["status"]["name"] == "测试未通过"
    waiting = move(_create(admin, title="待测"), "研发中", "开发完成", "测试中")

    body = admin.get("/api/projects/ELEC/reports/quality?days=7").json()
    assert (body["in_test"], body["failing"], body["passed"], body["failed"]) == (1, 1, 0, 1)
    assert body["pass_rate"] == 0 and body["failed_issues"] == 1
    assert {i["key"] for i in body["queue"]} == {failing["key"], waiting["key"]}

    move(failing, "重新打开")
    assert admin.get("/api/projects/ELEC/reports/quality?days=7").json()["reopened"] == 1
    summary = admin.get("/api/projects/ELEC/reports/summary?days=7").json()
    assert summary["test_window"] == {"days": 7, "passed": 0, "failed": 1}


def test_status_changes_log_ids(admin):
    move = _mover(admin)
    issue = move(_create(admin, title="记录"), "研发中")
    rows = admin.get(f"/api/issues/{issue['key']}/activity").json()["activity"]
    change = next(r for r in rows if r["field"] == "status_id")
    assert change["new_value"] == "研发中"

    from backend.db import session_factory
    from backend.models import ActivityLog

    with session_factory()() as db:
        row = db.get(ActivityLog, change["id"])
        assert row.new_ref == issue["status"]["id"] and row.old_ref is not None
        created = db.scalar(select(ActivityLog).where(ActivityLog.issue_id == issue["id"],
                                                      ActivityLog.action == "created"))
        assert created.meta["status_id"] == row.old_ref


def test_cfd_replays_history_through_renames(admin):
    move = _mover(admin)
    tracked = move(_create(admin, title="新记录"), "研发中", "开发完成", "测试中")
    legacy = move(_create(admin, title="旧记录"), "研发中", "开发完成", "测试中")
    first = admin.get(f"/api/issues/{legacy['key']}/activity").json()["activity"][-2]["old_value"]
    _rewrite_history(tracked["key"], created=5, moves=[3, 2, 1])
    _rewrite_history(legacy["key"], created=5, moves=[3, 2, 1], legacy=True)
    project = admin.get("/api/projects/ELEC").json()
    developing = next(s for s in project["statuses"] if s["name"] == "研发中")
    renamed = admin.put(f"/api/projects/ELEC/statuses/{developing['id']}",
                        json={"name": "开发中", "category": "doing", "color": developing["color"]})
    assert renamed.status_code == 200, renamed.text

    body = admin.get("/api/projects/ELEC/reports/cfd?days=7").json()
    names = [s["name"] for s in body["statuses"]]
    assert "开发中" in names and "研发中" not in names

    def day(days_ago: int) -> dict:
        return body["series"][-1 - days_ago]

    assert sum(day(6)[n] for n in names) == 0, "not created yet"
    assert day(5)[first] == 2 and day(4)[first] == 2
    assert day(3)["开发中"] == 1, "logged ids survive the rename"
    assert day(3)[first] == 1, "an unknown old name keeps the previous status"
    assert all(sum(day(d)[n] for n in names) == 2 for d in range(6)), "nothing drops out"
    assert day(1)["测试中"] == 2 and day(0)["测试中"] == 2


def test_completion_is_split_from_rejection_and_survives_reopen(admin):
    move = _mover(admin)
    done = move(
        _create(admin, title="完成后重开"),
        "研发中", "开发完成", "测试中", "测试通过", "已完成",
    )
    move(_create(admin, title="驳回"), "已驳回")
    move(done, "重新打开")

    trend = admin.get("/api/projects/ELEC/reports/summary?days=7").json()["trend"][-1]
    assert (trend["completed"], trend["rejected"]) == (1, 1)
    health = admin.get("/api/projects/ELEC/reports/health?days=14").json()
    assert health["flow"]["completed"] == 1 and health["flow"]["rejected"] == 1
    assert health["throughput"][-1]["open"] == 1, "the reopened issue is open again"


def test_burndown_follows_status_history(admin):
    move = _mover(admin)
    sprint = admin.post("/api/projects/ELEC/sprints", json={"name": "S1"}).json()
    issue = move(_create(admin, title="冲刺", sprint_id=sprint["id"]), "研发中", "开发完成",
                 "测试中", "测试通过", "已完成")
    start = admin.post(f"/api/sprints/{sprint['id']}/start",
                       json={"start_date": (date.today() - timedelta(days=4)).isoformat(),
                             "end_date": (date.today() + timedelta(days=4)).isoformat()})
    assert start.status_code == 200, start.text
    _rewrite_history(issue["key"], created=6, moves=[4, 3, 2, 1, 1])

    def remaining() -> list[int]:
        series = admin.get(f"/api/projects/ELEC/reports/burndown?sprint_id={sprint['id']}")
        return [p["remaining_count"] for p in series.json()["series"] if "remaining_count" in p]

    assert remaining() == [1, 1, 1, 0, 0]
    move(issue, "重新打开")
    assert remaining() == [1, 1, 1, 0, 1], "reopening today does not rewrite earlier days"


def test_health_signals_list_their_issues(admin):
    from backend.db import session_factory
    from backend.models import Issue

    keys = [_create(admin, title=f"无截止 {n}")["key"] for n in range(3)]
    with session_factory()() as db:
        for issue in db.scalars(select(Issue).where(Issue.key.in_(keys))):
            issue.due_date = None
        db.commit()
    body = admin.get("/api/projects/ELEC/reports/health").json()
    signals = {s["key"]: s for s in body["signals"]}
    assert signals["no_due"]["count"] == 3 and len(body["lists"]["no_due"]) == 3
    assert signals["net_flow"]["count"] == 3 and len(body["lists"]["net_flow"]) == 3
    assert len(body["lists"]["unassigned"]) == signals["unassigned"]["count"]


def test_flow_measures_cycle_lead_and_aging(admin):
    move = _mover(admin)
    slow = move(_create(admin, title="慢"), "研发中", "开发完成", "测试中", "测试通过", "已完成")
    quick = move(_create(admin, title="快"), "研发中", "开发完成", "测试中", "测试通过", "已完成")
    stuck = move(_create(admin, title="卡住"), "研发中")
    fresh = _create(admin, title="新的")
    move(_create(admin, title="驳回"), "已驳回")
    _rewrite_history(slow["key"], created=10, moves=[8, 7, 5, 3, 2])
    _rewrite_history(quick["key"], created=6, moves=[4, 3, 2, 1, 1])
    _rewrite_history(stuck["key"], created=20, moves=[15])

    body = admin.get("/api/projects/ELEC/reports/flow?days=30").json()
    points = {p["key"]: p for p in body["completed"]}
    assert set(points) == {slow["key"], quick["key"]}, "rejected work is not completed"
    assert (points[slow["key"]]["lead"], points[slow["key"]]["cycle"]) == (8.0, 6.0)
    assert (points[quick["key"]]["lead"], points[quick["key"]]["cycle"]) == (5.0, 3.0)
    assert body["cycle"]["count"] == 2 and body["cycle"]["median"] == 4.5
    assert body["lead"]["median"] == 6.5

    developing = next(s for s in body["statuses"] if s["name"] == "研发中")
    assert developing["count"] == 2 and developing["avg"] == 1.0
    assert developing["current"] == 1 and _about(developing["current_avg"], 15)
    assert all(s["category"] != "done" for s in body["statuses"])

    aging = body["aging"]
    assert [a["key"] for a in aging] == [stuck["key"], fresh["key"]]
    assert _about(aging[0]["days_in_status"], 15) and aging[0]["level"] == "danger"
    assert _about(aging[0]["days_in_progress"], 15) and _about(aging[0]["age"], 20)
    assert aging[1]["level"] is None and aging[1]["days_in_progress"] is None

    narrow = admin.get("/api/projects/ELEC/reports/flow?days=7").json()
    assert {p["key"] for p in narrow["completed"]} == {slow["key"], quick["key"]}
    _rewrite_history(slow["key"], created=30, moves=[28, 26, 25, 22, 20])
    narrow = admin.get("/api/projects/ELEC/reports/flow?days=7").json()
    assert {p["key"] for p in narrow["completed"]} == {quick["key"]}


def test_load_counts_open_work_per_person(admin, make_user):
    move = _mover(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    doing = move(_create(admin, title="进行中", assignee_id=me), "研发中")
    _create(admin, title="逾期", assignee_id=me, due_date="2020-01-01")
    move(_create(admin, title="已完成", assignee_id=me), "研发中", "开发完成", "测试中",
         "测试通过", "已完成")
    unassigned = _create(admin, title="无人")
    _rewrite_history(doing["key"], created=30, moves=[20])

    body = admin.get("/api/projects/ELEC/reports/load").json()
    person = next(p for p in body["people"] if p["user"]["id"] == me)
    assert (person["open"], person["todo"], person["doing"]) == (2, 1, 1)
    assert (person["overdue"], person["aging"]) == (1, 1)
    assert [i["title"] for i in person["issues"]] == ["逾期", "进行中"], "ordered by due date"
    assert unassigned["key"] in {i["key"] for i in body["unassigned"]["issues"]}
    assert sum(u["open"] for u in body["units"]) == sum(p["open"] for p in body["people"])
    assert "completed" not in person


def test_board_reports_when_issues_entered_their_status(admin):
    move = _mover(admin)
    old = move(_create(admin, title="久留"), "研发中")
    new = _create(admin, title="刚建")
    _rewrite_history(old["key"], created=30, moves=[10])
    project = admin.get("/api/projects/ELEC").json()
    board = admin.get(f"/api/boards/{project['boards'][0]['id']}").json()
    aging = board["aging"]
    assert (aging["warn"], aging["danger"]) == (7, 14)
    entered = datetime.fromisoformat(aging["since"][str(old["id"])])
    assert entered.date() == _days_ago(10).date()
    fresh = datetime.fromisoformat(aging["since"][str(new["id"])])
    assert datetime.now(timezone.utc) - fresh < timedelta(minutes=5)
