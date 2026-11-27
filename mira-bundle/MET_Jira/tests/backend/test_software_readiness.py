"""Software-readiness gate and the reserved software-Jira client."""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

VERIFY = {"verify_method": "上机", "verify_criteria": "稳定"}


def _ids(admin, key: str = "ELEC") -> dict[str, int]:
    project = admin.get(f"/api/projects/{key}").json()
    return {status["name"]: status["id"] for status in project["statuses"]}


def _issue(admin, **fields) -> dict:
    body = {
        "project": "ELEC", "type": "requirement", "title": "软件门禁",
        "due_date": "2099-12-31", **fields,
    }
    created = admin.post("/api/issues", json=body)
    assert created.status_code == 200, created.text
    return created.json()


def _confirm_scheme(admin, key: str) -> None:
    issue = admin.get(f"/api/issues/{key}").json()
    if issue["type"] != "requirement" or issue["status"]["name"] != "方案讨论":
        return
    software = issue["involves_software"] is True
    if (
        issue["scheme_locked"]
        and issue["electrical_scheme_ok"] is True
        and issue["process_scheme_ok"] is True
        and (not software or issue["software_scheme_ok"] is True)
    ):
        return
    if not (issue["scheme_note"] or "").strip():
        noted = admin.patch(f"/api/issues/{key}", json={"scheme_note": "三方方案"})
        assert noted.status_code == 200
    if not issue["scheme_locked"]:
        submitted = admin.patch(f"/api/issues/{key}", json={"scheme_action": "submit"})
        assert submitted.status_code == 200
    votes = {"electrical_scheme_ok": True, "process_scheme_ok": True}
    if software:
        votes["software_scheme_ok"] = True
    assert admin.patch(f"/api/issues/{key}", json=votes).status_code == 200


def _stamp_done(key: str, status: str = "待上机验证") -> None:
    from backend.db import session_factory
    from backend.models import Issue

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == key))
        snap = dict(row.software_jira_snapshot or {})
        snap.setdefault("assignee", None)
        snap.setdefault("tester", None)
        snap.setdefault("due_date", None)
        snap.setdefault("fetched_at", "2026-11-08T00:00:00+00:00")
        snap["error"] = None
        snap["status"] = status
        row.software_jira_snapshot = snap
        db.commit()


def _through_dev(admin, key: str, ids: dict[str, int]) -> dict:
    """Step 研发中 → 开发完成. A finished snapshot lets software work through."""
    issue = admin.get(f"/api/issues/{key}").json()
    if issue["status"]["name"] != "研发中":
        return issue
    if issue.get("involves_software") is True:
        current = ((issue.get("software_jira_snapshot") or {}).get("status") or "").casefold()
        done = {"待上机验证"}
        if current not in done:
            _stamp_done(key)
    response = admin.patch(f"/api/issues/{key}", json={"status_id": ids["开发完成"]})
    assert response.status_code == 200, response.text
    return response.json()


