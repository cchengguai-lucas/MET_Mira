"""SLA deadlines, in-app escalation notifications and capacity snapshots.

The clock of an issue starts at ``sla_started_at`` (creation, or the moment it was reopened)
and runs for the project's target hours for its priority. Time spent in the project's paused
statuses is added back (``sla_paused_hours``). With the business calendar only working hours
on working days count; otherwise every hour counts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import utcnow
from ..models import Issue, Project, ProjectMember, Status, User

SLA_TZ = ZoneInfo("Asia/Shanghai")
CALENDARS = ("natural", "business")
DEFAULT_BUSINESS_HOURS = {"start": "09:00", "end": "18:00", "workdays": [1, 2, 3, 4, 5]}
MAX_DAYS = 3660


@dataclass(frozen=True)
class Calendar:
    start: time
    end: time
    workdays: frozenset[int]
    holidays: frozenset[str]
    extra_workdays: frozenset[str]

    def window(self, day: date) -> tuple[datetime, datetime] | None:
        iso = day.isoformat()
        working = iso in self.extra_workdays or (
            day.isoweekday() in self.workdays and iso not in self.holidays
        )
        if not working:
            return None
        return (datetime.combine(day, self.start, SLA_TZ), datetime.combine(day, self.end, SLA_TZ))


def _clock(value: str) -> time:
    hour, minute = str(value).split(":")
    return time(int(hour), int(minute))


def calendar_of(project: Project) -> Calendar | None:
    """The project's working calendar, or ``None`` when every hour counts."""
    settings = project.settings or {}
    if settings.get("sla_calendar") != "business":
        return None
    hours = {**DEFAULT_BUSINESS_HOURS, **(settings.get("sla_business_hours") or {})}
    return Calendar(
        start=_clock(hours["start"]),
        end=_clock(hours["end"]),
        workdays=frozenset(int(d) for d in hours["workdays"]),
        holidays=frozenset(settings.get("sla_holidays") or []),
        extra_workdays=frozenset(settings.get("sla_extra_workdays") or []),
    )


def validate_settings(settings: dict[str, Any]) -> None:
    """Raise ``ValueError`` for SLA settings that cannot be used."""
    if "sla_calendar" in settings and settings["sla_calendar"] not in CALENDARS:
        raise ValueError("无效的 SLA 计时日历")
    if "sla_hours" in settings:
        for priority, hours in (settings["sla_hours"] or {}).items():
            if hours is not None and float(hours) <= 0:
                raise ValueError(f"SLA 时限必须大于 0：{priority}")
    if "sla_business_hours" in settings:
        hours = {**DEFAULT_BUSINESS_HOURS, **(settings["sla_business_hours"] or {})}
        try:
            start, end = _clock(hours["start"]), _clock(hours["end"])
            workdays = {int(d) for d in hours["workdays"]}
        except (TypeError, ValueError) as error:
            raise ValueError("工作时间格式应为 HH:MM") from error
        if start >= end:
            raise ValueError("工作时间的结束必须晚于开始")
        if not workdays or not workdays <= set(range(1, 8)):
            raise ValueError("至少选择一个工作日")
    for key, label in (("sla_holidays", "节假日"), ("sla_extra_workdays", "调休上班日")):
        for value in settings.get(key) or []:
            try:
                date.fromisoformat(str(value))
            except ValueError as error:
                raise ValueError(f"{label}日期格式应为 YYYY-MM-DD：{value}") from error


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def add_time(start: datetime, hours: float, cal: Calendar | None) -> datetime:
    """The moment ``hours`` of SLA time after ``start``."""
    start = _aware(start)
    if cal is None:
        return start + timedelta(hours=hours)
    remaining = timedelta(hours=hours)
    cursor = start.astimezone(SLA_TZ)
    for _ in range(MAX_DAYS):
        window = cal.window(cursor.date())
        if window is not None:
            opens, closes = window
            cursor = max(cursor, opens)
            if cursor < closes:
                if remaining <= closes - cursor:
                    return (cursor + remaining).astimezone(timezone.utc)
                remaining -= closes - cursor
        cursor = datetime.combine(cursor.date() + timedelta(days=1), time.min, SLA_TZ)
    return cursor.astimezone(timezone.utc)


def elapsed(start: datetime, end: datetime, cal: Calendar | None,
            cap: float | None = None) -> float:
    """Hours of SLA time between two moments; stops counting once past ``cap``."""
    start, end = _aware(start), _aware(end)
    if end <= start:
        return 0.0
    if cal is None:
        return (end - start).total_seconds() / 3600
    total = timedelta()
    day = start.astimezone(SLA_TZ).date()
    last = end.astimezone(SLA_TZ).date()
    while day <= last:
        window = cal.window(day)
        if window is not None:
            opens, closes = max(window[0], start), min(window[1], end)
            if closes > opens:
                total += closes - opens
                if cap is not None and total.total_seconds() / 3600 > cap:
                    break
        day += timedelta(days=1)
    return total.total_seconds() / 3600


