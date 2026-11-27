from __future__ import annotations

from datetime import date

ELEC_ISSUE = {"project": "ELEC", "due_date": "2099-12-31"}


def _project(admin) -> dict:
    response = admin.get("/api/projects/ELEC")
    assert response.status_code == 200
    return response.json()


def _status(project: dict, name: str) -> int:
    return next(s["id"] for s in project["statuses"] if s["name"] == name)


def test_seed_project_and_me(admin):
    me = admin.get("/api/auth/me").json()["user"]
    assert me["is_admin"] is True
    project = _project(admin)
    names = [s["name"] for s in project["statuses"]]
    assert names == ["待评估", "方案讨论", "重新打开", "研发中", "开发完成", "测试中",
                     "测试通过", "测试未通过", "已完成", "已驳回"]
    board = admin.get(f"/api/boards/{project['boards'][0]['id']}").json()
    columns = [c["name"] for c in board["board"]["columns"]]
    assert columns == names
    assert any(g["name"] == "CMB-1" for g in project["machine_groups"])
    assert len(project["boards"]) == 2


def test_requirement_three_sections_and_workflow(admin, make_user):
    project = _project(admin)
    requester, requester_id = make_user("req", "需求人")
    elec, elec_id = make_user("elec", "电气工程师")
    tester, tester_id = make_user("qa", "测试员")
    for user_id, role in (
        (requester_id, "requester"), (elec_id, "electrical"), (elec_id, "process"),
        (tester_id, "tester"),
    ):
        assert admin.post("/api/projects/ELEC/members",
                          json={"user_id": user_id, "role": role}).status_code == 200

    machine = project["machine_groups"][0]["machines"][0]["id"]
    created = requester.post("/api/issues", json={
        **ELEC_ISSUE, "title": "PM-A 腔体温度报警", "description": "报警阈值需要调整",
        "machine_ids": [machine], "assignee_id": elec_id, "tester_id": tester_id,
    })
    assert created.status_code == 200, created.text
    issue = created.json()
    key = issue["key"]
    assert issue["status"]["name"] == "待评估"
    assert "requester" in issue["editable_sections"]

    # requester cannot fill electrical fields
    denied = requester.patch(f"/api/issues/{key}", json={"verify_method": "x"})
    assert denied.status_code == 403
    # electrical cannot edit requester fields, but can edit its own section
    assert elec.patch(f"/api/issues/{key}", json={"title": "改标题"}).status_code == 403
    ok = elec.patch(f"/api/issues/{key}", json={"update_scope": ["软件"],
                                                 "involves_software": True,
                                                 "software_owner_id": elec_id,
                                                 "verify_method": "上机验证",
                                                 "verify_criteria": "温度稳定"})
    assert ok.status_code == 200, ok.text
    assert admin.patch(
        f"/api/issues/{key}", json={"software_jira_key": "CVD-1"},
    ).status_code == 200

    # workflow: 待评估 -> 测试中 skips steps
    blocked = elec.patch(f"/api/issues/{key}", json={"status_id": _status(project, "测试中")})
    assert blocked.status_code == 409
    for name in ("方案讨论", "研发中", "开发完成", "测试中"):
        if name == "研发中":
            noted = elec.patch(f"/api/issues/{key}", json={"scheme_note": "三方方案"})
            assert noted.status_code == 200
            submitted = elec.patch(f"/api/issues/{key}", json={"scheme_action": "submit"})
            assert submitted.status_code == 200
            assert elec.patch(f"/api/issues/{key}", json={
                "electrical_scheme_ok": True,
                "process_scheme_ok": True,
                "software_scheme_ok": True,
            }).status_code == 200
        if name == "开发完成":
            from sqlalchemy import select

            from backend.db import session_factory
            from backend.models import Issue

            with session_factory()() as db:
                row = db.scalar(select(Issue).where(Issue.key == key))
                row.software_jira_snapshot = {
                    "assignee": None, "tester": None, "status": "待上机验证",
                    "due_date": None, "fetched_at": "2026-11-08T00:00:00+00:00",
                    "error": None,
                }
                db.commit()
        if name == "测试中":
            ready = elec.patch(f"/api/issues/{key}", json={"software_ready": True})
            assert ready.status_code == 200, ready.text
            denied = elec.patch(f"/api/issues/{key}", json={"status_id": _status(project, name)})
            assert denied.status_code == 403, denied.text
            hidden = {s["name"] for s in elec.get(f"/api/issues/{key}").json()["transitions"]}
            assert name not in hidden
            step = tester.patch(f"/api/issues/{key}", json={"status_id": _status(project, name)})
        else:
            step = elec.patch(f"/api/issues/{key}", json={"status_id": _status(project, name)})
        assert step.status_code == 200, step.text
        if name == "开发完成":
            notes = tester.get("/api/notifications").json()["notifications"]
            assert any(note["kind"] == "ready_for_test" for note in notes)

    # tester failing moves it to 测试未通过 and tells the assignee; rework goes through 重新打开
    assert elec.patch(f"/api/issues/{key}",
                      json={"status_id": _status(project, "测试通过")}).status_code == 403
    failed = tester.patch(f"/api/issues/{key}", json={"test_passed": False})
    assert failed.status_code == 200
    assert failed.json()["status"]["name"] == "测试未通过"
    assert failed.json()["update_date"] is not None
    kinds = [n["kind"] for n in elec.get("/api/notifications").json()["notifications"]]
    assert "test_failed" in kinds
    assert elec.patch(f"/api/issues/{key}",
                      json={"status_id": _status(project, "研发中")}).status_code == 409
    for name in ("重新打开", "研发中", "开发完成", "测试中"):
        if name == "测试中":
            ready = elec.patch(f"/api/issues/{key}", json={"software_ready": True})
            assert ready.status_code == 200, ready.text
        payload = {"status_id": _status(project, name)}
        if name == "重新打开":
            payload["reopen_reason"] = "返工验证"
        actor = tester if name == "测试中" else elec
        step = actor.patch(f"/api/issues/{key}", json=payload)
        assert step.status_code == 200, step.text
    assert step.json()["test_passed"] is None, "a new test round starts without a result"
    passed = tester.patch(f"/api/issues/{key}", json={"test_passed": True})
    assert passed.json()["status"]["name"] == "测试通过"
    assert passed.json()["resolved_at"] is None, "pass holds until the assignee accepts"
    notes = elec.get("/api/notifications").json()["notifications"]
    assert any(note["kind"] == "ready_to_close" for note in notes)
    # Tester (not assignee) cannot finish or reject from 测试通过
    assert tester.patch(f"/api/issues/{key}",
                        json={"status_id": _status(project, "已完成")}).status_code == 403
    done = elec.patch(f"/api/issues/{key}", json={"status_id": _status(project, "已完成")})
    assert done.status_code == 200 and done.json()["status"]["name"] == "已完成"
    assert done.json()["resolved_at"] is not None

    activity = admin.get(f"/api/issues/{key}/activity").json()["activity"]
    assert any(a["field"] == "status_id" and a["meta"].get("auto") for a in activity)
    notes = elec.get("/api/notifications").json()
    assert notes["unread"] >= 1


