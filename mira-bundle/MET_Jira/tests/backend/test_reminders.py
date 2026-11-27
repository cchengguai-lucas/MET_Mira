from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest


@pytest.fixture()
def mailbox(app, monkeypatch):
    """Configure SMTP and capture worker deliveries without using a real server."""
    from backend.config import get_settings
    from backend.routers import admin as admin_router
    from backend.services import alerts, reminders

    settings = replace(get_settings(), smtp_host="smtp.test", smtp_user="met.ai@test",
                       smtp_password="pw", mail_from="met.ai@test", public_url="http://mira")
    for module in (alerts, reminders, admin_router):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    sent: list = []

    class FakeSmtp:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, *_args):
            return None

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(alerts.smtplib, "SMTP_SSL", FakeSmtp)
    return sent


def _outbox_mails():
    from backend.db import session_factory
    from backend.models import OutboxEvent
    from backend.services.alerts import Mail

    with session_factory()() as db:
        rows = db.query(OutboxEvent).filter_by(kind="mail", status="pending").all()
        return [Mail(**mail) for row in rows for mail in row.payload["mails"]]


def _deliver_outbox():
    from backend.services.outbox import process_batch

    while process_batch()["claimed"]:
        pass


def _set_email(user_id: int, email: str) -> None:
    from backend.db import session_factory
    from backend.models import User

    with session_factory()() as db:
        db.get(User, user_id).email = email
        db.commit()


def test_approval_request_and_result_notify_by_mail(admin, make_user, mailbox):
    project = admin.get("/api/projects/ELEC").json()
    target = next(s for s in project["statuses"] if s["name"] == "方案讨论")
    gates = {target["name"]: {"required_fields": [],
                              "approvals": [{"role": "electrical", "count": 1}]}}
    assert admin.put("/api/projects/ELEC",
                     json={"settings": {"transition_gates": gates}}).status_code == 200
    requester, requester_id = make_user("mail-req", "提交人")
    approver, approver_id = make_user("mail-approver", "审批人")
    _set_email(requester_id, "req@micropolaris.com.cn")
    _set_email(approver_id, "approver@micropolaris.com.cn")
    assert admin.post(
        "/api/projects/ELEC/members", json={"user_id": requester_id, "role": "requester"},
    ).status_code == 200
    assert admin.post(
        "/api/projects/ELEC/members", json={"user_id": approver_id, "role": "electrical"},
    ).status_code == 200
    issue = requester.post("/api/issues", json={
        "project": "ELEC", "title": "需要审批的变更", "due_date": "2099-01-01",
    }).json()
    assert "key" in issue, issue

    request = requester.post(f"/api/issues/{issue['key']}/transition-requests",
                             json={"status_id": target["id"]})
    assert request.status_code == 200, request.text
    mail = next(m for m in _outbox_mails() if m.to == ["approver@micropolaris.com.cn"])
    assert "请你审批" in mail.subject and f"http://mira/issues/{issue['key']}" in mail.text
    inbox = approver.get("/api/notifications").json()["notifications"]
    assert any(n["kind"] == "approval_requested" for n in inbox)

    approver.post(f"/api/approval-requests/{request.json()['id']}/decision",
                  json={"approve": False, "comment": "资料不全"})
    result = next(m for m in _outbox_mails() if m.to == ["req@micropolaris.com.cn"])
    assert "驳回" in result.subject and "资料不全" in result.subject


def test_assignment_mail_only_after_commit(admin, make_user, mailbox):
    _client, owner_id = make_user("mail-owner", "负责人")
    _set_email(owner_id, "owner@micropolaris.com.cn")
    bad = admin.post("/api/issues", json={
        "project": "ELEC", "title": "无效", "due_date": "2099-01-01",
        "assignee_id": owner_id, "priority": "nonsense",
    })
    assert bad.status_code >= 400 and _outbox_mails() == []
    admin.post("/api/issues", json={
        "project": "ELEC", "title": "指派给你", "due_date": "2099-01-01",
        "assignee_id": owner_id,
    })
    queued = _outbox_mails()
    assert [m.to for m in queued] == [["owner@micropolaris.com.cn"]]
    assert "指派给你" in queued[0].subject


