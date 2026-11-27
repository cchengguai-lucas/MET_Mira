"""Project workflows: default statuses, transitions and requirement automation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Board, Issue, Machine, MachineGroup, Project, Status
from ..models.issue import ISSUE_TYPES
from .profiles import DEFAULT_PROFILE, PROFILES, builtin_definition, profile_of

REOPENED = "重新打开"
TEST_FAILED = "测试未通过"
TEST_PASSED = "测试通过"
REJECTED = "已驳回"
DEV_COMPLETE = "开发完成"

DEFAULT_STATUSES: list[tuple[str, str, str]] = [
    ("待评估", "todo", "#8e8e93"),
    ("方案讨论", "todo", "#af52de"),
    (REOPENED, "todo", "#32ade6"),
    ("研发中", "doing", "#0a84ff"),
    (DEV_COMPLETE, "doing", "#5ac8fa"),
    ("测试中", "doing", "#ff9f0a"),
    (TEST_PASSED, "doing", "#34c759"),
    (TEST_FAILED, "doing", "#ff375f"),
    ("已完成", "done", "#30d158"),
    (REJECTED, "done", "#ff453a"),
]

# The first target of each rule is the forward step; the flow diagram's main line follows it.
DEFAULT_REQUIREMENT_TRANSITIONS: dict[str, list[str]] = {
    "待评估": ["方案讨论", "已驳回"],
    "方案讨论": ["研发中", "待评估", "已驳回"],
    REOPENED: ["研发中", "方案讨论"],
    "研发中": [DEV_COMPLETE, "方案讨论"],
    DEV_COMPLETE: ["测试中", "研发中"],
    "测试中": [TEST_PASSED, TEST_FAILED],
    TEST_PASSED: ["已完成", REJECTED],
    TEST_FAILED: [REOPENED],
    "已完成": [REOPENED],
    "已驳回": ["待评估"],
}

# Built-in flow per issue type. Statuses are shared by the project (boards, reports), but each
# type only walks the steps it needs; a project may override any type in settings.transitions.
# Anything that fails verification or is reopened after completion goes through 重新打开 before
# development resumes, so rework stays visible on the board and in the history.
DEFAULT_TYPE_TRANSITIONS: dict[str, dict[str, list[str]]] = {
    # Full process: evaluate → design discussion → develop → test → accept → done, may be
    # rejected; reopened work may go back to design if the approach was wrong.
    "requirement": DEFAULT_REQUIREMENT_TRANSITIONS,
    # Triage → fix → verify → accept; invalid/duplicate reports are rejected, failed fixes reopen.
    "bug": {
        "待评估": ["研发中", "已驳回"],
        REOPENED: ["研发中"],
        "研发中": [DEV_COMPLETE, "待评估"],
        DEV_COMPLETE: ["测试中", "研发中"],
        "测试中": [TEST_PASSED, TEST_FAILED],
        TEST_PASSED: ["已完成", REJECTED],
        TEST_FAILED: [REOPENED],
        "已完成": [REOPENED],
        "已驳回": ["待评估"],
    },
    # Develop → verify → accept; descoped stories go back to the backlog instead of rejected.
    "story": {
        "待评估": ["研发中"],
        REOPENED: ["研发中", "待评估"],
        "研发中": [DEV_COMPLETE, "待评估"],
        DEV_COMPLETE: ["测试中", "研发中"],
        "测试中": [TEST_PASSED, TEST_FAILED],
        TEST_PASSED: ["已完成"],
        TEST_FAILED: [REOPENED],
        "已完成": [REOPENED],
    },
    # Plain to-do → doing → done; no verification step.
    "task": {
        "待评估": ["研发中"],
        REOPENED: ["研发中"],
        "研发中": ["已完成", "待评估"],
        "已完成": [REOPENED],
    },
    # Checklist items: may be ticked off directly.
    "subtask": {
        "待评估": ["研发中", "已完成"],
        REOPENED: ["研发中", "已完成"],
        "研发中": ["已完成", "待评估"],
        "已完成": [REOPENED],
    },
    # Container for child work: open → in progress → done, or cancelled.
    "epic": {
        "待评估": ["研发中", "已驳回"],
        REOPENED: ["研发中"],
        "研发中": ["已完成", "待评估"],
        "已完成": [REOPENED],
        "已驳回": ["待评估"],
    },
}

DEFAULT_UPDATE_SCOPES = ["硬件", "软件", "工作流程", "程序/参数", "图纸/文档", "其他"]

DEFAULT_MACHINE_GROUPS: dict[str, list[str]] = {
    "CMB-1": ["整机", "EFEM", "TM", "LL", "PM-A", "PM-B"],
    "CMB-2": ["整机", "EFEM", "TM", "LL", "PM-A", "PM-B"],
    "CMB-3": ["整机", "EFEM", "TM", "PM-A"],
    "Nebula-1": ["整机", "TM", "PM-A"],
    "Nebula-2": ["整机", "TM", "PM-A"],
}


def default_settings(profile_key: str = DEFAULT_PROFILE,
                     definition: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = PROFILES.get(profile_key, PROFILES[DEFAULT_PROFILE])
    if definition:
        defaults = dict(definition.get("defaults") or {})
        return {
            "profile": str(definition.get("key") or profile_key),
            "update_scope_options": list(defaults.pop("update_scope_options", [])),
            "transitions": dict(definition.get("workflows") or {}),
            "transition_gates": dict(definition.get("default_gates") or {}),
            **defaults,
        }
    transitions: dict[str, dict[str, list[str]]] = {}
    if profile.key != DEFAULT_PROFILE:
        names = list(profile.default_statuses)
        linear = {
            name: ([names[index + 1]] if index + 1 < len(names) else [])
            for index, name in enumerate(names)
        }
        if "已驳回" in names:
            for name in names[:-2]:
                linear[name] = [*linear[name], "已驳回"]
            linear["已驳回"] = [names[0]]
        transitions["requirement"] = linear
    return {
        "profile": profile.key,
        "update_scope_options": list(DEFAULT_UPDATE_SCOPES),
        "transitions": transitions,
        "test_status": "测试中",
        "pass_status": TEST_PASSED,
        "fail_return_status": TEST_FAILED,
        "sla_hours": {"highest": 24, "high": 48, "medium": 120, "low": 240, "lowest": 480},
        "sla_warning_hours": 4,
    }


def include_missing_status_columns(
    columns: list[dict[str, Any]] | None,
    statuses: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    """Add one column for each status that no column maps yet.

    ``statuses`` is ``(id, name)`` in project order. Columns that already exist keep
    their name, WIP limit, grouped statuses and relative order. A new column is inserted
    after the nearest earlier mapped status, so it follows the status list.
    """
    result: list[dict[str, Any]] = []
    for column in columns or []:
        ids = [int(sid) for sid in (column.get("status_ids") or []) if sid is not None]
        result.append({**column, "status_ids": ids})
    mapped = {sid for column in result for sid in column["status_ids"]}
    order = [sid for sid, _name in statuses]
    for status_id, name in statuses:
        if status_id in mapped:
            continue
        result.insert(
            _column_insert_at(result, order, status_id),
            {"name": name, "status_ids": [status_id], "wip": None},
        )
        mapped.add(status_id)
    return result


def _column_insert_at(columns: list[dict[str, Any]], order: list[int], status_id: int) -> int:
    position = order.index(status_id)
    earlier = set(order[:position])
    for index in range(len(columns) - 1, -1, -1):
        if earlier.intersection(columns[index]["status_ids"]):
            return index + 1
    later = set(order[position + 1:])
    for index, column in enumerate(columns):
        if later.intersection(column["status_ids"]):
            return index
    return len(columns)


def ensure_status_columns(
    columns: list[dict[str, Any]] | None,
    statuses: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    """Give every current status a column of its own.

    A column that already maps exactly one status stays, including its name and WIP limit.
    A status that only shares a column is moved out. The column keeps the status it is
    named after, or its first status. Ids of deleted statuses are dropped. A column with
    no statuses is kept only when it was already empty.
    """
    names = dict(statuses)
    kept: list[dict[str, Any]] = []
    for column in columns or []:
        raw = [sid for sid in (column.get("status_ids") or []) if sid is not None]
        ids = [int(sid) for sid in raw if int(sid) in names]
        if not ids:
            if not raw:
                kept.append({**column, "status_ids": []})
            continue
        owner = ids[0]
        if len(ids) > 1:
            owner = next((sid for sid in ids if names[sid] == column.get("name")), ids[0])
        kept.append({**column, "status_ids": [owner]})
    return include_missing_status_columns(kept, statuses)


def default_columns(statuses: list[Status]) -> list[dict[str, Any]]:
    """One column per status, in project order."""
    return include_missing_status_columns([], [(status.id, status.name) for status in statuses])


def attach_status_column(db: Session, project_id: int, status_id: int) -> None:
    """Put one new status on every board without restoring columns an admin removed."""
    statuses = project_statuses(db, project_id)
    for board in db.scalars(select(Board).where(Board.project_id == project_id)):
        mapped = {
            int(sid)
            for column in (board.columns or [])
            for sid in (column.get("status_ids") or [])
            if sid is not None
        }
        pairs = [
            (status.id, status.name)
            for status in statuses
            if status.id == status_id or status.id in mapped
        ]
        board.columns = include_missing_status_columns(board.columns, pairs)


def detach_status_column(db: Session, project_id: int, status_id: int) -> None:
    """Drop a deleted status from board columns, and drop a column left with no statuses."""
    for board in db.scalars(select(Board).where(Board.project_id == project_id)):
        columns: list[dict[str, Any]] = []
        for column in board.columns or []:
            ids = [
                int(sid)
                for sid in (column.get("status_ids") or [])
                if sid not in (None, status_id)
            ]
            if ids:
                columns.append({**column, "status_ids": ids})
        board.columns = columns


def rename_status_column(db: Session, project_id: int, status_id: int, old: str, new: str) -> None:
    """Keep a column titled with its status name in step when that status is renamed."""
    if old == new:
        return
    for board in db.scalars(select(Board).where(Board.project_id == project_id)):
        columns: list[dict[str, Any]] = []
        for column in board.columns or []:
            ids = [int(sid) for sid in (column.get("status_ids") or []) if sid is not None]
            name = new if column.get("name") == old and status_id in ids else column.get("name")
            columns.append({**column, "name": name, "status_ids": ids})
        board.columns = columns


def bootstrap_project(
    db: Session,
    project: Project,
    *,
    with_machines: bool = False,
) -> None:
    """Create default statuses, a kanban + scrum board and optional machine groups."""
    profile = profile_of(project)
    definition = profile.definition or builtin_definition(profile)
    project.settings = {
        **default_settings(profile.key, definition),
        **(project.settings or {}),
        "profile": profile.key,
    }
    statuses = []
    raw_statuses = definition.get("statuses") or []
    status_defs = [
        (str(row["name"]), str(row.get("category") or "todo"),
         str(row.get("color") or "#8e8e93"))
        for row in raw_statuses
    ] or DEFAULT_STATUSES
    for index, (name, category, color) in enumerate(status_defs):
        status = Status(
            project_id=project.id, name=name, category=category, color=color, sort_order=index
        )
        db.add(status)
        statuses.append(status)
    db.flush()
    columns = default_columns(statuses)
    db.add(Board(project_id=project.id, name=f"{project.name} 看板", kind="kanban",
                 columns=columns, swimlane="none",
                 quick_filters=[
                     {"name": "仅我的", "jql": "assignee = currentUser()"},
                     {"name": "高优先级", "jql": "priority in (highest, high)"},
                     {"name": "涉及软件", "jql": "software = true"},
                 ]))
    db.add(Board(project_id=project.id, name=f"{project.name} 冲刺看板", kind="scrum",
                 columns=[dict(c) for c in columns], swimlane="none", quick_filters=[]))
    if with_machines:
        for g_index, (group_name, machines) in enumerate(DEFAULT_MACHINE_GROUPS.items()):
            group = MachineGroup(project_id=project.id, name=group_name, sort_order=g_index)
            db.add(group)
            db.flush()
            for m_index, machine in enumerate(machines):
                db.add(Machine(group_id=group.id, name=machine, sort_order=m_index))
    db.flush()


def project_statuses(db: Session, project_id: int) -> list[Status]:
    return list(
        db.scalars(
            select(Status).where(Status.project_id == project_id).order_by(Status.sort_order)
        )
    )


def template_transitions(project: Project) -> dict[str, dict[str, list[str]]]:
    """The flow per type defined by the project's template version (the electrical flow for
    legacy projects without one)."""
    definition = profile_of(project).definition
    workflows = (definition or {}).get("workflows") or {}
    return {t: workflows.get(t) or DEFAULT_TYPE_TRANSITIONS[t] for t in ISSUE_TYPES}


def transition_rules(project: Project, issue_type: str) -> dict[str, list[str]] | None:
    """The project's rules for ``issue_type``, else its template's flow for that type."""
    transitions = (project.settings or {}).get("transitions") or {}
    return (transitions.get(issue_type) or template_transitions(project).get(issue_type)
            or transitions.get("requirement"))


def effective_transitions(project: Project) -> dict[str, dict[str, list[str]]]:
    return {t: dict(transition_rules(project, t) or {}) for t in ISSUE_TYPES}


def customized_types(project: Project) -> list[str]:
    """Types whose project rules differ from the template's flow."""
    stored = (project.settings or {}).get("transitions") or {}
    baseline = template_transitions(project)
    return [t for t in ISSUE_TYPES if stored.get(t) and stored[t] != baseline[t]]