def _to(admin, key: str, *names: str, ids: dict[str, int]) -> dict:
    body: dict = {}
    for name in names:
        if name == "研发中":
            _confirm_scheme(admin, key)
        if name == "测试中":
            current = admin.get(f"/api/issues/{key}").json()["status"]["name"]
            if current == "研发中" and "开发完成" in ids:
                body = _through_dev(admin, key, ids)
        payload = {"status_id": ids[name]}
        if name == "重新打开":
            payload["reopen_reason"] = "返工"
        response = admin.patch(f"/api/issues/{key}", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
    return body


def _arm(admin, key: str, *, tester: int, owner: int, jira: str = "SW-1") -> dict:
    response = admin.patch(f"/api/issues/{key}", json={
        "tester_id": tester,
        "involves_software": True,
        "software_owner_id": owner,
        "software_ready": True,
        "software_note": "已烧录",
        "software_jira_key": jira,
        **VERIFY,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _developing(admin, **fields) -> tuple[dict, dict[str, int], int]:
    me = admin.get("/api/auth/me").json()["user"]["id"]
    ids = _ids(admin)
    issue = _issue(admin, **fields)
    _to(admin, issue["key"], "方案讨论", "研发中", ids=ids)
    return issue, ids, me


def test_each_missing_test_field_is_reported_alone(admin):
    issue, ids, me = _developing(admin)
    _arm(admin, issue["key"], tester=me, owner=me)
    testing = ids["测试中"]
    cases = {
        "tester_id": {"tester_id": None},
        "verify_method": {"verify_method": ""},
        "verify_criteria": {"verify_criteria": ""},
        "software_owner_id": {"software_owner_id": None},
        "software_ready": {"software_ready": None},
    }
    for field, clear in cases.items():
        fresh, fresh_ids, user = _developing(admin, title=field)
        _arm(admin, fresh["key"], tester=user, owner=user)
        assert admin.patch(f"/api/issues/{fresh['key']}", json=clear).status_code == 200
        _through_dev(admin, fresh["key"], fresh_ids)
        blocked = admin.patch(
            f"/api/issues/{fresh['key']}", json={"status_id": fresh_ids["测试中"]},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "transition_gate"
        assert blocked.json()["fields"] == [field]
    _through_dev(admin, issue["key"], ids)
    entered = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": testing})
    assert entered.status_code == 200, entered.text
    assert entered.json()["status"]["name"] == "测试中"


def test_one_request_can_fill_the_gate_and_enter_testing(admin):
    issue, ids, me = _developing(admin)
    _through_dev(admin, issue["key"], ids)
    moved = admin.patch(f"/api/issues/{issue['key']}", json={
        "status_id": ids["测试中"],
        "tester_id": me,
        "involves_software": True,
        "software_owner_id": me,
        "software_ready": True,
        "software_jira_key": "CVD-1",
        **VERIFY,
    })
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"]["name"] == "测试中"
    assert moved.json()["software_ready"] is True


def test_software_flag_off_skips_owner_and_ready(admin):
    issue, ids, me = _developing(admin)
    ready = admin.patch(f"/api/issues/{issue['key']}", json={
        "tester_id": me, "involves_software": False, **VERIFY,
    })
    assert ready.status_code == 200, ready.text
    _through_dev(admin, issue["key"], ids)
    moved = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["测试中"]})
    assert moved.status_code == 200, moved.text


def test_turning_software_off_clears_owner_and_ready_but_keeps_jira(admin):
    from backend.db import session_factory
    from backend.models import Issue

    issue, _ids, me = _developing(admin)
    _arm(admin, issue["key"], tester=me, owner=me, jira="SW-9")
    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_snapshot = {"assignee": "张三", "status": "开发中"}
        db.commit()
    cleared = admin.patch(f"/api/issues/{issue['key']}", json={"involves_software": False})
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["software_owner"] is None
    assert body["software_ready"] is None
    assert body["software_jira_key"] == "SW-9"
    assert body["software_note"] == "已烧录"
    assert body["software_jira_snapshot"]["assignee"] == "张三"


def test_ready_clears_off_the_test_path_and_stays_on_results(admin):
    issue, ids, me = _developing(admin)
    _arm(admin, issue["key"], tester=me, owner=me)
    testing = _to(admin, issue["key"], "测试中", ids=ids)
    assert testing["software_ready"] is True
    failed = admin.patch(f"/api/issues/{issue['key']}", json={"test_passed": False}).json()
    assert failed["status"]["name"] == "测试未通过"
    assert failed["software_ready"] is True
    reopened = _to(admin, issue["key"], "重新打开", ids=ids)
    assert reopened["software_ready"] is None

    again, ids, me = _developing(admin, title="保留")
    _arm(admin, again["key"], tester=me, owner=me)
    _to(admin, again["key"], "测试中", ids=ids)
    passed = admin.patch(f"/api/issues/{again['key']}", json={"test_passed": True}).json()
    assert passed["status"]["name"] == "测试通过" and passed["software_ready"] is True
    done = _to(admin, again["key"], "已完成", ids=ids)
    assert done["software_ready"] is True
    back = _to(admin, again["key"], "重新打开", "方案讨论", "研发中", ids=ids)
    assert back["software_ready"] is None


def test_electrical_member_cannot_set_ready_or_smuggle_it(admin, make_user):
    elec, elec_id = make_user("sw-elec", "电气成员")
    owner, owner_id = make_user("sw-owner", "软件负责人")
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": elec_id, "role": "electrical"}).status_code == 200
    issue, ids, me = _developing(admin)
    _arm(admin, issue["key"], tester=me, owner=owner_id)
    denied = elec.patch(f"/api/issues/{issue['key']}", json={"software_ready": False})
    assert denied.status_code == 403
    smuggled = elec.patch(f"/api/issues/{issue['key']}", json={
        "status_id": ids["方案讨论"], "software_ready": False,
    })
    assert smuggled.status_code == 403
    assert admin.get(f"/api/issues/{issue['key']}").json()["status"]["name"] == "研发中"
    assert admin.get(f"/api/issues/{issue['key']}").json()["software_ready"] is True


@pytest.fixture()
def mailbox(monkeypatch):
    from backend.config import get_settings
    from backend.services import alerts, reminders

    settings = replace(
        get_settings(), smtp_host="smtp.test", smtp_user="met.ai@test",
        smtp_password="pw", mail_from="met.ai@test", public_url="http://mira",
    )
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)
    monkeypatch.setattr(reminders, "get_settings", lambda: settings)
    return settings


def _email(user_id: int, email: str) -> None:
    from backend.db import session_factory
    from backend.models import User

    with session_factory()() as db:
        db.get(User, user_id).email = email
        db.commit()


def _mails() -> list:
    from backend.db import session_factory
    from backend.models import OutboxEvent
    from backend.services.alerts import Mail

    with session_factory()() as db:
        rows = db.query(OutboxEvent).filter_by(kind="mail", status="pending").all()
        return [Mail(**mail) for row in rows for mail in row.payload["mails"]]


def test_software_owner_confirmation_notifies_the_assignee(admin, make_user, mailbox):
    owner, owner_id = make_user("ready-owner", "软件Owner")
    lead, lead_id = make_user("ready-lead", "需求负责人")
    _email(lead_id, "lead@example.com")
    issue, _status_ids, _me = _developing(admin)
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "assignee_id": lead_id, "involves_software": True, "software_owner_id": owner_id,
    }).status_code == 200
    confirmed = owner.patch(f"/api/issues/{issue['key']}", json={"software_ready": True})
    assert confirmed.status_code == 200, confirmed.text
    notes = lead.get("/api/notifications").json()["notifications"]
    assert any(
        note["kind"] == "software_ready" and "可以安排进入测试" in note["message"]
        for note in notes
    )
    assert any(
        mail.to == ["lead@example.com"] and "可以安排进入测试" in mail.subject
        for mail in _mails()
    )