def target_hours(project: Project, priority: str) -> float | None:
    hours = ((project.settings or {}).get("sla_hours") or {}).get(priority)
    return float(hours) if hours else None


def paused_statuses(project: Project) -> set[str]:
    return set((project.settings or {}).get("sla_paused_statuses") or [])


def recompute_due(issue: Issue, project: Project) -> None:
    hours = target_hours(project, issue.priority)
    start = issue.sla_started_at or issue.created_at or utcnow()
    issue.sla_due_at = (
        add_time(start, hours + (issue.sla_paused_hours or 0.0), calendar_of(project))
        if hours else None
    )


def sync(issue: Issue, project: Project, status: Status, now: datetime | None = None) -> None:
    """Bring the issue's clock in line with its (new) status and the project's settings."""
    now = now or utcnow()
    cal = calendar_of(project)
    if issue.sla_paused_at is not None and (
        status.category == "done" or status.name not in paused_statuses(project)
    ):
        issue.sla_paused_hours = (issue.sla_paused_hours or 0.0) + elapsed(
            issue.sla_paused_at, now, cal
        )
        issue.sla_paused_at = None
    if status.category == "done":
        issue.sla_state = "completed"
        return
    if issue.sla_state == "completed":
        issue.sla_started_at, issue.sla_paused_hours = now, 0.0
        issue.sla_state, issue.sla_notified_level = "ok", 0
    if issue.sla_started_at is None:
        issue.sla_started_at = issue.created_at or now
    if status.name in paused_statuses(project) and issue.sla_paused_at is None:
        issue.sla_paused_at = now
    recompute_due(issue, project)
    issue.sla_state = state_of(issue, project, now)
    issue.sla_notified_level = min(issue.sla_notified_level or 0, _level(issue.sla_state))


def _level(state: str) -> int:
    return {"warning": 1, "breached": 2}.get(state, 0)


def state_of(issue: Issue, project: Project, now: datetime | None = None) -> str:
    """completed / paused / none / ok / warning / breached, as of ``now``."""
    if issue.sla_state == "completed":
        return "completed"
    if issue.sla_paused_at is not None:
        return "paused"
    if issue.sla_due_at is None:
        return "none"
    now = _aware(now or utcnow())
    due = _aware(issue.sla_due_at)
    if now >= due:
        return "breached"
    warning = float((project.settings or {}).get("sla_warning_hours", 4))
    if due - now <= timedelta(hours=warning):
        return "warning"
    cal = calendar_of(project)
    if cal is not None and elapsed(now, due, cal, cap=warning) <= warning:
        return "warning"
    return "ok"


def resync_project(db: Session, project: Project) -> int:
    """Re-derive deadlines of the project's open issues after its SLA settings changed."""
    rows = list(db.scalars(
        select(Issue).join(Status, Status.id == Issue.status_id)
        .where(Issue.project_id == project.id, Status.category != "done")
    ))
    for issue in rows:
        sync(issue, project, issue.status)
    return len(rows)


