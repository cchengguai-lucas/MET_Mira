"""A compact JQL dialect compiled to SQLAlchemy expressions.

Examples::

    project = ELEC AND status = 测试中 AND machine ~ CMB
    assignee = currentUser() AND category != done ORDER BY priority DESC
    type in (需求, bug) AND created >= -7d
    team = 电气组 AND passed is EMPTY
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time as monotonic_time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import String, and_, case, cast, exists, func, not_, or_, select, true
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from ..models import (
    Issue,
    IssueLink,
    IssueMachine,
    Machine,
    MachineGroup,
    OrgUnit,
    Project,
    Sprint,
    Status,
    User,
    Watcher,
)
from .permissions import visible_issue_clause
from .profiles import profile_of

TYPE_ALIASES = {
    "需求": "requirement",
    "requirement": "requirement",
    "bug": "bug",
    "缺陷": "bug",
    "task": "task",
    "任务": "task",
    "story": "story",
    "故事": "story",
    "epic": "epic",
    "史诗": "epic",
    "subtask": "subtask",
    "sub-task": "subtask",
    "子任务": "subtask",
}
PRIORITY_ALIASES = {
    "highest": "highest",
    "最高": "highest",
    "high": "high",
    "高": "high",
    "medium": "medium",
    "中": "medium",
    "low": "low",
    "低": "low",
    "lowest": "lowest",
    "最低": "lowest",
}
CATEGORY_ALIASES = {
    "todo": "todo", "待办": "todo", "doing": "doing", "进行中": "doing",
    "done": "done", "完成": "done",
}
PRIORITY_ORDER = {"highest": 5, "high": 4, "medium": 3, "low": 2, "lowest": 1}
BOOL_ALIASES = {"true": True, "yes": True, "是": True, "通过": True, "1": True,
                "false": False, "no": False, "否": False, "不通过": False, "0": False}

FIELD_ALIASES = {
    "project": "project", "项目": "project",
    "type": "type", "issuetype": "type", "类型": "type",
    "status": "status", "状态": "status",
    "category": "category", "statuscategory": "category",
    "priority": "priority", "优先级": "priority",
    "assignee": "assignee", "负责人": "assignee",
    "reporter": "reporter", "报告人": "reporter", "提出人": "reporter", "需求提出人": "reporter",
    "tester": "tester", "测试人": "tester", "测试人员": "tester",
    "software_owner": "software_owner", "softwareowner": "software_owner",
    "软件负责人": "software_owner",
    "watcher": "watcher", "watchers": "watcher", "关注人": "watcher", "关注者": "watcher",
    "linked": "linked", "link": "linked", "关联": "linked", "linkedissues": "linked",
    "linktype": "link_type", "link_type": "link_type", "关联类型": "link_type",
    "machine": "machine", "机台": "machine",
    "group": "group", "机台组": "group",
    "label": "label", "labels": "label", "标签": "label",
    "scope": "scope", "更新涉及": "scope", "影响范围": "scope",
    "sprint": "sprint", "冲刺": "sprint",
    "epic": "epic", "史诗": "epic",
    "parent": "parent", "父任务": "parent",
    "text": "text", "summary": "text", "title": "text", "文本": "text",
    "key": "key", "编号": "key",
    "created": "created", "创建": "created",
    "updated": "updated", "更新": "updated",
    "resolved": "resolved",
    "due": "due", "duedate": "due", "截止": "due",
    "software": "software", "涉及软件": "software",
    "software_ready": "software_ready",
    "软件是否准备好": "software_ready", "软件准备好": "software_ready",
    "electrical_scheme_ok": "electrical_scheme_ok", "电气确认": "electrical_scheme_ok",
    "process_scheme_ok": "process_scheme_ok", "工艺确认": "process_scheme_ok",
    "software_scheme_ok": "software_scheme_ok", "软件确认": "software_scheme_ok",
    "scheme_locked": "scheme_locked", "方案已发起": "scheme_locked",
    "software_jira": "software_jira_key", "software_jira_key": "software_jira_key",
    "软件jira单号": "software_jira_key", "软件jira": "software_jira_key",
    "software_jira_status": "software_jira_status", "软件jira状态": "software_jira_status",
    "software_jira_assignee": "software_jira_assignee", "软件jira经办人": "software_jira_assignee",
    "passed": "passed", "是否通过": "passed",
    "projectname": "project_name", "项目名称": "project_name",
    "team": "team", "部门": "team", "小组": "team",
    "points": "points", "storypoints": "points", "故事点": "points",
}

ORDER_FIELDS = {"created", "updated", "priority", "rank", "key", "due", "status", "resolved"}


class JqlError(ValueError):
    pass


@dataclass
class Token:
    kind: str  # word | string | op | lparen | rparen | comma
    value: str
    pos: int


TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<op>!=|!~|>=|<=|=|~|>|<)
      | (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<comma>,)
      | (?P<word>[^\s()=!~<>,"']+(?:\([+-]?\d*\))?)
    )""",
    re.VERBOSE,
)


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        match = TOKEN_RE.match(text, pos)
        if not match or match.end() == pos:
            if text[pos:].strip() == "":
                break
            raise JqlError(f"无法解析的位置 {pos}: {text[pos:pos + 12]}")
        kind = match.lastgroup or "word"
        value = match.group(kind)
        if kind == "string":
            value = re.sub(r"\\(.)", r"\1", value[1:-1])
        tokens.append(Token(kind, value, match.start(kind)))
        pos = match.end()
    return tokens


