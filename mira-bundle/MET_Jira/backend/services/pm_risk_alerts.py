"""Deduplicated Hardware-PM schedule alerts with staging shadow mode."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Notification, Project, ProjectMember, User
from . import permissions, schedule_cascade
from .alerts import Mail
from .outbox import enqueue_mail, enqueue_webhook, enqueue_wecom

ALERT_SEVERITIES = {"critical", "high"}
ALERT_STATUSES = {"new", "worsening"}


def _managers(db: Session, project: Project) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .outerjoin(
                ProjectMember,
                (ProjectMember.user_id == User.id)
                & (ProjectMember.project_id == project.id),
            )
            .where(
                User.enabled.is_(True),
                or_(
                    User.id == project.lead_id,
                    ProjectMember.role == "admin",
                    User.is_admin.is_(True),
                ),
            )
            .distinct()
        )
    )


def scan(db: Session, *, shadow: bool | None = None) -> dict[str, int | bool]:
    """Refresh risks and alert once per event version.

    Non-production deployments default to shadow mode: events are refreshed and counted,
    while no user notification or outbound event is created.
    """
    if shadow is None:
        shadow = get_settings().deploy_env != "production"
    stats: dict[str, int | bool] = {
        "projects": 0,
        "candidates": 0,
        "notified": 0,
        "shadow": shadow,
    }
    projects = list(
        db.scalars(
            select(Project).where(Project.archived.is_(False)).order_by(Project.id)
        )
    )
    for project in projects:
        if permissions.is_ongoing(project):
            continue
        stats["projects"] = int(stats["projects"]) + 1
        analysis = schedule_cascade.suggest_cascade(db, project)
        events = schedule_cascade.sync_risk_events(db, project, analysis)
        for event in events:
            if event.severity not in ALERT_SEVERITIES or event.status not in ALERT_STATUSES:
                continue
            if int((event.payload or {}).get("alerted_version") or 0) >= event.lock_version:
                continue
            stats["candidates"] = int(stats["candidates"]) + 1
            if shadow:
                continue
            managers = _managers(db, project)
            payload = event.payload or {}
            key = str(payload.get("key") or project.key)
            impact = int(payload.get("target_impact_days") or 0)
            message = (
                f"{project.key} · {key} 出现{event.status}顺序风险，"
                f"预测影响目标 {impact} 个工作日。请确认改期、接受风险或稍后处理。"
            )
            for manager in managers:
                db.add(
                    Notification(
                        user_id=manager.id,
                        issue_id=event.source_issue_id,
                        kind="pm_schedule_risk",
                        message=message,
                    )
                )
            emails = [manager.email for manager in managers if manager.email]
            dedupe = f"pm-risk:{event.id}:v{event.lock_version}"
            enqueue_webhook(
                db,
                "pm_schedule_risk",
                f"{project.key} 项目顺序风险",
                {
                    "project": project.key,
                    "event_id": event.id,
                    "severity": event.severity,
                    **payload,
                },
                dedupe_key=f"{dedupe}:webhook",
            )
            if emails:
                enqueue_wecom(
                    db,
                    emails,
                    f"## {project.key} 项目顺序风险\n{message}",
                    dedupe_key=f"{dedupe}:wecom",
                )
                enqueue_mail(
                    db,
                    [
                        Mail(
                            to=emails,
                            subject=f"[Mira] {project.key} 项目顺序风险",
                            text=message,
                        )
                    ],
                    dedupe_key=f"{dedupe}:mail",
                )
            event.payload = {**payload, "alerted_version": event.lock_version}
            stats["notified"] = int(stats["notified"]) + len(managers)
    db.commit()
    return stats
