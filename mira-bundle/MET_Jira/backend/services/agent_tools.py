"""Tools exposed to the LLM agent. Reads run immediately; writes become pending actions."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import session_factory, utcnow
from ..models import (
    AgentAction,
    Comment,
    Issue,
    Machine,
    MachineGroup,
    MasterAsset,
    OrgUnit,
    Project,
    ProjectMember,
    Sprint,
    Status,
    User,
)
from ..models.issue import ISSUE_TYPES, PRIORITIES
from ..serialize import issue_query
from . import (
    help_kb,
    permissions,
    profiles,
    reports,
    schedule_cascade,
    workflow,
    workflow_audit,
)
from .issues import IssueError, add_comment, create_issue, update_issue
from .search import PRIORITY_ALIASES, TYPE_ALIASES, JqlError, search_issues

WRITE_TOOLS = {
    "create_issue",
    "update_issue",
    "transition_issue",
    "assign_issue",
    "add_comment",
    "apply_workflow",
}
# Confirm-before-run tools that are not issue writes (navigation, etc.).
CONFIRM_TOOLS = WRITE_TOOLS | {"open_page"}


@dataclass(frozen=True)
class NavDestination:
    """In-app place the agent can offer to jump to. Add entries here to extend."""

    id: str
    label: str
    # Path with optional ``{project}`` placeholder for the current/requested project key.
    path: str
    aliases: tuple[str, ...] = ()
    needs_project: bool = False


# Stable catalogue — ids are what the LLM should prefer; aliases catch natural language.
NAV_DESTINATIONS: tuple[NavDestination, ...] = (
    NavDestination("home", "首页", "/", ("首页", "主页", "仪表盘", "我的工作", "home")),
    NavDestination("projects", "项目列表", "/projects", ("项目列表", "全部项目", "projects")),
    NavDestination("search", "搜索", "/search", ("搜索", "高级搜索", "JQL", "search")),
    NavDestination("shared_filters", "共享筛选器", "/search/shared",
                    ("共享筛选", "共享过滤器", "筛选器库")),
    NavDestination("org", "组织架构", "/org", ("组织架构", "组织", "部门", "org")),
    NavDestination("guide", "使用说明", "/guide", ("使用说明", "帮助", "指南", "guide", "帮助文档")),
    NavDestination("guide.glossary", "名词表", "/guide/glossary", ("名词表", "术语", "glossary")),
    NavDestination("admin", "管理后台", "/admin", ("管理后台", "系统管理", "admin", "模板管理")),
    NavDestination("settings", "个人设置", "/settings", ("个人设置", "账号设置", "我的设置")),
    NavDestination("settings.notifications", "通知偏好", "/settings/notifications",
                    ("通知设置", "通知偏好", "邮件通知", "企业微信通知")),
    NavDestination("project.board", "项目看板", "/projects/{project}/board",
                    ("看板", "看板视图", "board"), needs_project=True),
    NavDestination("project.backlog", "待办事项", "/projects/{project}/backlog",
                    ("待办", "待办事项", "backlog", "冲刺待办"), needs_project=True),
    NavDestination("project.list", "列表视图", "/projects/{project}/list",
                    ("列表", "表格", "list"), needs_project=True),
    NavDestination("project.summary", "项目摘要", "/projects/{project}/summary",
                    ("摘要", "概览", "summary"), needs_project=True),
    NavDestination("project.timeline", "时间线", "/projects/{project}/timeline",
                    ("时间线", "timeline", "甘特"), needs_project=True),
    NavDestination("project.reports", "项目报表", "/projects/{project}/reports",
                    ("报表", "报告", "reports", "统计报表"), needs_project=True),
    NavDestination("project.settings", "项目设置", "/projects/{project}/settings",
                    ("项目设置", "设置"), needs_project=True),
    NavDestination("project.settings.general", "项目设置 · 基本信息",
                    "/projects/{project}/settings?section=general",
                    ("基本信息", "项目信息", "封面", "项目图标"), needs_project=True),
    NavDestination("project.settings.members", "项目设置 · 成员与角色",
                    "/projects/{project}/settings?section=members",
                    ("成员", "角色", "加人", "权限", "成员与角色"), needs_project=True),
    NavDestination("project.settings.workflow", "项目设置 · 状态与工作流",
                    "/projects/{project}/settings?section=workflow",
                    ("工作流", "状态", "门禁", "会签", "审批", "审批人", "流程审批", "流转",
                     "SLA", "测试闭环", "状态与工作流", "transition", "状态门禁", "会签审批"),
                    needs_project=True),
    NavDestination("project.settings.machines", "项目设置 · 机台",
                    "/projects/{project}/settings?section=machines",
                    ("机台", "机台组", "machines"), needs_project=True),
    NavDestination("project.settings.fields", "项目设置 · 字段选项",
                    "/projects/{project}/settings?section=fields",
                    ("字段", "标签选项", "影响范围选项", "字段选项"), needs_project=True),
    NavDestination("project.settings.boards", "项目设置 · 看板配置",
                    "/projects/{project}/settings?section=boards",
                    ("看板配置", "看板列", "WIP", "快速筛选", "泳道"), needs_project=True),
    NavDestination("project.settings.import", "项目设置 · Excel 导入",
                    "/projects/{project}/settings?section=import",
                    ("导入", "Excel", "导入需求"), needs_project=True),
)


@dataclass
class AgentContext:
    db: Session | None
    user: User | None
    project_key: str | None = None
    actor_id: int | None = None
    allowed_tools: frozenset[str] | None = None
    navigation_allowed: bool = False


# ---------------------------------------------------------------- resolution helpers


def resolve_project(db: Session, user: User, key: str | None,
                    fallback: str | None = None) -> Project:
    hidden = permissions.hidden_project_ids(db, user)
    for candidate in (key, fallback):
        if not candidate:
            continue
        project = db.scalar(
            select(Project).where(
                or_(Project.key == str(candidate).upper(), Project.name == candidate)
            )
        )
        if project is not None and project.id not in hidden:
            return project
    raise IssueError("请明确指定项目 Key；当前问题没有唯一的项目上下文")


def resolve_user(db: Session, name: str | None) -> User | None:
    if not name:
        return None
    name = str(name).strip().lstrip("@")
    user = db.scalar(select(User).where(User.username == name))
    if user is None:
        user = db.scalar(select(User).where(User.display_name == name, User.enabled.is_(True)))
    if user is None:
        candidates = list(
            db.scalars(
                select(User).where(
                    User.enabled.is_(True),
                    or_(User.display_name.ilike(f"%{name}%"), User.username.ilike(f"%{name}%")),
                ).limit(2)
            )
        )
        if len(candidates) == 1:
            user = candidates[0]
    if user is None:
        raise IssueError(f"找不到用户：{name}")
    return user


def resolve_machines(db: Session, project: Project, names: list[str] | None) -> list[int]:
    ids: list[int] = []
    if not names:
        return ids
    machines = list(
        db.scalars(
            select(Machine)
            .options(selectinload(Machine.group))
            .join(MachineGroup)
            .where(MachineGroup.project_id == project.id)
        )
    )
    for raw in names:
        text = str(raw).strip()
        if not text:
            continue
        if "/" in text:
            group, name = (part.strip() for part in text.split("/", 1))
            hits = [m for m in machines if m.group.name.lower() == group.lower()
                    and m.name.lower() == name.lower()]
        else:
            hits = [m for m in machines if m.group.name.lower() == text.lower()]
            if hits:
                whole = [m for m in hits if m.name in {"整机", "All", "all"}]
                hits = whole[:1] or hits[:1]
            else:
                hits = [m for m in machines if m.name.lower() == text.lower()][:1]
        if not hits:
            hits = [m for m in machines if text.lower() in f"{m.group.name}/{m.name}".lower()][:1]
        if not hits:
            raise IssueError(f"找不到机台：{text}")
        ids.extend(m.id for m in hits)
    return list(dict.fromkeys(ids))


def resolve_status(db: Session, project: Project, name: str) -> Status:
    status = db.scalar(
        select(Status).where(Status.project_id == project.id, Status.name == str(name).strip())
    )
    if status is None:
        names = db.scalars(select(Status.name).where(Status.project_id == project.id))
        raise IssueError(f"状态「{name}」不存在，可选：{'、'.join(names)}")
    return status


def resolve_issue(db: Session, key: str, user: User | None = None) -> Issue:
    issue = db.scalars(issue_query().where(Issue.key == str(key).strip().upper())).first()
    if issue is None or (user is not None and not permissions.can_view_issue(
            user, issue, issue.project, permissions.project_roles(db, issue.project_id, user))):
        raise IssueError(f"问题不存在：{key}")
    return issue


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise IssueError(f"日期格式错误：{value}") from error


def _as_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "是", "通过", "y"}


def fields_from_args(db: Session, project: Project, args: dict[str, Any]) -> dict[str, Any]:
    """Translate agent-friendly arguments (names) into service fields (ids)."""
    args = {
        key: value for key, value in args.items()
        if key not in {
            "software_ready", "software_jira_snapshot",
            "scheme_note", "electrical_scheme_ok", "process_scheme_ok",
            "software_scheme_ok", "scheme_action", "scheme_locked", "scheme_confirmed_note",
        }
    }
    out: dict[str, Any] = {}
    if "type" in args and args["type"]:
        mapped = TYPE_ALIASES.get(str(args["type"]).lower(), str(args["type"]).lower())
        if mapped not in ISSUE_TYPES:
            raise IssueError(f"未知类型：{args['type']}")
        out["type"] = mapped
    if "priority" in args and args["priority"]:
        mapped = PRIORITY_ALIASES.get(str(args["priority"]).lower(), str(args["priority"]).lower())
        if mapped not in PRIORITIES:
            raise IssueError(f"未知优先级：{args['priority']}")
        out["priority"] = mapped
    for field in ("title", "description", "project_name", "verify_method", "verify_criteria",
                  "remark", "software_note", "software_jira_key"):
        if field in args and args[field] is not None:
            out[field] = str(args[field])
    for field, target in (("assignee", "assignee_id"), ("tester", "tester_id"),
                          ("software_owner", "software_owner_id")):
        if field in args:
            user = resolve_user(db, args[field]) if args[field] else None
            out[target] = user.id if user else None
    if "machines" in args and args["machines"] is not None:
        out["machine_ids"] = resolve_machines(db, project, list(args["machines"]))
    if "update_scope" in args and args["update_scope"] is not None:
        out["update_scope"] = [str(s) for s in args["update_scope"]]
    if "labels" in args and args["labels"] is not None:
        out["labels"] = [str(s) for s in args["labels"]]
    for field in ("involves_software", "test_passed"):
        if field in args:
            out[field] = _as_bool(args[field])
    for field in ("due_date", "update_date"):
        if field in args:
            out[field] = _as_date(args[field])
    if "story_points" in args and args["story_points"] not in (None, ""):
        out["story_points"] = float(args["story_points"])
    if "custom_fields" in args and isinstance(args["custom_fields"], dict):
        out["custom_fields"] = profiles.validate_custom_fields(
            profiles.profile_of(project), args["custom_fields"]
        )
    for field, target in (("epic_key", "epic_id"), ("parent_key", "parent_id")):
        if args.get(field):
            out[target] = resolve_issue(db, args[field]).id
    if args.get("status"):
        out["status_id"] = resolve_status(db, project, args["status"]).id
    if args.get("sprint"):
        sprint = db.scalar(
            select(Sprint).where(Sprint.project_id == project.id,
                                 Sprint.name == str(args["sprint"]))
        )
        if sprint is None:
            raise IssueError(f"冲刺不存在：{args['sprint']}")
        out["sprint_id"] = sprint.id
    return out


# ---------------------------------------------------------------- compact views


def compact(issue: Issue) -> dict[str, Any]:
    return {
        "key": issue.key,
        "title": issue.title,
        "type": issue.type,
        "status": issue.status.name if issue.status else None,
        "priority": issue.priority,
        "assignee": issue.assignee.display_name if issue.assignee else None,
        "tester": issue.tester.display_name if issue.tester else None,
        "reporter": issue.reporter.display_name if issue.reporter else None,
        "machines": [f"{m.group.name}/{m.name}" for m in issue.machines],
        "update_scope": issue.update_scope or [],
        "custom_fields": issue.extra or {},
        "test_passed": issue.test_passed,
        "due_date": issue.due_date.isoformat() if issue.due_date else None,
        "updated_at": issue.updated_at.strftime("%Y-%m-%d %H:%M") if issue.updated_at else None,
    }


def detail(db: Session, issue: Issue) -> dict[str, Any]:
    comments = list(
        db.scalars(
            select(Comment).options(selectinload(Comment.author))
            .where(Comment.issue_id == issue.id).order_by(Comment.id.desc()).limit(10)
        )
    )
    return {
        **compact(issue),
        "description": (issue.description or "")[:2500],
        "project_name": issue.project_name,
        "involves_software": issue.involves_software,
        "software_owner": issue.software_owner.display_name if issue.software_owner else None,
        "software_ready": issue.software_ready,
        "scheme_note": issue.scheme_note or "",
        "scheme_locked": bool(issue.scheme_locked),
        "electrical_scheme_ok": issue.electrical_scheme_ok,
        "process_scheme_ok": issue.process_scheme_ok,
        "software_scheme_ok": issue.software_scheme_ok,
        "software_note": issue.software_note or "",
        "software_jira_key": issue.software_jira_key or "",
        "software_jira": issue.software_jira_snapshot,
        "verify_method": issue.verify_method,
        "verify_criteria": issue.verify_criteria,
        "update_date": issue.update_date.isoformat() if issue.update_date else None,
        "remark": issue.remark,
        "epic": issue.epic.key if issue.epic else None,
        "created_at": issue.created_at.strftime("%Y-%m-%d"),
        "comments": [
            {"author": c.author.display_name if c.author else None, "body": c.body[:800],
             "at": c.created_at.strftime("%Y-%m-%d %H:%M")}
            for c in reversed(comments)
        ],
    }


# ---------------------------------------------------------------- read tools


def t_search_issues(ctx: AgentContext, jql: str, limit: int = 20) -> dict[str, Any]:
    try:
        total, rows = search_issues(ctx.db, jql, user_id=ctx.user.id, limit=min(int(limit), 50))
    except JqlError as error:
        return {"error": f"JQL 语法错误：{error}"}
    return {"total": total, "issues": [compact(i) for i in rows]}


def t_get_issue(ctx: AgentContext, key: str) -> dict[str, Any]:
    return detail(ctx.db, resolve_issue(ctx.db, key, ctx.user))


def t_find_users(ctx: AgentContext, query: str = "", org: str = "",
                 limit: int = 20) -> dict[str, Any]:
    stmt = select(User).options(selectinload(User.org_unit)).where(User.enabled.is_(True))
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(User.display_name.ilike(like), User.username.ilike(like),
                              User.title.ilike(like)))
    if org:
        units = select(OrgUnit.id).where(OrgUnit.path.ilike(f"%{org}%"))
        stmt = stmt.where(User.org_unit_id.in_(units))
    users = ctx.db.scalars(stmt.limit(min(int(limit), 50)))
    return {
        "users": [
            {"username": u.username, "name": u.display_name, "title": u.title,
             "org": u.org_unit.path if u.org_unit else None}
            for u in users
        ]
    }


def t_list_org_units(ctx: AgentContext, query: str = "") -> dict[str, Any]:
    stmt = select(OrgUnit).where(OrgUnit.removed.is_(False), OrgUnit.hidden.is_(False))
    if query:
        stmt = stmt.where(OrgUnit.path.ilike(f"%{query}%"))
    units = ctx.db.scalars(stmt.order_by(OrgUnit.path).limit(60))
    return {"units": [{"id": u.id, "path": u.path} for u in units]}


def t_project_meta(
    ctx: AgentContext,
    project: str = "",
    section: str = "",
) -> dict[str, Any]:
    proj = resolve_project(ctx.db, ctx.user, project, ctx.project_key)
    profile = profiles.profile_of(proj)
    statuses = ctx.db.scalars(
        select(Status).where(Status.project_id == proj.id).order_by(Status.sort_order)
    )
    groups = ctx.db.scalars(
        select(MachineGroup).options(selectinload(MachineGroup.machines))
        .where(MachineGroup.project_id == proj.id).order_by(MachineGroup.sort_order)
    )
    members = ctx.db.scalars(
        select(ProjectMember).options(selectinload(ProjectMember.user))
        .where(ProjectMember.project_id == proj.id)
    )
    sprints = ctx.db.scalars(
        select(Sprint).where(Sprint.project_id == proj.id, Sprint.state != "closed")
    )
    master_kinds = {field.master_kind for field in profile.custom_fields if field.master_kind}
    assets = list(ctx.db.scalars(select(MasterAsset).where(
        MasterAsset.kind.in_(master_kinds), MasterAsset.active.is_(True)
    ))) if master_kinds else []
    settings = proj.settings or {}
    layout = profiles.layout(profile)
    layout.pop("definition", None)
    version = proj.template_version
    result = {
        "project": {"key": proj.key, "name": proj.name,
                    "lead": proj.lead.display_name if proj.lead else None,
                    "visibility": settings.get("visibility", "public"),
                    "archived": proj.archived},
        "template": {"key": profile.key, "name": profile.label,
                     "version": version.version if version else None},
        "profile": layout,
        "statuses": [{"name": s.name, "category": s.category} for s in statuses],
        "workflows": workflow.effective_transitions(proj),
        "customized_workflows": workflow.customized_types(proj),
        "test_flow": {
            **{k: settings.get(k) for k in ("test_status", "pass_status", "fail_return_status")},
            "software_gate": (
                "需求进入测试状态前，需已指定测试人，并填写验证方法和验证标准。"
                "涉及软件时，还需软件负责人确认软件已准备好。"
                "对方 Jira 的经办人、测试人、状态和截止日期只是进度，不代表 Mira 里已经准备好。"
            ) if profiles.form_has_field(profile, "software_ready", "requirement") else None,
            "scheme_gate": (
                "电气需求进入研发中前，要先填写方案说明并发起方案确认。"
                "电气组、工艺工程师，以及涉及软件时的软件负责人，都要确认为是。"
                "助手不能代写确认，也不能代为发起或解锁。"
            ) if profiles.form_has_field(profile, "scheme_note", "requirement") else None,
        },
        "transition_gates": settings.get("transition_gates") or {},
        "sla_hours": settings.get("sla_hours"),
        "sla_warning_hours": settings.get("sla_warning_hours"),
        "sla_calendar": settings.get("sla_calendar") or "natural",
        "sla_business_hours": settings.get("sla_business_hours"),
        "sla_holidays": settings.get("sla_holidays") or [],
        "sla_extra_workdays": settings.get("sla_extra_workdays") or [],
        "sla_paused_statuses": settings.get("sla_paused_statuses") or [],
        "software_units": settings.get("software_units") or [],
        "machines": {g.name: [m.name for m in g.machines if m.active] for g in groups},
        "update_scope_options": settings.get("update_scope_options", []),
        "label_options": settings.get("label_options", []),
        "master_data": {
            kind: [{"id": asset.id, "code": asset.code, "name": asset.name}
                   for asset in assets if asset.kind == kind]
            for kind in sorted(master_kinds)
        },
        "members": [{"username": m.user.username, "name": m.user.display_name, "role": m.role}
                    for m in members],
        "sprints": [{"name": s.name, "state": s.state} for s in sprints],
    }
    if section:
        allowed = {"project", "template", section}
        return {key: value for key, value in result.items() if key in allowed}
    return result


def _workflow_context(
    ctx: AgentContext,
    project: str,
    issue_type: str,
) -> tuple[Project, list[Status], dict[str, list[str]]]:
    proj = resolve_project(ctx.db, ctx.user, project, ctx.project_key)
    if issue_type not in ISSUE_TYPES:
        raise IssueError(f"未知的问题类型：{issue_type}")
    statuses = list(
        ctx.db.scalars(
            select(Status).where(Status.project_id == proj.id).order_by(Status.sort_order)
        )
    )
    return proj, statuses, dict(workflow.transition_rules(proj, issue_type) or {})


def _workflow_issue_counts(
    ctx: AgentContext,
    project: Project,
    issue_type: str,
    statuses: list[Status],
) -> dict[str, int]:
    by_id = {status.id: status.name for status in statuses}
    rows = ctx.db.execute(
        select(Issue.status_id, func.count())
        .where(Issue.project_id == project.id, Issue.type == issue_type)
        .group_by(Issue.status_id)
    ).all()
    return {by_id[status_id]: int(count) for status_id, count in rows if status_id in by_id}


def _edges(rules: dict[str, list[str]]) -> set[tuple[str, str]]:
    return {(source, target) for source, targets in rules.items() for target in targets}


def t_analyze_workflow(
    ctx: AgentContext,
    project: str = "",
    issue_type: str = "requirement",
) -> dict[str, Any]:
    """Read-only audit of one effective project workflow."""
    proj, statuses, rules = _workflow_context(ctx, project, issue_type)
    findings = workflow_audit.lint_flow(
        [(status.name, status.category) for status in statuses], rules, issue_type
    )
    return {
        "project": proj.key,
        "issue_type": issue_type,
        "customized": issue_type in workflow.customized_types(proj),
        "statuses": [{"name": status.name, "category": status.category} for status in statuses],
        "transitions": rules,
        "findings": [
            {"level": finding.level, "message": finding.message} for finding in findings
        ],
        "issues_by_status": _workflow_issue_counts(ctx, proj, issue_type, statuses),
    }


def t_suggest_workflow(
    ctx: AgentContext,
    project: str = "",
    issue_type: str = "requirement",
    transitions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Validate an LLM-authored draft and return a reviewable diff without writing."""
    proj, statuses, current = _workflow_context(ctx, project, issue_type)
    draft = transitions or {}
    pairs = [(status.name, status.category) for status in statuses]
    findings = workflow_audit.lint_flow(pairs, draft, issue_type)
    current_edges = _edges(current)
    draft_edges = _edges(draft)
    used = set(draft) | {target for targets in draft.values() for target in targets}
    counts = _workflow_issue_counts(ctx, proj, issue_type, statuses)
    outside = {name: count for name, count in counts.items() if count and name not in used}
    return {
        "project": proj.key,
        "issue_type": issue_type,
        "valid": not any(finding.level == "error" for finding in findings),
        "transitions": draft,
        "diff": {
            "add": [
                {"from": source, "to": target}
                for source, target in sorted(draft_edges - current_edges)
            ],
            "remove": [
                {"from": source, "to": target}
                for source, target in sorted(current_edges - draft_edges)
            ],
        },
        "findings": [
            {"level": finding.level, "message": finding.message} for finding in findings
        ],
        "affected_issue_count": sum(counts.values()),
        "issues_outside_flow": outside,
        "note": (
            "这是草稿，尚未修改项目。仅 valid=true 时才可调用 apply_workflow；"
            "apply_workflow 仍需用户在界面明确确认。"
        ),
    }


