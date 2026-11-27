"""Three-party scheme confirmation before an electrical requirement enters development."""

from __future__ import annotations

from backend.services.scheme import SchemeReview, _parse_review


def _ids(admin, key: str = "ELEC") -> dict[str, int]:
    project = admin.get(f"/api/projects/{key}").json()
    return {status["name"]: status["id"] for status in project["statuses"]}


def _requirement(admin, **fields) -> dict:
    body = {
        "project": "ELEC", "type": "requirement", "title": "方案确认",
        "due_date": "2099-12-31", **fields,
    }
    created = admin.post("/api/issues", json=body)
    assert created.status_code == 200, created.text
    return created.json()


def _move(admin, key: str, name: str, ids: dict[str, int]) -> dict:
    payload = {"status_id": ids[name]}
    if name == "重新打开":
        payload["reopen_reason"] = "返工"
    response = admin.patch(f"/api/issues/{key}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _ready(admin, key: str, *, software: bool = False) -> dict:
    note = admin.patch(f"/api/issues/{key}", json={"scheme_note": "采用原传感器，增加滤波"})
    assert note.status_code == 200, note.text
    submitted = admin.patch(f"/api/issues/{key}", json={"scheme_action": "submit"})
    assert submitted.status_code == 200, submitted.text
    votes = {
        "electrical_scheme_ok": True,
        "process_scheme_ok": True,
    }
    if software:
        votes["software_scheme_ok"] = True
    voted = admin.patch(f"/api/issues/{key}", json=votes)
    assert voted.status_code == 200, voted.text
    return voted.json()


def test_empty_note_cannot_be_submitted_or_confirmed(admin):
    ids = _ids(admin)
    issue = _requirement(admin)
    _move(admin, issue["key"], "方案讨论", ids)
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "submit"})
    assert blocked.status_code == 409
    assert blocked.json()["fields"] == ["scheme_note"]
    vote = admin.patch(f"/api/issues/{issue['key']}", json={"electrical_scheme_ok": True})
    assert vote.status_code == 409
    assert vote.json()["code"] == "scheme_submit"


def test_parties_confirm_then_gate_opens_without_auto_move(admin, make_user):
    ids = _ids(admin)
    process_client, process_id = make_user("proc", "工艺")
    elec_client, elec_id = make_user("elec", "电气")
    for user_id, role in ((process_id, "process"), (elec_id, "electrical")):
        added = admin.post(
            "/api/projects/ELEC/members", json={"user_id": user_id, "role": role},
        )
        assert added.status_code == 200
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(
        admin, assignee_id=elec_id, involves_software=True, software_owner_id=me,
        software_jira_key="CVD-1",
    )
    _move(admin, issue["key"], "方案讨论", ids)
    assert elec_client.patch(
        f"/api/issues/{issue['key']}", json={"scheme_note": "三相确认方案"},
    ).status_code == 200
    assert process_client.patch(
        f"/api/issues/{issue['key']}", json={"scheme_note": "工艺不能改说明"},
    ).status_code == 403
    submitted = elec_client.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "submit"})
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["scheme_locked"] is True
    assert body["status"]["name"] == "方案讨论"
    assert elec_client.patch(
        f"/api/issues/{issue['key']}", json={"scheme_note": "偷偷改"},
    ).status_code == 409
    assert process_client.patch(
        f"/api/issues/{issue['key']}", json={"electrical_scheme_ok": True},
    ).status_code == 403
    signed = elec_client.patch(
        f"/api/issues/{issue['key']}", json={"electrical_scheme_ok": True},
    )
    assert signed.status_code == 200
    assert signed.json()["electrical_scheme_by"]["id"] == elec_id
    assert signed.json()["electrical_scheme_by"]["username"] == "elec"
    early = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["研发中"]})
    assert early.status_code == 409
    assert "process_scheme_ok" in early.json()["fields"]
    assert process_client.patch(
        f"/api/issues/{issue['key']}", json={"process_scheme_ok": True},
    ).status_code == 200
    assert admin.patch(
        f"/api/issues/{issue['key']}", json={"software_scheme_ok": True},
    ).status_code == 200
    done = admin.get(f"/api/issues/{issue['key']}").json()
    assert done["scheme_locked"] is True
    assert done["status"]["name"] == "方案讨论"
    moved = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["研发中"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"]["name"] == "研发中"
    requested = [
        item["kind"] for item in process_client.get("/api/notifications").json()["notifications"]
    ]
    confirmed = [
        item["kind"] for item in elec_client.get("/api/notifications").json()["notifications"]
    ]
    assert "scheme_review_requested" in requested
    assert "scheme_confirmed" in confirmed


def test_unlock_keeps_votes_until_the_note_changes(admin, monkeypatch):
    ids = _ids(admin)
    issue = _requirement(admin)
    _move(admin, issue["key"], "方案讨论", ids)
    _ready(admin, issue["key"])
    locked = admin.patch(f"/api/issues/{issue['key']}", json={"electrical_scheme_ok": False})
    assert locked.status_code == 409
    unlocked = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "unlock"})
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["scheme_locked"] is False
    assert unlocked.json()["electrical_scheme_ok"] is True
    calls = []

    def fake_compare(old, new, title):
        calls.append((old, new, title))
        return SchemeReview(False, "wording", "只改了标点")

    monkeypatch.setattr("backend.services.scheme.compare_notes", fake_compare)
    edited = admin.patch(
        f"/api/issues/{issue['key']}", json={"scheme_note": "采用原传感器，增加滤波。"},
    )
    assert edited.status_code == 200, edited.text
    again = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "submit"})
    assert again.status_code == 200, again.text
    assert calls
    assert again.json()["electrical_scheme_ok"] is True
    assert again.json()["scheme_locked"] is True


