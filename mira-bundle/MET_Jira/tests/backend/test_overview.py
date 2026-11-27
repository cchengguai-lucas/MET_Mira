from __future__ import annotations

from datetime import date, timedelta


def _create(admin, **data):
    response = admin.post("/api/issues", json={
        "project": "ELEC", "due_date": "2099-01-01", **data,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_overview_counts_breakdowns_and_epic_progress(admin):
    soon = (date.today() + timedelta(days=3)).isoformat()
    epic = _create(admin, type="epic", title="概要史诗")
    _create(admin, type="task", title="即将到期", due_date=soon, epic_id=epic["id"])
    _create(admin, type="bug", title="缺陷", priority="highest", epic_id=epic["id"])

    body = admin.get("/api/projects/ELEC/overview").json()
    assert body["counts"]["created"] >= 3
    assert body["counts"]["due_soon"] >= 1
    assert sum(s["count"] for s in body["statuses"]) == body["total"]
    assert [p["priority"] for p in body["priorities"]] == [
        "highest", "high", "medium", "low", "lowest"]
    assert {"epic", "task", "bug"} <= {t["type"] for t in body["types"]}
    assert sum(w["count"] for w in body["workload"]) == body["open"]
    row = next(e for e in body["epics"] if e["key"] == epic["key"])
    assert row["total"] == 2 and row["done"] == 0


def test_project_activity_feed_is_paginated(admin):
    issue = _create(admin, type="task", title="动态")
    admin.post(f"/api/issues/{issue['key']}/comments", json={"body": "你好"})
    page = admin.get("/api/projects/ELEC/activity?size=1").json()
    assert page["total"] >= 2 and len(page["items"]) == 1
    assert page["items"][0]["issue"]["key"] == issue["key"]
    assert page["items"][0]["action"] == "comment"


def test_dashboard_overview_personal_progress(admin):
    me = admin.get("/api/auth/me").json()["user"]["id"]
    soon = (date.today() + timedelta(days=2)).isoformat()
    past = (date.today() - timedelta(days=1)).isoformat()
    open_issue = _create(admin, type="task", title="我的未完成", due_date=soon,
                         priority="high", assignee_id=me)
    overdue = _create(admin, type="bug", title="我的逾期", due_date=past,
                      priority="highest", assignee_id=me)
    assert open_issue["assignee"]["id"] == me
    assert overdue["assignee"]["id"] == me

    body = admin.get("/api/dashboard/overview").json()
    assert body["total"] >= 2
    assert body["open"] >= 2
    assert body["counts"]["created"] >= 2
    assert body["counts"]["due_soon"] >= 1
    assert body["counts"]["overdue"] >= 1
    assert sum(s["count"] for s in body["statuses"]) == body["total"]
    assert all(s["count"] > 0 for s in body["statuses"])
    assert [p["priority"] for p in body["priorities"]] == [
        "highest", "high", "medium", "low", "lowest"]
    assert sum(p["count"] for p in body["priorities"]) == body["total"]


def test_timeline_rolls_children_up_into_epic_bars(admin):
    epic = _create(admin, type="epic", title="时间线史诗", due_date="2099-01-01")
    child = _create(admin, type="task", title="子工作", due_date="2099-03-01",
                    epic_id=epic["id"])
    _create(admin, type="subtask", title="子任务", parent_id=child["id"])

    body = admin.get("/api/projects/ELEC/timeline").json()
    items = {row["key"]: row for row in body["items"]}
    assert all(row["type"] != "subtask" for row in body["items"])
    assert items[child["key"]]["epic_key"] == epic["key"]
    assert items[child["key"]]["end"] == "2099-03-01"
    assert items[epic["key"]]["end"] == "2099-03-01"
    assert items[epic["key"]]["total"] == 1
    assert body["today"] == date.today().isoformat()