def suggest_pages(text: str, *, limit: int = 3, min_score: int = 8) -> list[dict[str, Any]]:
    """Rank catalogue pages against free text. Used to never miss a jump offer."""
    raw = re.sub(r"\s+", "", (text or "").lower())
    if not raw:
        return []
    scored: list[tuple[int, NavDestination]] = []
    for dest in NAV_DESTINATIONS:
        score = 0
        for alias in (dest.id, dest.label, *dest.aliases):
            token = alias.lower().replace(" ", "")
            if not token or len(token) < 2:
                continue
            if token == raw:
                score = max(score, 1000 + len(token))
            elif token in raw:
                # Longer / more specific aliases win (e.g. 审批人 > 审批).
                score = max(score, 40 + len(token) * 3)
            elif raw in token and len(raw) >= 2:
                score = max(score, 12 + len(raw))
        # Bonus when several aliases hit the same question.
        hits = sum(1 for a in dest.aliases if a.lower().replace(" ", "") in raw and len(a) >= 2)
        if hits:
            score += hits * 6
        if score >= min_score:
            scored.append((score, dest))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for score, dest in scored:
        if dest.id in seen:
            continue
        seen.add(dest.id)
        out.append({
            "id": dest.id,
            "label": dest.label,
            "path_template": dest.path,
            "needs_project": dest.needs_project,
            "score": score,
        })
        if len(out) >= limit:
            break
    return out


