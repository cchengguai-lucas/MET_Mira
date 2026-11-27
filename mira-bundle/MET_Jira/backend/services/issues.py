"""Issue lifecycle: creation, field updates, transitions, comments, activity and notifications."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import is_postgres, utcnow
from ..models import (
    ActivityLog,
    Comment,
    Issue,
    IssueMachine,
    Machine,
    MachineGroup,
    Notification,
    Project,
    Sprint,
    Status,
    User,
    Watcher,
)
from ..models.issue import ISSUE_TYPES, PRIORITIES
from . import permissions, profiles, reminders, sla, software, workflow

MENTION_RE = re.compile(r"@([A-Za-z0-9._-]{2,64})")
RANK_STEP = 1024.0

FIELD_LABELS = {
    "type": "类型",
    "title": "标题",
    "description": "描述",
    "status_id": "状态",
    "priority": "优先级",
    "assignee_id": "负责人",
    "tester_id": "测试人",
    "parent_id": "父任务",
    "epic_id": "史诗",
    "sprint_id": "冲刺",
    "labels": "标签",
    "due_date": "截止日期",
    "story_points": "故事点",
    "project_name": "项目名称",
    "machine_ids": "机台",
    "update_scope": "影响范围",
    "involves_software": "是否涉及软件",
    "software_owner_id": "软件负责人",
    "software_ready": "软件是否准备好",
    "software_note": "软件交付说明",
    "software_jira_key": "软件 Jira 单号",
    "scheme_note": "方案说明",
    "electrical_scheme_ok": "电气确认",
    "process_scheme_ok": "工艺确认",
    "software_scheme_ok": "软件确认",
    "scheme_locked": "发起方案确认",
    "scheme_review": "方案比对",
    "verify_method": "验证方法",
    "verify_criteria": "验证标准",
    "update_date": "更新日期",
    "test_passed": "是否通过",
    "remark": "备注",
}

SILENT_FIELDS = {"rank", "scheme_action"}
PERSON_FIELDS = ("assignee_id", "tester_id", "software_owner_id")
REF_FIELDS = frozenset({"status_id", "sprint_id", "parent_id", "epic_id", *PERSON_FIELDS})
SOFTWARE_OWNER_NEEDS_FLAG = "请先将「是否涉及软件」设为「是」再指定软件负责人"


class IssueError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str | None = None,
        fields: list[str] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.fields = list(fields or [])


def _fmt(db: Session, field: str, value: Any) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if field in PERSON_FIELDS:
        user = db.get(User, value)
        return user.display_name if user else str(value)
    if field == "status_id":
        status = db.get(Status, value)
        return status.name if status else str(value)
    if field == "sprint_id":
        sprint = db.get(Sprint, value)
        return sprint.name if sprint else str(value)
    if field in {"parent_id", "epic_id"}:
        issue = db.get(Issue, value)
        return issue.key if issue else str(value)
    if field == "machine_ids":
        machines = db.scalars(select(Machine).where(Machine.id.in_(list(value))))
        return ", ".join(f"{m.group.name}/{m.name}" for m in machines)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _ref(field: str | None, value: Any) -> int | None:
    if field in REF_FIELDS and isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def log(db: Session, issue: Issue, actor: User | None, action: str, *, field: str | None = None,
        old: Any = None, new: Any = None, meta: dict[str, Any] | None = None,
        formatted: bool = False) -> None:
    if formatted:
        old_value = old if old is None or isinstance(old, str) else str(old)
        new_value = new if new is None or isinstance(new, str) else str(new)
        old_ref = new_ref = None
    else:
        old_value = _fmt(db, field, old) if field else (str(old) if old is not None else None)
        new_value = _fmt(db, field, new) if field else (str(new) if new is not None else None)
        old_ref = _ref(field, old)
        new_ref = _ref(field, new)
    db.add(
        ActivityLog(
            issue_id=issue.id,
            actor_id=actor.id if actor else None,
            action=action,
            field=field,
            old_value=old_value,
            new_value=new_value,
            old_ref=old_ref,
            new_ref=new_ref,
            meta=meta or {},
        )
    )


def _fmt_custom(db: Session, kind: str | None, value: Any) -> str | None:
    """Format one custom-field value for the activity stream."""
    if value is None or value == "" or value == []:
        return None
    if kind == "user":
        uid = value if isinstance(value, int) and not isinstance(value, bool) else None
        user = db.get(User, uid) if uid is not None else None
        return user.display_name if user else str(value)
    if kind in {"bool", "boolean"} or isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        # Never dump whole blobs into history (e.g. leftover nested structures).
        parts = [f"{k}={v}" for k, v in value.items() if not str(k).startswith("_")]
        return ", ".join(parts) if parts else None
    return str(value)


def notify(db: Session, user_ids: Iterable[int | None], actor: User | None, issue: Issue | None,
           kind: str, message: str) -> None:
    from . import notify_prefs

    recipients = {u for u in user_ids if u and (actor is None or u != actor.id)}
    if not recipients:
        return
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(recipients)))}
    in_app: set[int] = set()
    for uid in recipients:
        user = users.get(uid)
        if user is None or not notify_prefs.wants_in_app(user):
            continue
        in_app.add(uid)
        db.add(Notification(user_id=uid, actor_id=actor.id if actor else None,
                            issue_id=issue.id if issue else None, kind=kind, message=message))
    reminders.queue(db, recipients, actor, issue, kind, message)


def watch(db: Session, issue_id: int, user_id: int | None) -> None:
    if not user_id:
        return
    db.flush()
    values = {"issue_id": issue_id, "user_id": user_id}
    if is_postgres(db):
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.execute(
        insert(Watcher)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["issue_id", "user_id"])
    )


def watcher_ids(db: Session, issue: Issue) -> set[int]:
    ids = set(db.scalars(select(Watcher.user_id).where(Watcher.issue_id == issue.id)))
    ids.update(u for u in (issue.assignee_id, issue.reporter_id, issue.tester_id,
                           issue.software_owner_id) if u)
    return ids


def _next_number(db: Session, project: Project) -> int:
    # Refresh the identity-mapped instance after acquiring the row lock. A
    # concurrent transaction may have incremented the sequence while this
    # session was waiting.
    stmt = (
        select(Project)
        .where(Project.id == project.id)
        .execution_options(populate_existing=True)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update()
    locked = db.scalars(stmt).one()
    locked.issue_seq = (locked.issue_seq or 0) + 1
    db.flush()
    return locked.issue_seq


def next_rank(db: Session, project_id: int) -> float:
    current = db.scalar(select(func.max(Issue.rank)).where(Issue.project_id == project_id))
    return float(current or 0.0) + RANK_STEP


def _validate(db: Session, project: Project, data: dict[str, Any],
              *, issue: Issue | None = None) -> None:
    if "type" in data and data["type"] not in ISSUE_TYPES:
        raise IssueError(f"未知的问题类型：{data['type']}")
    if "priority" in data and data["priority"] not in PRIORITIES:
        raise IssueError(f"未知的优先级：{data['priority']}")
    if "title" in data and not str(data["title"] or "").strip():
        raise IssueError("标题不能为空")
    if data.get("status_id") is not None:
        status = db.get(Status, data["status_id"])
        if status is None or status.project_id != project.id:
            raise IssueError("状态不属于该项目")
    for field in PERSON_FIELDS:
        if data.get(field) is not None and db.get(User, data[field]) is None:
            raise IssueError(f"{FIELD_LABELS[field]}不存在")
    if data.get("software_owner_id") is not None:
        owner = db.get(User, data["software_owner_id"])
        if owner is not None and not software.is_software_member(db, project, owner):
            raise IssueError("软件负责人必须是软件部门的成员")
    if data.get("sprint_id") is not None:
        sprint = db.get(Sprint, data["sprint_id"])
        if sprint is None or sprint.project_id != project.id or sprint.state == "closed":
            raise IssueError("冲刺不存在或已关闭")
    for field in ("parent_id", "epic_id"):
        if data.get(field) is not None:
            other = db.get(Issue, data[field])
            if other is None or other.project_id != project.id:
                raise IssueError(f"{FIELD_LABELS[field]}不存在")
            if field == "epic_id" and other.type != "epic":
                raise IssueError("关联的问题不是史诗")
    # Epics are containers — they must not nest under another epic via epic_id.
    final_type = data["type"] if "type" in data else (issue.type if issue else None)
    final_epic = data["epic_id"] if "epic_id" in data else (issue.epic_id if issue else None)
    if final_epic is not None and final_type == "epic":
        raise IssueError("史诗不能关联到另一史诗")
    if data.get("machine_ids"):
        valid = set(
            db.scalars(
                select(Machine.id)
                .join(MachineGroup)
                .where(MachineGroup.project_id == project.id, Machine.id.in_(data["machine_ids"]))
            )
        )
        missing = set(data["machine_ids"]) - valid
        if missing:
            raise IssueError("机台不属于该项目")


def _has_children(db: Session, issue_id: int) -> bool:
    linked = (Issue.parent_id == issue_id) | (Issue.epic_id == issue_id)
    return bool(db.scalar(select(func.count(Issue.id)).where(linked)))


def open_subtasks(db: Session, issue_id: int) -> list[Issue]:
    rows = db.scalars(select(Issue).join(Status, Status.id == Issue.status_id).where(
        Issue.parent_id == issue_id, Status.category != "done").order_by(Issue.rank))
    return list(rows)


def _apply_hierarchy(db: Session, project: Project, issue: Issue | None,
                     data: dict[str, Any]) -> dict[str, Any]:
    """Only sub-tasks have a parent and every sub-task has one; the tree is one level deep.
    Setting a parent turns an issue into a sub-task, clearing it (or changing the type)
    turns the sub-task back into a task."""
    data = dict(data)
    current_type = issue.type if issue else None
    current_parent = issue.parent_id if issue else None
    if data.get("parent_id") is not None:
        if "type" in data and data["type"] != "subtask":
            raise IssueError("只有子任务可以设置父任务")
        data["type"] = "subtask"
    elif "parent_id" in data and current_type == "subtask" and "type" not in data:
        data["type"] = "task"
    if data.get("type", "subtask") != "subtask" and current_parent and "parent_id" not in data:
        data["parent_id"] = None
    final_type = data.get("type", current_type)
    final_parent = data.get("parent_id", current_parent)
    if final_type == "subtask" and final_parent is None:
        raise IssueError("子任务必须有父任务")
    if final_parent is not None and (final_parent != current_parent or final_type != current_type):
        parent = db.get(Issue, final_parent)
        if parent is None or parent.project_id != project.id:
            raise IssueError("父任务不存在")
        if issue is not None and parent.id == issue.id:
            raise IssueError("不能把自己设为父任务")
        if parent.type == "subtask":
            raise IssueError("子任务下不能再建子任务")
        if parent.type == "epic":
            raise IssueError("史诗下的工作请通过「史诗」字段关联")
        if issue is not None and _has_children(db, issue.id):
            raise IssueError("已有子项的问题不能改为子任务")
    if (issue is not None and final_type == "epic" and current_type != "epic"
            and db.scalar(select(func.count(Issue.id)).where(Issue.parent_id == issue.id))):
        raise IssueError("有子任务的问题不能改为史诗")
    return data


def _inherit_from_parent(db: Session, data: dict[str, Any]) -> None:
    parent = db.get(Issue, data["parent_id"])
    if parent is None:
        return
    if "sprint_id" not in data and parent.sprint_id is not None:
        sprint = db.get(Sprint, parent.sprint_id)
        if sprint is not None and sprint.state != "closed":
            data["sprint_id"] = parent.sprint_id
    if not data.get("machine_ids"):
        data["machine_ids"] = [link.machine_id for link in parent.machine_links]
    if "due_date" not in data and parent.due_date is not None:
        data["due_date"] = parent.due_date


def _set_machines(db: Session, issue: Issue, machine_ids: list[int]) -> None:
    wanted = list(dict.fromkeys(machine_ids or []))
    current = {link.machine_id: link for link in issue.machine_links}
    for mid, link in list(current.items()):
        if mid not in wanted:
            issue.machine_links.remove(link)
    for mid in wanted:
        if mid not in current:
            issue.machine_links.append(IssueMachine(machine_id=mid))


def _require_assignee(project: Project, data: dict[str, Any], *, clearing: bool = False) -> None:
    """Enforce template-required assignee (AI and any profile that marks it required)."""
    profile = profiles.profile_of(project)
    if "assignee_id" not in profile.required_builtins:
        return
    if clearing or not data.get("assignee_id"):
        raise IssueError("请选择负责人", 400)


def create_issue(db: Session, project: Project, actor: User, data: dict[str, Any],
                 *, allow_status: bool = False) -> Issue:
    if project.archived:
        raise IssueError("归档项目不可创建问题", 403)
    if data.get("software_jira_snapshot") is not None:
        raise IssueError("软件 Jira 快照不能手工修改", 400)
    data = _apply_hierarchy(db, project, None, {k: v for k, v in data.items() if v is not None})
    data.setdefault("type", "requirement")
    if data.get("parent_id") is not None:
        _inherit_from_parent(db, data)
    if data.get("software_owner_id") and data.get("involves_software") is not True:
        raise IssueError(SOFTWARE_OWNER_NEEDS_FLAG)
    try:
        custom_fields = profiles.validate_custom_fields(
            profiles.profile_of(project), data.pop("custom_fields", {}) or {}
        )
    except ValueError as error:
        raise IssueError(str(error)) from error
    _require_assignee(project, data)
    _validate(db, project, data, issue=None)
    statuses = workflow.project_statuses(db, project.id)
    if not statuses:
        raise IssueError("项目没有配置状态")
    initial = workflow.initial_status(project, data["type"], statuses)
    # Normal creates always start at the type's initial status (workflow + gates apply later).
    # Ignore any client-supplied status_id so board-column "create" cannot trap users.
    # Import / trusted paths may pass allow_status=True to honour an explicit status_id.
    if allow_status and data.get("status_id") is not None:
        status_id = int(data["status_id"])
    else:
        status_id = initial.id
        data.pop("status_id", None)
    number = _next_number(db, project)
    issue = Issue(
        project_id=project.id,
        number=number,
        key=f"{project.key}-{number}",
        type=data["type"],
        title=str(data["title"]).strip(),
        description=data.get("description", ""),
        status_id=status_id,
        priority=data.get("priority", "medium"),
        reporter_id=data.get("reporter_id") or actor.id,
        assignee_id=data.get("assignee_id"),
        tester_id=data.get("tester_id"),
        parent_id=data.get("parent_id"),
        epic_id=data.get("epic_id"),
        sprint_id=data.get("sprint_id"),
        labels=list(data.get("labels") or []),
        due_date=data.get("due_date"),
        story_points=data.get("story_points"),
        rank=next_rank(db, project.id),
        project_name=data.get("project_name", ""),
        update_scope=list(data.get("update_scope") or []),
        involves_software=data.get("involves_software"),
        software_owner_id=data.get("software_owner_id"),
        software_ready=data.get("software_ready"),
        software_note=data.get("software_note") or "",
        software_jira_key=str(data.get("software_jira_key") or "")[:64],
        scheme_note=str(data.get("scheme_note") or ""),
        electrical_scheme_ok=data.get("electrical_scheme_ok"),
        process_scheme_ok=data.get("process_scheme_ok"),
        software_scheme_ok=data.get("software_scheme_ok"),
        scheme_locked=bool(data.get("scheme_locked") or False),
        scheme_confirmed_note=str(data.get("scheme_confirmed_note") or ""),
        verify_method=data.get("verify_method", ""),
        verify_criteria=data.get("verify_criteria", ""),
        update_date=data.get("update_date"),
        test_passed=data.get("test_passed"),
        remark=data.get("remark", ""),
        extra=custom_fields,
        import_batch_id=data.get("import_batch_id"),
        created_at=data.get("created_at") or utcnow(),
    )
    db.add(issue)
    db.flush()
    if (issue.software_jira_key or "").strip() and not issue.import_batch_id:
        from .software_jira import sync_snapshot

        sync_snapshot(issue)
    _set_machines(db, issue, data.get("machine_ids") or [])
    remember_labels(project, issue.labels or [])
    db.flush()
    db.refresh(issue)
    status = db.get(Status, issue.status_id)
    if status is not None:
        sla.sync(issue, project, status, issue.created_at)
        if status.category == "done":
            issue.resolved_at = utcnow()
    log(db, issue, actor, "created", new=issue.key,
        meta={"status_id": issue.status_id, "sprint_id": issue.sprint_id,
              "assignee_id": issue.assignee_id, "story_points": issue.story_points})
    for uid in (issue.reporter_id, issue.assignee_id, issue.tester_id, issue.software_owner_id):
        watch(db, issue.id, uid)
    if issue.assignee_id:
        notify(db, [issue.assignee_id], actor, issue, "assigned",
               f"{actor.display_name} 将 {issue.key}「{issue.title}」指派给你")
    if issue.tester_id:
        notify(db, [issue.tester_id], actor, issue, "tester",
               f"{actor.display_name} 指定你为 {issue.key} 的测试人")
    if issue.software_owner_id:
        _notify_software_owner(db, issue, actor)
    db.flush()
    from . import live

    live.queue(
        db,
        project_key=project.key,
        kind="issue",
        issue_key=issue.key,
        actor_id=actor.id,
        fields=["create"],
    )
    try:
        from .metrology_xp import notify_metrology_requirement

        reporter = db.get(User, issue.reporter_id) if issue.reporter_id else None
        notify_metrology_requirement(
            project=project,
            issue=issue,
            actor=actor,
            reporter=reporter,
        )
    except Exception:  # noqa: BLE001 — XP must never block issue create
        pass
    return issue


def remember_labels(project: Project, labels: Iterable[str]) -> None:
    """Grow the project's shared label catalog when new labels appear on issues."""
    incoming = [str(lab).strip() for lab in labels if str(lab).strip()]
    if not incoming:
        return
    settings = dict(project.settings or {})
    options = list(settings.get("label_options") or [])
    added = [lab for lab in incoming if lab not in options]
    if not added:
        return
    settings["label_options"] = options + added
    project.settings = settings