def test_comments_mentions_links_and_bulk(admin, make_user):
    other, other_id = make_user("zhang", "张三")
    a = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "A", "type": "task"}).json()
    b = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "B", "type": "bug"}).json()
    comment = admin.post(f"/api/issues/{a['key']}/comments", json={"body": "请 @zhang 看一下"})
    assert comment.status_code == 200
    kinds = [n["kind"] for n in other.get("/api/notifications").json()["notifications"]]
    assert "mention" in kinds
    linked = admin.post(f"/api/issues/{a['key']}/links",
                        json={"target_key": b["key"], "link_type": "blocks"}).json()
    assert linked["links"][0]["label"] == "阻塞"
    result = admin.post("/api/issues/bulk", json={"keys": [a["key"], b["key"]],
                                                  "changes": {"priority": "high",
                                                              "assignee_id": other_id}}).json()
    assert len(result["updated"]) == 2 and not result["failed"]


def test_rank_board_and_sprint(admin):
    project = _project(admin)
    keys = [admin.post("/api/issues", json={**ELEC_ISSUE, "title": f"T{i}",
                                            "type": "task", "story_points": 2}).json()["key"]
            for i in range(3)]
    moved = admin.post(f"/api/issues/{keys[2]}/rank",
                       json={"before_key": None, "after_key": keys[0]}).json()
    stale_move = admin.post(
        f"/api/issues/{keys[2]}/rank",
        json={"after_key": keys[1], "expected_version": moved["lock_version"] - 1},
    )
    assert stale_move.status_code == 409 and stale_move.json()["code"] == "stale_write"
    board_id = project["boards"][0]["id"]
    board = admin.get(f"/api/boards/{board_id}").json()
    ordered = [i["key"] for i in board["issues"]]
    assert ordered.index(moved["key"]) < ordered.index(keys[0])

    sprint = admin.post("/api/projects/ELEC/sprints", json={"name": "S1"}).json()
    admin.post(f"/api/issues/{keys[0]}/rank", json={"sprint_id": sprint["id"]})
    started = admin.post(f"/api/sprints/{sprint['id']}/start",
                         json={"start_date": "2026-10-01", "end_date": "2026-10-14"})
    assert started.status_code == 200, started.text
    stale_complete = admin.post(
        f"/api/sprints/{sprint['id']}/complete",
        json={"expected_version": sprint["lock_version"]},
    )
    assert stale_complete.status_code == 409
    assert stale_complete.json()["code"] == "stale_write"
    scrum = next(b for b in project["boards"] if b["kind"] == "scrum")
    assert [i["key"] for i in admin.get(f"/api/boards/{scrum['id']}").json()["issues"]] == [
        keys[0]]
    burndown = admin.get("/api/projects/ELEC/reports/burndown").json()
    assert burndown["total_count"] == 1
    done = admin.post(
        f"/api/sprints/{sprint['id']}/complete",
        json={"expected_version": started.json()["lock_version"]},
    ).json()
    assert done["state"] == "closed" and done["stats"]["incomplete_count"] == 1
    backlog = admin.get("/api/projects/ELEC/backlog").json()
    assert keys[0] in [i["key"] for i in backlog["backlog"]]
    summary = admin.get("/api/projects/ELEC/reports/summary").json()
    assert summary["total"] == 3
    cfd = admin.get("/api/projects/ELEC/reports/cfd?days=7").json()
    assert len(cfd["series"]) == 7
    dash = admin.get("/api/dashboard").json()
    assert "assigned" in dash