def _conflict_notes(client) -> list[dict]:
    notes = client.get("/api/notifications").json()["notifications"]
    return [note for note in notes if note["kind"] == "software_due_conflict"]


def test_late_software_due_emails_both_sides_once(admin, make_user, mailbox):
    from backend.db import session_factory
    from backend.models import Issue, User

    lead, lead_id = make_user("due-lead", "需求负责人")
    owner, owner_id = make_user("due-soft", "软件负责人")
    tao, tao_id = make_user("due-tao", "due-tao")
    _email(lead_id, "lead-due@example.com")
    _email(owner_id, "soft-due@example.com")
    _email(tao_id, "tao-due@example.com")
    with session_factory()() as db:
        db.get(User, tao_id).native_name = "张涛"
        db.commit()
    issue = _issue(
        admin, title="日期冲突", assignee_id=lead_id,
        involves_software=True, software_owner_id=owner_id,
    )
    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_key = "CVD-DATE"
        row.software_jira_snapshot = {
            "assignee": "张涛", "tester": None, "status": "开发中",
            "due_date": "2099-12-31", "fetched_at": "2026-11-08T00:00:00+00:00", "error": None,
        }
        db.commit()
    same = admin.get(f"/api/issues/{issue['key']}").json()
    assert same["due_date"] == "2099-12-31"
    assert _conflict_notes(lead) == []

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_snapshot = {
            **row.software_jira_snapshot,
            "due_date": "2099-12-30",
        }
        db.commit()
    early = admin.get(f"/api/issues/{issue['key']}").json()
    assert "_software_due_conflict" not in (early["extra"] or {})
    assert _conflict_notes(lead) == []

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_snapshot = {
            **row.software_jira_snapshot,
            "due_date": "2100-01-01",
        }
        db.commit()
    conflict = admin.get(f"/api/issues/{issue['key']}").json()
    assert "2100-01-01" in conflict["extra"]["_software_due_conflict"]
    assert len(_conflict_notes(lead)) == 1
    assert len(_conflict_notes(owner)) == 1
    assert len(_conflict_notes(tao)) == 1
    assert "软件交付晚于" in _conflict_notes(lead)[0]["message"]
    mailed = [mail.to[0] for mail in _mails() if "软件交付晚于" in mail.text]
    assert sorted(mailed) == [
        "lead-due@example.com", "soft-due@example.com", "tao-due@example.com",
    ]
    admin.get(f"/api/issues/{issue['key']}")
    assert len(_conflict_notes(lead)) == 1

    aligned = admin.patch(
        f"/api/issues/{issue['key']}", json={"due_date": "2100-01-01"},
    )
    assert aligned.status_code == 200, aligned.text
    assert "_software_due_conflict" not in (aligned.json()["extra"] or {})
    assert len(_conflict_notes(lead)) == 1
    moved = admin.patch(f"/api/issues/{issue['key']}", json={"due_date": "2099-01-02"})
    assert moved.status_code == 200, moved.text
    assert len(_conflict_notes(lead)) == 2
    assert len([mail for mail in _mails() if "软件交付晚于" in mail.text]) == 6


def test_assignee_confirming_ready_does_not_notify_themselves(admin, make_user, mailbox):
    person, person_id = make_user("self-ready", "自己确认")
    _email(person_id, "self@example.com")
    issue, _status_ids, _me = _developing(admin, title="自己确认")
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "assignee_id": person_id, "involves_software": True, "software_owner_id": person_id,
    }).status_code == 200
    confirmed = person.patch(f"/api/issues/{issue['key']}", json={"software_ready": True})
    assert confirmed.status_code == 200, confirmed.text
    notes = person.get("/api/notifications").json()["notifications"]
    assert not any(note["kind"] == "software_ready" for note in notes)
    assert not any("可以安排进入测试" in mail.subject for mail in _mails())