def test_material_change_clears_votes_and_failure_does_too(admin, monkeypatch):
    ids = _ids(admin)
    issue = _requirement(admin)
    _move(admin, issue["key"], "方案讨论", ids)
    _ready(admin, issue["key"])
    unlocked = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "unlock"})
    assert unlocked.status_code == 200
    assert admin.patch(
        f"/api/issues/{issue['key']}", json={"scheme_note": "改为无线方案"},
    ).status_code == 200

    monkeypatch.setattr(
        "backend.services.scheme.compare_notes",
        lambda old, new, title: SchemeReview(True, "scheme", "通信方式变了"),
    )
    changed = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "submit"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["electrical_scheme_ok"] is None
    assert changed.json()["process_scheme_ok"] is None
    assert changed.json()["scheme_locked"] is True
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["研发中"]})
    assert blocked.status_code == 409

    unlocked = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "unlock"})
    assert unlocked.status_code == 200
    assert admin.patch(
        f"/api/issues/{issue['key']}", json={"scheme_note": "再改一版"},
    ).status_code == 200
    monkeypatch.setattr(
        "backend.services.scheme.compare_notes",
        lambda old, new, title: SchemeReview(True, "failed", "自动比对未完成，请重新确认"),
    )
    failed = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "submit"})
    assert failed.status_code == 200, failed.text
    assert failed.json()["scheme_locked"] is True
    assert failed.json()["electrical_scheme_ok"] is None


def test_withdraw_without_a_note_change_skips_the_model(admin, make_user, monkeypatch):
    ids = _ids(admin)
    _assignee, assignee_id = make_user("asg", "负责人")
    issue = _requirement(admin, assignee_id=assignee_id)
    _move(admin, issue["key"], "方案讨论", ids)
    _ready(admin, issue["key"])
    unlocked = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "unlock"})
    assert unlocked.status_code == 200

    def explode(old, new, title):
        raise AssertionError("model should not run")

    monkeypatch.setattr("backend.services.scheme.compare_notes", explode)
    withdrawn = admin.patch(f"/api/issues/{issue['key']}", json={"process_scheme_ok": False})
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["process_scheme_ok"] is False
    kinds = [item["kind"] for item in _assignee.get("/api/notifications").json()["notifications"]]
    assert "scheme_withdrawn" in kinds


def test_return_to_discussion_keeps_the_lock_and_reject_clears_it(admin):
    ids = _ids(admin)
    issue = _requirement(admin)
    _move(admin, issue["key"], "方案讨论", ids)
    _ready(admin, issue["key"])
    developing = _move(admin, issue["key"], "研发中", ids)
    assert developing["scheme_locked"] is True
    back = _move(admin, issue["key"], "方案讨论", ids)
    assert back["scheme_locked"] is True
    assert back["electrical_scheme_ok"] is True
    rejected = _move(admin, issue["key"], "已驳回", ids)
    assert rejected["scheme_locked"] is False
    assert rejected["electrical_scheme_ok"] is None
    assert rejected["scheme_confirmed_note"] == ""


def test_bugs_skip_the_scheme_gate(admin):
    ids = _ids(admin)
    created = admin.post("/api/issues", json={
        "project": "ELEC", "type": "bug", "title": "缺陷", "due_date": "2099-12-31",
    })
    assert created.status_code == 200, created.text
    moved = admin.patch(f"/api/issues/{created.json()['key']}", json={"status_id": ids["研发中"]})
    assert moved.status_code == 200, moved.text


def test_software_jira_key_blocks_the_next_status(admin):
    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin)
    _move(admin, issue["key"], "方案讨论", ids)
    flagged = admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": me,
    })
    assert flagged.status_code == 200, flagged.text
    _ready(admin, issue["key"], software=True)
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["研发中"]})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["fields"] == ["software_jira_key"]
    moved = admin.patch(f"/api/issues/{issue['key']}", json={
        "status_id": ids["研发中"], "software_jira_key": "CVD-9",
    })
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"]["name"] == "研发中"
    assert moved.json()["software_jira_key"] == "CVD-9"


def test_turning_software_on_asks_the_owner(admin):
    ids = _ids(admin)
    owner_id = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin)
    _move(admin, issue["key"], "方案讨论", ids)
    _ready(admin, issue["key"])
    flagged = admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": owner_id,
    })
    assert flagged.status_code == 200, flagged.text
    assert flagged.json()["software_scheme_ok"] is None
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["研发中"]})
    assert blocked.status_code == 409
    assert "software_scheme_ok" in blocked.json()["fields"]


def test_review_parser_fails_closed():
    assert _parse_review("not json").changed is True
    parsed = _parse_review('{"changed": true, "kind": "wording", "reason": "标点"}')
    assert parsed.changed is False
    assert parsed.kind == "wording"
