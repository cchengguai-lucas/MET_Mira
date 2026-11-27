from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from openpyxl import load_workbook


def test_mechanical_profile_custom_fields_and_required_gate(admin):
    created = admin.post("/api/projects", json={
        "key": "MECH", "name": "机械试点", "profile": "mechanical",
    })
    assert created.status_code == 200, created.text
    detail = admin.get("/api/projects/MECH").json()
    assert detail["profile"]["key"] == "mechanical"
    assert "mechanical" in detail["roles"]
    first, second = detail["statuses"][:2]

    missing = admin.post("/api/issues", json={
        "project": "MECH", "title": "修改夹具", "due_date": "2099-01-01",
    })
    assert missing.status_code == 200
    blocked = admin.patch(f"/api/issues/{missing.json()['key']}",
                          json={"status_id": second["id"]})
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["code"] == "transition_gate"
    assert body["fields"]
    assert "custom_fields.drawing_no" in body["fields"]

    rejected = admin.patch(f"/api/issues/{missing.json()['key']}", json={
        "custom_fields": {"material": "SUS304"},
    })
    assert rejected.status_code == 400
    material = admin.post("/api/assets", json={
        "kind": "material", "code": "SUS304", "name": "不锈钢 304",
    })
    assert material.status_code == 200, material.text
    saved = admin.patch(f"/api/issues/{missing.json()['key']}", json={
        "custom_fields": {
            "drawing_no": "DWG-100", "material": material.json()["id"],
            "inspection_result": "通过",
        },
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["custom_fields"]["drawing_no"] == "DWG-100"
    key = missing.json()["key"]
    activity = admin.get(f"/api/issues/{key}/activity").json()["activity"]
    custom_rows = [a for a in activity if (a.get("field") or "").startswith("custom_fields.")]
    assert {a["field"] for a in custom_rows} >= {
        "custom_fields.drawing_no",
        "custom_fields.material",
        "custom_fields.inspection_result",
    }
    drawing = next(a for a in custom_rows if a["field"] == "custom_fields.drawing_no")
    assert drawing["new_value"] == "DWG-100"
    assert "{" not in (drawing["new_value"] or "")
    moved = admin.patch(f"/api/issues/{key}",
                        json={"status_id": second["id"]})
    assert moved.status_code == 200
    assert moved.json()["status"]["id"] == second["id"]
    assert first["name"] == "待评估"
    found = admin.get(
        '/api/search?jql=project%20%3D%20MECH%20AND%20cf_drawing_no%20%3D%20%22DWG-100%22'
    )
    assert found.status_code == 200 and found.json()["total"] == 1
    report = admin.get("/api/projects/MECH/reports/summary").json()
    assert report["profile_distributions"]["inspection_result"]["values"] == {"通过": 1}
    workbook = load_workbook(BytesIO(admin.get("/api/projects/MECH/export.xlsx").content))
    headers = [cell.value for cell in workbook.active[2]]
    assert "图号" in headers and "材料" in headers


def test_role_countersign_approval_auto_transitions(admin, make_user):
    project = admin.get("/api/projects/ELEC").json()
    target = next(status for status in project["statuses"] if status["name"] == "方案讨论")
    settings = {
        "transition_gates": {
            target["name"]: {
                "required_fields": ["title"],
                "approvals": [{"role": "electrical", "count": 1}],
            }
        }
    }
    assert admin.put("/api/projects/ELEC", json={"settings": settings}).status_code == 200
    requester, requester_id = make_user("gate-req", "门禁提出人")
    approver, approver_id = make_user("gate-elec", "门禁审批人")
    assert admin.post("/api/projects/ELEC/members", json={
        "user_id": requester_id, "role": "requester",
    }).status_code == 200
    assert admin.post("/api/projects/ELEC/members", json={
        "user_id": approver_id, "role": "electrical",
    }).status_code == 200
    issue = requester.post("/api/issues", json={
        "project": "ELEC", "title": "需要审批", "due_date": "2099-01-01",
    }).json()
    assert "key" in issue, issue
    direct = requester.patch(f"/api/issues/{issue['key']}", json={"status_id": target["id"]})
    assert direct.status_code == 409 and direct.json()["code"] == "approval_required"
    request = requester.post(f"/api/issues/{issue['key']}/transition-requests",
                             json={"status_id": target["id"], "comment": "请审批"})
    assert request.status_code == 200, request.text
    decided = approver.post(f"/api/approval-requests/{request.json()['id']}/decision",
                            json={"approve": True, "comment": "同意",
                                  "expected_version": request.json()["lock_version"]})
    assert decided.status_code == 200, decided.text
    assert decided.json()["request"]["status"] == "approved"
    assert decided.json()["issue"]["status"]["id"] == target["id"]
    assert decided.json()["request"]["decisions"][0]["comment"] == "同意"
    stale = approver.post(
        f"/api/approval-requests/{request.json()['id']}/decision",
        json={"approve": True, "expected_version": request.json()["lock_version"]},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "stale_write"


def test_shared_asset_mapping_is_non_destructive(admin):
    before = admin.get("/api/projects/ELEC").json()["machine_groups"]
    response = admin.post("/api/assets/project/ELEC/map-machines")
    assert response.status_code == 200, response.text
    assert response.json()["machines"] > 0
    bindings = admin.get("/api/assets/project/ELEC").json()["assets"]
    assert bindings and all(row["local_machine_id"] for row in bindings)
    after = admin.get("/api/projects/ELEC").json()["machine_groups"]
    assert before == after


def test_machine_cannot_move_to_group_in_another_project(admin):
    created = admin.post("/api/projects", json={
        "key": "BOUND", "name": "边界测试", "profile": "electrical",
    })
    assert created.status_code == 200, created.text
    foreign_group = admin.post(
        "/api/projects/BOUND/machine-groups", json={"name": "外部组"}
    )
    assert foreign_group.status_code == 200, foreign_group.text

    source = admin.get("/api/projects/ELEC").json()["machine_groups"][0]["machines"][0]
    moved = admin.put(f"/api/projects/ELEC/machines/{source['id']}", json={
        "group_id": foreign_group.json()["id"],
        "name": source["name"],
        "active": source["active"],
    })
    assert moved.status_code == 404
    assert moved.json()["detail"] == "机台组不存在"


def test_sla_scan_and_capacity_endpoint(admin):
    from backend.db import session_factory, utcnow
    from backend.models import Issue

    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "超时任务", "priority": "highest",
        "due_date": "2099-01-01",
    }).json()
    with session_factory()() as db:
        row = db.get(Issue, issue["id"])
        row.sla_due_at = utcnow() - timedelta(hours=1)
        db.commit()
    scan = admin.post("/api/admin/sla/scan")
    assert scan.status_code == 200 and scan.json()["breached"] >= 1
    capacity = admin.get("/api/admin/capacity")
    assert capacity.status_code == 200
    assert capacity.json()["sla_breached"] >= 1


def test_sla_pushes_markdown_to_wecom_bridge(admin, make_user, monkeypatch):
    from dataclasses import replace

    from backend.config import get_settings
    from backend.db import session_factory, utcnow
    from backend.models import Issue, User
    from backend.services import alerts, sla

    settings = replace(get_settings(), wecom_push_url="http://bridge/internal/push_markdown",
                       wecom_push_token="secret", public_url="http://mira")
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)
    monkeypatch.setattr(sla, "get_settings", lambda: settings)
    calls = []

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return {"delivered": [{"email": e, "ok": True} for e in calls[-1]["json"]["emails"]]}

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Reply()

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    _client, assignee_id = make_user("sla-owner", "SLA 负责人")
    with session_factory()() as db:
        db.get(User, assignee_id).email = "SLA.Owner@micropolaris.com.cn"
        db.commit()
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "推送超时", "priority": "high",
        "due_date": "2099-01-01", "assignee_id": assignee_id,
    }).json()
    with session_factory()() as db:
        db.get(Issue, issue["id"]).sla_due_at = utcnow() - timedelta(hours=1)
        db.commit()

    scan = admin.post("/api/admin/sla/scan").json()
    assert scan["wecom_queued"] >= 1
    from backend.services.outbox import process_batch

    assert process_batch()["succeeded"] >= 1
    pushed = next(c for c in calls if "sla.owner@micropolaris.com.cn" in c["json"]["emails"])
    assert pushed["headers"]["Authorization"] == "Bearer secret"
    assert f"[{issue['key']}](http://mira/issues/{issue['key']})" in pushed["json"]["markdown"]
    assert "已超时" in pushed["json"]["markdown"]

    assert admin.get("/api/admin/alerts").json()["wecom_configured"] is True
    assert admin.post("/api/admin/alerts/test", json={}).status_code == 400
    test = admin.post("/api/admin/alerts/test", json={"email": "a@micropolaris.com.cn"})
    assert test.status_code == 200 and test.json()["delivered"][0]["ok"] is True