def test_issue_expected_version_and_watch_are_idempotent(admin):
    issue = admin.post(
        "/api/issues",
        json={**ELEC_ISSUE, "title": "并发写入", "type": "task"},
    ).json()
    updated = admin.patch(
        f"/api/issues/{issue['key']}",
        json={"title": "第一版", "expected_version": issue["lock_version"]},
    )
    assert updated.status_code == 200
    stale = admin.patch(
        f"/api/issues/{issue['key']}",
        json={"title": "过期版本", "expected_version": issue["lock_version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_write"
    assert admin.post(f"/api/issues/{issue['key']}/watch").status_code == 200
    assert admin.post(f"/api/issues/{issue['key']}/watch").status_code == 200


def test_workflow_is_stepwise_for_everyone(admin):
    project = _project(admin)
    req = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "逐步流转"}).json()
    bug = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "缺陷", "type": "bug"}).json()
    expected = {req["key"]: {"方案讨论", "已驳回"}, bug["key"]: {"研发中", "已驳回"}}
    for issue in (req, bug):
        detail = admin.get(f"/api/issues/{issue['key']}").json()
        assert {s["name"] for s in detail["transitions"]} == expected[issue["key"]]
        jump = admin.patch(f"/api/issues/{issue['key']}",
                           json={"status_id": _status(project, "已完成")})
        assert jump.status_code == 409, "project admins must follow the workflow too"

    rules = dict(project["workflows"]["requirement"])
    rules.pop("待评估")
    settings = {"transitions": {"requirement": rules}}
    rejected = admin.put("/api/projects/ELEC", json={"settings": settings})
    assert rejected.status_code == 400
    assert "工作流无效" in rejected.json()["detail"]
    detail = admin.get(f"/api/issues/{req['key']}").json()
    assert {s["name"] for s in detail["transitions"]} == {"方案讨论", "已驳回"}