def collect_project_labels(db: Session, project: Project) -> list[str]:
    """Union of configured label_options and labels already used on project issues."""
    ordered: list[str] = []
    seen: set[str] = set()
    for lab in list((project.settings or {}).get("label_options") or []):
        name = str(lab).strip()
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    for raw in db.scalars(select(Issue.labels).where(Issue.project_id == project.id)):
        for lab in raw or []:
            name = str(lab).strip()
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


def _keeps_software_ready(project: Project, status: Status) -> bool:
    """Ready stays on the test path and on a successful completion. Rework clears it."""
    settings = project.settings or {}
    test_status = settings.get("test_status", "测试中")
    fail_status = settings.get("fail_return_status", workflow.TEST_FAILED)
    names = {
        name for name in (
            test_status, workflow.pass_status_name(project), fail_status,
            workflow.dev_complete_name(project),
        ) if name
    }
    return status.name in names or workflow.is_completed(status)


def _notify_software_owner(db: Session, issue: Issue, actor: User) -> None:
    notify(db, [issue.software_owner_id], actor, issue, "software_owner",
           f"{actor.display_name} 指定你为 {issue.key} 的软件负责人")


def _current_value(issue: Issue, field: str) -> Any:
    if field == "machine_ids":
        return sorted(link.machine_id for link in issue.machine_links)
    return getattr(issue, field)