def test_client_cannot_write_the_snapshot(admin, make_user):
    stranger, stranger_id = make_user("snap-elec", "不能改快照")
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": stranger_id, "role": "electrical"}).status_code == 200
    issue = _issue(admin)
    rejected = stranger.patch(f"/api/issues/{issue['key']}", json={
        "software_jira_snapshot": {"status": "开发中"},
    })
    assert rejected.status_code == 400
    assert "快照" in rejected.json()["detail"]


def test_other_types_only_require_a_tester(admin):
    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    for issue_type in ("bug", "story"):
        issue = _issue(admin, type=issue_type, title=issue_type)
        _to(admin, issue["key"], "研发中", ids=ids)
        _through_dev(admin, issue["key"], ids)
        blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["测试中"]})
        assert blocked.status_code == 409
        assert blocked.json()["fields"] == ["tester_id"]
        assert admin.patch(f"/api/issues/{issue['key']}", json={"tester_id": me}).status_code == 200
        moved = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["测试中"]})
        assert moved.status_code == 200, moved.text
    task = _issue(admin, type="task", title="任务")
    _to(admin, task["key"], "研发中", ids=ids)
    blocked = admin.patch(f"/api/issues/{task['key']}", json={"status_id": ids["测试中"]})
    assert blocked.status_code == 409
    assert "工作流" in blocked.json()["detail"]


def test_mechanical_and_production_are_not_software_gated(admin):
    paths = {
        "mechanical": ("MECHSW", ("设计中", "图纸评审", "试装", "终检")),
        "production": ("PRODSW", ("已排产", "在制", "质检")),
    }
    for profile, (key, names) in paths.items():
        assert admin.post("/api/projects", json={
            "key": key, "name": key, "profile": profile,
        }).status_code == 200
        ids = _ids(admin, key)
        issue = admin.post("/api/issues", json={
            "project": key, "type": "story", "title": "无软件门禁", "due_date": "2099-12-31",
        }).json()
        for name in names:
            moved = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids[name]})
            assert moved.status_code == 200, moved.text
            assert "verify_method" not in (moved.json().get("fields") or [])


def test_gate_follows_renamed_test_status_and_stacks_extra_fields(admin):
    ids = _ids(admin)
    saved = admin.put("/api/projects/ELEC", json={"settings": {"test_status": "方案讨论"}})
    assert saved.status_code == 200
    issue = _issue(admin, title="改名")
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["方案讨论"]})
    assert blocked.status_code == 409
    assert set(blocked.json()["fields"]) == {"tester_id", "verify_method", "verify_criteria"}
    me = admin.get("/api/auth/me").json()["user"]["id"]
    filled = admin.patch(f"/api/issues/{issue['key']}", json={"tester_id": me, **VERIFY})
    assert filled.status_code == 200
    assert admin.patch(f"/api/issues/{issue['key']}",
                       json={"status_id": ids["方案讨论"]}).status_code == 200
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "verify_method": "", "verify_criteria": "",
    }).status_code == 200
    noted = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_note": "三方方案"})
    assert noted.status_code == 200
    submitted = admin.patch(f"/api/issues/{issue['key']}", json={"scheme_action": "submit"})
    assert submitted.status_code == 200
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "electrical_scheme_ok": True, "process_scheme_ok": True,
    }).status_code == 200
    assert admin.patch(f"/api/issues/{issue['key']}",
                       json={"status_id": ids["研发中"]}).status_code == 200
    _through_dev(admin, issue["key"], ids)
    later = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["测试中"]})
    assert later.status_code == 200, later.text

    assert admin.put("/api/projects/ELEC", json={"settings": {
        "test_status": "测试中",
        "transition_gates": {"测试中": {"required_fields": ["story_points"], "approvals": []}},
    }}).status_code == 200
    second, ids, me = _developing(admin, title="叠加")
    assert admin.patch(f"/api/issues/{second['key']}", json={
        "tester_id": me, **VERIFY,
    }).status_code == 200
    _through_dev(admin, second["key"], ids)
    stacked = admin.patch(f"/api/issues/{second['key']}", json={"status_id": ids["测试中"]})
    assert stacked.status_code == 409
    assert stacked.json()["fields"] == ["story_points"]


