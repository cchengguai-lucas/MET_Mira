"""Excel export (requirement-sheet layout) and import of existing requirement sheets."""

from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, selectinload

from ..models import Issue, Machine, MachineGroup, MasterAsset, Project, Status, User
from .issues import create_issue, update_issue
from .profiles import Profile, profile_of
from .software import software_unit_ids

PRIORITY_LABELS = {"highest": "最高", "high": "高", "medium": "中", "low": "低", "lowest": "最低"}
TYPE_LABELS = {"requirement": "需求", "bug": "Bug", "task": "任务", "story": "故事",
               "epic": "Epic", "subtask": "子任务"}

SECTIONS = [
    ("需求提出人填写", "8EA9C1", ["序号", "Key", "需求提出人", "项目名称", "机台组", "机台", "需求说明"]),
    ("电气组填写", "A9D08E", ["影响范围", "是否涉及软件", "软件负责人", "验证方法", "验证标准",
                          "当前状态", "负责人"]),
    ("软件组填写", "8FB4E0", [
        "软件是否准备好", "软件交付说明", "软件Jira单号",
        "软件Jira经办人", "软件Jira测试人", "软件Jira状态", "软件Jira截止日期",
    ]),
    ("方案确认", "B7A3F0", ["方案说明", "电气确认", "工艺确认", "软件确认"]),
    ("测试人员填写", "5FC9BF", ["更新日期", "测试人员", "是否通过", "备注"]),
]
WIDTHS = {"序号": 6, "Key": 11, "需求提出人": 12, "项目名称": 16, "机台组": 11, "机台": 12,
          "需求说明": 48, "影响范围": 16, "是否涉及软件": 10, "软件负责人": 10, "验证方法": 24, "验证标准": 24,
          "当前状态": 10, "负责人": 10, "更新日期": 12, "测试人员": 10, "是否通过": 9, "备注": 24,
          "软件是否准备好": 12, "软件交付说明": 24, "软件Jira单号": 16,
          "方案说明": 36, "电气确认": 10, "工艺确认": 10, "软件确认": 10,
          "软件Jira经办人": 12, "软件Jira测试人": 12, "软件Jira状态": 12, "软件Jira截止日期": 14}


def _yes_no(value: bool | None) -> str:
    return "" if value is None else ("是" if value else "否")


def _passed(value: bool | None) -> str:
    return "" if value is None else ("通过" if value else "不通过")


def _plain(text: str) -> str:
    """Embedded screenshots become ``[图片：name]`` instead of raw Markdown in the sheet."""
    return re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[图片：\1]", text or "")