def update_issue(
    db: Session,
    issue: Issue,
    actor: User,
    changes: dict[str, Any],
    *,
    enforce: bool = True,
    skip_approval_gate: bool = False,
    close_subtasks: bool = False,
    sync_jira: bool = True,
) -> list[str]:
    project = db.get(Project, issue.project_id)
    assert project is not None
    if project.archived and enforce:
        raise IssueError("归档项目不可修改问题", 403)
    if "software_jira_snapshot" in changes:
        raise IssueError("软件 Jira 快照不能手工修改", 400)
    scheme_action = changes.pop("scheme_action", None)
    if scheme_action not in {None, "submit", "unlock"}:
        raise IssueError("未知的方案操作")
    if "scheme_locked" in changes or "scheme_confirmed_note" in changes:
        raise IssueError("方案锁定状态不能手工修改", 400)
    roles = permissions.project_roles(db, project.id, actor)
    from . import scheme as scheme_flow

    scheme_before = scheme_flow.snapshot(issue)
    custom_input = changes.get("custom_fields")
    permission_fields = set(changes)
    if isinstance(custom_input, dict):
        permission_fields.remove("custom_fields")
        permission_fields.update(f"custom_fields.{key}" for key in custom_input)
    if enforce:
        denied = permissions.forbidden_fields(actor, issue, roles, permission_fields)
        if denied:
            labels = "、".join(FIELD_LABELS.get(f, f) for f in denied)
            raise IssueError(f"没有权限修改：{labels}", 403)
    if "assignee_id" in changes and changes.get("assignee_id") is None:
        _require_assignee(project, {}, clearing=True)
    involves = changes.get("involves_software", issue.involves_software)
    if changes.get("software_jira_key") is not None:
        changes = {**changes, "software_jira_key": str(changes["software_jira_key"]).strip()[:64]}
    if changes.get("software_owner_id") is not None and involves is not True:
        raise IssueError(SOFTWARE_OWNER_NEEDS_FLAG)
    if involves is not True and issue.software_owner_id and "software_owner_id" not in changes:
        changes = {**changes, "software_owner_id": None}
    if involves is not True and (
        issue.software_ready is not None or changes.get("software_ready") is not None
    ):
        changes = {**changes, "software_ready": None}
    if "scheme_note" in changes and changes["scheme_note"] is not None:
        changes["scheme_note"] = str(changes["scheme_note"])
    if enforce:
        scheme_flow.validate_edits(issue, actor, roles, changes)
    if "parent_id" in changes or "type" in changes:
        changes = _apply_hierarchy(db, project, issue, changes)
    if isinstance(custom_input, dict):
        try:
            changes["custom_fields"] = {
                **(issue.extra or {}),
                **profiles.validate_custom_fields(profiles.profile_of(project), custom_input),
            }
        except ValueError as error:
            raise IssueError(str(error)) from error
    reopen_reason = changes.pop("reopen_reason", None)
    if isinstance(reopen_reason, str):
        reopen_reason = reopen_reason.strip() or None
    elif reopen_reason is not None:
        reopen_reason = str(reopen_reason).strip() or None

    _validate(db, project, changes, issue=issue)
    statuses = workflow.project_statuses(db, project.id)
    if enforce and "test_passed" in changes and changes["test_passed"] != issue.test_passed:
        if not workflow.test_result_editable(project, issue, statuses):
            raise IssueError(f"「{issue.status.name}」状态下不能修改测试结果", 409)
        result_target = workflow.test_result_target(
            project, issue, changes["test_passed"], statuses,
        )
        if (result_target is not None and result_target.id != issue.status_id
                and not permissions.may_enter_verification(actor, project, issue, roles)):
            raise IssueError(f"只有测试人员可以将问题流转到「{result_target.name}」", 403)

    transition_meta: dict[str, Any] = {}
    if "status_id" in changes and changes["status_id"] != issue.status_id:
        target = db.get(Status, changes["status_id"])
        if target is None:
            raise IssueError("状态不存在")
        # skip_approval_gate: still enforce workflow + field gates; only skip the
        # "needs approval" raise (used after a completed countersign).
        check = enforce or skip_approval_gate
        if check and not workflow.can_transition(project, issue, target, statuses):
            raise IssueError(f"工作流不允许从「{issue.status.name}」流转到「{target.name}」", 409)
        if (check and workflow.requires_assignee_accept(project, issue.status, target)
                and not (permissions.is_project_admin(actor, roles)
                         or issue.assignee_id == actor.id)):
            raise IssueError(
                f"只有负责人可以将「{issue.status.name}」流转为「{target.name}」", 403,
            )
        if (enforce and workflow.is_verification_status(project, target)
                and not permissions.may_enter_verification(actor, project, issue, roles)):
            raise IssueError(f"只有测试人员可以将问题流转到「{target.name}」", 403)
        if check:
            from . import approvals

            approvals.check_fields(issue, target, changes, statuses=statuses)
            needs_approval = (
                enforce and not skip_approval_gate
                and approvals.approval_requirements(issue, target)
            )
            if needs_approval:
                raise IssueError(f"进入「{target.name}」需要审批", 409, "approval_required")
        if (target.name == workflow.REOPENED
                and (issue.status is None or issue.status.name != workflow.REOPENED)):
            if enforce and not reopen_reason:
                raise IssueError("重新打开前请填写原因", 409, "reopen_reason")
            if reopen_reason:
                transition_meta["reopen_reason"] = reopen_reason
        if workflow.is_completed(target):
            _finish_subtasks(db, project, issue, actor, statuses, close_subtasks)

    previous_status = issue.status
    changed: list[str] = []
    custom_kinds = {
        f.key: f.kind for f in profiles.profile_of(project).custom_fields
    }
    for field, value in changes.items():
        if field == "title":
            value = str(value or "").strip()
        if field == "machine_ids":
            value = sorted(set(value or []))
        model_field = "extra" if field == "custom_fields" else field
        old = _current_value(issue, model_field)
        if old == value:
            continue
        if field == "machine_ids":
            _set_machines(db, issue, value)
        else:
            setattr(issue, model_field, value)
        changed.append(field)
        if field in SILENT_FIELDS:
            continue
        if field == "custom_fields":
            # Log each changed key — never the whole extra dict (unreadable in History).
            old_extra = old if isinstance(old, dict) else {}
            new_extra = value if isinstance(value, dict) else {}
            for key in sorted(set(old_extra) | set(new_extra)):
                if key.startswith("_"):
                    continue
                ov, nv = old_extra.get(key), new_extra.get(key)
                if ov == nv:
                    continue
                kind = custom_kinds.get(key)
                log(
                    db, issue, actor, "updated",
                    field=f"custom_fields.{key}",
                    old=_fmt_custom(db, kind, ov),
                    new=_fmt_custom(db, kind, nv),
                    formatted=True,
                )
            continue
        log(
            db, issue, actor, "transition" if field == "status_id" else "updated",
            field=field, old=old, new=value,
            meta=transition_meta if field == "status_id" and transition_meta else None,
        )

    if sync_jira and "software_jira_key" in changed:
        from .software_jira import sync_snapshot

        sync_snapshot(issue)

    scheme_flow.remember_signers(issue, actor, changed)
    if "labels" in changed:
        remember_labels(project, list(issue.labels or []))

    if "test_passed" in changed:
        db.flush()
        db.refresh(issue)
        target = workflow.test_result_target(project, issue, issue.test_passed, statuses)
        if target is not None and target.id != issue.status_id:
            old_status = issue.status_id
            issue.status_id = target.id
            changed.append("status_id")
            log(db, issue, actor, "transition", field="status_id", old=old_status, new=target.id,
                meta={"auto": True, "reason": "test_result"})
        if "update_date" not in changes and issue.test_passed is not None:
            issue.update_date = date.today()
    elif "status_id" in changed:
        new_status = next(s for s in statuses if s.id == issue.status_id)
        result = workflow.test_result_after_move(project, previous_status, new_status,
                                                 issue.test_passed)
        if result != issue.test_passed:
            log(db, issue, actor, "updated", field="test_passed", old=issue.test_passed,
                new=result, meta={"auto": True, "reason": "status_change"})
            issue.test_passed = result
            changed.append("test_passed")
        if result is not None and "update_date" not in changes:
            issue.update_date = date.today()
        from .software_jira import mark_software_wait

        if issue.involves_software is not True or new_status.name != "研发中":
            mark_software_wait(issue, False)
        if not _keeps_software_ready(project, new_status) and issue.software_ready is not None:
            log(db, issue, actor, "updated", field="software_ready", old=issue.software_ready,
                new=None, meta={"auto": True, "reason": "status_change"})
            issue.software_ready = None
            changed.append("software_ready")
        scheme_flow.on_status(db, issue, actor, new_status.name)
        from sqlalchemy import update

        from ..models import ApprovalRequest

        db.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.issue_id == issue.id, ApprovalRequest.status == "pending")
            .values(status="cancelled", finished_at=utcnow())
        )
    if "priority" in changed or "status_id" in changed:
        sla.sync(issue, project, next(s for s in statuses if s.id == issue.status_id))

    if issue.involves_software is not True:
        from .software_jira import mark_software_wait

        mark_software_wait(issue, False)
    if issue.involves_software is not True and issue.software_scheme_ok is not None:
        log(
            db, issue, actor, "updated", field="software_scheme_ok",
            old=issue.software_scheme_ok, new=None,
            meta={"auto": True, "reason": "involves_software"},
        )
        issue.software_scheme_ok = None
        issue.software_scheme_by_id = None
        changed.append("software_scheme_ok")
    review_kind = None
    if scheme_action and enforce:
        review_kind = scheme_flow.apply_action(db, issue, actor, roles, scheme_action)
        changed.append("scheme_action")
    if changed:
        status_name = next((item.name for item in statuses if item.id == issue.status_id), None)
        scheme_flow.notify_after(
            db, issue, actor, scheme_before, review_kind=review_kind, status_name=status_name,
        )

    from .software_jira import remind_due_conflict

    remind_due_conflict(db, issue)
    if not changed:
        return changed
    issue.lock_version += 1
    issue.updated_at = utcnow()
    db.flush()
    db.refresh(issue)
    if "status_id" in changed:
        issue.resolved_at = utcnow() if issue.status.category == "done" else None
        if issue.type == "subtask" and issue.status.category == "doing":
            _start_parent(db, project, issue, actor, statuses)
    if "sprint_id" in changed and issue.type != "subtask":
        for child in open_subtasks(db, issue.id):
            if child.sprint_id != issue.sprint_id:
                log(db, child, actor, "updated", field="sprint_id", old=child.sprint_id,
                    new=issue.sprint_id, meta={"auto": True, "reason": "parent_sprint"})
                child.sprint_id = issue.sprint_id
                child.lock_version += 1
    if "assignee_id" in changed and issue.assignee_id:
        watch(db, issue.id, issue.assignee_id)
        notify(db, [issue.assignee_id], actor, issue, "assigned",
               f"{actor.display_name} 将 {issue.key}「{issue.title}」指派给你")
    if "tester_id" in changed and issue.tester_id:
        watch(db, issue.id, issue.tester_id)
        notify(db, [issue.tester_id], actor, issue, "tester",
               f"{actor.display_name} 指定你为 {issue.key} 的测试人")
    if "software_owner_id" in changed and issue.software_owner_id:
        watch(db, issue.id, issue.software_owner_id)
        _notify_software_owner(db, issue, actor)
    if "software_ready" in changed and issue.software_ready is True and issue.assignee_id:
        notify(db, [issue.assignee_id], actor, issue, "software_ready",
               f"{actor.display_name} 确认 {issue.key} 的软件已准备好，可以安排进入测试")
    visible = [f for f in changed if f not in SILENT_FIELDS]
    if visible:
        notified: set[int | None] = {issue.assignee_id if "assignee_id" in changed else 0}
        if "status_id" in visible:
            message = f"{actor.display_name} 将 {issue.key} 流转为「{issue.status.name}」"
            settings = project.settings or {}
            developers = [issue.assignee_id, issue.software_owner_id]
            profile_key = profiles.profile_of(project).key
            if workflow.is_dev_complete(project, issue.status) and issue.tester_id:
                notify(
                    db, [issue.tester_id], actor, issue, "ready_for_test",
                    f"{issue.key}「{issue.title}」开发已完成，请检查验证资料并进入测试",
                )
                notified.add(issue.tester_id)
            if issue.status.name == settings.get("test_status") and issue.tester_id:
                notify(db, [issue.tester_id], actor, issue, "to_test",
                       f"{issue.key}「{issue.title}」已进入测试，请安排验证")
                notified.add(issue.tester_id)
            elif workflow.is_pass_status(project, issue.status) and issue.assignee_id:
                notify(
                    db, [issue.assignee_id], actor, issue, "ready_to_close",
                    f"{issue.key}「{issue.title}」测试已通过，请确认并流转为已完成或已驳回",
                )
                notified.add(issue.assignee_id)
            elif (previous_status is not None
                  and (previous_status.name == settings.get("test_status")
                       or "test_passed" in changes)
                  and workflow.is_fail_status(project, issue.status)):
                notify(db, developers, actor, issue, "test_failed",
                       f"{issue.key}「{issue.title}」测试未通过，请确认后重新打开处理")
                notified.update(developers)
            elif issue.status.name == workflow.REOPENED:
                reopen_msg = (
                    f"{actor.display_name} 重新打开了 {issue.key}「{issue.title}」：{reopen_reason}"
                    if reopen_reason else
                    f"{actor.display_name} 重新打开了 {issue.key}「{issue.title}」"
                )
                notify(db, developers, actor, issue, "reopened", reopen_msg)
                notified.update(developers)
            quality_messages = {
                ("mechanical", "试装"): "已进入试装，请安排试装确认",
                ("mechanical", "终检"): "已进入终检，请完成终检",
                ("production", "质检"): "已进入质检，请进行质量确认",
            }
            quality_message = quality_messages.get((profile_key, issue.status.name))
            if quality_message and issue.tester_id:
                notify(
                    db, [issue.tester_id], actor, issue, "ready_for_quality",
                    f"{issue.key}「{issue.title}」{quality_message}",
                )
                notified.add(issue.tester_id)
        else:
            labels = "、".join(FIELD_LABELS.get(f, f) for f in visible[:4])
            message = f"{actor.display_name} 更新了 {issue.key} 的{labels}"
        notify(db, watcher_ids(db, issue) - notified, actor, issue, "updated", message)
    db.flush()
    if changed:
        from . import live

        live.queue(
            db,
            project_key=project.key,
            kind="issue",
            issue_key=issue.key,
            actor_id=actor.id,
            fields=list(changed),
        )
    if "status_id" in changed:
        try:
            from .metrology_xp import notify_metrology_requirement

            reporter = db.get(User, issue.reporter_id) if issue.reporter_id else None
            notify_metrology_requirement(
                project=project,
                issue=issue,
                actor=actor,
                reporter=reporter,
                previous_status=previous_status,
            )
        except Exception:  # noqa: BLE001 — XP must never block issue update
            pass
    return changed