@dataclass
class Context:
    user_id: int | None
    today: date


def _start_of_day(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def parse_date(raw: str, ctx: Context) -> datetime:
    value = raw.strip().lower()
    if value in {"now()", "today", "today()", "startofday()"}:
        return _start_of_day(ctx.today) if value != "now()" else datetime.now(timezone.utc)
    start_of = re.fullmatch(r"startof(week|month)\(([+-]?\d*)\)", value)
    if start_of:
        offset = int(start_of.group(2) or 0)
        if start_of.group(1) == "week":
            monday = ctx.today - timedelta(days=ctx.today.weekday())
            return _start_of_day(monday + timedelta(weeks=offset))
        months = ctx.today.year * 12 + ctx.today.month - 1 + offset
        return _start_of_day(date(months // 12, months % 12 + 1, 1))
    rel = re.fullmatch(r"([+-]?\d+)([dwhm])", value)
    if rel:
        amount, unit = int(rel.group(1)), rel.group(2)
        delta = {
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
            "h": timedelta(hours=amount),
            "m": timedelta(days=30 * amount),
        }[unit]
        if unit == "h":
            return datetime.now(timezone.utc) + delta
        return _start_of_day(ctx.today + delta)
    try:
        return _start_of_day(date.fromisoformat(value))
    except ValueError as error:
        raise JqlError(f"无法识别的日期：{raw}") from error


class Parser:
    def __init__(self, tokens: list[Token], ctx: Context):
        self.tokens = tokens
        self.i = 0
        self.ctx = ctx

    def peek(self, offset: int = 0) -> Token | None:
        index = self.i + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def is_kw(self, token: Token | None, *words: str) -> bool:
        return token is not None and token.kind == "word" and token.value.upper() in words

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise JqlError("查询意外结束")
        self.i += 1
        return token

    def expect(self, kind: str) -> Token:
        token = self.take()
        if token.kind != kind:
            raise JqlError(f"位置 {token.pos} 期望 {kind}，得到 {token.value}")
        return token

    def parse(self) -> tuple[ColumnElement[bool], list[Any]]:
        where: ColumnElement[bool] = true()
        if self.peek() is not None and not self.is_kw(self.peek(), "ORDER"):
            where = self.parse_or()
        order = []
        if self.is_kw(self.peek(), "ORDER"):
            self.take()
            if not self.is_kw(self.peek(), "BY"):
                raise JqlError("ORDER 后需要 BY")
            self.take()
            order = self.parse_order()
        if self.peek() is not None:
            raise JqlError(f"多余的内容：{self.peek().value}")  # type: ignore[union-attr]
        return where, order

    def parse_order(self) -> list[Any]:
        result = []
        while True:
            field = self.take().value.lower()
            if field not in ORDER_FIELDS:
                raise JqlError(f"不支持排序字段：{field}")
            direction = "ASC"
            if self.is_kw(self.peek(), "ASC", "DESC"):
                direction = self.take().value.upper()
            column = order_column(field)
            result.append(column.desc() if direction == "DESC" else column.asc())
            if self.peek() is not None and self.peek().kind == "comma":  # type: ignore[union-attr]
                self.take()
                continue
            return result

    def parse_or(self) -> ColumnElement[bool]:
        parts = [self.parse_and()]
        while self.is_kw(self.peek(), "OR"):
            self.take()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else or_(*parts)

    def parse_and(self) -> ColumnElement[bool]:
        parts = [self.parse_not()]
        while True:
            token = self.peek()
            if token is None or token.kind == "rparen" or self.is_kw(token, "OR", "ORDER"):
                break
            if self.is_kw(token, "AND"):
                self.take()
            parts.append(self.parse_not())
        return parts[0] if len(parts) == 1 else and_(*parts)

    def parse_not(self) -> ColumnElement[bool]:
        if self.is_kw(self.peek(), "NOT"):
            self.take()
            return not_(self.parse_not())
        token = self.peek()
        if token is not None and token.kind == "lparen":
            self.take()
            expr = self.parse_or()
            self.expect("rparen")
            return expr
        return self.parse_clause()

    def parse_values(self) -> list[str]:
        self.expect("lparen")
        values: list[str] = []
        while True:
            token = self.take()
            if token.kind == "rparen":
                break
            if token.kind == "comma":
                continue
            values.append(token.value)
        if not values:
            raise JqlError("IN 列表不能为空")
        return values

    def parse_clause(self) -> ColumnElement[bool]:
        head = self.take()
        if head.kind not in {"word", "string"}:
            raise JqlError(f"位置 {head.pos} 期望字段名")
        nxt = self.peek()
        field = FIELD_ALIASES.get(head.value.lower()) if head.kind == "word" else None
        if field is None and head.kind == "word" and head.value.lower().startswith("cf_"):
            field = f"custom:{head.value[3:]}"
        starts_clause = nxt is not None and (
            nxt.kind == "op" or self.is_kw(nxt, "IN", "IS", "NOT") and field is not None
        )
        if field is None or not starts_clause:
            if field is None and nxt is not None and nxt.kind == "op":
                raise JqlError(f"未知字段：{head.value}")
            return text_match(head.value)
        if self.is_kw(nxt, "IS"):
            self.take()
            negate = False
            if self.is_kw(self.peek(), "NOT"):
                self.take()
                negate = True
            if not self.is_kw(self.peek(), "EMPTY", "NULL"):
                raise JqlError("IS 后需要 EMPTY")
            self.take()
            expr = empty_clause(field)
            return not_(expr) if negate else expr
        if self.is_kw(nxt, "NOT"):
            self.take()
            if not self.is_kw(self.peek(), "IN"):
                raise JqlError("NOT 后需要 IN")
            self.take()
            return not_(build(field, "=", self.parse_values(), self.ctx))
        if self.is_kw(nxt, "IN"):
            self.take()
            return build(field, "=", self.parse_values(), self.ctx)
        op = self.take().value
        value = self.take()
        if value.kind not in {"word", "string"}:
            raise JqlError(f"位置 {value.pos} 缺少取值")
        if op in {"!=", "!~"}:
            return not_(build(field, "=" if op == "!=" else "~", [value.value], self.ctx))
        return build(field, op, [value.value], self.ctx)


def order_column(field: str) -> Any:
    if field == "priority":
        return case(PRIORITY_ORDER, value=Issue.priority, else_=0)
    if field == "status":
        return (
            select(Status.sort_order).where(Status.id == Issue.status_id).scalar_subquery()
        )
    return {
        "created": Issue.created_at,
        "updated": Issue.updated_at,
        "rank": Issue.rank,
        "key": Issue.number,
        "due": Issue.due_date,
        "resolved": Issue.resolved_at,
    }[field]


def text_match(value: str) -> ColumnElement[bool]:
    pattern = f"%{value}%"
    return or_(
        Issue.title.ilike(pattern),
        Issue.description.ilike(pattern),
        Issue.key.ilike(pattern),
        Issue.project_name.ilike(pattern),
    )


def _json_contains(column: Any, value: str) -> ColumnElement[bool]:
    escaped = value.replace('"', '\\"')
    return cast(column, String).like(f'%"{escaped}"%')


def _json_text(column: Any) -> Any:
    return cast(column, String)


def empty_clause(field: str) -> ColumnElement[bool]:
    if field.startswith("custom:"):
        value = Issue.extra[field.split(":", 1)[1]].as_string()
        return or_(value.is_(None), value == "")
    simple = {
        "assignee": Issue.assignee_id,
        "reporter": Issue.reporter_id,
        "tester": Issue.tester_id,
        "software_owner": Issue.software_owner_id,
        "sprint": Issue.sprint_id,
        "epic": Issue.epic_id,
        "parent": Issue.parent_id,
        "due": Issue.due_date,
        "resolved": Issue.resolved_at,
        "software": Issue.involves_software,
        "software_ready": Issue.software_ready,
        "electrical_scheme_ok": Issue.electrical_scheme_ok,
        "process_scheme_ok": Issue.process_scheme_ok,
        "software_scheme_ok": Issue.software_scheme_ok,
        "scheme_locked": Issue.scheme_locked,
        "passed": Issue.test_passed,
        "points": Issue.story_points,
    }
    if field in simple:
        return simple[field].is_(None)
    if field == "machine" or field == "group":
        return ~Issue.machine_links.any()
    if field == "watcher":
        return ~exists(select(Watcher.id).where(Watcher.issue_id == Issue.id))
    if field == "linked":
        return ~exists(select(IssueLink.id).where(
            or_(IssueLink.source_id == Issue.id, IssueLink.target_id == Issue.id)
        ))
    if field == "link_type":
        return ~exists(select(IssueLink.id).where(
            or_(IssueLink.source_id == Issue.id, IssueLink.target_id == Issue.id)
        ))
    if field in {"label", "scope"}:
        column = Issue.labels if field == "label" else Issue.update_scope
        return or_(column.is_(None), _json_text(column).in_(["[]", "null"]))
    if field == "project_name":
        return or_(Issue.project_name.is_(None), Issue.project_name == "")
    if field == "software_jira_key":
        return or_(Issue.software_jira_key.is_(None), Issue.software_jira_key == "")
    raise JqlError(f"字段 {field} 不支持 IS EMPTY")


def _users(values: list[str], ctx: Context) -> tuple[Any, bool]:
    names = []
    include_empty = False
    ids: list[int] = []
    for value in values:
        lowered = value.lower()
        if lowered in {"currentuser()", "me", "我"}:
            if ctx.user_id:
                ids.append(ctx.user_id)
        elif lowered in {"empty", "null", "unassigned"}:
            include_empty = True
        else:
            names.append(value)
    sub = select(User.id).where(
        or_(User.id.in_(ids), User.username.in_(names), User.display_name.in_(names))
    )
    return sub, include_empty


def build(field: str, op: str, values: list[str], ctx: Context) -> ColumnElement[bool]:
    if field.startswith("custom:"):
        value = Issue.extra[field.split(":", 1)[1]].as_string()
        if op == "~":
            return or_(*[value.ilike(f"%{item}%") for item in values])
        if op in {"=", "!="}:
            expr = value.in_(values)
            return ~expr if op == "!=" else expr
        raise JqlError("自定义字段支持 =、!=、~、IN 和 IS EMPTY")
    if field in {"assignee", "reporter", "tester", "software_owner"}:
        column = {"assignee": Issue.assignee_id, "reporter": Issue.reporter_id,
                  "tester": Issue.tester_id, "software_owner": Issue.software_owner_id}[field]
        if op == "~":
            sub = select(User.id).where(or_(*[
                or_(User.display_name.ilike(f"%{v}%"), User.username.ilike(f"%{v}%"))
                for v in values
            ]))
            return column.in_(sub)
        sub, include_empty = _users(values, ctx)
        expr = column.in_(sub)
        return or_(expr, column.is_(None)) if include_empty else expr

    if field == "watcher":
        if op == "~":
            people = select(User.id).where(or_(*[
                or_(User.display_name.ilike(f"%{v}%"), User.username.ilike(f"%{v}%"))
                for v in values
            ]))
            return exists(select(Watcher.id).where(
                Watcher.issue_id == Issue.id, Watcher.user_id.in_(people)))
        sub, include_empty = _users(values, ctx)
        watched = exists(select(Watcher.id).where(
            Watcher.issue_id == Issue.id, Watcher.user_id.in_(sub)))
        if include_empty:
            return or_(watched, ~exists(select(Watcher.id).where(Watcher.issue_id == Issue.id)))
        return watched

    if field == "linked":
        keys = [v.upper() for v in values]
        other = aliased(Issue)
        peers = select(other.id).where(other.key.in_(keys))
        return or_(
            exists(select(IssueLink.id).where(
                IssueLink.source_id == Issue.id, IssueLink.target_id.in_(peers))),
            exists(select(IssueLink.id).where(
                IssueLink.target_id == Issue.id, IssueLink.source_id.in_(peers))),
        )

    if field == "link_type":
        types = [v.lower() for v in values]
        return or_(
            exists(select(IssueLink.id).where(
                IssueLink.source_id == Issue.id, IssueLink.link_type.in_(types))),
            exists(select(IssueLink.id).where(
                IssueLink.target_id == Issue.id, IssueLink.link_type.in_(types))),
        )

    if field == "project":
        return Issue.project_id.in_(
            select(Project.id).where(
                or_(Project.key.in_([v.upper() for v in values]), Project.name.in_(values))
            )
        )
    if field == "type":
        mapped = [TYPE_ALIASES.get(v.lower(), v.lower()) for v in values]
        return Issue.type.in_(mapped)
    if field == "priority":
        mapped = [PRIORITY_ALIASES.get(v.lower(), v.lower()) for v in values]
        if op in {">", ">=", "<", "<="}:
            pivot = PRIORITY_ORDER.get(mapped[0], 3)
            ordered = case(PRIORITY_ORDER, value=Issue.priority, else_=0)
            return {">": ordered > pivot, ">=": ordered >= pivot, "<": ordered < pivot,
                    "<=": ordered <= pivot}[op]
        return Issue.priority.in_(mapped)
    if field == "status":
        cond = (
            or_(*[Status.name.ilike(f"%{v}%") for v in values])
            if op == "~"
            else Status.name.in_(values)
        )
        return Issue.status_id.in_(select(Status.id).where(cond))
    if field == "category":
        mapped = [CATEGORY_ALIASES.get(v.lower(), v.lower()) for v in values]
        return Issue.status_id.in_(select(Status.id).where(Status.category.in_(mapped)))
    if field in {"machine", "group"}:
        conds = []
        for value in values:
            if field == "group":
                conds.append(
                    MachineGroup.name.ilike(f"%{value}%") if op == "~"
                    else MachineGroup.name == value
                )
            elif "/" in value:
                group, name = value.split("/", 1)
                conds.append(and_(MachineGroup.name == group, Machine.name == name))
            else:
                like = f"%{value}%"
                conds.append(
                    or_(Machine.name.ilike(like), MachineGroup.name.ilike(like)) if op == "~"
                    else or_(Machine.name == value, MachineGroup.name == value)
                )
        sub = select(Machine.id).join(MachineGroup).where(or_(*conds))
        return Issue.machine_links.any(IssueMachine.machine_id.in_(sub))
    if field in {"label", "scope"}:
        column = Issue.labels if field == "label" else Issue.update_scope
        if op == "~":
            return or_(*[_json_text(column).ilike(f"%{v}%") for v in values])
        return or_(*[_json_contains(column, v) for v in values])
    if field == "sprint":
        conds = []
        for value in values:
            lowered = value.lower()
            if lowered == "opensprints()":
                conds.append(Sprint.state == "active")
            elif lowered == "futuresprints()":
                conds.append(Sprint.state == "future")
            elif lowered == "closedsprints()":
                conds.append(Sprint.state == "closed")
            elif value.isdigit():
                conds.append(Sprint.id == int(value))
            else:
                conds.append(Sprint.name.ilike(f"%{value}%") if op == "~"
                             else Sprint.name == value)
        return Issue.sprint_id.in_(select(Sprint.id).where(or_(*conds)))
    if field in {"epic", "parent"}:
        column = Issue.epic_id if field == "epic" else Issue.parent_id
        other = aliased(Issue)
        return column.in_(select(other.id).where(other.key.in_([v.upper() for v in values])))
    if field == "text":
        return or_(*[text_match(v) for v in values])
    if field == "project_name":
        if op == "~":
            return or_(*[Issue.project_name.ilike(f"%{v}%") for v in values])
        return Issue.project_name.in_(values)
    if field == "key":
        return Issue.key.in_([v.upper() for v in values])
    if field in {
        "software", "passed", "software_ready",
        "electrical_scheme_ok", "process_scheme_ok", "software_scheme_ok", "scheme_locked",
    }:
        column = {
            "software": Issue.involves_software,
            "passed": Issue.test_passed,
            "software_ready": Issue.software_ready,
            "electrical_scheme_ok": Issue.electrical_scheme_ok,
            "process_scheme_ok": Issue.process_scheme_ok,
            "software_scheme_ok": Issue.software_scheme_ok,
            "scheme_locked": Issue.scheme_locked,
        }[field]
        conds = []
        for value in values:
            lowered = value.lower()
            if lowered in {"empty", "null"}:
                conds.append(column.is_(None))
            elif lowered in BOOL_ALIASES:
                conds.append(column.is_(True) if BOOL_ALIASES[lowered] else column.is_(False))
            else:
                raise JqlError(f"{field} 只能为 true/false/EMPTY")
        return or_(*conds)
    if field == "software_jira_key":
        if op == "~":
            return or_(*[Issue.software_jira_key.ilike(f"%{item}%") for item in values])
        return Issue.software_jira_key.in_(values)
    if field in {"software_jira_assignee", "software_jira_status", "software_jira_tester",
                 "software_jira_due"}:
        raise JqlError("软件 Jira 快照不能用于筛选")
    if field == "team":
        units = select(OrgUnit.id).where(
            or_(*[OrgUnit.path.ilike(f"%{v}%") for v in values])
        )
        members = select(User.id).where(User.org_unit_id.in_(units))
        return or_(Issue.assignee_id.in_(members), Issue.reporter_id.in_(members))
    if field == "points":
        try:
            number = float(values[0])
        except ValueError as error:
            raise JqlError("points 需要数字") from error
        return _compare(Issue.story_points, op, number)
    if field in {"created", "updated", "resolved", "due"}:
        column = {"created": Issue.created_at, "updated": Issue.updated_at,
                  "resolved": Issue.resolved_at, "due": Issue.due_date}[field]
        moment = parse_date(values[0], ctx)
        target: Any = moment.date() if field == "due" else moment
        if op == "=" and field != "due":
            return and_(column >= moment, column < moment + timedelta(days=1))
        return _compare(column, op, target)
    raise JqlError(f"未知字段：{field}")


def _compare(column: Any, op: str, value: Any) -> ColumnElement[bool]:
    if op == "=":
        return column == value
    if op == ">":
        return column > value
    if op == ">=":
        return column >= value
    if op == "<":
        return column < value
    if op == "<=":
        return column <= value
    raise JqlError(f"操作符 {op} 不适用")


def compile_jql(jql: str, *, user_id: int | None, today: date | None = None
                ) -> tuple[ColumnElement[bool], list[Any]]:
    ctx = Context(user_id=user_id, today=today or date.today())
    tokens = tokenize(jql or "")
    if not tokens:
        return true(), []
    return Parser(tokens, ctx).parse()


def search_issues(
    db: Session,
    jql: str,
    *,
    user_id: int | None,
    limit: int = 50,
    offset: int = 0,
    extra: list[ColumnElement[bool]] | None = None,
    default_order: list[Any] | None = None,
) -> tuple[int, list[Issue]]:
    from ..serialize import issue_query

    jql = normalize_custom_fields(db, jql)
    where, order = compile_jql(jql, user_id=user_id)
    conditions = [where, *_visible(db, user_id), *(extra or [])]
    total = db.scalar(select(func.count(Issue.id)).where(*conditions)) or 0
    stmt = issue_query().where(*conditions)
    stmt = stmt.order_by(*(order or default_order or [Issue.updated_at.desc()]), Issue.id)
    rows = list(db.scalars(stmt.offset(offset).limit(limit)))
    return total, rows


CF_FIELD_RE = re.compile(
    r"\bcf_([A-Za-z][A-Za-z0-9_-]*)\b(?=\s*(?:=|!=|~|>=|<=|>|<|in\b|is\b|not\b))",
    re.IGNORECASE,
)
PROJECT_EQ_RE = re.compile(
    r"\bproject\s*=\s*(?:\"([A-Za-z0-9_-]+)\"|([A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


def normalize_custom_fields(db: Session, jql: str) -> str:
    """Validate cf_* against the selected project template and resolve field aliases.

    A global search is only accepted when the key has one unambiguous type across all
    project-bound template versions.
    """
    references = {match.group(1) for match in CF_FIELD_RE.finditer(jql)}
    if not references:
        return jql
    project_match = PROJECT_EQ_RE.search(jql)
    projects: list[Project]
    if project_match:
        key = (project_match.group(1) or project_match.group(2)).upper()
        project = db.scalar(select(Project).where(func.upper(Project.key) == key))
        if project is None:
            raise JqlError(f"未知项目：{key}")
        projects = [project]
    else:
        projects = list(db.scalars(select(Project)))
    registry: dict[str, list[tuple[str, str]]] = {}
    for project in projects:
        for field in (profile_of(project).definition or {}).get("fields") or []:
            canonical = str(field["key"])
            kind = str(field.get("kind") or "text")
            for name in (canonical, *(field.get("aliases") or [])):
                registry.setdefault(str(name).lower(), []).append((canonical, kind))
    replacements: dict[str, str] = {}
    for reference in references:
        matches = registry.get(reference.lower(), [])
        if not matches:
            raise JqlError(f"当前查询范围没有模板字段 cf_{reference}")
        canonical = {item[0] for item in matches}
        kinds = {item[1] for item in matches}
        if len(canonical) > 1 or len(kinds) > 1:
            raise JqlError(f"跨项目字段 cf_{reference} 类型或别名不一致，请限定项目")
        replacements[reference.lower()] = next(iter(canonical))
    return CF_FIELD_RE.sub(
        lambda match: f"cf_{replacements[match.group(1).lower()]}",
        jql,
    )


def _visible(db: Session, user_id: int | None) -> list[ColumnElement[bool]]:
    clause = visible_issue_clause(db, db.get(User, user_id) if user_id else None)
    return [] if clause is None else [clause]


# View filters narrow a query's results by exact values picked from its facets.
VIEW_FIELDS = ("project", "type", "status", "priority", "machine", "assignee", "reporter", "label")
EMPTY = "EMPTY"
FACET_CACHE_TTL = 20.0
FACET_CACHE_MAX = 256
_facet_cache: OrderedDict[tuple[int | None, str], tuple[float, dict[str, Any]]] = OrderedDict()
_facet_cache_lock = threading.Lock()


def clear_facet_cache() -> None:
    with _facet_cache_lock:
        _facet_cache.clear()


def _facet_key(user_id: int | None, jql: str, view: dict[str, list[str]]) -> tuple[int | None, str]:
    """Hash high-cardinality filter strings so they never live in cache keys or metrics."""
    normalized = json.dumps(
        {"jql": jql, "view": view},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return user_id, hashlib.sha256(normalized.encode()).hexdigest()


def cached_search_facets(
    db: Session,
    jql: str,
    view: dict[str, list[str]],
    *,
    user_id: int | None,
) -> dict[str, Any]:
    from ..metrics import facets_cache

    key = _facet_key(user_id, jql, view)
    now = monotonic_time.monotonic()
    with _facet_cache_lock:
        cached = _facet_cache.get(key)
        if cached is not None and now - cached[0] < FACET_CACHE_TTL:
            _facet_cache.move_to_end(key)
            facets_cache("hit")
            return cached[1]
        _facet_cache.pop(key, None)
    facets_cache("miss")
    result = search_facets(db, jql, view, user_id=user_id)
    with _facet_cache_lock:
        _facet_cache[key] = (now, result)
        _facet_cache.move_to_end(key)
        while len(_facet_cache) > FACET_CACHE_MAX:
            _facet_cache.popitem(last=False)
    return result


def view_conditions(view: dict[str, list[str]], *, user_id: int | None,
                    skip: str | None = None) -> list[ColumnElement[bool]]:
    ctx = Context(user_id=user_id, today=date.today())
    conditions = []
    for field in VIEW_FIELDS:
        values = [str(v) for v in (view.get(field) or []) if str(v)]
        if field == skip or not values:
            continue
        if field in {"machine", "label"}:
            named = [v for v in values if v != EMPTY]
            parts = [build(field, "=", named, ctx)] if named else []
            if EMPTY in values:
                parts.append(empty_clause(field))
            conditions.append(or_(*parts))
        else:
            conditions.append(build(field, "=", values, ctx))
    return conditions


def search_facets(db: Session, jql: str, view: dict[str, list[str]], *,
                  user_id: int | None) -> dict[str, Any]:
    """Value counts per view field. Each field is counted with the *other* view filters applied,
    so picking a status still shows how many issues every other status has."""
    from ..serialize import user_brief

    where, _ = compile_jql(normalize_custom_fields(db, jql), user_id=user_id)
    where = and_(where, *_visible(db, user_id))

    def scope(field: str) -> list[ColumnElement[bool]]:
        return [where, *view_conditions(view, user_id=user_id, skip=field)]

    count = func.count(Issue.id.distinct())
    facets: dict[str, Any] = {
        "total": db.scalar(select(func.count(Issue.id)).where(where)) or 0,
        "project": [
            {"value": key, "label": name, "count": n}
            for key, name, n in db.execute(
                select(Project.key, Project.name, count)
                .join(Project, Issue.project_id == Project.id)
                .where(*scope("project")).group_by(Project.key, Project.name).order_by(Project.key)
            )
        ],
        "type": [
            {"value": value, "count": n}
            for value, n in db.execute(
                select(Issue.type, count).where(*scope("type")).group_by(Issue.type)
            )
        ],
        "status": [
            {"value": name, "color": color, "category": category, "count": n}
            for name, color, category, n in db.execute(
                select(Status.name, func.min(Status.color), func.min(Status.category), count)
                .join(Status, Issue.status_id == Status.id)
                .where(*scope("status")).group_by(Status.name)
                .order_by(func.min(Status.sort_order))
            )
        ],
        "priority": [
            {"value": value, "count": n}
            for value, n in db.execute(
                select(Issue.priority, count).where(*scope("priority")).group_by(Issue.priority)
            )
        ],
    }
    machines = [
        {"value": f"{group}/{name}", "group": group, "label": name, "count": n}
        for group, name, n in db.execute(
            select(MachineGroup.name, Machine.name, count)
            .join(IssueMachine, IssueMachine.issue_id == Issue.id)
            .join(Machine, IssueMachine.machine_id == Machine.id)
            .join(MachineGroup, Machine.group_id == MachineGroup.id)
            .where(*scope("machine")).group_by(MachineGroup.name, Machine.name)
            .order_by(MachineGroup.name, func.min(Machine.sort_order))
        )
    ]
    no_machine = db.scalar(
        select(count).where(*scope("machine"), empty_clause("machine"))
    ) or 0
    if no_machine:
        machines.append({"value": EMPTY, "count": no_machine})
    facets["machine"] = machines
    for field, column in (("assignee", Issue.assignee_id), ("reporter", Issue.reporter_id)):
        rows = list(db.execute(
            select(column, count).where(*scope(field)).group_by(column).order_by(count.desc())
        ))
        users = {u.id: u for u in db.scalars(
            select(User).where(User.id.in_([uid for uid, _ in rows if uid]))
        )}
        facets[field] = [
            {"value": users[uid].username, "user": user_brief(users[uid]), "count": n}
            if uid in users else {"value": EMPTY, "count": n}
            for uid, n in rows if uid is None or uid in users
        ]
    facets["label"] = _label_facets(db, scope("label"))
    return facets


def _label_facets(db: Session, conditions: list[ColumnElement[bool]]) -> list[dict[str, Any]]:
    """Count labels; Postgres unnests in SQL, SQLite keeps a light Python pass."""
    from ..db import is_postgres

    if is_postgres(db):
        from sqlalchemy import true
        from sqlalchemy.dialects.postgresql import JSONB

        empty_json = cast("[]", JSONB)
        labels_jsonb = func.coalesce(cast(Issue.labels, JSONB), empty_json)
        lab = func.jsonb_array_elements_text(labels_jsonb).table_valued("value").alias("lab")
        trimmed = func.trim(lab.c.value)
        rows = list(db.execute(
            select(trimmed, func.count())
            .select_from(Issue)
            .join(lab, true())
            .where(*conditions, trimmed != "")
            .group_by(trimmed)
            .order_by(func.count().desc(), trimmed)
        ))
        empty_labels = db.scalar(
            select(func.count(Issue.id)).where(
                *conditions,
                or_(
                    Issue.labels.is_(None),
                    cast(Issue.labels, String) == "[]",
                    cast(Issue.labels, String) == "null",
                ),
            )
        ) or 0
        out = [{"value": value, "count": n} for value, n in rows]
        if empty_labels:
            out.append({"value": EMPTY, "count": empty_labels})
        return out

    label_counts: Counter[str] = Counter()
    empty_labels = 0
    for labels in db.scalars(select(Issue.labels).where(*conditions)):
        cleaned = [str(lab).strip() for lab in (labels or []) if str(lab).strip()]
        if not cleaned:
            empty_labels += 1
            continue
        label_counts.update(cleaned)
    out = [
        {"value": value, "count": n}
        for value, n in sorted(label_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    if empty_labels:
        out.append({"value": EMPTY, "count": empty_labels})
    return out
