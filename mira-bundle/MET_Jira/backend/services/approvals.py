"""Required-field gates and auditable role/count based transition approvals."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import ApprovalRequest, Issue, ProjectMember, Status, User
from . import profiles, templates, workflow
from .issues import IssueError, notify


def gate_for(issue: Issue, target: Status) -> dict[str, Any]:
    gates = (issue.project.settings or {}).get("transition_gates") or {}
    return dict(gates.get(target.name) or {})


def value_of(issue: Issue, field: str, pending: dict[str, Any] | None = None) -> Any:
    """The field's value, preferring ``pending`` changes saved in the same update."""
    pending = pending or {}
    if field.startswith("custom_fields."):
        key = field.split(".", 1)[1]
        staged = pending.get("custom_fields")
        if isinstance(staged, dict) and key in staged:
            return staged[key]
        return (issue.extra or {}).get(key)
    if field in pending:
        return pending[field]
    if field == "machine_ids":
        return [link.machine_id for link in issue.machine_links]
    if hasattr(issue, field):
        return getattr(issue, field)
    definition = templates.field_def(profiles.profile_of(issue.project), field)
    return templates.get_value(issue, definition) if definition else None


def missing_fields(issue: Issue, target: Status,
                   pending: dict[str, Any] | None = None,
                   statuses: list[Status] | None = None) -> list[str]:
    gate = gate_for(issue, target)
    required = list(gate.get("required_fields") or [])
    # Profile fields marked required become gates when leaving the type's initial status.
    profile = profiles.profile_of(issue.project)
    if statuses is None:
        from sqlalchemy.orm import object_session
        session = object_session(issue)
        statuses = (
            workflow.project_statuses(session, issue.project_id)
            if session is not None
            else list(getattr(issue.project, "statuses", None) or [])
        )
    initial = workflow.initial_status(issue.project, issue.type, statuses) if statuses else None
    if initial is not None and issue.status_id == initial.id and target.id != initial.id:
        required.extend(f"custom_fields.{f.key}" for f in profile.custom_fields if f.required)
        required.extend(profile.required_builtins)
    # Skip fields that this issue type cannot edit (sections filtered by issue_types).
    required = [field for field in required
                if profile.field_applies_to_type(field, issue.type)]
    return sorted({field for field in required
                   if value_of(issue, field, pending) in (None, "", [])})


def approval_requirements(issue: Issue, target: Status) -> list[dict[str, Any]]:
    rows = gate_for(issue, target).get("approvals") or []
    return [
        {"role": str(row.get("role") or "admin"), "count": max(1, int(row.get("count") or 1))}
        for row in rows
    ]


_GENERAL_LABELS = {
    "type": "类型", "status_id": "状态", "priority": "优先级", "assignee_id": "负责人",
    "parent_id": "父任务", "epic_id": "史诗", "sprint_id": "冲刺", "labels": "标签",
    "due_date": "截止日期", "story_points": "故事点",
}


def _forward_status_name(issue: Issue) -> str | None:
    """The workflow's next step: the first target listed for the current status."""
    rules = workflow.transition_rules(issue.project, issue.type) or {}
    current = issue.status.name if issue.status is not None else ""
    targets = list(rules.get(current) or [])
    return str(targets[0]) if targets else None


def _software_jira_key_required(issue: Issue, pending: dict[str, Any] | None) -> bool:
    """Involved software work needs a Jira key before the forward step.

    The field must be on this issue type's form. Going back or onto a branch
    stays available, so an empty key cannot trap the issue.
    """
    profile = profiles.profile_of(issue.project)
    if not profiles.form_has_field(profile, "software_jira_key", issue.type):
        return False
    if value_of(issue, "involves_software", pending) is not True:
        return False
    return not str(value_of(issue, "software_jira_key", pending) or "").strip()


def _check_development_complete(issue: Issue, pending: dict[str, Any] | None) -> None:
    """Software work stays in 研发中 until the other Jira reports a finished status."""
    if value_of(issue, "involves_software", pending) is not True:
        return
    from .software_jira import mark_software_wait, snapshot_is_complete, wait_message

    if snapshot_is_complete(issue.software_jira_snapshot):
        return
    mark_software_wait(issue, True)
    gaps = test_entry_gaps(issue, pending, ready=False)
    raise IssueError(wait_message(False, gaps, issue), 409, "software_pending", fields=gaps)