def export_issues(
    issues: list[Issue],
    title: str = "需求跟踪",
    project: Project | None = None,
    *,
    base_url: str | None = None,
) -> bytes:
    profile = profile_of(project or (issues[0].project if issues else None))
    sections = SECTIONS
    if profile.key != "electrical":
        base = SECTIONS[0]
        sections = [base]
        colors = ("A9D08E", "5FC9BF", "C4A7E7")
        for index, section in enumerate(profile.sections):
            custom = [profile.custom_field(key) for key in section.fields]
            custom = [
                field for field in custom
                if field is not None and (
                    next((raw for raw in (profile.definition or {}).get("fields", [])
                          if raw.get("key") == field.key), {}).get("excel", True)
                )
            ]
            labels = [field.label for field in custom if field is not None]
            if labels:
                sections.append((section.label, colors[index % len(colors)], labels))
        sections.append(("跟踪", "8E9AAF", ["当前状态", "负责人"]))
    wb = Workbook()
    ws = wb.active
    ws.title = title[:28] or "需求跟踪"
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    col = 1
    headers: list[str] = []
    for section, color, columns in sections:
        start = col
        ws.cell(row=1, column=start, value=section)
        for name in columns:
            cell = ws.cell(row=2, column=col, value=name)
            cell.fill = PatternFill("solid", fgColor=color)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border
            ws.column_dimensions[get_column_letter(col)].width = WIDTHS.get(name, 12)
            headers.append(name)
            col += 1
        ws.merge_cells(start_row=1, start_column=start, end_row=1, end_column=col - 1)
        head = ws.cell(row=1, column=start)
        head.fill = PatternFill("solid", fgColor=color)
        head.font = Font(bold=True, size=12)
        head.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "C3"

    def group_of(issue: Issue) -> str:
        names = sorted({m.group.name for m in issue.machines if m.group})
        return names[0] if names else "未指定"

    ordered = sorted(issues, key=lambda i: (group_of(i), i.number))
    row = 3
    group_start: dict[str, int] = {}
    group_end: dict[str, int] = {}
    for index, issue in enumerate(ordered, start=1):
        group = group_of(issue)
        machines = "、".join(m.name for m in issue.machines if m.group and m.group.name == group)
        other = [f"{m.group.name}/{m.name}" for m in issue.machines
                 if m.group and m.group.name != group]
        if other:
            machines = "、".join(filter(None, [machines, *other]))
        values = {
            "序号": index,
            "Key": issue.key,
            "需求提出人": issue.reporter.display_name if issue.reporter else "",
            "项目名称": issue.project_name,
            "机台组": group,
            "机台": machines,
            "需求说明": f"{issue.title}\n{_plain(issue.description)}".strip()
            if issue.description else issue.title,
            "影响范围": "、".join(issue.update_scope or []),
            "是否涉及软件": _yes_no(issue.involves_software),
            "软件负责人": issue.software_owner.display_name if issue.software_owner else "",
            "方案说明": issue.scheme_note or "",
            "电气确认": _yes_no(issue.electrical_scheme_ok),
            "工艺确认": _yes_no(issue.process_scheme_ok),
            "软件确认": _yes_no(issue.software_scheme_ok),
            "软件是否准备好": _yes_no(issue.software_ready),
            "软件交付说明": issue.software_note or "",
            "软件Jira单号": issue.software_jira_key or "",
            "软件Jira经办人": (issue.software_jira_snapshot or {}).get("assignee") or "",
            "软件Jira测试人": (issue.software_jira_snapshot or {}).get("tester") or "",
            "软件Jira状态": (issue.software_jira_snapshot or {}).get("status") or "",
            "软件Jira截止日期": (issue.software_jira_snapshot or {}).get("due_date") or "",
            "验证方法": issue.verify_method,
            "验证标准": issue.verify_criteria,
            "当前状态": issue.status.name if issue.status else "",
            "负责人": issue.assignee.display_name if issue.assignee else "",
            "更新日期": issue.update_date,
            "测试人员": issue.tester.display_name if issue.tester else "",
            "是否通过": _passed(issue.test_passed),
            "备注": _plain(issue.remark),
        }
        session = object_session(issue)
        for field in profile.custom_fields:
            raw = (issue.extra or {}).get(field.key)
            if field.kind == "master_ref" and session is not None:
                ids = raw if isinstance(raw, list) else [raw] if isinstance(raw, int) else []
                assets = list(session.scalars(select(MasterAsset).where(MasterAsset.id.in_(ids))))
                names = {asset.id: f"{asset.code} · {asset.name}" for asset in assets}
                raw = "、".join(names.get(asset_id, str(asset_id)) for asset_id in ids)
            elif field.kind == "user" and isinstance(raw, int) and session is not None:
                referenced = session.get(User, raw)
                raw = referenced.display_name if referenced else str(raw)
            elif field.kind == "machine" and isinstance(raw, list) and session is not None:
                machines = list(session.scalars(select(Machine).where(Machine.id.in_(raw))))
                names = {machine.id: f"{machine.group.name}/{machine.name}" for machine in machines}
                raw = "、".join(names.get(machine_id, str(machine_id)) for machine_id in raw)
            values[field.label] = raw
        for c, name in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=c, value=values.get(name))
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if name == "Key" and issue.key and base_url:
                cell.hyperlink = f"{base_url.rstrip('/')}/issues/{issue.key}"
                cell.font = Font(color="0563C1", underline="single")
            if name == "更新日期" and values.get(name):
                cell.number_format = "yyyy-mm-dd"
        group_start.setdefault(group, row)
        group_end[group] = row
        row += 1
    group_col = headers.index("机台组") + 1
    for group, start in group_start.items():
        end = group_end[group]
        if end > start:
            ws.merge_cells(start_row=start, start_column=group_col, end_row=end,
                           end_column=group_col)
        ws.cell(row=start, column=group_col).alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def apply_profile_guesses(parsed: dict[str, Any], profile: Profile) -> None:
    """Recognize schema fields by their exact exported label."""
    labels = {}
    for field in profile.custom_fields:
        for label in (field.label, field.key, *field.aliases):
            labels[label.replace(" ", "").lower()] = field.key
    for column in parsed["columns"]:
        key = labels.get(str(column["header"]).replace(" ", "").lower())
        if key:
            column["guess"] = f"custom.{key}"