def nav_intent(text: str) -> bool:
    """True only when the user explicitly wants to go somewhere or find the page entry.

    Conceptual questions (what / whether / how it works) must not trigger navigation,
    even if they mention 「设置」or a feature name.
    """
    raw = (text or "").lower()
    cues = (
        "跳转", "带我", "带我去", "打开页面", "打开设置", "打开项目", "打开看板",
        "打开", "去看", "去一下", "带我到",
        "去哪", "到哪", "在哪", "哪里", "哪儿", "入口在", "入口是",
        "哪个页面", "哪一页", "点哪", "从哪进", "怎么进",
        "take me", "open the", "go to", "where is", "where do i", "show me the page",
        "navigate",
    )
    return any(c in raw for c in cues)


def t_product_help(ctx: AgentContext, query: str = "", topic: str = "") -> dict[str, Any]:
    found = [s for s in help_kb.sections() if topic and s.title == topic.strip()]
    found += [s for s in help_kb.lookup(query or topic, limit=4) if s not in found]
    pages = suggest_pages(f"{query} {topic}", limit=3, min_score=8)
    if not found:
        return {"sections": [], "topics": help_kb.topics(), "suggested_pages": pages,
                "note": "使用说明中没有找到相关内容，请从 topics 中选择小节标题重试，不要臆测功能。"
                        + ("仅当用户明确要求跳转/打开页面时，才对 suggested_pages 调用 open_page；"
                           "否则直接回答问题即可。" if pages else "")}
    return {
        "sections": [s.text for s in found[:4]],
        "suggested_pages": pages,
        "note": ("suggested_pages 是可选的界面入口。仅当用户明确说「跳转 / 打开 / 带我去 / 在哪」时"
                 "才调用 open_page；普通概念或规则问题请直接据 sections 如实回答，并可用文字写出路径，"
                 "不要主动弹出跳转。") if pages else None,
    }