def flow_statuses(project: Project, issue_type: str, statuses: list[Status]) -> list[Status]:
    """The statuses ``issue_type`` uses, in project order."""
    rules = transition_rules(project, issue_type)
    if not rules:
        return list(statuses)
    names = set(rules) | {name for targets in rules.values() for name in targets}
    return [s for s in statuses if s.name in names]


def initial_status(project: Project, issue_type: str, statuses: list[Status]) -> Status:
    flow = flow_statuses(project, issue_type, statuses)
    return flow[0] if flow else statuses[0]


def allowed_targets(project: Project, issue_type: str, current: Status,
                    statuses: list[Status]) -> list[Status]:
    """Next statuses for everyone, admins included. A status without its own rule (or one the
    type does not use, e.g. after a type change) may only step to the nearest statuses of the
    type's flow, so an issue can never jump freely."""
    rules = transition_rules(project, issue_type)
    if rules and current.name in rules:
        names = set(rules[current.name])
        return [s for s in statuses if s.name in names and s.id != current.id]
    order = {s.id: i for i, s in enumerate(statuses)}
    if current.id not in order:
        return []
    position = order[current.id]
    flow = [s for s in flow_statuses(project, issue_type, statuses) if s.id != current.id]
    before = [s for s in flow if order[s.id] < position]
    after = [s for s in flow if order[s.id] > position]
    return [*before[-1:], *after[:1]]