def _finish_subtasks(db: Session, project: Project, issue: Issue, actor: User,
                     statuses: list[Status], close: bool) -> None:
    pending = open_subtasks(db, issue.id)
    if not pending:
        return
    if not close:
        keys = "、".join(c.key for c in pending[:5]) + ("…" if len(pending) > 5 else "")
        raise IssueError(f"还有 {len(pending)} 个子任务未完成：{keys}", 409, code="open_subtasks")
    done = workflow.completion_status(project, "subtask", statuses)
    if done is None:
        raise IssueError("子任务工作流没有完成状态")
    for child in pending:
        update_issue(db, child, actor, {"status_id": done.id}, enforce=False)


def _start_parent(db: Session, project: Project, issue: Issue, actor: User,
                  statuses: list[Status]) -> None:
    """Starting a sub-task starts its parent too, when the parent's flow allows that step."""
    parent = db.get(Issue, issue.parent_id) if issue.parent_id else None
    if parent is None or parent.status.category != "todo":
        return
    targets = workflow.allowed_targets(project, parent.type, parent.status, statuses)
    target = next((s for s in targets if s.id == issue.status_id), None)
    if target is None:
        return
    log(db, parent, actor, "transition", field="status_id", old=parent.status_id, new=target.id,
        meta={"auto": True, "reason": "subtask_started", "subtask": issue.key})
    parent.status_id = target.id
    parent.lock_version += 1
    parent.updated_at = utcnow()
    sla.sync(parent, project, target)