# ------------------------------------------------------------------------- import

FIELD_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("seq", ("序号", "编号", "no.")),
    ("key", ("key",)),
    ("reporter", ("需求提出人", "提出人", "申请人")),
    ("project_name", ("项目名称", "项目")),
    ("machine_group", ("机台组",)),
    ("machine", ("机台", "设备")),
    ("title", ("标题", "主题")),
    ("description", ("需求说明", "需求描述", "说明", "描述")),
    ("update_scope", ("影响范围", "更新涉及", "涉及范围", "Impact scope", "Update scope")),
    ("software_owner", ("软件负责人",)),
    ("involves_software", ("是否涉及软件", "涉及软件")),
    ("software_ready", ("软件是否准备好",)),
    ("scheme_note", ("方案说明",)),
    ("electrical_scheme_ok", ("电气确认",)),
    ("process_scheme_ok", ("工艺确认",)),
    ("software_scheme_ok", ("软件确认",)),
    ("software_note", ("软件交付说明",)),
    ("software_jira_key", ("软件jira单号",)),
    ("verify_method", ("验证方法",)),
    ("verify_criteria", ("验证标准",)),
    ("status", ("当前状态", "状态")),
    ("assignee", ("负责人", "处理人")),
    ("update_date", ("更新日期", "完成日期")),
    ("tester", ("测试人员", "测试人")),
    ("test_passed", ("是否通过", "测试结果")),
    ("remark", ("备注",)),
    ("priority", ("优先级",)),
    ("due_date", ("截止日期", "计划完成")),
]
IMPORT_FIELDS = [f for f, _ in FIELD_KEYWORDS]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _guess(header: str) -> str | None:
    lowered = header.lower().replace(" ", "")
    if lowered in {
        "软件jira经办人", "软件jira测试人", "软件jira状态", "软件jira截止日期",
    }:
        return None
    exact = {
        "软件是否准备好": "software_ready",
        "软件交付说明": "software_note",
        "软件jira单号": "software_jira_key",
        "方案说明": "scheme_note",
        "电气确认": "electrical_scheme_ok",
        "工艺确认": "process_scheme_ok",
        "软件确认": "software_scheme_ok",
    }
    if lowered in exact:
        return exact[lowered]
    for field, words in FIELD_KEYWORDS:
        if any(w in lowered for w in words):
            return field
    return None


