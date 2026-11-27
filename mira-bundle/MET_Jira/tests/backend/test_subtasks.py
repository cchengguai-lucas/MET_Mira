from __future__ import annotations

ELEC_ISSUE = {"project": "ELEC", "due_date": "2099-12-31"}


def _status(project: dict, name: str) -> int:
    return next(s["id"] for s in project["statuses"] if s["name"] == name)


def _create(admin, **fields) -> dict:
    response = admin.post("/api/issues", json={**ELEC_ISSUE, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def test_subtask_inherits_from_parent(admin):
    project = admin.get("/api/projects/ELEC").json()
    machine = project["machine_groups"][0]["machines"][0]["id"]
    sprint = admin.post("/api/projects/ELEC/sprints", json={"name": "S1"}).json()
    parent = _create(admin, title="父任务", type="task", machine_ids=[machine],
                     sprint_id=sprint["id"], due_date="2099-06-30")
    child = admin.post("/api/issues", json={"project": "ELEC", "title": "子任务",
                                            "parent_id": parent["id"]})
    assert child.status_code == 200, child.text
    body = child.json()
    assert body["type"] == "subtask" and body["parent"]["key"] == parent["key"]
    assert body["due_date"] == "2099-06-30" and body["sprint_id"] == sprint["id"]
    assert [m["id"] for m in body["machines"]] == [machine]
    assert body["parent_progress"] == {"done": 0, "total": 1}


def test_parent_and_type_rules(admin):
    parent = _create(admin, title="父", type="task")
    child = _create(admin, title="子", parent_id=parent["id"])
    epic = _create(admin, title="大目标", type="epic")

    def error(response) -> str:
        assert response.status_code == 400, response.text
        return response.json()["detail"]

    assert "父任务" in error(admin.post("/api/issues", json={**ELEC_ISSUE, "title": "孤儿",
                                                             "type": "subtask"}))
    assert "子任务下" in error(admin.post("/api/issues", json={**ELEC_ISSUE, "title": "孙",
                                                              "parent_id": child["id"]}))
    assert "史诗" in error(admin.post("/api/issues", json={**ELEC_ISSUE, "title": "挂 Epic",
                                                          "parent_id": epic["id"]}))
    assert "自己" in error(admin.patch(f"/api/issues/{parent['key']}",
                                     json={"parent_id": parent["id"]}))
    other = _create(admin, title="另一个", type="task")
    assert "已有子项" in error(admin.patch(f"/api/issues/{parent['key']}",
                                        json={"parent_id": other["id"]}))
    assert "改为史诗" in error(admin.patch(f"/api/issues/{parent['key']}", json={"type": "epic"}))
    other_epic = _create(admin, title="另一史诗", type="epic")
    assert "史诗不能关联" in error(admin.post("/api/issues", json={
        **ELEC_ISSUE, "title": "嵌套史诗", "type": "epic", "epic_id": epic["id"],
    }))
    assert "史诗不能关联" in error(admin.patch(
        f"/api/issues/{epic['key']}", json={"epic_id": other_epic["id"]},
    ))

    detached = admin.patch(f"/api/issues/{child['key']}", json={"type": "bug"}).json()
    assert detached["type"] == "bug" and detached["parent"] is None
    converted = admin.patch(f"/api/issues/{other['key']}", json={"parent_id": parent["id"]}).json()
    assert converted["type"] == "subtask" and converted["parent"]["key"] == parent["key"]
    cleared = admin.patch(f"/api/issues/{other['key']}", json={"parent_id": None}).json()
    assert cleared["type"] == "task" and cleared["parent"] is None


def test_status_linkage(admin):
    project = admin.get("/api/projects/ELEC").json()
    parent = _create(admin, title="父", type="task")
    first = _create(admin, title="子一", parent_id=parent["id"])
    second = _create(admin, title="子二", parent_id=parent["id"])

    admin.patch(f"/api/issues/{first['key']}", json={"status_id": _status(project, "研发中")})
    assert admin.get(f"/api/issues/{parent['key']}").json()["status"]["name"] == "研发中"

    done = _status(project, "已完成")
    blocked = admin.patch(f"/api/issues/{parent['key']}", json={"status_id": done})
    assert blocked.status_code == 409 and blocked.json()["code"] == "open_subtasks"
    assert first["key"] in blocked.json()["detail"]

    closed = admin.patch(f"/api/issues/{parent['key']}",
                         json={"status_id": done, "close_subtasks": True})
    assert closed.status_code == 200, closed.text
    children = {c["key"]: c["status"]["name"] for c in closed.json()["children"]}
    assert children == {first["key"]: "已完成", second["key"]: "已完成"}
    assert admin.get(f"/api/issues/{first['key']}").json()["parent_progress"] == {
        "done": 2, "total": 2}


def test_requirement_parent_is_not_skipped_past_evaluation(admin):
    project = admin.get("/api/projects/ELEC").json()
    parent = _create(admin, title="需求", type="requirement")
    child = _create(admin, title="子", parent_id=parent["id"])
    admin.patch(f"/api/issues/{child['key']}", json={"status_id": _status(project, "研发中")})
    assert admin.get(f"/api/issues/{parent['key']}").json()["status"]["name"] == "待评估"


def test_sprint_follow_progress_and_delete(admin):
    s1 = admin.post("/api/projects/ELEC/sprints", json={"name": "S1"}).json()
    s2 = admin.post("/api/projects/ELEC/sprints", json={"name": "S2"}).json()
    parent = _create(admin, title="父", type="task", sprint_id=s1["id"])
    child = _create(admin, title="子", parent_id=parent["id"])
    assert child["sprint_id"] == s1["id"]

    admin.patch(f"/api/issues/{parent['key']}", json={"sprint_id": s2["id"]})
    assert admin.get(f"/api/issues/{child['key']}").json()["sprint_id"] == s2["id"]

    backlog = admin.get("/api/projects/ELEC/backlog").json()
    rows = next(s for s in backlog["sprints"] if s["id"] == s2["id"])["issues"]
    assert [r["key"] for r in rows] == [parent["key"]]
    assert rows[0]["subtasks"] == {"done": 0, "total": 1}

    found = admin.get("/api/search", params={"jql": f"key = {parent['key']}"}).json()["issues"]
    assert found[0]["subtasks"] == {"done": 0, "total": 1}

    assert admin.delete(f"/api/issues/{parent['key']}").status_code == 200
    orphan = admin.get(f"/api/issues/{child['key']}").json()
    assert orphan["type"] == "task" and orphan["parent"] is None


def test_report_summary_can_exclude_subtasks(admin):
    parent = _create(admin, title="父", type="task")
    _create(admin, title="子", parent_id=parent["id"])
    both = admin.get("/api/projects/ELEC/reports/summary").json()
    top = admin.get("/api/projects/ELEC/reports/summary", params={"subtasks": False}).json()
    assert both["total"] == top["total"] + 1 and "subtask" not in top["by_type"]


def test_linking_a_subtask_adopts_it(admin):
    first = _create(admin, title="主任务一", type="task")
    second = _create(admin, title="主任务二", type="task")
    child = _create(admin, title="子", parent_id=first["id"])
    plain = _create(admin, title="普通", type="task")

    again = admin.post(f"/api/issues/{first['key']}/links", json={"target_key": child["key"]})
    assert again.json()["link_result"] == "already_subtask"

    asked = admin.post(f"/api/issues/{second['key']}/links", json={"target_key": child["key"]})
    assert asked.status_code == 409 and asked.json()["code"] == "subtask_has_parent"
    moved = admin.post(f"/api/issues/{second['key']}/links",
                       json={"target_key": child["key"], "reparent": True}).json()
    assert moved["link_result"] == "subtask_added" and moved["links"] == []
    assert [c["key"] for c in moved["children"]] == [child["key"]]

    info = admin.get(f"/api/issues/{child['key']}").json()["parent_info"]
    assert info["key"] == second["key"] and info["status"]["name"]

    linked = admin.post(f"/api/issues/{second['key']}/links", json={"target_key": plain["key"]})
    assert [link["issue"]["key"] for link in linked.json()["links"]] == [plain["key"]]
