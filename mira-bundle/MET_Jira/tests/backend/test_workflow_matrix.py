"""Regression matrix: gates × types, hot-reload, Pass/Fail remap, rename, approvals."""

from __future__ import annotations

from backend.db import session_factory
from backend.models import Project
from backend.services import profiles, workflow_audit
from backend.services.profiles import Profile, Section


def _project(admin, key: str, profile: str = "electrical") -> dict:
    assert admin.post("/api/projects", json={
        "key": key, "name": f"矩阵 {key}", "profile": profile,
    }).status_code == 200
    return admin.get(f"/api/projects/{key}").json()


def _ids(project: dict) -> dict[str, int]:
    return {s["name"]: s["id"] for s in project["statuses"]}


def test_field_applies_to_type_matrix():
    electrical = profiles.ELECTRICAL
    assert electrical.field_applies_to_type("update_scope", "requirement")
    assert not electrical.field_applies_to_type("update_scope", "task")
    assert not electrical.field_applies_to_type("verify_method", "bug")
    assert electrical.field_applies_to_type("story_points", "task"), "unsectioned always applies"
    # empty issue_types → all types
    open_profile = Profile(
        key="open", label="open", roles=("admin",),
        sections=(Section(key="s", label="s", fields=("update_scope",), issue_types=()),),
    )
    assert open_profile.field_applies_to_type("update_scope", "task")
    assert open_profile.field_applies_to_type("update_scope", "epic")


def test_requirement_gate_blocks_task_skips(admin):
    key = "MXGATE"
    project = _project(admin, key)
    by = _ids(project)
    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "研发中": {
                    "required_fields": ["update_scope", "verify_method"],
                    "approvals": [],
                },
            },
        },
    }).status_code == 200

    task = admin.post("/api/issues", json={
        "project": key, "type": "task", "title": "任务跳过电气门禁",
        "due_date": "2099-12-31",
    }).json()
    ok = admin.patch(f"/api/issues/{task['key']}", json={"status_id": by["研发中"]})
    assert ok.status_code == 200, ok.text

    req = admin.post("/api/issues", json={
        "project": key, "type": "requirement", "title": "需求要填电气字段",
        "due_date": "2099-12-31",
    }).json()
    assert admin.patch(f"/api/issues/{req['key']}",
                       json={"status_id": by["方案讨论"]}).status_code == 200
    blocked = admin.patch(f"/api/issues/{req['key']}", json={"status_id": by["研发中"]})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "transition_gate"
    assert set(blocked.json()["fields"]) >= {"update_scope", "verify_method"}


def test_settings_hot_reload_gates_and_transitions(admin):
    key = "MXHOT"
    project = _project(admin, key)
    by = _ids(project)
    task = admin.post("/api/issues", json={
        "project": key, "type": "task", "title": "热更新",
        "due_date": "2099-12-31",
    }).json()
    assert admin.patch(f"/api/issues/{task['key']}",
                       json={"status_id": by["研发中"]}).status_code == 200

    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "已完成": {"required_fields": ["story_points"], "approvals": []},
            },
        },
    }).status_code == 200
    gated = admin.patch(f"/api/issues/{task['key']}", json={"status_id": by["已完成"]})
    assert gated.status_code == 409 and gated.json()["code"] == "transition_gate"
    assert "story_points" in gated.json()["fields"]

    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transitions": {
                "task": {
                    "待评估": ["已完成", "研发中"],
                    "已完成": ["待评估"],
                    "研发中": ["已完成", "待评估"],
                },
            },
        },
    }).status_code == 200
    detail = admin.get(f"/api/issues/{task['key']}").json()
    names = {s["name"] for s in detail["transitions"]}
    assert "已完成" in names and "待评估" in names