def test_sla_reminder_mail_and_staging_redirect(admin, make_user, monkeypatch):
    from dataclasses import replace

    from backend.config import get_settings
    from backend.db import session_factory, utcnow
    from backend.models import Issue, User
    from backend.routers import admin as admin_router
    from backend.services import alerts

    settings = replace(get_settings(), smtp_host="smtp.test", smtp_user="met.ai@test",
                       smtp_password="pw", mail_from="met.ai@test",
                       alert_redirect="sandbox@test")
    for module in (alerts, admin_router):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    sent = []

    class FakeSmtp:
        def __init__(self, host, port, **_kwargs):
            assert (host, port) == ("smtp.test", 465)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, user, password):
            assert (user, password) == ("met.ai@test", "pw")

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(alerts.smtplib, "SMTP_SSL", FakeSmtp)
    _client, owner_id = make_user("mail-owner", "邮件负责人")
    with session_factory()() as db:
        db.get(User, owner_id).email = "owner@micropolaris.com.cn"
        db.commit()
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "邮件提醒", "priority": "high",
        "due_date": "2099-01-01", "assignee_id": owner_id,
    }).json()
    with session_factory()() as db:
        db.get(Issue, issue["id"]).sla_due_at = utcnow() - timedelta(hours=1)
        db.commit()

    scan = admin.post("/api/admin/sla/scan").json()
    assert scan["mail_queued"] >= 1
    from backend.services.outbox import process_batch

    assert process_batch()["succeeded"] >= 1
    mail = next(m for m in sent if f"{issue['key']} 已超时" in m["Subject"])
    assert mail["To"] == "sandbox@test"
    assert mail["Date"] and mail["Message-ID"]

    status = admin.get("/api/admin/alerts").json()
    assert status["mail_configured"] is True and status["redirect"] == "sandbox@test"
    test = admin.post("/api/admin/alerts/test-mail", json={"email": "real@micropolaris.com.cn"})
    assert test.status_code == 200 and sent[-1]["To"] == "real@micropolaris.com.cn"