def add_comment(db: Session, issue: Issue, actor: User, body: str) -> Comment:
    body = (body or "").strip()
    if not body:
        raise IssueError("评论不能为空")
    names = set(MENTION_RE.findall(body))
    mentioned: list[User] = []
    if names:
        mentioned = list(db.scalars(select(User).where(User.username.in_(names))))
    comment = Comment(issue_id=issue.id, author_id=actor.id, body=body,
                      mentions=[u.id for u in mentioned])
    db.add(comment)
    db.flush()
    log(db, issue, actor, "comment", meta={"comment_id": comment.id})
    watch(db, issue.id, actor.id)
    notify(db, [u.id for u in mentioned], actor, issue, "mention",
           f"{actor.display_name} 在 {issue.key} 中提到了你")
    notify(db, watcher_ids(db, issue) - {u.id for u in mentioned}, actor, issue, "comment",
           f"{actor.display_name} 评论了 {issue.key}「{issue.title}」")
    issue.updated_at = utcnow()
    db.flush()
    return comment


def clone_issue(
    db: Session,
    source: Issue,
    actor: User,
    *,
    include_assignee: bool = False,
    include_sprint: bool = False,
    relate: bool = True,
) -> Issue:
    """Create a new issue copying fields from ``source``; always starts at initial status."""
    project = db.get(Project, source.project_id)
    assert project is not None
    title = source.title
    suffix = "（副本）"
    if not title.endswith(suffix):
        title = f"{title}{suffix}"
    # Copy only template-defined custom fields. Internal markers in ``extra``
    # (e.g. ``_demo``, ``_template_orphans``) must not go through create validation.
    profile = profiles.profile_of(project)
    known_custom = {field.key for field in profile.custom_fields}
    data: dict[str, Any] = {
        "type": source.type if source.type != "subtask" else "task",
        "title": title[:500],
        "description": source.description or "",
        "priority": source.priority,
        "labels": list(source.labels or []),
        "due_date": source.due_date,
        "story_points": source.story_points,
        "project_name": source.project_name or "",
        "machine_ids": [row.machine_id for row in source.machine_links],
        "update_scope": list(source.update_scope or []),
        "involves_software": source.involves_software,
        "verify_method": source.verify_method or "",
        "verify_criteria": source.verify_criteria or "",
        "remark": source.remark or "",
        "custom_fields": {
            key: value for key, value in (source.extra or {}).items() if key in known_custom
        },
    }
    if source.type == "subtask" and source.parent_id:
        data["type"] = "subtask"
        data["parent_id"] = source.parent_id
    elif source.epic_id:
        data["epic_id"] = source.epic_id
    if include_assignee:
        data["assignee_id"] = source.assignee_id
        data["tester_id"] = source.tester_id
        data["software_owner_id"] = source.software_owner_id
    if include_sprint and source.sprint_id:
        data["sprint_id"] = source.sprint_id
    clone = create_issue(db, project, actor, data)
    if relate:
        from ..models import IssueLink

        db.add(IssueLink(source_id=clone.id, target_id=source.id, link_type="relates",
                         created_by_id=actor.id))
        log(db, clone, actor, "link", new=f"关联 {source.key}")
        log(db, source, actor, "link", new=f"关联 {clone.key}")
    log(db, clone, actor, "cloned", new=source.key)
    db.flush()
    return clone