def test_custom_pass_fail_status_remap(admin):
    key = "MXPF"
    project = _project(admin, key)
    by = _ids(project)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    # Remap pass → 方案讨论 is wrong category; use 已完成 / 测试未通过 renamed conceptually
    # by pointing pass_status at 已完成 and fail at 测试未通过 (defaults) then change labels
    # via settings only: use 已驳回 as fail and keep 已完成 as pass for a short bug flow.
    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "test_status": "测试中",
            "pass_status": "已完成",
            "fail_return_status": "已驳回",
            "transitions": {
                "bug": {
                    "待评估": ["研发中"],
                    "研发中": ["测试中"],
                    "测试中": ["已完成", "已驳回"],
                    "已完成": ["重新打开"],
                    "已驳回": ["待评估"],
                    "重新打开": ["研发中"],
                },
            },
        },
    }).status_code == 200
    bug = admin.post("/api/issues", json={
        "project": key, "type": "bug", "title": "自定义通过失败",
        "due_date": "2099-12-31", "tester_id": me,
    }).json()
    url = f"/api/issues/{bug['key']}"
    assert admin.patch(url, json={"status_id": by["研发中"]}).status_code == 200
    assert admin.patch(url, json={"status_id": by["测试中"]}).status_code == 200
    detail = admin.get(url).json()
    assert detail["test_result_editable"] is True
    assert {s["name"] for s in detail["transitions"]} >= {"已完成", "已驳回"}

    passed = admin.patch(url, json={"test_passed": True}).json()
    assert passed["status"]["name"] == "已完成" and passed["test_passed"] is True
    assert admin.patch(url, json={
        "status_id": by["重新打开"], "reopen_reason": "再测",
    }).status_code == 200
    assert admin.patch(url, json={"status_id": by["研发中"]}).status_code == 200
    assert admin.patch(url, json={"status_id": by["测试中"]}).status_code == 200
    failed = admin.patch(url, json={"test_passed": False}).json()
    assert failed["status"]["name"] == "已驳回" and failed["test_passed"] is False


def test_status_rename_cascades_gates_and_test_settings(admin):
    key = "MXREN"
    project = _project(admin, key)
    developing = next(s for s in project["statuses"] if s["name"] == "研发中")
    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "test_status": "测试中",
            "pass_status": "已完成",
            "fail_return_status": "测试未通过",
            "transition_gates": {
                "研发中": {"required_fields": ["story_points"], "approvals": []},
            },
        },
    }).status_code == 200
    renamed = admin.put(
        f"/api/projects/{key}/statuses/{developing['id']}",
        json={"name": "开发中", "category": "doing", "color": developing["color"]},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "开发中"
    settings = admin.get(f"/api/projects/{key}").json()["project"]["settings"]
    assert "研发中" not in (settings.get("transition_gates") or {})
    assert "开发中" in settings["transition_gates"]
    assert settings["transition_gates"]["开发中"]["required_fields"] == ["story_points"]
    # transitions for task should mention 开发中
    task_flow = settings.get("transitions", {}).get("task") or {}
    flat = set(task_flow) | {t for ts in task_flow.values() for t in ts}
    assert "开发中" in flat and "研发中" not in flat


def test_approval_still_enforces_field_gates_after_countersign(admin, make_user):
    key = "MXAPG"
    project = _project(admin, key)
    by = _ids(project)
    target = by["方案讨论"]
    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "方案讨论": {
                    "required_fields": ["story_points"],
                    "approvals": [{"role": "electrical", "count": 1}],
                },
            },
        },
    }).status_code == 200
    requester, requester_id = make_user("mx-req", "矩阵提出人")
    approver, approver_id = make_user("mx-elec", "矩阵电气")
    assert admin.post(f"/api/projects/{key}/members", json={
        "user_id": requester_id, "role": "requester",
    }).status_code == 200
    assert admin.post(f"/api/projects/{key}/members", json={
        "user_id": approver_id, "role": "electrical",
    }).status_code == 200

    issue = requester.post("/api/issues", json={
        "project": key, "title": "审批加字段门禁", "due_date": "2099-12-31",
    }).json()
    url = f"/api/issues/{issue['key']}"
    bare = requester.post(f"{url}/transition-requests", json={"status_id": target})
    assert bare.status_code == 409 and bare.json().get("code") == "transition_gate"

    assert requester.patch(url, json={"story_points": 2}).status_code == 200
    request = requester.post(f"{url}/transition-requests",
                             json={"status_id": target, "comment": "请批"})
    assert request.status_code == 200, request.text
    # Clear the field while pending — approval apply must still check gates.
    assert requester.patch(url, json={"story_points": None}).status_code == 200
    failed = approver.post(f"/api/approval-requests/{request.json()['id']}/decision",
                           json={"approve": True, "comment": "同意"})
    assert failed.status_code == 409
    detail = admin.get(url).json()
    assert detail["status"]["name"] == "待评估"


