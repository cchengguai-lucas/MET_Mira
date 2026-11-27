"""Email copies of actionable notifications, the daily due digest and weekly report mail."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AiReport, Issue, Project, ProjectMember, Status, User
from . import alerts, report_html
from .alerts import Mail

LOGGER = logging.getLogger(__name__)

# Notifications that ask the recipient to act; watcher chatter stays in-app only.
EMAIL_KINDS = frozenset({
    "approval_requested", "approval_result", "assigned", "tester", "software_owner",
    "to_test", "test_failed", "reopened", "mention", "software_ready",
    "software_due_conflict",
    "ready_for_test", "ready_to_close", "ready_for_quality",
    "scheme_review_requested", "scheme_changed", "scheme_confirmed", "scheme_withdrawn",
})


def _link(path: str) -> str:
    base = get_settings().public_url
    return f"{base}{path}" if base else ""


def _issue_link(key: str) -> str | None:
    return _link(f"/issues/{key}") or None


def _issue_mail(
    to: str, issue: Issue, actor: User | None, message: str, *, html_ok: bool = True,
) -> Mail:
    link = _link(f"/issues/{issue.key}")
    status = issue.status.name if issue.status is not None else ""
    rows = [("项目", issue.project.name if issue.project else ""), ("状态", status)]
    if actor is not None:
        rows.append(("操作人", actor.display_name))
    text = "\n".join(
        [message, "", f"{issue.key} {issue.title}"]
        + [f"{name}：{value}" for name, value in rows if value]
        + ([f"查看：{link}"] if link else [])
    )
    title = escape(f"{issue.key} {issue.title}")
    html = None
    if html_ok:
        html = (
            f"<p>{escape(message)}</p>"
            + (f'<p><a href="{escape(link)}">{title}</a></p>' if link else f"<p>{title}</p>")
            + "".join(f"<div>{name}：{escape(value)}</div>" for name, value in rows if value)
        )
    return Mail([to], f"[Mira] {message}", text, html)


def queue(db: Session, user_ids: Iterable[int], actor: User | None, issue: Issue | None,
          kind: str, message: str) -> None:
    """Enqueue emails in the same transaction as the in-app notification."""
    from . import notify_prefs
    from .outbox import enqueue_mail

    if kind not in EMAIL_KINDS or issue is None or not alerts.mail_configured():
        return
    ids = {uid for uid in user_ids if uid and (actor is None or uid != actor.id)}
    if not ids:
        return
    users = list(db.scalars(select(User).where(User.id.in_(ids), User.enabled.is_(True))))
    mails = []
    for user in users:
        if not user.email or not notify_prefs.wants_email(user, issue, actor):
            continue
        prefs = notify_prefs.of(user)
        mails.append(
            _issue_mail(
                user.email, issue, actor, message,
                html_ok=prefs.get("email_format") != "text",
            )
        )
    if mails:
        enqueue_mail(db, mails)


def _due_label(due: date, today: date) -> str:
    days = (due - today).days
    if days < 0:
        return f"已逾期 {-days} 天"
    return "今天到期" if days == 0 else "明天到期"


def due_digest(db: Session, today: date | None = None) -> dict[str, Any]:
    """Queue one email per assignee for due or overdue open issues."""
    from .outbox import enqueue_mail

    today = today or date.today()
    rows = db.scalars(
        select(Issue).join(Status, Status.id == Issue.status_id)
        .where(Status.category != "done", Issue.assignee_id.is_not(None),
               Issue.due_date.is_not(None), Issue.due_date <= today + timedelta(days=1))
        .order_by(Issue.due_date, Issue.id)
    )
    by_user: dict[int, list[Issue]] = defaultdict(list)
    for issue in rows:
        by_user[issue.assignee_id].append(issue)
    users = {u.id: u for u in db.scalars(select(User).where(
        User.id.in_(by_user), User.enabled.is_(True), User.email.is_not(None)
    ))}
    mails = []
    for uid, issues in by_user.items():
        user = users.get(uid)
        if user is None or not user.email:
            continue
        overdue = sum(1 for i in issues if i.due_date < today)
        subject = f"[Mira] 你有 {len(issues)} 个问题即将到期" + (
            f"（{overdue} 个已逾期）" if overdue else "")
        lines, items = [], []
        for issue in issues:
            label = _due_label(issue.due_date, today)
            link = _link(f"/issues/{issue.key}")
            lines.append(f"- {issue.key} {issue.title}（{label}，截止 {issue.due_date}）"
                         + (f" {link}" if link else ""))
            name = escape(f"{issue.key} {issue.title}")
            anchor = f'<a href="{escape(link)}">{name}</a>' if link else name
            color = "#d93025" if issue.due_date < today else "#b06000"
            items.append(f'<li>{anchor} <span style="color:{color}">{label}</span>'
                         f"，截止 {issue.due_date}</li>")
        text = "\n".join([f"{user.display_name}，以下问题需要关注：", ""] + lines)
        html = (f"<p>{escape(user.display_name)}，以下问题需要关注：</p>"
                f"<ul>{''.join(items)}</ul>")
        mails.append(Mail([user.email], subject, text, html))
    event = (
        enqueue_mail(db, mails, dedupe_key=f"due-digest:{today.isoformat()}")
        if alerts.mail_configured()
        else None
    )
    db.commit()
    return {
        "status": "queued" if event else "not_configured",
        "users": len(mails),
        "issues": sum(len(v) for v in by_user.values()),
        "queued": len(mails),
        "outbox_id": event.id if event else None,
    }


def report_recipients(db: Session, project: Project) -> list[str]:
    ids = set(db.scalars(select(ProjectMember.user_id).where(
        ProjectMember.project_id == project.id, ProjectMember.role == "admin"
    )))
    if project.lead_id:
        ids.add(project.lead_id)
    return [e for e in db.scalars(select(User.email).where(
        User.id.in_(ids), User.enabled.is_(True)
    )) if e]


def report_mail_html(project: Project, report: AiReport, *, preview: bool = False) -> str:
    """The report email body; the in-app preview also links issues without a public URL."""
    def issue_url(key: str) -> str | None:
        return _issue_link(key) or (f"/issues/{key}" if preview else None)

    link = _link(f"/projects/{project.key}/reports?tab=ai")
    return report_html.render_report(report, project, issue_url=issue_url, link=link)


def mail_report(db: Session, project: Project, report: AiReport) -> dict[str, Any]:
    from .outbox import enqueue_mail

    to = report_recipients(db, project)
    if not to:
        return {"configured": alerts.mail_configured(), "queued": 0, "recipients": 0}
    if not alerts.mail_configured():
        return {"configured": False, "queued": 0, "recipients": len(to)}
    link = _link(f"/projects/{project.key}/reports?tab=ai")
    footer = f"\n\n查看全部报告：{link}" if link else ""
    html = report_mail_html(project, report)
    event = enqueue_mail(
        db,
        [Mail(to, f"[Mira] {report.title}", report.content + footer, html)],
        dedupe_key=f"ai-report-mail:{report.id}",
    )
    return {
        "status": "queued",
        "configured": alerts.mail_configured(),
        "queued": 1 if event else 0,
        "recipients": len(to),
        "outbox_id": event.id if event else None,
    }