def test_rank_bulk_and_approval_use_the_same_gate(admin, make_user):
    ready, ids, me = _developing(admin, title="齐全")
    missing, _, _ = _developing(admin, title="缺验证")
    _arm(admin, ready["key"], tester=me, owner=me)
    assert admin.patch(f"/api/issues/{missing['key']}", json={"tester_id": me}).status_code == 200
    _through_dev(admin, ready["key"], ids)
    _through_dev(admin, missing["key"], ids)
    testing = ids["测试中"]
    ranked = admin.post(f"/api/issues/{missing['key']}/rank", json={"status_id": testing})
    assert ranked.status_code == 409 and ranked.json()["code"] == "transition_gate"
    assert "verify_method" in ranked.json()["fields"]
    bulk = admin.post("/api/issues/bulk", json={
        "keys": [ready["key"], missing["key"]],
        "changes": {"status_id": testing},
    }).json()
    assert [row["key"] for row in bulk["updated"]] == [ready["key"]]
    assert bulk["failed"][0]["key"] == missing["key"]

    approver, approver_id = make_user("gate-approver", "审批人")
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": approver_id, "role": "electrical"}).status_code == 200
    assert admin.put("/api/projects/ELEC", json={"settings": {
        "transition_gates": {
            "测试中": {"required_fields": [], "approvals": [{"role": "electrical", "count": 1}]},
        },
    }}).status_code == 200
    bare = admin.post(f"/api/issues/{missing['key']}/transition-requests",
                      json={"status_id": testing})
    assert bare.status_code == 409 and bare.json()["code"] == "transition_gate"
    assert admin.patch(f"/api/issues/{missing['key']}", json=VERIFY).status_code == 200
    requested = admin.post(f"/api/issues/{missing['key']}/transition-requests",
                           json={"status_id": testing, "comment": "请批"})
    assert requested.status_code == 200, requested.text
    cleared = admin.patch(f"/api/issues/{missing['key']}", json={"verify_method": ""})
    assert cleared.status_code == 200
    decision = approver.post(
        f"/api/approval-requests/{requested.json()['id']}/decision",
        json={"approve": True, "comment": "同意"},
    )
    assert decision.status_code == 409
    assert admin.get(f"/api/issues/{missing['key']}").json()["status"]["name"] == "开发完成"


def test_create_cannot_start_in_testing(admin):
    ids = _ids(admin)
    created = admin.post("/api/issues", json={
        "project": "ELEC", "title": "不能绕过", "due_date": "2099-12-31",
        "status_id": ids["测试中"],
    })
    assert created.status_code == 400
    assert admin.get("/api/search", params={"jql": "text ~ 不能绕过"}).json()["total"] == 0


def _xlsx(headers: list[str], rows: list[list]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _commit_sheet(admin, content: bytes) -> dict:
    files = {"file": (
        "software.xlsx", content,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )}
    preview = admin.post("/api/projects/ELEC/import/preview", files=files)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    commit = admin.post("/api/projects/ELEC/import/commit", json={
        "token": data["token"], "mapping": data["mapping"],
    })
    assert commit.status_code == 200, commit.text
    return commit.json()


def test_excel_trusts_history_and_ignores_snapshot_columns(admin):
    headers = [
        "标题", "当前状态", "是否涉及软件", "验证方法", "验证标准",
        "软件是否准备好", "软件交付说明", "软件Jira单号",
        "软件Jira经办人", "软件Jira测试人", "软件Jira状态", "软件Jira截止日期",
    ]
    content = _xlsx(headers, [
        ["历史已在测试", "测试中", "是", None, None, None, None, None,
         "张三", "李四", "开发中", "2026-11-20"],
        ["往返", "研发中", "是", "上机", "稳定", "是", "已烧录", "SW-7",
         "赵六", "钱七", "开发中", "2026-12-01"],
    ])
    created = {row["title"]: row for row in _commit_sheet(admin, content)["created"]}
    historical = admin.get(f"/api/issues/{created['历史已在测试']['key']}").json()
    assert historical["status"]["name"] == "测试中"
    assert historical["software_ready"] is None
    assert historical["software_jira_snapshot"] is None
    roundtrip = admin.get(f"/api/issues/{created['往返']['key']}").json()
    assert roundtrip["software_ready"] is True
    assert roundtrip["software_note"] == "已烧录"
    assert roundtrip["software_jira_key"] == "SW-7"
    assert roundtrip["status"]["name"] == "研发中"

    from backend.db import session_factory
    from backend.models import Issue

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == roundtrip["key"]))
        row.software_jira_snapshot = {
            "assignee": "张三", "tester": "李四", "status": "开发中", "due_date": "2026-11-20",
        }
        row.software_ready = True
        db.commit()
    exported = admin.get("/api/projects/ELEC/export.xlsx")
    book = load_workbook(io.BytesIO(exported.content))
    sheet = book.active
    header = [sheet.cell(2, col).value for col in range(1, sheet.max_column + 1)]
    note_col = header.index("软件交付说明") + 1
    person_col = header.index("软件Jira经办人") + 1
    target = next(
        row for row in range(3, sheet.max_row + 1)
        if sheet.cell(row, header.index("Key") + 1).value == roundtrip["key"]
    )
    sheet.cell(target, note_col).value = "改了说明"
    sheet.cell(target, person_col).value = "王五"
    buffer = io.BytesIO()
    book.save(buffer)
    _commit_sheet(admin, buffer.getvalue())
    updated = admin.get(f"/api/issues/{roundtrip['key']}").json()
    assert updated["software_note"] == "改了说明"
    assert updated["software_ready"] is True
    assert updated["status"]["name"] == "研发中"
    assert updated["software_jira_snapshot"]["assignee"] == "张三"