def t_project_stats(ctx: AgentContext, project: str = "") -> dict[str, Any]:
    proj = resolve_project(ctx.db, ctx.user, project, ctx.project_key)
    data = reports.summary(ctx.db, proj, days=14)
    data.pop("trend", None)
    return {"project": proj.key, **data}


def t_get_issues(ctx: AgentContext, keys: list[str]) -> dict[str, Any]:
    unique = list(dict.fromkeys(str(key).strip().upper() for key in keys if key))[:50]
    rows = []
    errors = []
    for key in unique:
        try:
            rows.append(detail(ctx.db, resolve_issue(ctx.db, key, ctx.user)))
        except IssueError as error:
            errors.append({"key": key, "error": error.message})
    return {"issues": rows, "errors": errors}


def t_my_work(ctx: AgentContext, limit: int = 30) -> dict[str, Any]:
    total, rows = search_issues(
        ctx.db,
        "assignee = currentUser() AND category != done ORDER BY due ASC",
        user_id=ctx.user.id,
        limit=min(int(limit), 50),
    )
    return {"total": total, "issues": [compact(issue) for issue in rows]}


def t_portfolio_overview(ctx: AgentContext, scope: str = "managed") -> dict[str, Any]:
    data = reports.portfolio_overview(ctx.db, ctx.user, scope)
    return {
        "scope": data["scope"],
        "totals": data["totals"],
        "projects": [
            {
                "key": project["key"],
                "name": project["name"],
                "risk_rank": project["risk_rank"],
                "target": project["target"],
                "risk_events": project["risk_events"],
                "impacted_gates": project["impacted_gates"],
            }
            for project in data["projects"][:20]
        ],
        "actions": data["actions"][:20],
        "generated_at": data["generated_at"],
    }


