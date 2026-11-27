"""Walk every transition of every built-in template and issue type through the API."""

from __future__ import annotations

from collections import deque

import pytest

from backend.models.issue import ISSUE_TYPES
from backend.services import profiles
from backend.services.workflow_audit import lint_definition


def _paths(rules: dict[str, list[str]], start: str) -> dict[str, list[str]]:
    """Shortest status path from ``start`` to every reachable status."""
    paths, queue = {start: []}, deque([start])
    while queue:
        source = queue.popleft()
        for target in rules.get(source, []):
            if target not in paths:
                paths[target] = [*paths[source], target]
                queue.append(target)
    return paths


@pytest.mark.parametrize("profile", sorted(profiles.PROFILES))
def test_builtin_template_has_no_workflow_errors(profile):
    definition = profiles.builtin_definition(profiles.PROFILES[profile])
    errors = [f for f in lint_definition(definition) if f.level == "error"]
    assert errors == []


@pytest.mark.parametrize("profile", sorted(profiles.PROFILES))
def test_every_transition_of_every_type(admin, profile):
    key = f"W{profile[:3].upper()}"
    created = admin.post("/api/projects", json={"key": key, "name": f"流程 {profile}",
                                                "profile": profile})
    assert created.status_code == 200, created.text
    project = admin.get(f"/api/projects/{key}").json()
    status_id = {s["name"]: s["id"] for s in project["statuses"]}
    category = {s["name"]: s["category"] for s in project["statuses"]}
    me = admin.get("/api/auth/me").json()["user"]["id"]
    definition = profiles.builtin_definition(profiles.PROFILES[profile])
    required = {f["key"]: f"{f['key']}-1" for f in definition["fields"]
                if f["required"] and f["kind"] == "text" and f["storage"] == "extra"}
    parent = admin.post("/api/issues", json={
        "project": key, "type": "task", "title": "父任务", "due_date": "2099-12-31",
    }).json()

    def new_issue(issue_type: str) -> str:
        body = {"project": key, "type": issue_type, "title": f"{issue_type} 流程",
                "due_date": "2099-12-31", "assignee_id": me, "tester_id": me,
                "custom_fields": required}
        if issue_type == "subtask":
            body["parent_id"] = parent["id"]
        response = admin.post("/api/issues", json=body)
        assert response.status_code == 200, response.text
        return response.json()["key"]

    def move(issue_key: str, name: str):
        if name == "测试中":
            issue = admin.get(f"/api/issues/{issue_key}").json()
            if issue["type"] == "requirement" and not (issue.get("verify_method") or "").strip():
                assert admin.patch(f"/api/issues/{issue_key}", json={
                    "verify_method": "上机", "verify_criteria": "稳定",
                }).status_code == 200
        if name == "研发中":
            issue = admin.get(f"/api/issues/{issue_key}").json()
            if issue["type"] == "requirement" and issue["status"]["name"] == "方案讨论":
                if not (issue.get("scheme_note") or "").strip():
                    assert admin.patch(
                        f"/api/issues/{issue_key}", json={"scheme_note": "流程方案"},
                    ).status_code == 200
                if not issue.get("scheme_locked"):
                    assert admin.patch(
                        f"/api/issues/{issue_key}", json={"scheme_action": "submit"},
                    ).status_code == 200
                if issue.get("electrical_scheme_ok") is not True:
                    assert admin.patch(f"/api/issues/{issue_key}", json={
                        "electrical_scheme_ok": True, "process_scheme_ok": True,
                    }).status_code == 200
        payload = {"status_id": status_id[name]}
        if name == "重新打开":
            payload["reopen_reason"] = "流程验证"
        return admin.patch(f"/api/issues/{issue_key}", json=payload)

    for issue_type in ISSUE_TYPES:
        rules = project["workflows"][issue_type]
        used = set(rules) | {t for targets in rules.values() for t in targets}
        flow = [s["name"] for s in project["statuses"] if s["name"] in used]
        start = flow[0]
        paths = _paths(rules, start)
        assert set(paths) == set(flow), f"{issue_type}: unreachable {set(flow) - set(paths)}"

        first = admin.get(f"/api/issues/{new_issue(issue_type)}").json()
        assert first["status"]["name"] == start, f"{issue_type} starts in {start}"

        for source, path in paths.items():
            allowed = rules.get(source, [])
            issue_key = new_issue(issue_type)
            for step in path:
                response = move(issue_key, step)
                assert response.status_code == 200, f"{issue_type} → {step}: {response.text}"
            detail = admin.get(f"/api/issues/{issue_key}").json()
            assert detail["status"]["name"] == source
            assert (detail["resolved_at"] is not None) == (category[source] == "done"), \
                f"{issue_type} {source}: resolved_at must follow the done category"
            assert {s["name"] for s in detail["transitions"]} == set(allowed), \
                f"{issue_type} {source}: offered transitions differ from the rules"
            for name in status_id:
                if name != source and name not in allowed:
                    blocked = move(issue_key, name)
                    assert blocked.status_code == 409, \
                        f"{issue_type} {source} → {name} must be refused"
            for target in allowed:
                again = new_issue(issue_type)
                for step in path:
                    assert move(again, step).status_code == 200
                response = move(again, target)
                assert response.status_code == 200, \
                    f"{issue_type} {source} → {target}: {response.text}"
                assert response.json()["status"]["name"] == target