def _merged_value_grid(ws) -> dict[tuple[int, int], Any]:
    grid: dict[tuple[int, int], Any] = {}
    for rng in ws.merged_cells.ranges:
        value = ws.cell(row=rng.min_row, column=rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                grid[(r, c)] = value
    return grid


def read_sheet(path: Path, sheet: str | None = None) -> dict[str, Any]:
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
    merged = _merged_value_grid(ws)
    max_col = min(ws.max_column, 60)

    def value(r: int, c: int) -> Any:
        if (r, c) in merged:
            return merged[(r, c)]
        return ws.cell(row=r, column=c).value

    best_row, best_score = 1, -1
    for r in range(1, min(ws.max_row, 12) + 1):
        score = sum(1 for c in range(1, max_col + 1) if _guess(_text(value(r, c))))
        if score > best_score:
            best_row, best_score = r, score
    columns = []
    used: set[str] = set()
    machine_cols = []
    for c in range(1, max_col + 1):
        header = _text(value(best_row, c))
        if not header:
            continue
        guess = _guess(header)
        if guess == "machine":
            machine_cols.append(c)
        columns.append({"index": c, "header": header, "guess": guess})
    if len(machine_cols) >= 2:
        for col in columns:
            if col["index"] == machine_cols[0]:
                col["guess"] = "machine_group"
    for col in columns:
        if col["guess"] in used and col["guess"] not in {"machine"}:
            col["guess"] = None
        if col["guess"]:
            used.add(col["guess"])
    rows = []
    for r in range(best_row + 1, ws.max_row + 1):
        cells = {c["index"]: value(r, c["index"]) for c in columns}
        own = [ws.cell(row=r, column=c["index"]).value for c in columns]
        if all(v in (None, "") for v in own):
            continue
        rows.append({"row": r, "cells": cells})
    return {
        "sheets": wb.sheetnames,
        "sheet": ws.title,
        "header_row": best_row,
        "columns": columns,
        "rows": rows,
    }


def _split_multi(raw: str) -> list[str]:
    parts = re.split(r"[,，、;；\n/]+", raw)
    return [re.sub(r"^\s*\d+[\.\、\)]\s*", "", p).strip() for p in parts if p.strip()]


def _bool(raw: str) -> bool | None:
    value = raw.strip().lower()
    if not value:
        return None
    if value in {"是", "y", "yes", "true", "√", "✓", "通过", "ok", "pass", "1"}:
        return True
    if value in {"否", "n", "no", "false", "×", "x", "不通过", "ng", "fail", "0"}:
        return False
    return None


def _date(raw: Any) -> date | None:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = _text(raw)
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


class ImportContext:
    def __init__(self, db: Session, project: Project, create_missing_machines: bool):
        self.db = db
        self.project = project
        self.create_missing = create_missing_machines
        self.statuses = list(db.scalars(select(Status).where(Status.project_id == project.id)))
        self.groups = {
            g.name.lower(): g for g in db.scalars(
                select(MachineGroup).options(selectinload(MachineGroup.machines))
                .where(MachineGroup.project_id == project.id)
            )
        }
        self.users = {}
        for user in db.scalars(select(User)):
            self.users.setdefault(user.display_name, user)
            self.users.setdefault(user.username.lower(), user)
        self.assets: dict[str, dict[str, MasterAsset]] = defaultdict(dict)
        for asset in db.scalars(select(MasterAsset)):
            self.assets[asset.kind][asset.code.lower()] = asset
            self.assets[asset.kind][asset.name.lower()] = asset
        self.scope_options = (project.settings or {}).get("update_scope_options", [])
        self.software_units: set[int] | None = None
        self.warnings: dict[str, set[str]] = defaultdict(set)

    def is_software_member(self, user: User) -> bool:
        if self.software_units is None:
            self.software_units = software_unit_ids(self.db, self.project)
        return not self.software_units or user.org_unit_id in self.software_units

    def user(self, name: str) -> User | None:
        if not name:
            return None
        user = self.users.get(name) or self.users.get(name.lower())
        if user is None:
            self.warnings["未匹配的人员"].add(name)
        return user

    def status(self, raw: str) -> Status | None:
        name = re.sub(r"^\s*\d+[\.\、\)]\s*", "", raw).strip()
        if not name:
            return None
        for status in self.statuses:
            if status.name == name:
                return status
        for status in self.statuses:
            if name in status.name or status.name in name:
                return status
        self.warnings["未匹配的状态"].add(name)
        return None

    def machines(self, group_raw: str, machine_raw: str) -> list[int]:
        group_names = _split_multi(group_raw) if group_raw else []
        machine_names = _split_multi(machine_raw) if machine_raw else []
        ids: list[int] = []
        if not group_names and machine_names:
            for name in machine_names:
                if "/" in name:
                    g, m = name.split("/", 1)
                    ids += self._machine(g.strip(), m.strip())
                else:
                    found = [m.id for g in self.groups.values() for m in g.machines
                             if m.name == name]
                    if found:
                        ids.append(found[0])
                    elif name.lower() in self.groups:
                        ids += self._machine(name, "整机")
                    else:
                        self.warnings["未匹配的机台"].add(name)
            return ids
        for group in group_names:
            for machine in machine_names or ["整机"]:
                ids += self._machine(group, machine)
        return ids

    def _machine(self, group_name: str, machine_name: str) -> list[int]:
        group = self.groups.get(group_name.lower())
        if group is None:
            if not self.create_missing:
                self.warnings["未匹配的机台组"].add(group_name)
                return []
            group = MachineGroup(project_id=self.project.id, name=group_name,
                                 sort_order=len(self.groups) + 1)
            self.db.add(group)
            self.db.flush()
            self.db.refresh(group)
            self.groups[group_name.lower()] = group
        for machine in group.machines:
            if machine.name.lower() == machine_name.lower():
                return [machine.id]
        if not self.create_missing:
            self.warnings["未匹配的机台"].add(f"{group_name}/{machine_name}")
            return []
        machine = Machine(group_id=group.id, name=machine_name, sort_order=len(group.machines))
        self.db.add(machine)
        self.db.flush()
        group.machines.append(machine)
        return [machine.id]

    def scope(self, raw: str) -> list[str]:
        result = []
        for part in _split_multi(raw):
            match = next((o for o in self.scope_options if o in part or part in o), None)
            result.append(match or part)
        return list(dict.fromkeys(result))

    def asset(self, kind: str, raw: str, multiple: bool) -> int | list[int] | None:
        ids = []
        for value in _split_multi(raw):
            clean = value.split("·", 1)[0].strip().lower()
            row = self.assets.get(kind, {}).get(clean) or self.assets.get(kind, {}).get(
                value.split("·")[-1].strip().lower()
            )
            if row is None:
                self.warnings["未匹配的主数据"].add(value)
            else:
                ids.append(row.id)
        return list(dict.fromkeys(ids)) if multiple else (ids[0] if ids else None)


def map_row(ctx: ImportContext, mapping: dict[int, str], cells: dict[int, Any],
            actor: User) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for col, field in mapping.items():
        if field and (field in IMPORT_FIELDS or field.startswith("custom.")):
            value = cells.get(col)
            if field in raw and _text(raw[field]):
                raw[field] = f"{_text(raw[field])}、{_text(value)}" if _text(value) else raw[field]
            else:
                raw[field] = value
    description = _text(raw.get("description"))
    title = _text(raw.get("title"))
    if not title:
        first_line = description.splitlines()[0] if description else ""
        title = first_line[:60] or "（无标题）"
    data: dict[str, Any] = {
        "type": "requirement",
        "title": title,
        "description": description,
        "project_name": _text(raw.get("project_name")),
        "machine_ids": ctx.machines(_text(raw.get("machine_group")), _text(raw.get("machine"))),
        "update_scope": ctx.scope(_text(raw.get("update_scope"))),
        "involves_software": _bool(_text(raw.get("involves_software"))),
        "verify_method": _text(raw.get("verify_method")),
        "verify_criteria": _text(raw.get("verify_criteria")),
        "update_date": _date(raw.get("update_date")),
        "due_date": _date(raw.get("due_date")),
        "test_passed": _bool(_text(raw.get("test_passed"))),
        "remark": _text(raw.get("remark")),
        "custom_fields": {},
    }
    if "software_ready" in raw:
        data["software_ready"] = _bool(_text(raw["software_ready"]))
    if "scheme_note" in raw:
        data["scheme_note"] = _text(raw["scheme_note"])
    for flag in ("electrical_scheme_ok", "process_scheme_ok", "software_scheme_ok"):
        if flag in raw:
            data[flag] = _bool(_text(raw[flag]))
    if "software_note" in raw:
        data["software_note"] = _text(raw["software_note"])
    if "software_jira_key" in raw:
        data["software_jira_key"] = _text(raw["software_jira_key"])[:64]
    profile = profile_of(ctx.project)
    for key, value in raw.items():
        if not key.startswith("custom."):
            continue
        name = key.removeprefix("custom.")
        field = profile.custom_field(name)
        if field is None:
            continue
        if field.kind == "date":
            parsed_date = _date(value)
            data["custom_fields"][name] = parsed_date.isoformat() if parsed_date else ""
        elif field.kind == "number":
            try:
                data["custom_fields"][name] = float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                data["custom_fields"][name] = None
        elif field.kind == "boolean":
            data["custom_fields"][name] = _bool(_text(value))
        elif field.kind == "master_ref" and field.master_kind:
            data["custom_fields"][name] = ctx.asset(
                field.master_kind, _text(value), field.multiple
            )
        elif field.kind == "user":
            user = ctx.user(_text(value))
            data["custom_fields"][name] = user.id if user else None
        elif field.kind == "machine":
            data["custom_fields"][name] = ctx.machines("", _text(value))
        else:
            data["custom_fields"][name] = _text(value)
    reporter_name = _text(raw.get("reporter"))
    reporter = ctx.user(reporter_name)
    data["reporter_id"] = reporter.id if reporter else actor.id
    if reporter_name and reporter is None:
        data["remark"] = "\n".join(filter(None, [data["remark"], f"原需求提出人：{reporter_name}"]))
    for field, target in (("tester", "tester_id"), ("assignee", "assignee_id")):
        person = ctx.user(_text(raw.get(field)))
        data[target] = person.id if person else None
    owner_name = _text(raw.get("software_owner"))
    owner = ctx.user(owner_name)
    if owner and data["involves_software"] and ctx.is_software_member(owner):
        data["software_owner_id"] = owner.id
    elif owner_name:
        data["remark"] = "\n".join(filter(None, [data["remark"], f"原软件负责人：{owner_name}"]))
    status = ctx.status(_text(raw.get("status")))
    if status is not None:
        data["status_id"] = status.id
    priority = _text(raw.get("priority"))
    reverse = {v: k for k, v in PRIORITY_LABELS.items()}
    if priority in reverse:
        data["priority"] = reverse[priority]
    return data


def import_rows(db: Session, project: Project, actor: User, parsed: dict[str, Any],
                mapping: dict[int, str], *, batch_id: int, create_missing_machines: bool,
                limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    ctx = ImportContext(db, project, create_missing_machines)
    results = []
    for row in parsed["rows"][:limit] if limit else parsed["rows"]:
        raw_cells = row["cells"]
        data = map_row(ctx, mapping, raw_cells, actor)
        if not data["description"] and data["title"] == "（无标题）":
            continue
        data["import_batch_id"] = batch_id
        # Honour Key for upsert. Import is a trusted path: allow_status keeps sheet
        # status + test_passed coherent (post-create status moves would clear the result).
        key = _text(_mapped_cell(mapping, raw_cells, "key"))
        existing = None
        if key:
            existing = db.scalar(
                select(Issue).where(Issue.project_id == project.id, Issue.key == key)
            )
            if existing is None and "-" in key:
                ctx.warnings.setdefault("unknown_key", set()).add(key)
        if existing is not None:
            payload = {k: v for k, v in data.items()
                       if k not in ("type", "import_batch_id", "reporter_id") and v is not None}
            update_issue(db, existing, actor, payload, enforce=False, sync_jira=False)
            results.append({
                "row": row["row"], "key": existing.key, "title": existing.title,
                "action": "updated",
            })
            continue
        issue = create_issue(db, project, actor, data, allow_status=True)
        results.append({
            "row": row["row"], "key": issue.key, "title": issue.title, "action": "created",
        })
    return results, {k: sorted(v) for k, v in ctx.warnings.items()}


def _mapped_cell(mapping: dict[int, str], cells: list[Any], field: str) -> Any:
    for idx, name in mapping.items():
        if name == field and idx < len(cells):
            return cells[idx]
    return None


def preview_rows(db: Session, project: Project, actor: User, parsed: dict[str, Any],
                 mapping: dict[int, str], limit: int = 30) -> dict[str, Any]:
    ctx = ImportContext(db, project, create_missing_machines=False)
    rows = []
    for row in parsed["rows"]:
        data = map_row(ctx, mapping, row["cells"], actor)
        key = _text(_mapped_cell(mapping, row["cells"], "key"))
        existing = (
            db.scalar(select(Issue.id).where(Issue.project_id == project.id, Issue.key == key))
            if key else None
        )
        if len(rows) < limit:
            rows.append({
                "row": row["row"],
                "key": key or None,
                "action": "updated" if existing else "created",
                "title": data["title"],
                "project_name": data["project_name"],
                "machine_count": len(data["machine_ids"]),
                "update_scope": data["update_scope"],
                "status_id": data.get("status_id"),
                "test_passed": data["test_passed"],
            })
    return {"rows": rows, "total": len(parsed["rows"]),
            "warnings": {k: sorted(v)[:50] for k, v in ctx.warnings.items()}}