def t_schedule_analysis(ctx: AgentContext, project: str = "") -> dict[str, Any]:
    proj = resolve_project(ctx.db, ctx.user, project, ctx.project_key)
    analysis = schedule_cascade.suggest_cascade(ctx.db, proj)
    analysis["work_mode"] = permissions.work_mode(proj)
    return analysis


def t_project_risk_snapshot(ctx: AgentContext, project: str = "") -> dict[str, Any]:
    analysis = t_schedule_analysis(ctx, project)
    return {
        "project_key": analysis["project_key"],
        "work_mode": analysis.get("work_mode") or "delivery",
        "baseline_version": analysis["baseline_version"],
        "target_date": analysis["target_date"],
        "forecast_target_date": analysis["forecast_target_date"],
        "target_impact_days": analysis["target_impact_days"],
        "confidence": analysis["confidence"],
        "delay_sources": analysis["delay_sources"][:10],
        "impacted_gates": analysis["impacted_gates"][:10],
        "top_decisions": analysis["suggestions"][:10],
        "data_quality": analysis["data_quality"],
    }


# ---------------------------------------------------------------- write tools (proposals)


def match_nav(destination: str) -> NavDestination:
    """Pick the best catalogue entry for a free-text destination (id or alias)."""
    raw = str(destination or "").strip()
    if not raw:
        raise IssueError("请说明要打开的页面，例如「工作流设置」或 destination=project.settings.workflow")
    needle = raw.lower().replace(" ", "")
    by_id = {d.id.lower(): d for d in NAV_DESTINATIONS}
    if needle in by_id:
        return by_id[needle]
    scored: list[tuple[int, NavDestination]] = []
    for dest in NAV_DESTINATIONS:
        for alias in (dest.id, dest.label, *dest.aliases):
            token = alias.lower().replace(" ", "")
            if not token:
                continue
            if needle == token:
                scored.append((1000 + len(token), dest))
            elif token in needle or needle in token:
                scored.append((len(token), dest))
    if not scored:
        known = "、".join(d.id for d in NAV_DESTINATIONS)
        raise IssueError(f"找不到页面「{raw}」。可用 destination id：{known}")
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def resolve_nav(destination: str, *, project_key: str | None = None,
                issue_key: str | None = None) -> dict[str, str]:
    """Map a free-text destination (id, alias, or phrase) to a concrete in-app path."""
    if issue_key:
        key = str(issue_key).strip().upper()
        return {"id": "issue", "label": f"问题 {key}", "path": f"/issues/{key}"}
    dest = match_nav(destination)
    path = dest.path
    if dest.needs_project:
        key = (project_key or "").strip().upper()
        if not key:
            raise IssueError(f"打开「{dest.label}」需要指定 project（项目 Key）")
        path = path.replace("{project}", key)
    return {"id": dest.id, "label": dest.label, "path": path}