def test_reopen_requires_reason_and_clears_test_passed(admin):
    key = "MXREO"
    project = _project(admin, key)
    by = _ids(project)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    bug = admin.post("/api/issues", json={
        "project": key, "type": "bug", "title": "重新打开",
        "due_date": "2099-12-31", "tester_id": me,
    }).json()
    url = f"/api/issues/{bug['key']}"
    for name in ("研发中", "开发完成", "测试中", "测试通过", "已完成"):
        assert admin.patch(url, json={"status_id": by[name]}).status_code == 200
    detail = admin.get(url).json()
    assert detail["test_passed"] is True
    blocked = admin.patch(url, json={"status_id": by["重新打开"]})
    assert blocked.status_code == 409 and blocked.json()["code"] == "reopen_reason"
    reopened = admin.patch(url, json={
        "status_id": by["重新打开"], "reopen_reason": "线上复现",
    }).json()
    assert reopened["test_passed"] is None
    activity = admin.get(f"{url}/activity").json()["activity"]
    reopen_logs = [
        a for a in activity
        if a["action"] == "transition" and a["new_value"] == "重新打开"
    ]
    assert reopen_logs and reopen_logs[0]["meta"].get("reopen_reason") == "线上复现"


def test_lint_warns_when_gate_field_blind_to_type(admin):
    key = "MXLINT"
    _project(admin, key)
    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "研发中": {
                    "required_fields": ["update_scope"],
                    "approvals": [],
                },
            },
        },
    }).status_code == 200
    with session_factory()() as db:
        from sqlalchemy import select
        project = db.scalars(select(Project).where(Project.key == key)).one()
        findings = workflow_audit.lint_project(db, project)
    warnings = [f for f in findings if f.level == "warning" and f.scope == "gates"]
    assert any("update_scope" in f.message and "task" in f.message for f in warnings)


def test_validate_gates_rejects_unknown_field_and_role(admin):
    key = "MXVAL"
    _project(admin, key)
    bad_field = admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "研发中": {"required_fields": ["not_a_real_field"], "approvals": []},
            },
        },
    })
    assert bad_field.status_code == 400
    assert "模板不支持字段" in bad_field.json()["detail"]

    bad_role = admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "方案讨论": {
                    "required_fields": [],
                    "approvals": [{"role": "no_such_role", "count": 1}],
                },
            },
        },
    })
    assert bad_role.status_code == 400
    assert "角色" in bad_role.json()["detail"]


def test_multi_approver_count_two_and_reject(admin, make_user):
    key = "MXMUL"
    project = _project(admin, key)
    target = _ids(project)["方案讨论"]
    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "方案讨论": {
                    "required_fields": [],
                    "approvals": [{"role": "electrical", "count": 2}],
                },
            },
        },
    }).status_code == 200
    requester, requester_id = make_user("mx-mreq", "多人提出")
    a1, a1_id = make_user("mx-a1", "电气甲")
    a2, a2_id = make_user("mx-a2", "电气乙")
    for uid in (requester_id, a1_id, a2_id):
        role = "requester" if uid == requester_id else "electrical"
        assert admin.post(f"/api/projects/{key}/members",
                          json={"user_id": uid, "role": role}).status_code == 200

    issue = requester.post("/api/issues", json={
        "project": key, "title": "双人会签", "due_date": "2099-12-31",
    }).json()
    url = f"/api/issues/{issue['key']}"
    request = requester.post(f"{url}/transition-requests",
                             json={"status_id": target, "comment": "请双签"})
    assert request.status_code == 200, request.text
    rid = request.json()["id"]

    first = a1.post(f"/api/approval-requests/{rid}/decision",
                    json={"approve": True, "comment": "甲同意"})
    assert first.status_code == 200, first.text
    assert first.json()["request"]["status"] == "pending"
    assert first.json()["issue"]["status"]["name"] == "待评估"

    second = a2.post(f"/api/approval-requests/{rid}/decision",
                     json={"approve": True, "comment": "乙同意"})
    assert second.status_code == 200, second.text
    assert second.json()["request"]["status"] == "approved"
    assert second.json()["issue"]["status"]["name"] == "方案讨论"

    # Reject path on a fresh issue
    other = requester.post("/api/issues", json={
        "project": key, "title": "会被驳回", "due_date": "2099-12-31",
    }).json()
    other_url = f"/api/issues/{other['key']}"
    req2 = requester.post(f"{other_url}/transition-requests",
                          json={"status_id": target}).json()
    rejected = a1.post(f"/api/approval-requests/{req2['id']}/decision",
                       json={"approve": False, "comment": "不行"})
    assert rejected.status_code == 200
    assert rejected.json()["request"]["status"] == "rejected"
    assert rejected.json()["issue"]["status"]["name"] == "待评估"