def field_label(issue: Issue, field: str) -> str:
    key = field.removeprefix("custom_fields.")
    definition = templates.field_def(profiles.profile_of(issue.project), key)
    label = ((definition or {}).get("labels") or {}).get("zh")
    builtin = profiles.BUILTIN_FIELD_LABELS.get(key, {}).get("zh")
    return str(label or builtin or _GENERAL_LABELS.get(key) or key)


def _tester_editable(issue: Issue) -> bool:
    """Whether the UI can assign a tester for this issue type.

    Requirement forms put ``tester_id`` in a profile section; other types that use
    the test status get a sidebar Prop in IssueDetail. Skip the hard gate when
    neither surface exists so types cannot get stuck with nowhere to fill it.
    """
    profile = profiles.profile_of(issue.project)
    if profile.field_applies_to_type("tester_id", issue.type):
        return True
    return issue.type != "requirement"


def test_entry_gaps(
    issue: Issue, pending: dict[str, Any] | None = None, *, ready: bool = True,
) -> list[str]:
    """Fields still empty before the test status. ``ready`` also requires software_ready."""
    profile = profiles.profile_of(issue.project)
    extra: list[str] = []
    if value_of(issue, "tester_id", pending) is None and _tester_editable(issue):
        extra.append("tester_id")
    if profiles.form_has_field(profile, "verify_method", issue.type):
        if not str(value_of(issue, "verify_method", pending) or "").strip():
            extra.append("verify_method")
    if profiles.form_has_field(profile, "verify_criteria", issue.type):
        if not str(value_of(issue, "verify_criteria", pending) or "").strip():
            extra.append("verify_criteria")
    if (
        profiles.form_has_field(profile, "software_ready", issue.type)
        and value_of(issue, "involves_software", pending) is True
    ):
        if value_of(issue, "software_owner_id", pending) is None:
            extra.append("software_owner_id")
        if ready and value_of(issue, "software_ready", pending) is not True:
            extra.append("software_ready")
    return extra


def check_fields(issue: Issue, target: Status, pending: dict[str, Any] | None = None,
                 statuses: list[Status] | None = None) -> None:
    profile = profiles.profile_of(issue.project)
    test_status = (issue.project.settings or {}).get("test_status", "测试中")
    extra: list[str] = []
    needs_jira_key = (
        _software_jira_key_required(issue, pending)
        and target.name == _forward_status_name(issue)
    )
    if needs_jira_key:
        extra.append("software_jira_key")
    elif target.name == workflow.dev_complete_name(issue.project):
        _check_development_complete(issue, pending)
    if target.name == test_status:
        extra.extend(test_entry_gaps(issue, pending))
    if (
        target.name == "研发中"
        and profiles.form_has_field(profile, "scheme_note", issue.type)
    ):
        if not str(value_of(issue, "scheme_note", pending) or "").strip():
            extra.append("scheme_note")
        if value_of(issue, "scheme_locked", pending) is not True:
            extra.append("scheme_locked")
        if value_of(issue, "electrical_scheme_ok", pending) is not True:
            extra.append("electrical_scheme_ok")
        if value_of(issue, "process_scheme_ok", pending) is not True:
            extra.append("process_scheme_ok")
        if value_of(issue, "involves_software", pending) is True:
            if value_of(issue, "software_owner_id", pending) is None:
                extra.append("software_owner_id")
            if value_of(issue, "software_scheme_ok", pending) is not True:
                extra.append("software_scheme_ok")
    missing = sorted(set(extra) | set(missing_fields(issue, target, pending, statuses)))
    if missing:
        names = ", ".join(field_label(issue, field) for field in missing)
        raise IssueError(
            f"进入「{target.name}」前请填写：{names}",
            409,
            "transition_gate",
            fields=missing,
        )


def role_holders(db: Session, issue: Issue, requirements: list[dict[str, Any]]) -> set[int]:
    """Users who hold a required project role (no admin fallback)."""
    roles = {req["role"] for req in requirements}
    ids = set(db.scalars(select(ProjectMember.user_id).where(
        ProjectMember.project_id == issue.project_id, ProjectMember.role.in_(roles)
    )))
    if "admin" in roles and issue.project.lead_id:
        ids.add(issue.project.lead_id)
    return ids