def _describe(tool: str, args: dict[str, Any]) -> str:
    if tool == "create_issue":
        return f"在 {args.get('project') or '默认项目'} 创建{args.get('type') or '需求'}「{args.get('title')}」"
    if tool == "update_issue":
        fields = "、".join((args.get("fields") or {}).keys())
        return f"更新 {args.get('key')} 的 {fields}"
    if tool == "transition_issue":
        return f"将 {args.get('key')} 流转为「{args.get('status')}」"
    if tool == "assign_issue":
        return f"将 {args.get('key')} 指派给 {args.get('assignee') or '无人'}"
    if tool == "add_comment":
        return f"在 {args.get('key')} 添加评论"
    if tool == "apply_workflow":
        return (
            f"应用 {str(args.get('project') or '当前项目').upper()} "
            f"{args.get('issue_type') or 'requirement'} 的工作流草稿"
        )
    if tool == "open_page":
        if args.get("issue_key"):
            return f"跳转到问题 {str(args['issue_key']).upper()}"
        try:
            nav = resolve_nav(str(args.get("destination") or ""),
                              project_key=args.get("project"),
                              issue_key=args.get("issue_key"))
            return f"跳转到「{nav['label']}」"
        except IssueError:
            return f"跳转到「{args.get('destination') or '页面'}」"
    return tool