def test_only_testers_enter_verification_statuses(admin, make_user):
    project = _project(admin)
    eng, eng_id = make_user("eng-only", "电气")
    named, named_id = make_user("named-tester", "指定测试人")
    qa, qa_id = make_user("qa-role", "测试角色")
    for user_id, role in ((eng_id, "electrical"), (named_id, "member"), (qa_id, "tester")):
        assert admin.post(
            "/api/projects/ELEC/members", json={"user_id": user_id, "role": role},
        ).status_code == 200
    created = admin.post("/api/issues", json={
        **ELEC_ISSUE, "title": "测试权限", "type": "bug", "tester_id": named_id,
    })
    assert created.status_code == 200, created.text
    key = created.json()["key"]

    def sid(name: str) -> int:
        return _status(project, name)

    for name in ("研发中", "开发完成"):
        step = admin.patch(f"/api/issues/{key}", json={"status_id": sid(name)})
        assert step.status_code == 200, step.text

    def names(client) -> set[str]:
        body = client.get(f"/api/issues/{key}").json()
        return {item["name"] for item in body["transitions"]}

    assert names(eng).isdisjoint({"测试中", "测试通过", "测试未通过"})
    assert "测试中" in names(named)
    assert "测试中" in names(qa)

    denied = eng.patch(f"/api/issues/{key}", json={"status_id": sid("测试中")})
    assert denied.status_code == 403, denied.text
    assert "只有测试人员" in denied.json()["detail"]
    ranked = eng.post(f"/api/issues/{key}/rank", json={"status_id": sid("测试中")})
    assert ranked.status_code == 403, ranked.text
    bulk = eng.post("/api/issues/bulk", json={
        "keys": [key], "changes": {"status_id": sid("测试中")},
    })
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["updated"] == []
    assert "只有测试人员" in bulk.json()["failed"][0]["error"]

    assert admin.put("/api/projects/ELEC", json={"settings": {
        "transition_gates": {
            "测试中": {"required_fields": [], "approvals": [{"role": "electrical", "count": 1}]},
        },
    }}).status_code == 200
    asked = eng.post(f"/api/issues/{key}/transition-requests", json={"status_id": sid("测试中")})
    assert asked.status_code == 403, asked.text
    cleared = admin.put("/api/projects/ELEC", json={"settings": {"transition_gates": {}}})
    assert cleared.status_code == 200

    entered = named.patch(f"/api/issues/{key}", json={"status_id": sid("测试中")})
    assert entered.status_code == 200, entered.text
    assert entered.json()["status"]["name"] == "测试中"
    assert entered.json()["test_passed"] is None
    for name in ("测试通过", "测试未通过"):
        outcome = eng.patch(f"/api/issues/{key}", json={"status_id": sid(name)})
        assert outcome.status_code == 403, outcome.text
        assert "只有测试人员" in outcome.json()["detail"]
    blocked = eng.patch(f"/api/issues/{key}", json={"test_passed": False})
    assert blocked.status_code == 403, blocked.text
    failed = qa.patch(f"/api/issues/{key}", json={"status_id": sid("测试未通过")})
    assert failed.status_code == 200, failed.text
    assert failed.json()["test_passed"] is False
    reopened = eng.patch(f"/api/issues/{key}", json={
        "status_id": sid("重新打开"), "reopen_reason": "电气返工",
    })
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"]["name"] == "重新打开"