def can_transition(project: Project, issue: Issue, target: Status,
                   statuses: list[Status]) -> bool:
    if target.id == issue.status_id:
        return True
    return any(s.id == target.id for s in allowed_targets(project, issue.type, issue.status,
                                                           statuses))


def is_rejected(status: Status | None) -> bool:
    """Rejected is category done but never counts as completed work."""
    return status is not None and status.name == REJECTED


def is_closed(status: Status | None) -> bool:
    """Off the board / backlog: any done-category status, including rejected."""
    return status is not None and status.category == "done"


def is_completed(status: Status | None) -> bool:
    """Successfully finished: done category and not rejected."""
    return is_closed(status) and not is_rejected(status)


def completion_status(project: Project, issue_type: str, statuses: list[Status]) -> Status | None:
    """The done status ``issue_type`` finishes in (not the rejected one)."""
    done = [s for s in flow_statuses(project, issue_type, statuses) if s.category == "done"]
    return next((s for s in done if s.name != REJECTED), done[0] if done else None)


def status_by_name(statuses: list[Status], name: str | None) -> Status | None:
    if not name:
        return None
    for status in statuses:
        if status.name == name:
            return status
    return None


def verification_statuses(project: Project, issue_type: str,
                          statuses: list[Status]) -> dict[str, Status] | None:
    """The type's test status and the statuses a result leads to: {"test", "pass", "fail"}.
    A pass goes to the configured pass status, a failure to 测试未通过 (or the configured fail
    status); if the type's flow does not allow those, the matching allowed step is used. None
    when the type has no verification step."""
    settings = project.settings or {}
    test = status_by_name(statuses, settings.get("test_status", "测试中"))
    if test is None or all(s.id != test.id for s in flow_statuses(project, issue_type, statuses)):
        return None
    targets = allowed_targets(project, issue_type, test, statuses)

    def preferred(key: str) -> Status | None:
        status = status_by_name(statuses, settings.get(key))
        return status if status is not None and any(s.id == status.id for s in targets) else None

    passed = preferred("pass_status") or next((s for s in targets if s.category == "done"), None)
    failed = (preferred("fail_return_status") or status_by_name(targets, TEST_FAILED)
              or next((s for s in targets if s.category != "done"), None))
    if passed is None or failed is None:
        return None
    return {"test": test, "pass": passed, "fail": failed}