def prepare(ctx: AgentContext, tool: str, args: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    """Resolve arguments now (to surface errors to the LLM) and return an executor."""
    db, user = ctx.db, ctx.user
    if tool == "open_page":
        project_key = args.get("project") or ctx.project_key
        if not args.get("issue_key"):
            dest = match_nav(str(args.get("destination") or ""))
            if dest.needs_project:
                project_key = resolve_project(db, user, project_key, ctx.project_key).key
        nav = resolve_nav(str(args.get("destination") or ""),
                          project_key=str(project_key) if project_key else None,
                          issue_key=args.get("issue_key"))

        def run_nav() -> dict[str, Any]:
            return {"navigate": True, **nav}

        return run_nav
    if tool == "create_issue":
        project = resolve_project(db, user, args.get("project"), ctx.project_key)
        fields = fields_from_args(db, project, args)
        if not fields.get("title"):
            raise IssueError("缺少标题 title")

        def run() -> dict[str, Any]:
            issue = create_issue(db, project, user, fields)
            return {"key": issue.key, "title": issue.title}

        return run
    if tool == "apply_workflow":
        project = resolve_project(db, user, args.get("project"), ctx.project_key)
        roles = permissions.project_roles(db, project.id, user)
        if not permissions.is_project_admin(user, roles):
            raise IssueError("需要项目管理员权限才能修改工作流")
        issue_type = str(args.get("issue_type") or "")
        if issue_type not in ISSUE_TYPES:
            raise IssueError(f"未知的问题类型：{issue_type}")
        raw = args.get("transitions")
        statuses = list(
            db.scalars(
                select(Status)
                .where(Status.project_id == project.id)
                .order_by(Status.sort_order)
            )
        )
        try:
            normalized = workflow_audit.validate_transitions(
                [(status.name, status.category) for status in statuses],
                {issue_type: raw},
            )[issue_type]
        except ValueError as error:
            raise IssueError(str(error)) from error

        def run_workflow() -> dict[str, Any]:
            stored = dict((project.settings or {}).get("transitions") or {})
            if normalized:
                stored[issue_type] = normalized
            else:
                stored.pop(issue_type, None)
            project.settings = {**(project.settings or {}), "transitions": stored}
            db.commit()
            return {
                "project": project.key,
                "issue_type": issue_type,
                "transitions": dict(workflow.transition_rules(project, issue_type) or {}),
                "path": f"/projects/{project.key}/settings?section=workflow",
            }

        return run_workflow
    issue = resolve_issue(db, args.get("key", ""), user)
    project = db.get(Project, issue.project_id)
    assert project is not None
    if tool == "update_issue":
        fields = fields_from_args(db, project, dict(args.get("fields") or {}))
    elif tool == "transition_issue":
        target = resolve_status(db, project, args.get("status", ""))
        from . import approvals
        approvals.check_fields(
            issue, target, statuses=workflow.project_statuses(db, project.id),
        )
        fields = {"status_id": target.id}
        reason = str(args.get("reopen_reason") or "").strip()
        if reason:
            fields["reopen_reason"] = reason
    elif tool == "assign_issue":
        assignee = resolve_user(db, args.get("assignee")) if args.get("assignee") else None
        fields = {"assignee_id": assignee.id if assignee else None}
    elif tool == "add_comment":
        body = str(args.get("body") or "").strip()
        if not body:
            raise IssueError("评论内容为空")

        def run_comment() -> dict[str, Any]:
            add_comment(db, issue, user, body)
            return {"key": issue.key}

        return run_comment
    else:
        raise IssueError(f"未知工具：{tool}")
    permission_fields = set(fields)
    if isinstance(fields.get("custom_fields"), dict):
        permission_fields.remove("custom_fields")
        permission_fields.update(f"custom_fields.{key}" for key in fields["custom_fields"])
    denied = permissions.forbidden_fields(user, issue,
                                          permissions.project_roles(db, project.id, user),
                                          permission_fields)
    if denied:
        raise IssueError(f"当前用户没有权限修改：{', '.join(denied)}")

    def run_update() -> dict[str, Any]:
        changed = update_issue(db, issue, user, fields)
        return {"key": issue.key, "changed": changed}

    return run_update


def propose(ctx: AgentContext, tool: str, args: dict[str, Any]) -> AgentAction:
    prepare(ctx, tool, args)
    action = AgentAction(user_id=ctx.user.id, tool=tool, args=args,
                         summary=_describe(tool, args), status="pending")
    ctx.db.add(action)
    ctx.db.commit()
    return action


def execute_action(ctx: AgentContext, action: AgentAction) -> dict[str, Any]:
    if action.status != "pending":
        raise IssueError("该操作已处理")
    runner = prepare(ctx, action.tool, dict(action.args or {}))
    result = runner()
    action.status = "done"
    action.result = result
    action.decided_at = utcnow()
    ctx.db.commit()
    return result


# ---------------------------------------------------------------- registry

_ISSUE_FIELD_PROPS: dict[str, Any] = {
    "type": {"type": "string", "enum": list(ISSUE_TYPES),
             "description": "requirement=需求, bug, task, story, epic, subtask"},
    "title": {"type": "string"},
    "description": {"type": "string", "description": "Markdown 描述 / 需求说明"},
    "priority": {"type": "string", "enum": list(PRIORITIES)},
    "assignee": {"type": "string", "description": "负责人 username 或姓名"},
    "tester": {"type": "string", "description": "测试人 username 或姓名"},
    "machines": {"type": "array", "items": {"type": "string"},
                 "description": "机台，格式 '机台组/机台' 或 '机台组'，如 'CMB-1/PM-A'"},
    "update_scope": {"type": "array", "items": {"type": "string"},
                     "description": "更新涉及，取值见项目 update_scope_options"},
    "project_name": {"type": "string", "description": "需求所属的项目名称（业务项目）"},
    "involves_software": {"type": "boolean"},
    "software_owner": {"type": "string", "description": "软件负责人 username 或姓名"},
    "software_note": {"type": "string", "description": "软件交付说明"},
    "software_jira_key": {"type": "string", "description": "软件 Jira 单号，如 SW-123"},
    "verify_method": {"type": "string"},
    "verify_criteria": {"type": "string"},
    "due_date": {"type": "string", "description": "YYYY-MM-DD"},
    "labels": {"type": "array", "items": {"type": "string"}},
    "story_points": {"type": "number"},
    "epic_key": {"type": "string"},
    "parent_key": {"type": "string"},
    "custom_fields": {
        "type": "object",
        "description": "项目 profile 中定义的 custom_fields；先调用 project_meta 获取字段定义",
    },
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_issues",
        "description": "用 JQL 搜索问题。字段: project,type,status,category(todo/doing/done),"
        "priority,assignee,reporter,tester,machine,group,label,scope,sprint,epic,text,created,"
        "updated,resolved,due,software,passed,team(组织名称)，以及 cf_字段key。"
        "函数: currentUser(), openSprints()。"
        "日期可用 -7d、startOfWeek()。示例: project=ELEC AND status=测试中 AND team~电气 "
        "ORDER BY priority DESC",
        "parameters": {"type": "object", "properties": {
            "jql": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
            "required": ["jql"]},
    },
    {
        "name": "get_issue",
        "description": "读取单个问题的完整信息和最近评论",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}},
                       "required": ["key"]},
    },
    {
        "name": "get_issues",
        "description": "批量读取多个问题，最多 50 个；用于一次性核实问题清单",
        "parameters": {"type": "object", "properties": {
            "keys": {"type": "array", "items": {"type": "string"}},
        }, "required": ["keys"]},
    },
    {
        "name": "my_work",
        "description": "读取当前用户负责且未完成的工作",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 30},
        }},
    },
    {
        "name": "find_users",
        "description": "按姓名/账号/职位或组织查找人员",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "org": {"type": "string", "description": "组织名称片段"},
            "limit": {"type": "integer"}}},
    },
    {
        "name": "list_org_units",
        "description": "列出组织架构（部门/小组）路径",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "project_meta",
        "description": "项目的状态列表、机台、更新涉及选项、成员角色、冲刺",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string"},
            "section": {"type": "string", "description": "只读取一个分区以节省上下文"},
        }},
    },
    {
        "name": "analyze_workflow",
        "description": "只读检查项目某一问题类型的当前工作流：可达性、死胡同、完成/重开路径及各状态问题数",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string", "description": "项目 Key"},
            "issue_type": {"type": "string", "enum": list(ISSUE_TYPES)},
        }, "required": ["issue_type"]},
    },
    {
        "name": "suggest_workflow",
        "description": "只读校验工作流草稿并返回增删连线、警告和受影响问题；不会修改项目。"
        "先读取 project_meta/analyze_workflow，再提交完整草稿。",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string", "description": "项目 Key"},
            "issue_type": {"type": "string", "enum": list(ISSUE_TYPES)},
            "transitions": {
                "type": "object",
                "description": "完整流转表：状态名映射到可到达状态名数组",
                "additionalProperties": {
                    "type": "array", "items": {"type": "string"},
                },
            },
        }, "required": ["issue_type", "transitions"]},
    },
    {
        "name": "project_stats",
        "description": "项目统计：状态分布、负责人负载、机台组、测试通过率等",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}},
    },
    {
        "name": "portfolio_overview",
        "description": "Hardware PM 全局项目风险排行、目标可信度和待决策事项",
        "parameters": {"type": "object", "properties": {
            "scope": {"type": "string", "enum": ["all", "lead", "managed"]},
        }},
    },
    {
        "name": "project_risk_snapshot",
        "description": "读取单项目目标预测、延误源、受影响 Gate、关键决策和数据质量",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string"},
        }},
    },
    {
        "name": "schedule_analysis",
        "description": "读取项目完整基线/当前/预测顺序分析、约束前置和置信度",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string"},
        }},
    },
    {
        "name": "product_help",
        "description": "查阅 Mira 使用说明与功能定义：功能在哪里、怎么操作、字段/状态/角色含义、"
        "权限、工作流、测试闭环、门禁审批、SLA、通知、看板、冲刺、列表、Excel、JQL 语法、"
        "筛选器、报表指标定义、AI 功能、部门模板、迁移、主数据、组织架构、管理后台、常见问题。"
        "query 用中文关键词；topic 可填使用说明目录中的小节标题以精确读取",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "topic": {"type": "string"}}},
    },
    {
        "name": "create_issue",
        "description": "创建问题（需要用户确认后才会真正执行）",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string", "description": "项目 Key，如 ELEC"},
            **_ISSUE_FIELD_PROPS}, "required": ["title"]},
    },
    {
        "name": "update_issue",
        "description": "修改问题字段（需要用户确认）。fields 可含 title, description, priority, "
        "assignee, tester, software_owner, machines, update_scope, project_name, involves_software, "
        "software_note, software_jira_key, verify_method, verify_criteria, update_date, "
        "test_passed, remark, due_date, labels, story_points, epic_key, sprint",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"},
            "fields": {"type": "object", "properties": {
                **_ISSUE_FIELD_PROPS,
                "test_passed": {"type": "boolean"},
                "update_date": {"type": "string"},
                "remark": {"type": "string"},
                "sprint": {"type": "string"},
            }}},
            "required": ["key", "fields"]},
    },
    {
        "name": "transition_issue",
        "description": "把问题流转到某个状态（需要用户确认）；流转到「重新打开」时必须提供 reopen_reason",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"},
            "status": {"type": "string"},
            "reopen_reason": {"type": "string", "description": "重新打开原因（进入「重新打开」时必填）"},
        }, "required": ["key", "status"]},
    },
    {
        "name": "assign_issue",
        "description": "指派负责人（需要用户确认），assignee 为空表示取消指派",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"}, "assignee": {"type": "string"}},
            "required": ["key"]},
    },
    {
        "name": "add_comment",
        "description": "添加评论（需要用户确认），可用 @username 提及",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"}, "body": {"type": "string"}},
            "required": ["key", "body"]},
    },
    {
        "name": "apply_workflow",
        "description": "应用已通过 suggest_workflow 校验的完整工作流草稿；"
        "需要项目管理员权限，并且必须由用户在界面确认后才执行",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string", "description": "项目 Key"},
            "issue_type": {"type": "string", "enum": list(ISSUE_TYPES)},
            "transitions": {
                "type": "object",
                "description": "与 suggest_workflow 完全一致的完整流转表",
                "additionalProperties": {
                    "type": "array", "items": {"type": "string"},
                },
            },
        }, "required": ["issue_type", "transitions"]},
    },
    {
        "name": "open_page",
        "description": "提议跳转到 Mira 内的某个页面或设置（需要用户确认后前端才会跳转）。"
        "仅在用户明确要求跳转/打开/带去某页，或询问入口「在哪」时调用；"
        "对「是不是 / 有没有 / 什么意思 / 怎么算」等概念问题不要调用，直接文字回答。"
        "destination 优先用 id，也可用中文别名。项目相关页面需带 project（当前对话项目可省略）。"
        "打开单个问题用 issue_key。"
        f"可用 id：{', '.join(d.id for d in NAV_DESTINATIONS)}",
        "parameters": {"type": "object", "properties": {
            "destination": {"type": "string",
                            "description": "页面 id 或中文关键词，如 project.settings.workflow / 工作流"},
            "project": {"type": "string", "description": "项目 Key，项目内页面必填（可沿用当前项目）"},
            "issue_key": {"type": "string", "description": "若要打开问题详情，填问题 Key"},
        }},
    },
]