def test_electrical_handoff_mails(admin, make_user, mailbox):
    _owner, owner_id = make_user("handoff-owner", "开发负责人")
    tester, tester_id = make_user("handoff-tester", "交接测试人")
    _set_email(owner_id, "handoff-owner@micropolaris.com.cn")
    _set_email(tester_id, "handoff-tester@micropolaris.com.cn")
    for user_id, role in ((owner_id, "electrical"), (tester_id, "tester")):
        response = admin.post(
            "/api/projects/ELEC/members", json={"user_id": user_id, "role": role},
        )
        assert response.status_code == 200, response.text
    project = admin.get("/api/projects/ELEC").json()
    status = {row["name"]: row["id"] for row in project["statuses"]}
    issue = admin.post("/api/issues", json={
        "project": "ELEC",
        "title": "交接邮件",
        "type": "bug",
        "due_date": "2099-01-01",
        "assignee_id": owner_id,
        "tester_id": tester_id,
    }).json()
    key = issue["key"]

    for name in ("研发中", "开发完成"):
        moved = admin.patch(f"/api/issues/{key}", json={"status_id": status[name]})
        assert moved.status_code == 200, moved.text
    mails = _outbox_mails()
    assert any(
        mail.to == ["handoff-tester@micropolaris.com.cn"]
        and "开发已完成" in mail.subject
        for mail in mails
    )

    for name in ("测试中", "测试通过"):
        moved = tester.patch(f"/api/issues/{key}", json={"status_id": status[name]})
        assert moved.status_code == 200, moved.text
    mails = _outbox_mails()
    assert any(
        mail.to == ["handoff-owner@micropolaris.com.cn"]
        and "测试已通过" in mail.subject
        for mail in mails
    )


@pytest.mark.parametrize(
    ("profile", "key", "roles", "custom_fields", "status_names", "messages"),
    [
        (
            "mechanical",
            "MECHMAIL",
            ("quality",),
            {"drawing_no": "DWG-MAIL"},
            ("设计中", "图纸评审", "试装", "终检"),
            ("已进入试装", "已进入终检"),
        ),
        (
            "production",
            "PRODMAIL",
            ("quality",),
            {"work_order": "WO-MAIL"},
            ("已排产", "在制", "质检"),
            ("已进入质检",),
        ),
    ],
)
def test_quality_handoff_mails(
    admin,
    make_user,
    mailbox,
    profile,
    key,
    roles,
    custom_fields,
    status_names,
    messages,
):
    _quality, quality_id = make_user(f"{profile}-mail-quality", "质量交接人")
    email = f"{profile}-quality@micropolaris.com.cn"
    _set_email(quality_id, email)
    created = admin.post("/api/projects", json={
        "key": key, "name": f"{profile} mail", "profile": profile,
    })
    assert created.status_code == 200, created.text
    for role in roles:
        response = admin.post(
            f"/api/projects/{key}/members",
            json={"user_id": quality_id, "role": role},
        )
        assert response.status_code == 200, response.text
    project = admin.get(f"/api/projects/{key}").json()
    status = {row["name"]: row["id"] for row in project["statuses"]}
    issue = admin.post("/api/issues", json={
        "project": key,
        "title": "质量交接邮件",
        "due_date": "2099-01-01",
        "tester_id": quality_id,
        "custom_fields": custom_fields,
    }).json()

    for name in status_names:
        moved = admin.patch(
            f"/api/issues/{issue['key']}", json={"status_id": status[name]},
        )
        assert moved.status_code == 200, moved.text
    mails = _outbox_mails()
    for message in messages:
        assert any(mail.to == [email] and message in mail.subject for mail in mails)


def test_due_digest_lists_overdue_and_upcoming(admin, make_user, mailbox):
    from backend.db import session_factory
    from backend.services.reminders import due_digest

    sent = mailbox
    _client, owner_id = make_user("due-owner", "到期负责人")
    _set_email(owner_id, "due@micropolaris.com.cn")
    today = date.today()
    for title, due in (("逾期", today - timedelta(days=2)), ("明天", today + timedelta(days=1)),
                       ("下周", today + timedelta(days=7))):
        admin.post("/api/issues", json={"project": "ELEC", "title": title,
                                        "due_date": due.isoformat(), "assignee_id": owner_id})
    with session_factory()() as db:
        result = due_digest(db, today)
    assert result["queued"] == 1 and result["issues"] == 2
    _deliver_outbox()
    mail = next(
        m for m in sent
        if m["To"] == "due@micropolaris.com.cn" and "即将到期" in m["Subject"]
    )
    body = mail.get_body(("plain",)).get_content()
    assert "已逾期 2 天" in body and "明天到期" in body and "下周" not in body
    assert "1 个已逾期" in mail["Subject"]
    assert body.rstrip().splitlines()[-2] == "MET AI Group", "plain text carries the signature"
    html_part = mail.get_body(("html",))
    html = html_part.get_content()
    logo = next(p for p in mail.walk() if p.get_content_type() == "image/png")
    assert "MET AI Group" in html and f'src="cid:{logo["Content-ID"][1:-1]}"' in html
    assert html_part in mail.walk() and logo.get_content()[:8] == b"\x89PNG\r\n\x1a\n"