def test_clone_keeps_owner_when_asked_and_drops_readiness(admin):
    from backend.db import session_factory
    from backend.models import Issue

    issue, _ids, me = _developing(admin, title="克隆源")
    _arm(admin, issue["key"], tester=me, owner=me, jira="SW-3")
    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_snapshot = {"status": "开发中"}
        db.commit()
    cloned = admin.post(f"/api/issues/{issue['key']}/clone", json={"include_assignee": True})
    assert cloned.status_code == 200, cloned.text
    body = cloned.json()
    assert body["involves_software"] is True
    assert body["verify_method"] == "上机"
    assert body["verify_criteria"] == "稳定"
    assert body["software_owner"]["id"] == me
    assert body["software_ready"] is None
    assert body["software_jira_key"] == ""
    assert body["software_jira_snapshot"] is None
    assert body["status"]["name"] == "待评估"


def test_jql_filters_ready_and_key_but_not_the_snapshot(admin):
    first, _ids, me = _developing(admin, title="甲")
    second, _, _ = _developing(admin, title="乙")
    _arm(admin, first["key"], tester=me, owner=me, jira="SW-1")
    assert admin.patch(f"/api/issues/{second['key']}", json={
        "involves_software": True, "software_owner_id": me, "software_jira_key": "SW-1",
    }).status_code == 200
    ready = admin.get("/api/search", params={"jql": "software_ready = true"}).json()
    assert {row["key"] for row in ready["issues"]} == {first["key"]}
    shared = admin.get("/api/search", params={"jql": 'software_jira_key = "SW-1"'}).json()
    assert {row["key"] for row in shared["issues"]} == {first["key"], second["key"]}
    blocked = admin.get("/api/search", params={"jql": "software_jira_status = 开发中"})
    assert blocked.status_code == 400
    assert "快照" in blocked.json()["detail"]


def test_activity_uses_the_chinese_label(admin):
    issue, _ids, me = _developing(admin)
    _arm(admin, issue["key"], tester=me, owner=me)
    activity = admin.get(f"/api/issues/{issue['key']}/activity").json()["activity"]
    labels = {row["field"]: row["label"] for row in activity}
    assert labels["software_ready"] == "软件是否准备好"
    assert labels["software_note"] == "软件交付说明"
    assert labels["software_jira_key"] == "软件 Jira 单号"


def test_workflow_notes_do_not_add_a_status(admin):
    from backend.services.workflow_assist import OUTPUT

    detail = admin.get("/api/projects/ELEC").json()
    advice = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "requirement", "mode": "optimize",
        "transitions": detail["workflows"]["requirement"],
    })
    assert advice.status_code == 200, advice.text
    body = advice.json()
    assert any("软件负责人确认准备好" in note for note in body["notes"])
    for item in body["suggestions"]:
        for created in item.get("create") or []:
            assert "软件" not in created.get("name", "")
    task = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize",
        "transitions": detail["workflows"]["task"],
    })
    assert task.json()["notes"] == []
    assert "不要新增状态" in OUTPUT


def test_agent_checks_the_gate_before_a_pending_action(admin):
    from backend.db import session_factory
    from backend.models import AgentAction, Issue, Project, User
    from backend.services.agent import _normalise_draft
    from backend.services.agent_tools import (
        AgentContext,
        detail,
        fields_from_args,
        propose,
        run_tool,
    )
    from backend.services.issues import IssueError

    issue, _status_ids, me = _developing(admin, title="助手")
    _through_dev(admin, issue["key"], _status_ids)
    with session_factory()() as db:
        user = db.query(User).filter_by(username="admin").one()
        project = db.query(Project).filter_by(key="ELEC").one()
        dropped = fields_from_args(db, project, {
            "software_ready": True, "software_jira_snapshot": {"status": "开发中"},
            "verify_method": "上机",
        })
        assert "software_ready" not in dropped and "software_jira_snapshot" not in dropped
        assert dropped["verify_method"] == "上机"
        ctx = AgentContext(db=db, user=user)
        with pytest.raises(IssueError) as caught:
            propose(ctx, "transition_issue", {"key": issue["key"], "status": "测试中"})
        assert "verify_method" in caught.value.fields
        assert db.query(AgentAction).count() == 0
        draft = _normalise_draft(db, project, {
            "title": "起草", "software_ready": True, "involves_software": True,
            "verify_method": "上机", "verify_criteria": "稳定",
        })
        assert "software_ready" not in draft
        assert draft["involves_software"] is True
        assert draft["verify_method"] == "上机"

    _arm(admin, issue["key"], tester=me, owner=me)

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_snapshot = {
            "assignee": "张三", "tester": "李四", "status": "开发中", "due_date": "2026-11-20",
        }
        db.commit()
        user = db.query(User).filter_by(username="admin").one()
        loaded = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        payload = detail(db, loaded)
        assert payload["software_ready"] is True
        assert payload["software_jira"]["status"] == "开发中"
        assert payload["software_jira"]["assignee"] == "张三"
        meta = run_tool(AgentContext(db=db, user=user), "project_meta", {"project": "ELEC"})
        assert "软件已准备好" in meta["test_flow"]["software_gate"]
        propose(AgentContext(db=db, user=user), "transition_issue",
                {"key": issue["key"], "status": "测试中"})
        assert db.query(AgentAction).filter_by(status="pending").count() == 1