@pytest.mark.parametrize("profile", sorted(profiles.PROFILES))
def test_gate_fields_and_approval_on_the_main_line(admin, profile):
    key = f"G{profile[:3].upper()}"
    assert admin.post("/api/projects", json={"key": key, "name": f"门禁 {profile}",
                                             "profile": profile}).status_code == 200
    project = admin.get(f"/api/projects/{key}").json()
    status_id = {s["name"]: s["id"] for s in project["statuses"]}
    rules = project["workflows"]["requirement"]
    first = next(s["name"] for s in project["statuses"] if s["name"] in rules)
    second = rules[first][0]
    third = rules[second][0]
    gates = {
        second: {"required_fields": [], "approvals": [{"role": "admin", "count": 1}]},
        third: {"required_fields": ["story_points"], "approvals": []},
    }
    saved = admin.put(f"/api/projects/{key}", json={"settings": {"transition_gates": gates}})
    assert saved.status_code == 200, saved.text
    me = admin.get("/api/auth/me").json()["user"]["id"]
    definition = profiles.builtin_definition(profiles.PROFILES[profile])
    required = {f["key"]: f"{f['key']}-1" for f in definition["fields"]
                if f["required"] and f["kind"] == "text" and f["storage"] == "extra"}
    issue = admin.post("/api/issues", json={
        "project": key, "title": "门禁", "due_date": "2099-12-31", "tester_id": me,
        "custom_fields": required,
    }).json()
    url = f"/api/issues/{issue['key']}"

    blocked = admin.patch(url, json={"status_id": status_id[second]})
    assert blocked.status_code == 409 and blocked.json()["code"] == "approval_required"
    request = admin.post(f"{url}/transition-requests",
                         json={"status_id": status_id[second], "comment": "请审批"})
    assert request.status_code == 200, request.text
    decided = admin.post(f"/api/approval-requests/{request.json()['id']}/decision",
                         json={"approve": True, "comment": "同意"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["issue"]["status"]["name"] == second

    if third == "研发中":
        assert admin.patch(url, json={"scheme_note": "流程方案"}).status_code == 200
        assert admin.patch(url, json={"scheme_action": "submit"}).status_code == 200
        assert admin.patch(url, json={
            "electrical_scheme_ok": True, "process_scheme_ok": True,
        }).status_code == 200
    gated = admin.patch(url, json={"status_id": status_id[third]})
    assert gated.status_code == 409 and gated.json()["code"] == "transition_gate"
    passed = admin.patch(url, json={"story_points": 3, "status_id": status_id[third]})
    assert passed.status_code == 200, passed.text
    assert passed.json()["status"]["name"] == third