def test_result_editable(project: Project, issue: Issue, statuses: list[Status]) -> bool:
    """A result is recorded while under test and may be corrected from the status it led to."""
    steps = verification_statuses(project, issue.type, statuses)
    return steps is not None and issue.status_id in {s.id for s in steps.values()}


def test_result_target(project: Project, issue: Issue, passed: bool | None,
                       statuses: list[Status]) -> Status | None:
    """Where recording ``passed`` moves the issue: under test, a pass or failure moves on to the
    pass / fail status; from those, a corrected result moves across and clearing it returns the
    issue to the test status."""
    steps = verification_statuses(project, issue.type, statuses)
    if steps is None or not test_result_editable(project, issue, statuses):
        return None
    if passed is None:
        return None if issue.status_id == steps["test"].id else steps["test"]
    return steps["pass"] if passed else steps["fail"]


def verification_names(project: Project) -> set[str]:
    """Statuses a tester records: under test, passed, and failed."""
    settings = project.settings or {}
    names = {
        settings.get("test_status") or "测试中",
        pass_status_name(project),
        TEST_FAILED,
    }
    custom_fail = settings.get("fail_return_status")
    if custom_fail:
        names.add(str(custom_fail))
    return {str(name) for name in names if name}


def is_verification_status(project: Project, status: Status) -> bool:
    return status.name in verification_names(project)