def test_parse_and_reserved_client_do_not_move_the_issue(admin):
    from backend.db import session_factory
    from backend.models import Issue
    from backend.services.software_jira import (
        HttpSoftwareJira,
        SoftwareJiraNotConfigured,
        UnavailableSoftwareJira,
        apply_snapshot,
        client_from_settings,
        parse_software_jira,
    )

    parsed = parse_software_jira({
        "key": "SW-123", "assignee": "张三", "tester": "李四",
        "status": "开发中", "due_date": "2026-11-20",
    })
    assert parsed == {
        "assignee": "张三", "tester": "李四", "status": "开发中",
        "due_date": "2026-11-20", "fetched_at": None, "error": None,
    }
    assert parse_software_jira({"due_date": "not-a-date"})["due_date"] is None
    issue = _issue(admin, title="预留接口")
    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        status_id = row.status_id
        with pytest.raises(SoftwareJiraNotConfigured):
            client_from_settings().fetch("SW-123")
        with pytest.raises(SoftwareJiraNotConfigured):
            UnavailableSoftwareJira().fetch("SW-123")
        with pytest.raises(SoftwareJiraNotConfigured):
            HttpSoftwareJira("http://software.example", "").fetch("SW-123")
        db.refresh(row)
        assert row.software_jira_snapshot is None
        assert row.software_ready is None
        assert row.status_id == status_id
        apply_snapshot(row, parsed)
        assert row.status_id == status_id
        assert row.software_ready is None
        assert row.software_jira_snapshot["assignee"] == "张三"
        db.commit()


def test_saving_a_key_stores_the_relay_snapshot(admin, monkeypatch):
    from backend.config import get_settings
    from backend.services import software_jira

    def fake_get(url, params, headers, timeout):
        assert url == "http://relay.example/api/jira/relay"
        assert params == {"key": "CVD-7488"}
        assert headers.get("X-Api-Key") == "test-token"
        assert timeout == 8.0

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "issueKey": "CVD-7488",
                    "summary": "test",
                    "assignee": "Cavin.Kim",
                    "tester": "",
                    "status": "待开发",
                    "dueDate": "2026-11-04",
                }

        return Response()

    monkeypatch.setenv("METJIRA_SOFTWARE_JIRA_URL", "http://relay.example")
    monkeypatch.setenv("METJIRA_SOFTWARE_JIRA_TOKEN", "test-token")
    get_settings.cache_clear()
    monkeypatch.setattr(software_jira.httpx, "get", fake_get)
    try:
        issue, ids, me = _developing(admin, title="拉取软件单")
        before = admin.get(f"/api/issues/{issue['key']}").json()
        armed = _arm(admin, issue["key"], tester=me, owner=me, jira="CVD-7488")
        snap = armed["software_jira_snapshot"]
        assert snap["assignee"] == "Cavin.Kim"
        assert snap["tester"] is None
        assert snap["status"] == "待开发"
        assert snap["due_date"] == "2026-11-04"
        assert snap["error"] is None
        assert snap["fetched_at"]
        assert armed["software_ready"] is True
        assert armed["status"]["name"] == before["status"]["name"]
        cleared = admin.patch(f"/api/issues/{issue['key']}", json={"software_jira_key": ""})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["software_jira_snapshot"] is None
        assert ids["研发中"]
    finally:
        get_settings.cache_clear()


def _stamp(when):
    return when.isoformat(timespec="seconds")


def _snap_status(admin, key: str) -> str:
    body = admin.get(f"/api/issues/{key}").json()
    return body["software_jira_snapshot"]["status"]