def scan(db: Session) -> dict[str, int]:
    from .issues import notify

    now = utcnow()
    rows = list(db.scalars(
        select(Issue)
        .join(Status, Status.id == Issue.status_id)
        .where(Status.category != "done")
        .options(selectinload(Issue.project), selectinload(Issue.status))
    ))
    project_ids = {issue.project_id for issue in rows}
    admins_by_project: dict[int, set[int]] = defaultdict(set)
    if project_ids:
        for pid, uid in db.execute(
            select(ProjectMember.project_id, ProjectMember.user_id).where(
                ProjectMember.project_id.in_(project_ids),
                ProjectMember.role == "admin",
            )
        ):
            admins_by_project[pid].add(uid)
    actor = db.scalar(select(User).where(User.is_admin.is_(True)).limit(1))
    warned = breached = initialized = 0
    pushes: list[tuple[Issue, int, list[str], str, Any]] = []
    pending_email_ids: set[int] = set()
    notify_jobs: list[tuple[Issue, set[int], int, Any]] = []
    for issue in rows:
        if issue.sla_state == "completed":
            sync(issue, issue.project, issue.status, now)
        if issue.sla_due_at is None and issue.sla_paused_at is None:
            if issue.sla_started_at is None:
                issue.sla_started_at = issue.created_at
            recompute_due(issue, issue.project)
            initialized += issue.sla_due_at is not None
        issue.sla_state = state_of(issue, issue.project, now)
        if issue.sla_due_at is None or issue.sla_state == "paused":
            continue
        due = _aware(issue.sla_due_at)
        level = _level(issue.sla_state)
        if level <= issue.sla_notified_level:
            continue
        recipients = {uid for uid in (issue.assignee_id, issue.reporter_id) if uid}
        recipients.update(admins_by_project.get(issue.project_id, ()))
        if recipients:
            pending_email_ids.update(recipients)
            notify_jobs.append((issue, recipients, level, due))
        issue.sla_notified_level = level
        warned += level == 1
        breached += level == 2
    from . import notify_prefs

    users_by_id: dict[int, User] = {}
    if pending_email_ids:
        for user in db.scalars(
            select(User).where(User.id.in_(pending_email_ids), User.enabled.is_(True))
        ):
            users_by_id[user.id] = user
    for issue, recipients, level, due in notify_jobs:
        if actor:
            label = "已超时" if level == 2 else "即将超时"
            notify(
                db,
                recipients,
                actor,
                issue,
                "sla_breached" if level == 2 else "sla_warning",
                f"{issue.key}「{issue.title}」{label}",
            )
        mail_emails = [
            u.email
            for uid in recipients
            if (u := users_by_id.get(uid)) is not None and u.email
        ]
        wecom_emails = [
            u.email
            for uid in recipients
            if (u := users_by_id.get(uid)) is not None
            and u.email
            and notify_prefs.wants_wecom(u)
        ]
        if mail_emails or wecom_emails:
            markdown, mail = _sla_messages(issue, due, level, mail_emails or wecom_emails)
            pushes.append((issue, level, wecom_emails, markdown, mail))
    result = {
        "checked": len(rows),
        "initialized": initialized,
        "warned": warned,
        "breached": breached,
    }
    if warned or breached:
        from . import alerts
        from .outbox import enqueue_mail, enqueue_webhook, enqueue_wecom

        if get_settings().alert_webhook_url:
            enqueue_webhook(db, "sla", "Mira SLA 提醒", result)
        wecom_queued = 0
        if alerts.wecom_configured():
            for issue, level, wecom_emails, markdown, _mail in pushes:
                if not wecom_emails:
                    continue
                enqueue_wecom(
                    db,
                    wecom_emails,
                    markdown,
                    dedupe_key=f"sla:{issue.id}:{level}:wecom",
                )
                wecom_queued += 1
        mail_queued = 0
        if alerts.mail_configured():
            for issue, level, _wecom_emails, _markdown, mail in pushes:
                if not mail.to:
                    continue
                if enqueue_mail(
                    db,
                    [mail],
                    dedupe_key=f"sla:{issue.id}:{level}:mail",
                ):
                    mail_queued += 1
        result["wecom_queued"] = wecom_queued
        result["mail_queued"] = mail_queued
    db.commit()
    return result


def _sla_messages(issue: Issue, due, level: int, emails: list[str | None]):
    from html import escape

    from .alerts import Mail
    from .excel_io import PRIORITY_LABELS

    label = "已超时" if level == 2 else "即将超时"
    base = get_settings().public_url
    link = f"{base}/issues/{issue.key}" if base else ""
    rows = [
        ("项目", issue.project.name),
        ("优先级", PRIORITY_LABELS.get(issue.priority, issue.priority)),
        ("截止", due.astimezone(SLA_TZ).strftime("%Y-%m-%d %H:%M")),
    ]
    key = f"[{issue.key}]({link})" if link else issue.key
    markdown = "\n".join(
        [f"**Mira SLA {label}**", f"> {key} {issue.title}"]
        + [f"> {name}：{value}" for name, value in rows]
    )
    text = "\n".join(
        [f"{issue.key} {issue.title} {label}。", ""]
        + [f"{name}：{value}" for name, value in rows]
        + ([f"查看：{link}"] if link else [])
    )
    title = escape(f"{issue.key} {issue.title}")
    html = (
        f"<p><b>Mira SLA {label}</b></p>"
        + (f'<p><a href="{escape(link)}">{title}</a></p>' if link else f"<p>{title}</p>")
        + "".join(f"<div>{name}：{escape(str(value))}</div>" for name, value in rows)
    )
    mail = Mail([e for e in emails if e], f"[Mira] {issue.key} {label}：{issue.title}",
                text, html)
    return markdown, mail


def capacity_snapshot(db: Session) -> dict[str, Any]:
    by_status = dict(db.execute(
        select(Status.category, func.count(Issue.id)).join(Issue, Issue.status_id == Status.id)
        .group_by(Status.category)
    ).all())
    total = sum(by_status.values())
    return {
        "issues": total,
        "by_status": by_status,
        "sla_warning": db.scalar(select(func.count(Issue.id)).where(Issue.sla_state == "warning")),
        "sla_breached": db.scalar(
            select(func.count(Issue.id)).where(Issue.sla_state == "breached")
        ),
        "generated_at": utcnow().isoformat(),
    }
