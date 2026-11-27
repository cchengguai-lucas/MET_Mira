"""Plain-dict serializers shared by routers and the agent."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Attachment,
    Comment,
    Issue,
    IssueMachine,
    Machine,
    OrgUnit,
    Project,
    Sprint,
    Status,
    User,
)
from .services import covers, sla


def iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        # Stored values are UTC; SQLite drops tzinfo on the way back.
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def user_brief(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "title": user.title,
        "email": user.email,
        "avatar_url": (
            f"/api/users/{user.id}/avatar?v={user.avatar_key}" if user.avatar_key else None
        ),
        "avatar_color": user.avatar_color,
    }


def user_full(user: User) -> dict[str, Any]:
    return {
        **(user_brief(user) or {}),
        "department": user.department,
        "phone": user.phone,
        "enabled": user.enabled,
        "is_admin": user.is_admin,
        "can_create_projects": user.can_create_projects,
        "auth_source": user.auth_source,
        "org_unit_id": user.org_unit_id,
        "org_path": user.org_unit.path if user.org_unit else None,
        "manager": user_brief(user.manager),
        "last_login_at": iso(user.last_login_at),
        "synced_at": iso(user.synced_at),
    }


def org_unit_out(ou: OrgUnit, member_count: int = 0) -> dict[str, Any]:
    return {
        "id": ou.id,
        "dn": ou.dn,
        "name": ou.name,
        "display_name": ou.display_name,
        "label": ou.label,
        "parent_id": ou.parent_id,
        "path": ou.path,
        "level": ou.level,
        "sort_order": ou.sort_order,
        "hidden": ou.hidden,
        "removed": ou.removed,
        "member_count": member_count,
    }


def status_out(status: Status | None) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "id": status.id,
        "name": status.name,
        "category": status.category,
        "color": status.color,
        "sort_order": status.sort_order,
    }


def machine_out(machine: Machine) -> dict[str, Any]:
    return {
        "id": machine.id,
        "name": machine.name,
        "group_id": machine.group_id,
        "group": machine.group.name if machine.group else None,
        "active": machine.active,
    }


def sprint_out(sprint: Sprint | None) -> dict[str, Any] | None:
    if sprint is None:
        return None
    return {
        "id": sprint.id,
        "lock_version": sprint.lock_version,
        "project_id": sprint.project_id,
        "name": sprint.name,
        "goal": sprint.goal,
        "state": sprint.state,
        "start_date": iso(sprint.start_date),
        "end_date": iso(sprint.end_date),
        "started_at": iso(sprint.started_at),
        "completed_at": iso(sprint.completed_at),
        "stats": sprint.stats or {},
    }


def project_out(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "key": project.key,
        "name": project.name,
        "description": project.description,
        "color": project.color,
        "icon": project.icon,
        "cover_url": (
            f"/api/projects/{project.key}/cover?v={project.cover_key}"
            if project.cover_key else None
        ),
        "cover_preset": covers.preset_id_of(project),
        "lead": user_brief(project.lead),
        "plan_start_date": iso(project.plan_start_date),
        "target_date": iso(project.target_date),
        "settings": project.settings or {},
        "archived": project.archived,
        "created_at": iso(project.created_at),
    }


def issue_ref(issue: Issue | None) -> dict[str, Any] | None:
    if issue is None:
        return None
    return {"id": issue.id, "key": issue.key, "title": issue.title, "type": issue.type}


def issue_query() -> Select[tuple[Issue]]:
    return select(Issue).options(
        selectinload(Issue.status),
        selectinload(Issue.assignee),
        selectinload(Issue.reporter),
        selectinload(Issue.tester),
        selectinload(Issue.software_owner),
        selectinload(Issue.electrical_scheme_by),
        selectinload(Issue.process_scheme_by),
        selectinload(Issue.software_scheme_by),
        selectinload(Issue.epic),
        selectinload(Issue.parent),
        selectinload(Issue.project),
        selectinload(Issue.machine_links)
        .selectinload(IssueMachine.machine)
        .selectinload(Machine.group),
    )


def issue_card(issue: Issue) -> dict[str, Any]:
    """Lean payload for boards/backlog cards — no description or long text fields."""
    return {
        "id": issue.id,
        "lock_version": issue.lock_version,
        "key": issue.key,
        "number": issue.number,
        "project_id": issue.project_id,
        "project_key": issue.project.key if issue.project else None,
        "type": issue.type,
        "title": issue.title,
        "status": status_out(issue.status),
        "priority": issue.priority,
        "assignee": user_brief(issue.assignee),
        "reporter": user_brief(issue.reporter),
        "tester": user_brief(issue.tester),
        "labels": issue.labels or [],
        "planned_start_date": iso(issue.planned_start_date),
        "due_date": iso(issue.due_date),
        "story_points": issue.story_points,
        "rank": issue.rank,
        "sprint_id": issue.sprint_id,
        "epic": issue_ref(issue.epic),
        "parent": issue_ref(issue.parent),
        "machines": [machine_out(m) for m in issue.machines],
        "test_passed": issue.test_passed,
        "created_at": iso(issue.created_at),
        "updated_at": iso(issue.updated_at),
        "resolved_at": iso(issue.resolved_at),
        "sla_due_at": iso(issue.sla_due_at),
        "sla_state": sla.state_of(issue, issue.project) if issue.project else issue.sla_state,
    }


def issue_brief(issue: Issue) -> dict[str, Any]:
    return {
        **issue_card(issue),
        "project_name": issue.project_name,
        "description": issue.description or "",
        "update_scope": issue.update_scope or [],
        "involves_software": issue.involves_software,
        "software_owner": user_brief(issue.software_owner),
        "software_ready": issue.software_ready,
        "software_note": issue.software_note or "",
        "software_jira_key": issue.software_jira_key or "",
        "software_jira_snapshot": issue.software_jira_snapshot,
        "scheme_note": issue.scheme_note or "",
        "electrical_scheme_ok": issue.electrical_scheme_ok,
        "electrical_scheme_by": user_brief(issue.electrical_scheme_by),
        "process_scheme_ok": issue.process_scheme_ok,
        "process_scheme_by": user_brief(issue.process_scheme_by),
        "software_scheme_ok": issue.software_scheme_ok,
        "software_scheme_by": user_brief(issue.software_scheme_by),
        "scheme_locked": bool(issue.scheme_locked),
        "scheme_confirmed_note": issue.scheme_confirmed_note or "",
        "verify_method": issue.verify_method,
        "verify_criteria": issue.verify_criteria,
        "update_date": iso(issue.update_date),
        "remark": issue.remark,
        "custom_fields": issue.extra or {},
        "custom_field_users": {},
        "sla_started_at": iso(issue.sla_started_at),
    }


def issue_page_url(key: str, base_url: str = "") -> str:
    """Absolute issue page URL when ``base_url`` is set; otherwise a site-relative path."""
    path = f"/issues/{key}"
    base = base_url.rstrip("/")
    return f"{base}{path}" if base else path


def assigned_open_issue_out(
    issue: Issue,
    *,
    base_url: str = "",
    assigned_at: datetime | date | None = None,
) -> dict[str, Any]:
    """Full issue row for external assignee open-issue lists (includes page URL).

    ``project_name`` is the issue's business project field (may be empty).
    ``project_title`` is the Mira board/project display name.
    ``assigned_at`` is when the current assignee received the issue (activity
    log, else ``created_at`` when set at create).
    """
    project = issue.project
    return {
        **issue_brief(issue),
        "project_title": project.name if project else None,
        "assigned_at": iso(assigned_at if assigned_at is not None else issue.created_at),
        "url": issue_page_url(issue.key, base_url),
    }


def attach_custom_field_users(
    db: Session, issues: list[Issue], payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Batch-resolve ``user`` custom fields so list views can show names, not raw ids."""
    from .services import profiles

    if not issues:
        return payloads
    refs: list[tuple[int, str, int]] = []
    needed: set[int] = set()
    for index, issue in enumerate(issues):
        project = issue.project
        if project is None:
            continue
        extra = issue.extra or {}
        for field in profiles.profile_of(project).custom_fields:
            if field.kind != "user":
                continue
            value = extra.get(field.key)
            if isinstance(value, int) and not isinstance(value, bool):
                needed.add(value)
                refs.append((index, field.key, value))
    users = {
        user.id: user_brief(user)
        for user in db.scalars(select(User).where(User.id.in_(needed)))
    } if needed else {}
    for payload in payloads:
        payload["custom_field_users"] = {}
    for index, key, user_id in refs:
        brief = users.get(user_id)
        if brief is not None:
            payloads[index]["custom_field_users"][key] = brief
    return payloads


def issue_brief_enriched(db: Session, issue: Issue) -> dict[str, Any]:
    payload = issue_brief(issue)
    return attach_custom_field_users(db, [issue], [payload])[0]


def comment_out(comment: Comment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "issue_id": comment.issue_id,
        "author": user_brief(comment.author),
        "body": comment.body,
        "created_at": iso(comment.created_at),
        "updated_at": iso(comment.updated_at),
    }


def attachment_out(att: Attachment) -> dict[str, Any]:
    return {
        "id": att.id,
        "issue_id": att.issue_id,
        "filename": att.filename,
        "size": att.size,
        "content_type": att.content_type,
        "uploader": user_brief(att.uploader),
        "created_at": iso(att.created_at),
        "url": f"/api/attachments/{att.id}",
    }