def _put_jira(key: str, jira: str, *, fetched_at: str | None, status_id: int | None = None) -> dict:
    from backend.db import session_factory
    from backend.models import Issue

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == key))
        row.software_jira_key = jira
        row.software_ready = True
        row.software_jira_snapshot = {
            "assignee": "旧", "tester": None, "status": "旧状态",
            "due_date": None, "fetched_at": fetched_at, "error": None,
        }
        if status_id is not None:
            row.status_id = status_id
        kept = {"status_id": row.status_id, "updated_at": row.updated_at, "lock": row.lock_version}
        db.commit()
        return kept


def test_refresh_groups_one_key_and_skips_fresh_or_done(admin, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from backend.db import session_factory
    from backend.models import Issue
    from backend.services import software_jira

    calls: list[str] = []

    class Fake:
        def fetch(self, key: str) -> dict:
            calls.append(key)
            return {
                "assignee": "Cavin.Kim", "tester": None, "status": "待开发",
                "due_date": "2026-11-04", "fetched_at": "2026-11-08T03:00:00+00:00",
                "error": None,
            }

    monkeypatch.setattr(software_jira, "client_from_settings", lambda: Fake())
    now = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
    fresh = _issue(admin, title="刚拉过")
    first = _issue(admin, title="旧甲")
    second = _issue(admin, title="旧乙")
    finished = _issue(admin, title="已结束")
    done_id = _ids(admin)["已完成"]
    _put_jira(fresh["key"], "CVD-FRESH", fetched_at=_stamp(now - timedelta(minutes=1)))
    kept = _put_jira(first["key"], "CVD-OLD", fetched_at=_stamp(now - timedelta(minutes=6)))
    _put_jira(second["key"], "CVD-OLD", fetched_at=_stamp(now - timedelta(minutes=9)))
    _put_jira(finished["key"], "CVD-DONE", fetched_at=None, status_id=done_id)
    with session_factory()() as db:
        result = software_jira.refresh_open_snapshots(db, now=now)
    assert calls == ["CVD-OLD"]
    assert result == {"keys": 1, "updated": 2, "stopped": None, "moved": 0}
    for key in (first["key"], second["key"]):
        body = admin.get(f"/api/issues/{key}").json()
        assert body["software_jira_snapshot"]["status"] == "待开发"
        assert body["software_jira_snapshot"]["assignee"] == "Cavin.Kim"
        assert body["software_ready"] is True
        assert body["status"]["id"] == kept["status_id"]
    assert _snap_status(admin, fresh["key"]) == "旧状态"
    assert _snap_status(admin, finished["key"]) == "旧状态"
    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == first["key"]))
        assert row.updated_at == kept["updated_at"]
        assert row.lock_version == kept["lock"]


def test_refresh_stops_when_the_relay_is_down(admin, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from backend.db import session_factory
    from backend.services import software_jira

    calls: list[str] = []

    class Fake:
        def fetch(self, key: str) -> dict:
            calls.append(key)
            if key == "CVD-DOWN":
                raise software_jira.SoftwareJiraError("软件 Jira 暂时无法连接")
            return {
                "assignee": "Cavin.Kim", "tester": None, "status": "待开发",
                "due_date": None, "fetched_at": "2026-11-08T03:00:00+00:00", "error": None,
            }

    monkeypatch.setattr(software_jira, "client_from_settings", lambda: Fake())
    now = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
    older = _issue(admin, title="更旧")
    down = _issue(admin, title="中断")
    waiting = _issue(admin, title="还没轮到")
    _put_jira(older["key"], "CVD-OLD", fetched_at=_stamp(now - timedelta(minutes=30)))
    _put_jira(down["key"], "CVD-DOWN", fetched_at=_stamp(now - timedelta(minutes=20)))
    _put_jira(waiting["key"], "CVD-NEXT", fetched_at=_stamp(now - timedelta(minutes=10)))
    with session_factory()() as db:
        result = software_jira.refresh_open_snapshots(db, now=now)
    assert calls == ["CVD-OLD", "CVD-DOWN"]
    assert result["updated"] == 1
    assert result["stopped"] == "软件 Jira 暂时无法连接"
    assert _snap_status(admin, older["key"]) == "待开发"
    assert _snap_status(admin, down["key"]) == "旧状态"
    assert _snap_status(admin, waiting["key"]) == "旧状态"


def test_refresh_does_nothing_when_the_relay_is_not_configured(admin, monkeypatch):
    from backend.db import session_factory
    from backend.services.software_jira import UnavailableSoftwareJira, refresh_open_snapshots

    monkeypatch.setattr(
        "backend.services.software_jira.client_from_settings", lambda: UnavailableSoftwareJira(),
    )
    issue = _issue(admin, title="未配置")
    _put_jira(issue["key"], "CVD-7488", fetched_at=None)
    with session_factory()() as db:
        result = refresh_open_snapshots(db)
    assert result == {"keys": 0, "updated": 0, "stopped": "not_configured", "moved": 0}
    assert _snap_status(admin, issue["key"]) == "旧状态"