def is_fail_status(project: Project, status: Status) -> bool:
    return status.name in {TEST_FAILED, (project.settings or {}).get("fail_return_status")}


def pass_status_name(project: Project) -> str:
    return (project.settings or {}).get("pass_status") or TEST_PASSED


def is_pass_status(project: Project, status: Status) -> bool:
    return status.name == pass_status_name(project)


def dev_complete_name(project: Project) -> str:
    """Status that means development finished and testing should start.

    Electrical templates keep the Chinese default; English AI templates set
    ``settings.dev_complete_status`` (e.g. ``Development Complete``).
    """
    custom = (project.settings or {}).get("dev_complete_status")
    return str(custom) if custom else DEV_COMPLETE


def is_dev_complete(project: Project, status: Status) -> bool:
    return status.name == dev_complete_name(project)


def requires_assignee_accept(project: Project, current: Status, target: Status) -> bool:
    """Leaving the test-passed holding status for 已完成 / 已驳回 is assignee-only."""
    if not is_pass_status(project, current):
        return False
    return target.name == REJECTED or (target.category == "done" and target.name != REJECTED)


def test_result_after_move(project: Project, old: Status | None, new: Status,
                           current: bool | None) -> bool | None:
    """The test result an issue should carry after a manual status change: entering the test
    status starts a fresh round; leaving it for the pass/fail status records the matching result,
    so the list and reports agree with the board. The result stays visible while the outcome or
    the reopen is pending, and is cleared once work resumes anywhere else (e.g. 研发中)."""
    settings = project.settings or {}
    test_status = settings.get("test_status", "测试中")
    passed_name = pass_status_name(project)
    if new.name == test_status:
        return None
    if old is not None and old.name == test_status:
        if new.name == passed_name:
            return True
        if is_fail_status(project, new):
            return False
    # Reopen starts a fresh cycle — clear the previous pass/fail so testers see Not set.
    if new.name == REOPENED:
        return None
    if is_fail_status(project, new) or (new.category == "done" and new.name != REJECTED):
        return current
    # Assignee rejects after a pass — keep the recorded pass visible in history/reports.
    if old is not None and old.name == passed_name and new.name == REJECTED:
        return current
    return None


def splice_development_complete(
    rules: dict[str, list[str]],
) -> dict[str, list[str]] | None:
    """Insert 开发完成 between 研发中 and 测试中.

    Returns a new rule map when 研发中 can reach 测试中 and 开发完成 is not
    already in the map. Returns None when the type should stay as stored.
    """
    developing = rules.get("研发中")
    if not isinstance(developing, list) or "测试中" not in developing:
        return None
    if DEV_COMPLETE in rules or DEV_COMPLETE in developing:
        return None
    rest = [name for name in developing if name != "测试中"]
    updated = {key: list(value) for key, value in rules.items() if isinstance(value, list)}
    updated["研发中"] = [DEV_COMPLETE, *rest]
    updated[DEV_COMPLETE] = ["测试中", "研发中"]
    return updated