def test_weekly_report_mail_to_lead_and_admins(admin, make_user, mailbox):
    from backend.db import session_factory
    from backend.models import AiReport, Project

    sent = mailbox
    _client, lead_id = make_user("report-lead", "项目负责人")
    _set_email(lead_id, "lead@micropolaris.com.cn")
    with session_factory()() as db:
        project = db.query(Project).filter_by(key="ELEC").one()
        project.lead_id = lead_id
        content = "## 本周概况\n- 完成 **3** 项\n\n| 指标 | 值 |\n|---|---|\n| 新建 | 5 |"
        metrics = {
            "period": "2026-10-06 ~ 2026-10-13", "scope": "全项目",
            "totals": {"created": 5, "resolved": 3, "open": 9, "overdue": 2},
            "trend": [{"date": "10-12", "created": 4, "resolved": 1},
                      {"date": "10-13", "created": 1, "resolved": 2}],
            "by_status": [{"name": "处理中", "color": "#0a84ff", "count": 6}],
            "by_priority": [{"key": "high", "count": 4}],
            "workload": [{"name": "张三<script>", "open": 5, "overdue": 2}],
            "forecast": {
                "scope": {"kind": "sprint", "name": "S1", "total": 12, "remaining": 7,
                          "end_date": "2026-10-20"},
                "sample_days": 30, "throughput_total": 18, "throughput_per_week": 4.2,
                "finish": {"p50": "2026-10-19", "p85": "2026-10-23", "p95": "2026-10-27"},
                "unreachable_pct": 0.0,
                "target": {"date": "2026-10-20", "probability": 62.5, "items": None},
                "curve": [{"date": "10-14", "probability": 0.0},
                          {"date": "10-20", "probability": 62.5},
                          {"date": "10-27", "probability": 96.0}],
            },
        }
        report = AiReport(project_id=project.id, period="weekly", title="ELEC 周报",
                          content=content + "\n- ELEC-1 逾期", metrics=metrics, scheduled=True)
        db.add(report)
        db.commit()
        report_id = report.id

    listed = admin.get("/api/projects/ELEC/ai-reports").json()["reports"]
    assert listed[0]["can_email"] is True
    response = admin.post(f"/api/projects/ELEC/ai-reports/{report_id}/email")
    assert response.status_code == 200, response.text
    assert response.json()["queued"] == 1
    _deliver_outbox()
    mail = next(m for m in sent if "ELEC 周报" in m["Subject"])
    assert "lead@micropolaris.com.cn" in mail["To"]
    html = mail.get_body(("html",)).get_content()
    assert "本周概况</h3>" in html and "<b>3</b>" in html and "新建</td>" in html
    assert "http://mira/projects/ELEC/reports?tab=ai" in html
    assert "新建与完成趋势" in html and "负责人工作量" in html and "未完成 · 按优先级" in html
    assert 'href="http://mira/issues/ELEC-1"' in html
    assert "交付预测" in html and "冲刺「S1」剩余" in html and "2026-10-23" in html
    assert "在冲刺结束 <b>2026-10-20</b> 前完成的概率" in html and "62.5%" in html
    assert "<script>" not in html and "张三&lt;script&gt;" in html
    assert "<svg" not in html, "mail clients drop SVG; charts are table based"

    preview = admin.get(f"/api/projects/ELEC/ai-reports/{report_id}/html")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in preview.headers["content-security-policy"]
    assert "新建与完成趋势" in preview.text and "data:image/png;base64," in preview.text
    download = admin.get(f"/api/projects/ELEC/ai-reports/{report_id}/html?download=true")
    assert download.headers["content-disposition"].startswith("attachment;")
    assert admin.get("/api/projects/ELEC/ai-reports/99999/html").status_code == 404


def test_admin_due_digest_endpoint_requires_mail(admin):
    assert admin.post("/api/admin/reminders/due-digest").status_code == 409