def test_bug_rework_loop(admin):
    project = _project(admin)
    bug = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "缺陷返工", "type": "bug"})
    key = bug.json()["key"]

    def move(name: str, **extra) -> dict:
        payload = {"status_id": _status(project, name), **extra}
        if name == "重新打开" and "reopen_reason" not in payload:
            payload["reopen_reason"] = "返工验证"
        r = admin.patch(f"/api/issues/{key}", json=payload)
        assert r.status_code == 200, r.text
        return r.json()

    def result(value: bool | None):
        return admin.patch(f"/api/issues/{key}", json={"test_passed": value})

    developing = move("研发中")
    assert developing["test_result_editable"] is False
    move("开发完成")
    locked = result(False)
    assert locked.status_code == 409, "a result is only recorded under test"

    gate = admin.patch(f"/api/issues/{key}", json={"status_id": _status(project, "测试中")})
    assert gate.status_code == 409 and "测试人" in gate.json()["detail"]
    me = admin.get("/api/auth/me").json()["user"]["id"]
    assert admin.patch(f"/api/issues/{key}", json={"tester_id": me}).status_code == 200

    move("测试中")
    detail = admin.get(f"/api/issues/{key}").json()
    assert {s["name"] for s in detail["transitions"]} == {"测试通过", "测试未通过"}
    assert move("测试未通过")["test_passed"] is False, "a manual fail records the result"
    detail = admin.get(f"/api/issues/{key}").json()
    assert [s["name"] for s in detail["transitions"]] == ["重新打开"]
    assert detail["test_result_editable"] is True

    # a mistaken failure can be corrected from 测试未通过; clearing it returns to 测试中
    corrected = result(True).json()
    assert corrected["status"]["name"] == "测试通过" and corrected["resolved_at"] is None
    assert result(False).json()["status"]["name"] == "测试未通过"
    cleared = result(None).json()
    assert cleared["status"]["name"] == "测试中" and cleared["resolved_at"] is None
    assert result(False).json()["status"]["name"] == "测试未通过"

    blocked = admin.patch(f"/api/issues/{key}", json={"status_id": _status(project, "重新打开")})
    assert blocked.status_code == 409 and blocked.json().get("code") == "reopen_reason"
    reopened_fail = move("重新打开", reopen_reason="定位到伺服参数问题")
    assert reopened_fail["test_passed"] is None, "reopen clears the previous test result"
    activity = admin.get(f"/api/issues/{key}/activity").json()["activity"]
    reopen_logs = [
        a for a in activity
        if a["action"] == "transition" and a["new_value"] == "重新打开"
    ]
    assert reopen_logs and reopen_logs[0]["meta"].get("reopen_reason") == "定位到伺服参数问题"
    assert move("研发中")["test_passed"] is None, "rework starts without the old result"
    move("开发完成")
    assert move("测试中")["test_passed"] is None
    assert move("测试通过")["test_passed"] is True
    done = move("已完成")
    assert done["test_passed"] is True and done["resolved_at"] is not None
    assert done["update_date"] == date.today().isoformat()
    reopened = move("重新打开", reopen_reason="线上复现需再测")
    assert reopened["resolved_at"] is None and reopened["test_passed"] is None
    assert move("研发中")["test_passed"] is None
    move("开发完成")
    activity = admin.get(f"/api/issues/{key}/activity").json()["activity"]
    assert any(a["field"] == "test_passed" and a["meta"].get("reason") == "status_change"
               for a in activity)