def request(
    db: Session, issue: Issue, target: Status, actor: User, comment: str = ""
) -> ApprovalRequest:
    statuses = workflow.project_statuses(db, issue.project_id)
    if not workflow.can_transition(issue.project, issue, target, statuses):
        raise IssueError(f"工作流不允许从「{issue.status.name}」流转到「{target.name}」", 409)
    from .permissions import may_enter_verification, project_roles

    roles = project_roles(db, issue.project_id, actor)
    if (workflow.is_verification_status(issue.project, target)
            and not may_enter_verification(actor, issue.project, issue, roles)):
        raise IssueError(f"只有测试人员可以将问题流转到「{target.name}」", 403)
    check_fields(issue, target, statuses=statuses)
    requirements = approval_requirements(issue, target)
    if not requirements:
        raise IssueError("该状态不需要审批，可直接流转", 409)
    holders = role_holders(db, issue, requirements)
    if not holders:
        roles = "、".join(sorted({req["role"] for req in requirements}))
        raise IssueError(
            f"项目中尚无「{roles}」角色成员，无法发起会签", 409, "approval_no_approvers"
        )
    existing = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.issue_id == issue.id,
            ApprovalRequest.target_status_id == target.id,
            ApprovalRequest.status == "pending",
        )
    )
    if existing:
        return existing
    row = ApprovalRequest(
        issue_id=issue.id,
        target_status_id=target.id,
        requested_by_id=actor.id,
        requirements=requirements,
        decisions=[],
        comment=comment,
    )
    db.add(row)
    db.flush()
    notify(db, holders, actor, issue, "approval_requested",
           f"{actor.display_name} 请你审批 {issue.key}「{issue.title}」流转到「{target.name}」")
    return row


def approver_ids(db: Session, issue: Issue, requirements: list[dict[str, Any]]) -> set[int]:
    ids = role_holders(db, issue, requirements)
    if not ids:
        # Legacy callers / notifications: fall back to system admins only for display.
        ids = set(db.scalars(select(User.id).where(User.is_admin.is_(True),
                                                   User.enabled.is_(True))))
    return ids


def actor_roles(db: Session, issue: Issue, actor: User) -> set[str]:
    roles = set(db.scalars(select(ProjectMember.role).where(
        ProjectMember.project_id == issue.project_id, ProjectMember.user_id == actor.id
    )))
    if actor.is_admin or issue.project.lead_id == actor.id:
        roles.add("admin")
    return roles


def decide(
    db: Session, row: ApprovalRequest, issue: Issue, actor: User, approve: bool, comment: str
) -> bool:
    if row.status != "pending":
        raise IssueError("该审批已结束", 409)
    decisions = list(row.decisions or [])
    if any(int(item["user_id"]) == actor.id for item in decisions):
        raise IssueError("你已经处理过该审批", 409)
    roles = actor_roles(db, issue, actor)
    eligible = {req["role"] for req in row.requirements} & roles
    if not eligible and not actor.is_admin:
        raise IssueError("你不是该审批要求的角色", 403)
    # System admins without a matching project role credit every still-unsatisfied
    # requirement so a fallback vote can complete the countersign instead of deadlocking.
    if not eligible and actor.is_admin:
        satisfied = {
            req["role"] for req in row.requirements
            if sum(1 for item in decisions if item["decision"] == "approved"
                   and req["role"] in item["roles"]) >= req["count"]
        }
        remaining = {req["role"] for req in row.requirements if req["role"] not in satisfied}
        eligible = remaining or {"admin"}
    decisions.append({
        "user_id": actor.id,
        "name": actor.display_name,
        "roles": sorted(eligible),
        "decision": "approved" if approve else "rejected",
        "comment": comment,
        "at": utcnow().isoformat(),
    })
    row.decisions = decisions
    row.lock_version += 1
    target = db.get(Status, row.target_status_id)
    target_name = target.name if target else ""
    if not approve:
        row.status = "rejected"
        row.finished_at = utcnow()
        reason = f"：{comment}" if comment else ""
        notify(db, [row.requested_by_id], actor, issue, "approval_result",
               f"{actor.display_name} 驳回了 {issue.key}「{issue.title}」流转到「{target_name}」"
               f"的审批{reason}")
        return False
    complete = all(
        sum(1 for item in decisions if item["decision"] == "approved"
            and requirement["role"] in item["roles"]) >= requirement["count"]
        for requirement in row.requirements
    )
    if complete:
        row.status = "approved"
        row.finished_at = utcnow()
        notify(db, [row.requested_by_id], actor, issue, "approval_result",
               f"{issue.key}「{issue.title}」流转到「{target_name}」的审批已通过")
    return complete


def out(row: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "lock_version": row.lock_version,
        "issue_id": row.issue_id,
        "target_status_id": row.target_status_id,
        "status": row.status,
        "requirements": row.requirements or [],
        "decisions": row.decisions or [],
        "comment": row.comment,
        "requested_by_id": row.requested_by_id,
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }
