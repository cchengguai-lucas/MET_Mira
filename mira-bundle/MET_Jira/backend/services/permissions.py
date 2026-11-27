"""Project roles, visibility and the per-section edit rules of the project's profile."""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from ..models import Issue, Project, ProjectMember, User
from . import scheme
from .profiles import GENERAL, person_roles, profile_of

VISIBILITIES = ("public", "members")
WORK_MODES = ("delivery", "ongoing")
DEFAULT_WORK_MODE = "delivery"


def project_roles(db: Session, project_id: int, user: User) -> set[str]:
    """Roles for this user in the project. System admins always count as project admins."""
    if user.is_admin:
        return {"admin"}
    roles = set(
        db.scalars(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user.id
            )
        )
    )
    project = db.get(Project, project_id)
    if project is not None and project.lead_id == user.id:
        roles.add("admin")
    return roles


def is_project_admin(user: User, roles: set[str]) -> bool:
    return user.is_admin or "admin" in roles


def can_create_projects(user: User) -> bool:
    """System admins and users granted the create-project flag may create projects."""
    return user.is_admin or user.can_create_projects


def can_mutate_project(user: User, project: Project, roles: set[str]) -> bool:
    """Members (or system admins) may write; archived projects are read-only."""
    if project.archived:
        return False
    return user.is_admin or bool(roles)


def can_mutate_issue(user: User, issue: Issue, project: Project, roles: set[str]) -> bool:
    """Project members, or anyone already on the issue (reporter/assignee/…)."""
    if project.archived:
        return False
    return user.is_admin or bool(roles) or involved(user, issue)


def visibility(project: Project) -> str:
    value = (project.settings or {}).get("visibility")
    return value if value in VISIBILITIES else "public"


def work_mode(project: Project) -> str:
    """Delivery projects keep a finish goal; ongoing projects are continuous work."""
    value = (project.settings or {}).get("work_mode")
    return value if value in WORK_MODES else DEFAULT_WORK_MODE


def is_delivery(project: Project) -> bool:
    return work_mode(project) == "delivery"


def is_ongoing(project: Project) -> bool:
    return work_mode(project) == "ongoing"


def normalize_work_mode(value: object) -> str:
    text = str(value or "").strip()
    if text not in WORK_MODES:
        raise ValueError("无效的项目类型")
    return text


def can_view_project(user: User, project: Project, roles: set[str]) -> bool:
    return user.is_admin or bool(roles) or visibility(project) == "public"


def involved(user: User, issue: Issue) -> bool:
    return user.id in {issue.reporter_id, issue.assignee_id, issue.tester_id,
                       issue.software_owner_id}


def can_view_issue(user: User, issue: Issue, project: Project, roles: set[str]) -> bool:
    """People on an issue keep seeing it even when its project is members-only."""
    return can_view_project(user, project, roles) or involved(user, issue)


def hidden_project_ids(db: Session, user: User) -> set[int]:
    """Members-only projects this user has no role in."""
    if user.is_admin:
        return set()
    # Only id/lead/settings — avoid hydrating full Project rows on every search.
    rows = db.execute(select(Project.id, Project.lead_id, Project.settings)).all()
    private = {
        pid for pid, _lead, settings in rows
        if (settings or {}).get("visibility") == "members"
    }
    if not private:
        return set()
    mine = set(db.scalars(select(ProjectMember.project_id)
                          .where(ProjectMember.user_id == user.id)))
    return {
        pid for pid, lead_id, _settings in rows
        if pid in private and pid not in mine and lead_id != user.id
    }


def visible_issue_clause(db: Session, user: User | None) -> ColumnElement[bool] | None:
    """Filter for cross-project issue queries; None when the user can see everything."""
    if user is None:
        return None
    hidden = hidden_project_ids(db, user)
    if not hidden:
        return None
    return or_(
        Issue.project_id.notin_(hidden),
        Issue.reporter_id == user.id,
        Issue.assignee_id == user.id,
        Issue.tester_id == user.id,
        Issue.software_owner_id == user.id,
    )


def may_enter_verification(actor: User, project: Project, issue: Issue, roles: set[str]) -> bool:
    """Testing, test passed and test failed are moved by testers.

    Project admins keep the same override they have on the rest of the form.
    The person already named as tester can move the issue they were given.
    """
    if is_project_admin(actor, roles):
        return True
    if issue.tester_id is not None and actor.id == issue.tester_id:
        return True
    return bool(person_roles(profile_of(project), "tester_id") & roles)


def editable_sections(user: User, issue: Issue, roles: set[str]) -> set[str]:
    """Requirements split their form by profile section; other types are all-or-nothing."""
    profile = profile_of(issue.project)
    if is_project_admin(user, roles):
        return {GENERAL, *profile.section_keys}
    sections: set[str] = set()
    if roles or involved(user, issue):
        sections.add(GENERAL)
    if issue.type != "requirement":
        if GENERAL in sections:
            sections.update(profile.section_keys)
        return sections
    for section in profile.sections:
        if (section.role is not None and section.role in roles) or user.id in {
            getattr(issue, attr) for attr in section.issue_users
        }:
            sections.add(section.key)
    if scheme.applies(issue) and scheme.can_participate(user, issue, roles):
        sections.add("scheme")
    return sections


def forbidden_fields(user: User, issue: Issue, roles: set[str], fields: set[str]) -> list[str]:
    allowed = editable_sections(user, issue, roles)
    profile = profile_of(issue.project)
    expanded: set[str] = set()
    for field in fields:
        if field == "custom_fields":
            expanded.update(f"custom_fields.{key}" for key in (issue.extra or {}))
        else:
            expanded.add(field)
    denied = {f for f in expanded if profile.field_section(f) not in allowed}
    if scheme.applies(issue):
        for field in expanded:
            if field not in scheme.FIELDS:
                continue
            if scheme.can_edit_field(user, issue, roles, field):
                denied.discard(field)
            else:
                denied.add(field)
    return sorted(denied)