def test_each_type_has_its_own_flow(admin):
    project = _project(admin)
    assert project["customized_workflows"] == []
    flows = project["workflows"]
    assert "方案讨论" not in {*flows["bug"], *sum(flows["bug"].values(), [])}
    assert "测试中" not in {*flows["task"], *sum(flows["task"].values(), [])}

    task = admin.post("/api/issues",
                      json={**ELEC_ISSUE, "title": "任务", "type": "task"}).json()
    key = task["key"]
    assert task["status"]["name"] == "待评估"
    for name in ("研发中", "已完成"):
        r = admin.patch(f"/api/issues/{key}", json={"status_id": _status(project, name)})
        assert r.status_code == 200, r.text
    assert admin.patch(f"/api/issues/{key}",
                       json={"status_id": _status(project, "测试中")}).status_code == 409

    me = admin.get("/api/auth/me").json()["user"]["id"]
    body = {**ELEC_ISSUE, "title": "故事", "type": "story", "tester_id": me}
    story = admin.post("/api/issues", json=body).json()
    for name in ("研发中", "开发完成", "测试中"):
        admin.patch(f"/api/issues/{story['key']}", json={"status_id": _status(project, name)})
    assert admin.patch(f"/api/issues/{story['key']}", json={"type": "task"}).status_code == 200
    detail = admin.get(f"/api/issues/{story['key']}").json()
    assert {s["name"] for s in detail["transitions"]} == {"研发中", "已完成"}, \
        "a status the type does not use steps to the nearest statuses of its flow"

    custom = {"待评估": ["已完成"], "已完成": ["待评估"]}
    admin.put("/api/projects/ELEC", json={"settings": {"transitions": {"task": custom}}})
    project = _project(admin)
    assert project["customized_workflows"] == ["task"]
    assert project["workflows"]["task"] == custom
    assert project["workflows"]["bug"] == flows["bug"]

    custom_task = admin.post(
        "/api/issues",
        json={**ELEC_ISSUE, "title": "自定义任务", "type": "task"},
    ).json()
    assert custom_task["status"]["name"] == "待评估"
    moved = admin.patch(
        f"/api/issues/{custom_task['key']}",
        json={"status_id": _status(project, "已完成")},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"]["name"] == "已完成"

    invalid = {"待评估": ["不存在"], "不存在": ["已完成"]}
    rejected = admin.put(
        "/api/projects/ELEC",
        json={"settings": {"transitions": {"task": invalid}}},
    )
    assert rejected.status_code == 400
    assert "工作流无效" in rejected.json()["detail"]
    assert _project(admin)["workflows"]["task"] == custom

    admin.put("/api/projects/ELEC", json={"settings": {"transitions": {}}})
    assert _project(admin)["workflows"]["task"] == flows["task"]


def test_create_requires_due_date(admin):
    missing = admin.post("/api/issues", json={"project": "ELEC", "title": "缺少截止日期"})
    assert missing.status_code == 422
    assert any(e["loc"][-1] == "due_date" for e in missing.json()["detail"])
    ok = admin.post("/api/issues",
                    json={"project": "ELEC", "title": "有截止日期", "due_date": "2026-12-31"})
    assert ok.status_code == 200 and ok.json()["due_date"] == "2026-12-31"


def test_non_requirement_types_are_editable_by_involved_users(admin, make_user):
    reporter, reporter_id = make_user("zhang", "张三")
    stranger, _ = make_user("wang", "王五")
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": reporter_id, "role": "member"}).status_code == 200
    bug = reporter.post("/api/issues", json={**ELEC_ISSUE, "title": "缺陷", "type": "bug"}).json()
    assert {"general", "requester", "electrical", "tester"} <= set(bug["editable_sections"])
    edited = reporter.patch(f"/api/issues/{bug['key']}",
                            json={"title": "缺陷（已补充）", "description": "现象"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "缺陷（已补充）"
    denied = stranger.patch(f"/api/issues/{bug['key']}", json={"title": "x"})
    assert denied.status_code == 403


def test_watched_activity_is_paged(admin, make_user):
    other, other_id = make_user("li", "李四")
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": other_id, "role": "member"}).status_code == 200
    key = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "关注的动态"}).json()["key"]
    assert admin.post(f"/api/issues/{key}/watch").status_code == 200
    for i in range(20):
        comment = other.post(f"/api/issues/{key}/comments", json={"body": f"第 {i} 条"})
        assert comment.status_code == 200
    first = admin.get("/api/dashboard/activity").json()
    second = admin.get("/api/dashboard/activity?page=2").json()
    assert first["size"] == 15 and len(first["items"]) == 15
    assert first["total"] == len(first["items"]) + len(second["items"]) >= 20
    ids = [a["id"] for a in first["items"] + second["items"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == len(ids)
    assert all(a["actor"]["username"] == "li" and a["issue"]["key"] == key for a in first["items"])
    assert "activity" not in admin.get("/api/dashboard").json()


def test_create_with_staged_screenshots_links_attachments(admin, make_user):
    import json

    requester, requester_id = make_user("paste-req", "贴图提出人")
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": requester_id, "role": "requester"}).status_code == 200
    payload = {
        **ELEC_ISSUE, "type": "requirement", "title": "带截图的需求",
        "description": "现象如下：\n![image.png](staged:aaaaaa11)\n",
        "verify_method": "对照截图 ![x](staged:bbbbbb22)",
        "verify_criteria": "残留占位 ![gone](staged:cccccc33) 被移除",
    }
    response = requester.post("/api/issues/with-attachments", data={
        "payload": json.dumps(payload, ensure_ascii=False),
        "tokens": json.dumps(["aaaaaa11", "bbbbbb22"]),
    }, files=[
        ("files", ("paste-1.png", b"\x89PNG\r\n\x1a\nfake", "image/png")),
        ("files", ("paste-2.png", b"\x89PNG\r\n\x1a\nfake2", "image/png")),
    ])
    assert response.status_code == 200, response.text
    issue = response.json()
    first, second = issue["attachments"]
    assert f"![paste-1.png]({first['url']}?inline=true)" in issue["description"]
    assert f"![paste-2.png]({second['url']}?inline=true)" in issue["verify_method"]
    assert "staged:" not in issue["verify_criteria"]
    assert requester.get(f"{first['url']}?inline=true").status_code == 200

    bad = requester.post("/api/issues/with-attachments", data={
        "payload": json.dumps(payload, ensure_ascii=False), "tokens": json.dumps(["../x"]),
    }, files=[("files", ("a.png", b"x", "image/png"))])
    assert bad.status_code == 400


def test_create_with_standalone_office_attachment(admin, make_user):
    import json

    requester, requester_id = make_user("attach-req", "附件提出人")
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": requester_id, "role": "requester"}).status_code == 200
    payload = {
        **ELEC_ISSUE, "type": "requirement", "title": "带办公附件的需求",
        "description": "见附件规格书",
    }
    response = requester.post("/api/issues/with-attachments", data={
        "payload": json.dumps(payload, ensure_ascii=False),
        "tokens": json.dumps(["office01"]),
    }, files=[
        ("files", (
            "spec.docx",
            b"PK\x03\x04fake-docx",
            "application/octet-stream",
        )),
    ])
    assert response.status_code == 200, response.text
    issue = response.json()
    assert len(issue["attachments"]) == 1
    att = issue["attachments"][0]
    assert att["filename"] == "spec.docx"
    assert att["content_type"].endswith("wordprocessingml.document")
    assert "staged:" not in issue["description"]
    downloaded = requester.get(att["url"])
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"PK\x03\x04")


def test_clone_skips_internal_extra_markers(admin):
    """Demo/seed markers in ``extra`` must not break clone template validation."""
    from backend.db import session_factory
    from backend.models import Issue

    created = admin.post("/api/projects", json={
        "key": "CLN", "name": "克隆试点", "profile": "mechanical",
    })
    assert created.status_code == 200, created.text
    material = admin.post("/api/assets", json={
        "kind": "material", "code": "AL6061", "name": "铝合金 6061",
    })
    assert material.status_code == 200, material.text
    issue = admin.post("/api/issues", json={
        "project": "CLN", "title": "夹具改型", "due_date": "2099-06-01",
        "custom_fields": {"drawing_no": "DWG-CLN", "material": material.json()["id"]},
    }).json()

    with session_factory()() as db:
        row = db.get(Issue, issue["id"])
        assert row is not None
        row.extra = {**(row.extra or {}), "_demo": "my_work", "_template_orphans": {"x": 1}}
        db.commit()

    cloned = admin.post(f"/api/issues/{issue['key']}/clone", json={})
    assert cloned.status_code == 200, cloned.text
    copy = cloned.json()
    assert copy["key"] != issue["key"]
    assert copy["title"].endswith("（副本）")
    assert copy["custom_fields"].get("drawing_no") == "DWG-CLN"
    assert "_demo" not in copy["custom_fields"]
    assert "_template_orphans" not in copy["custom_fields"]


def test_create_ignores_non_initial_status_id(admin):
    """Board-column create may send a column status; new issues still start at initial."""
    project = _project(admin)
    doing = _status(project, "研发中")
    created = admin.post("/api/issues", json={
        **ELEC_ISSUE, "title": "从看板列创建", "status_id": doing,
        "machine_ids": [project["machine_groups"][0]["machines"][0]["id"]],
    })
    assert created.status_code == 200, created.text
    assert created.json()["status"]["name"] == "待评估"