def test_org_probe_is_safe_without_service_credentials(admin):
    probe = admin.get("/api/admin/sync/probe")
    assert probe.status_code == 200
    assert probe.json()["configured"] is False
    assert "password" not in str(probe.json()).lower()


def test_org_preview_reports_changes_without_persisting(admin, monkeypatch):
    from backend.services.ldap_auth import LdapProfile
    from backend.services.org_sync import Directory

    directory = Directory(profiles=[
        LdapProfile("preview-user", "预览用户", "preview@example.test"),
    ])
    monkeypatch.setattr("backend.routers.admin.fetch_directory", lambda: directory)

    preview = admin.post("/api/admin/sync/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["stats"]["users_added_count"] == 1
    users = admin.get("/api/admin/users?q=preview-user").json()["users"]
    assert all(user["username"] != "preview-user" for user in users)


def test_master_data_overview_bulk_and_usage(admin):
    bulk = admin.post("/api/assets/bulk", json={"kind": "material", "items": [
        {"code": "AL6061", "name": "铝 6061"}, {"code": "AL6061", "name": "重复"},
        {"code": "POM", "name": "POM 塑料"},
    ]})
    assert bulk.status_code == 200, bulk.text
    assert bulk.json() == {"created": 2, "skipped": ["AL6061"]}
    assets = admin.get("/api/assets", params={"kind": "material"}).json()["assets"]
    aluminium = next(a for a in assets if a["code"] == "AL6061")
    pom = next(a for a in assets if a["code"] == "POM")
    clash = admin.put(f"/api/assets/{pom['id']}", json={
        "kind": "material", "code": "AL6061", "name": "POM 塑料",
    })
    assert clash.status_code == 409

    assert admin.post("/api/projects", json={
        "key": "MDATA", "name": "主数据", "profile": "mechanical",
    }).status_code == 200
    created = admin.post("/api/issues", json={
        "project": "MDATA", "title": "用铝", "due_date": "2099-01-01",
        "custom_fields": {"material": aluminium["id"]},
    })
    assert created.status_code == 200, created.text

    overview = admin.get("/api/assets/overview").json()
    material = overview["kinds"]["material"]
    assert material["active"] >= 2
    assert any(f["field"] == "material" for f in material["fields"])
    assert any(f["field"] == "line" for f in overview["kinds"]["line"]["fields"])
    assert overview["usage"][str(aluminium["id"])]["issues"] == 1
    assert str(pom["id"]) not in overview["usage"]


def test_requirement_only_gates_do_not_block_tasks(admin):
    """Electrical form fields are requirement-only; tasks must not be stuck on them."""
    key = "TGATE"
    assert admin.post("/api/projects", json={
        "key": key, "name": "门禁类型隔离", "profile": "electrical",
    }).status_code == 200
    project = admin.get(f"/api/projects/{key}").json()
    by_name = {s["name"]: s["id"] for s in project["statuses"]}
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
        "project": key, "type": "task", "title": "无电气表单的任务",
        "due_date": "2099-12-31",
    }).json()
    moved = admin.patch(f"/api/issues/{task['key']}",
                        json={"status_id": by_name["研发中"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"]["name"] == "研发中"

    req = admin.post("/api/issues", json={
        "project": key, "type": "requirement", "title": "需要电气字段的需求",
        "due_date": "2099-12-31",
    }).json()
    discuss = admin.patch(f"/api/issues/{req['key']}",
                          json={"status_id": by_name["方案讨论"]})
    assert discuss.status_code == 200, discuss.text
    blocked = admin.patch(f"/api/issues/{req['key']}",
                          json={"status_id": by_name["研发中"]})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "transition_gate"
    assert set(blocked.json()["fields"]) >= {"update_scope", "verify_method"}


def test_story_skips_requirement_only_verify_gate(admin):
    key = "TSTORY"
    assert admin.post("/api/projects", json={
        "key": key, "name": "故事跳过验证门禁", "profile": "electrical",
    }).status_code == 200
    project = admin.get(f"/api/projects/{key}").json()
    by_name = {s["name"]: s["id"] for s in project["statuses"]}
    me = admin.get("/api/auth/me").json()["user"]["id"]
    assert admin.put(f"/api/projects/{key}", json={
        "settings": {
            "transition_gates": {
                "测试中": {"required_fields": ["verify_criteria"], "approvals": []},
            },
        },
    }).status_code == 200
    story = admin.post("/api/issues", json={
        "project": key, "type": "story", "title": "无验证标准的故事",
        "due_date": "2099-12-31", "tester_id": me,
    }).json()
    assert admin.patch(f"/api/issues/{story['key']}",
                       json={"status_id": by_name["研发中"]}).status_code == 200
    assert admin.patch(f"/api/issues/{story['key']}",
                       json={"status_id": by_name["开发完成"]}).status_code == 200
    into_test = admin.patch(f"/api/issues/{story['key']}",
                            json={"status_id": by_name["测试中"]})
    assert into_test.status_code == 200, into_test.text


def test_mechanical_task_skips_required_drawing_no(admin):
    key = "MTASK"
    assert admin.post("/api/projects", json={
        "key": key, "name": "机械任务无图号", "profile": "mechanical",
    }).status_code == 200
    project = admin.get(f"/api/projects/{key}").json()
    second = project["statuses"][1]
    task = admin.post("/api/issues", json={
        "project": key, "type": "task", "title": "无图号任务",
        "due_date": "2099-12-31",
    }).json()
    moved = admin.patch(f"/api/issues/{task['key']}", json={"status_id": second["id"]})
    assert moved.status_code == 200, moved.text