READ_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "search_issues": t_search_issues,
    "get_issue": t_get_issue,
    "get_issues": t_get_issues,
    "my_work": t_my_work,
    "find_users": t_find_users,
    "list_org_units": t_list_org_units,
    "project_meta": t_project_meta,
    "analyze_workflow": t_analyze_workflow,
    "suggest_workflow": t_suggest_workflow,
    "project_stats": t_project_stats,
    "portfolio_overview": t_portfolio_overview,
    "project_risk_snapshot": t_project_risk_snapshot,
    "schedule_analysis": t_schedule_analysis,
    "product_help": t_product_help,
}


def openai_tools(allowed: set[str] | None = None) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": spec}
        for spec in TOOL_SPECS
        if allowed is None or spec["name"] in allowed
    ]


def tools_prompt(allowed: set[str] | None = None) -> str:
    lines = []
    for spec in TOOL_SPECS:
        if allowed is not None and spec["name"] not in allowed:
            continue
        params = json.dumps(spec["parameters"].get("properties", {}), ensure_ascii=False)
        lines.append(f"- {spec['name']}: {spec['description']} 参数: {params[:600]}")
    return "\n".join(lines)


def run_tool(ctx: AgentContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a read tool or queue a write tool. Always returns a JSON-able dict."""
    if ctx.db is None:
        if ctx.actor_id is None:
            return {"error": "用户上下文已失效"}
        with session_factory()() as db:
            user = db.get(User, ctx.actor_id)
            if user is None or not user.enabled:
                return {"error": "用户不存在或已停用"}
            return run_tool(
                AgentContext(
                    db,
                    user,
                    ctx.project_key,
                    ctx.actor_id,
                    ctx.allowed_tools,
                    ctx.navigation_allowed,
                ),
                name,
                args,
            )
    assert ctx.user is not None
    try:
        if ctx.allowed_tools is not None and name not in ctx.allowed_tools:
            return {"error": f"当前问题类型不允许调用工具 {name}"}
        if name == "open_page" and not ctx.navigation_allowed:
            return {"error": "用户没有明确要求跳转，已阻止页面导航"}
        if name in READ_HANDLERS:
            return READ_HANDLERS[name](ctx, **args)
        if name in CONFIRM_TOOLS:
            action = propose(ctx, name, args)
            note = ("已生成跳转提议，用户确认后才会打开页面。不要声称已经跳转。"
                    if name == "open_page" else
                    "已生成待确认操作，用户确认后才会执行。不要声称已经完成。")
            return {
                "pending_action_id": action.id,
                "summary": action.summary,
                "note": note,
            }
        return {"error": f"未知工具 {name}"}
    except IssueError as error:
        ctx.db.rollback()
        return {"error": error.message}
    except TypeError as error:
        return {"error": f"参数错误：{error}"}
