"""Safe preview and transactional application of a project template change."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    ApprovalRequest,
    Board,
    DepartmentTemplateVersion,
    Issue,
    Project,
    ProjectMember,
    ProjectTemplateMigration,
    SavedFilter,
    Status,
)
from . import profiles, templates
from .workflow import include_missing_status_columns


def _definition(project: Project) -> dict[str, Any]:
    profile = profiles.profile_of(project)
    return deepcopy(profile.definition or profiles.builtin_definition(profile))


def _fields(definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["key"]): row for row in definition.get("fields") or []}


def _status_rows(db: Session, project: Project) -> list[tuple[Status, int]]:
    counts = dict(db.execute(
        select(Issue.status_id, func.count(Issue.id))
        .where(Issue.project_id == project.id).group_by(Issue.status_id)
    ).all())
    return [(status, counts.get(status.id, 0)) for status in project.statuses]


def preview(db: Session, project: Project, target: DepartmentTemplateVersion) -> dict[str, Any]:
    if target.status != "published":
        raise ValueError("目标模板版本尚未发布")
    source = _definition(project)
    target_def = templates.validate_definition(dict(target.definition))
    source_fields, target_fields = _fields(source), _fields(target_def)
    usage: Counter[str] = Counter()
    for issue in db.scalars(select(Issue).where(Issue.project_id == project.id)):
        for key, field in source_fields.items():
            if templates.get_value(issue, field) not in (None, "", []):
                usage[key] += 1
    target_statuses = target_def.get("statuses") or []
    status_diff = []
    for status, count in _status_rows(db, project):
        exact = next((s for s in target_statuses if s["name"] == status.name), None)
        category = next((s for s in target_statuses
                         if s["category"] == status.category), None)
        status_diff.append({
            "id": status.id, "name": status.name, "category": status.category,
            "issues": count, "suggested": (exact or category or target_statuses[0])["name"],
        })
    field_diff = []
    for key, field in source_fields.items():
        target_key = key if key in target_fields else next((
            name for name, candidate in target_fields.items()
            if key in (candidate.get("aliases") or [])
            or (candidate.get("labels") or {}).get("zh") == (field.get("labels") or {}).get("zh")
        ), None)
        field_diff.append({
            "key": key, "label": (field.get("labels") or {}).get("zh") or key,
            "kind": field.get("kind"), "usage": usage[key], "suggested": target_key,
            "compatible": (
                not target_key
                or field.get("kind") == target_fields[target_key].get("kind")
            ),
        })
    target_roles = {
        str(r["key"] if isinstance(r, dict) else r) for r in target_def.get("roles") or []
    }
    members = list(db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id)))
    invalid_roles = Counter(row.role for row in members if row.role not in target_roles)
    gates = (project.settings or {}).get("transition_gates") or {}
    pending = db.scalar(
        select(func.count(ApprovalRequest.id)).join(Issue)
        .where(Issue.project_id == project.id, ApprovalRequest.status == "pending")
    ) or 0
    references = [SavedFilter.jql.ilike(f"%cf_{key}%") for key in source_fields]
    filters = list(db.scalars(select(SavedFilter).where(
        SavedFilter.share_project_id == project.id,
        or_(*references) if references else SavedFilter.id < 0,
    )))
    return {
        "source": {
            "version_id": project.template_version_id,
            "key": source.get("key"),
            "label": (source.get("labels") or {}).get("zh"),
        },
        "target": {
            "version_id": target.id, "key": target_def.get("key"),
            "label": (target_def.get("labels") or {}).get("zh"),
        },
        "sections": {
            "removed": sorted({s["key"] for s in source.get("sections") or []}
                              - {s["key"] for s in target_def.get("sections") or []}),
            "added": sorted({s["key"] for s in target_def.get("sections") or []}
                            - {s["key"] for s in source.get("sections") or []}),
        },
        "fields": field_diff,
        "added_fields": sorted(set(target_fields) - set(source_fields)),
        "statuses": status_diff,
        "target_statuses": target_statuses,
        "invalid_roles": dict(invalid_roles),
        "target_roles": sorted(target_roles),
        "gate_statuses": list(gates),
        "pending_approvals": pending,
        "boards": db.scalar(
            select(func.count(Board.id)).where(Board.project_id == project.id)
        ) or 0,
        "saved_filters": [{"id": row.id, "name": row.name, "jql": row.jql} for row in filters],
    }


def apply(
    db: Session,
    project: Project,
    target: DepartmentTemplateVersion,
    mapping: dict[str, Any],
    actor_id: int,
) -> ProjectTemplateMigration:
    snapshot = preview(db, project, target)
    target_def = templates.validate_definition(dict(target.definition))
    source_def = _definition(project)
    status_map = {int(k): str(v) for k, v in (mapping.get("status_map") or {}).items()}
    used_statuses = {row["id"] for row in snapshot["statuses"] if row["issues"]}
    if not used_statuses <= set(status_map):
        raise ValueError("所有有问题的原状态都必须映射")
    allowed_names = {s["name"] for s in target_def["statuses"]}
    if any(name not in allowed_names for name in status_map.values()):
        raise ValueError("状态映射引用了目标模板以外的状态")
    field_map = {str(k): v for k, v in (mapping.get("field_map") or {}).items()}
    source_fields, target_fields = _fields(source_def), _fields(target_def)
    used_fields = {row["key"] for row in snapshot["fields"] if row["usage"]}
    if not used_fields <= set(field_map):
        raise ValueError("所有有数据的旧字段都必须映射、保留或清空")
    allowed_field_actions = {*target_fields, "clear", "orphan", None}
    if any(action not in allowed_field_actions for action in field_map.values()):
        raise ValueError("字段映射包含未知目标")
    role_map = {str(k): str(v) for k, v in (mapping.get("role_map") or {}).items()}
    target_roles = {
        str(r["key"] if isinstance(r, dict) else r) for r in target_def["roles"]
    }
    if any(value not in target_roles for value in role_map.values()):
        raise ValueError("角色映射引用了目标模板以外的角色")

    by_name = {status.name: status for status in project.statuses}
    for order, raw in enumerate(target_def["statuses"]):
        status = by_name.get(raw["name"])
        if status is None:
            status = Status(project_id=project.id, name=raw["name"])
            db.add(status)
            db.flush()
            by_name[status.name] = status
        status.category = raw["category"]
        status.color = raw.get("color") or status.color
        status.sort_order = order
    target_ids = {name: row.id for name, row in by_name.items() if name in allowed_names}

    issues = list(db.scalars(select(Issue).where(Issue.project_id == project.id)))
    moved_values = orphaned = cleared = rewritten_filters = 0
    for issue in issues:
        original: dict[str, Any] = {}
        for key, field in source_fields.items():
            value = templates.get_value(issue, field)
            if value not in (None, "", []):
                original[key] = value
        for key, action in field_map.items():
            if key not in original:
                continue
            source_field = source_fields.get(key)
            value = original[key]
            # Remove the old extra value before writing a mapped destination. This also handles
            # key-preserving migrations without deleting the newly written value afterwards.
            if source_field and source_field.get("storage") != "column":
                extra = dict(issue.extra or {})
                extra.pop(key, None)
                issue.extra = extra
            if action == "clear":
                if (
                    source_field is not None
                    and source_field.get("storage") == "column"
                    and key != "machine_ids"
                ):
                    empty: Any = None
                    if isinstance(value, list):
                        empty = []
                    elif isinstance(value, str):
                        empty = ""
                    templates.set_value(issue, source_field, empty)
                cleared += 1
            elif action == "orphan" or action is None:
                orphan = dict((issue.extra or {}).get("_template_orphans") or {})
                orphan[f"{source_def.get('key')}@{project.template_version_id}:{key}"] = value
                issue.extra = {**(issue.extra or {}), "_template_orphans": orphan}
                orphaned += 1
            elif str(action) in target_fields:
                target_field = target_fields[str(action)]
                in_place = (
                    action == key
                    and source_field is not None
                    and source_field.get("storage") == "column"
                    and target_field.get("storage") == "column"
                )
                if not in_place:
                    templates.set_value(
                        issue, target_field, templates.convert_value(value, target_field),
                    )
                    moved_values += 1
        if issue.status_id in status_map:
            issue.status_id = target_ids[status_map[issue.status_id]]

    for member in db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id)):
        if member.role not in target_roles:
            if member.role not in role_map:
                raise ValueError(f"角色 {member.role} 尚未映射")
            member.role = role_map[member.role]

    settings = dict(project.settings or {})
    defaults = dict(target_def.get("defaults") or {})
    settings.update(defaults)
    settings["profile"] = target_def["key"]
    settings["transitions"] = target_def["workflows"]
    old_gates = settings.get("transition_gates") or {}
    remapped_gates = {
        status_map.get(next((s.id for s in project.statuses if s.name == name), -1), name): gate
        for name, gate in old_gates.items()
        if status_map.get(next((s.id for s in project.statuses if s.name == name), -1), name)
        in allowed_names
    }
    settings["transition_gates"] = {
        **dict(target_def.get("default_gates") or {}),
        **remapped_gates,
    }
    project.settings = settings
    project.template_version_id = target.id
    project.template_version = target

    ordered_pairs = [(target_ids[raw["name"]], str(raw["name"])) for raw in target_def["statuses"]]
    for board in db.scalars(select(Board).where(Board.project_id == project.id)):
        board.columns = include_missing_status_columns([], ordered_pairs)

    for request in db.scalars(
        select(ApprovalRequest).join(Issue).where(
            Issue.project_id == project.id, ApprovalRequest.status == "pending"
        )
    ):
        mapped = status_map.get(request.target_status_id)
        if mapped:
            request.target_status_id = target_ids[mapped]
        else:
            request.status = "cancelled"
            request.finished_at = utcnow()

    # Only rewrite the unambiguous cf_key token form. Free text and labels are deliberately
    # untouched so migration cannot silently change unrelated filter expressions.
    for saved_filter in db.scalars(select(SavedFilter).where(
        SavedFilter.share_project_id == project.id
    )):
        jql = saved_filter.jql
        for source_key, action in field_map.items():
            if str(action) in target_fields and action != source_key:
                jql = re.sub(
                    rf"(?i)\bcf_{re.escape(source_key)}\b",
                    f"cf_{action}",
                    jql,
                )
        if jql != saved_filter.jql:
            saved_filter.jql = jql
            saved_filter.updated_at = utcnow()
            rewritten_filters += 1

    record = ProjectTemplateMigration(
        project_id=project.id, source_version_id=snapshot["source"]["version_id"],
        target_version_id=target.id, status="applied", preview=snapshot, mapping=mapping,
        result={
            "issues": len(issues), "values_moved": moved_values,
            "values_orphaned": orphaned, "values_cleared": cleared,
            "filters_rewritten": rewritten_filters,
        },
        created_by_id=actor_id, created_at=utcnow(), applied_at=utcnow(),
    )
    db.add(record)
    db.flush()
    return record
