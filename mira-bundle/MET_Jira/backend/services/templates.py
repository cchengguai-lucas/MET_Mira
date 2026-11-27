"""Versioned department-template catalog, validation and field access."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    DepartmentTemplate,
    DepartmentTemplateVersion,
    Issue,
    OrgUnit,
    OrgUnitTemplateRecommendation,
    Project,
)
from ..models.issue import ISSUE_TYPES
from . import profiles

FIELD_KINDS = {
    "builtin", "text", "textarea", "markdown", "date", "boolean", "number", "select",
    "master_ref", "user", "machine",
}
STORAGES = {"column", "extra", "master_ref"}
MASTER_KINDS = {"equipment", "line", "material", "service"}
LIST_PSEUDO_COLUMNS = {"key", "reporter_id", "machine_ids", "machine_group"}
COLUMN_FIELDS = {
    "title", "description", "project_name", "update_scope", "involves_software",
    "software_owner_id", "software_ready", "software_note", "software_jira_key",
    "scheme_note", "electrical_scheme_ok", "process_scheme_ok", "software_scheme_ok",
    "verify_method", "verify_criteria", "update_date", "tester_id",
    "test_passed", "remark", "type", "priority", "assignee_id", "parent_id", "epic_id",
    "sprint_id", "labels", "due_date", "story_points",
}

# Reusable field/section packs. Templates may list pack keys in ``include_packs``;
# they are merged (roles / sections / fields) before validation so departments can
# compose without copying whole scheme definitions.
FIELD_PACKS: dict[str, dict[str, Any]] = {
    "software": {
        "roles": [{"key": "software", "labels": {"zh": "软件", "en": "Software"}}],
        "sections": [{
            "key": "software",
            "role": "software",
            "issue_types": ["requirement"],
            "fields": [
                "involves_software", "software_owner_id", "software_ready",
                "software_note", "software_jira_key",
            ],
            "labels": {"zh": "软件", "en": "Software"},
        }],
        "fields": [
            {
                "key": "involves_software", "kind": "boolean", "storage": "column",
                "labels": {"zh": "是否涉及软件", "en": "Involves software"},
            },
            {
                "key": "software_owner_id", "kind": "user", "storage": "column",
                "labels": {"zh": "软件负责人", "en": "Software owner"},
            },
            {
                "key": "software_ready", "kind": "boolean", "storage": "column",
                "labels": {"zh": "软件是否准备好", "en": "Software ready"},
            },
            {
                "key": "software_note", "kind": "textarea", "storage": "column",
                "labels": {"zh": "软件交付说明", "en": "Software delivery note"},
            },
            {
                "key": "software_jira_key", "kind": "text", "storage": "column",
                "labels": {"zh": "软件 Jira 单号", "en": "Software Jira key"},
            },
        ],
    },
    "verification": {
        "roles": [{"key": "tester", "labels": {"zh": "测试", "en": "Tester"}}],
        "sections": [{
            "key": "tester",
            "role": "tester",
            "issue_types": ["requirement", "bug"],
            "fields": ["verify_method", "verify_criteria", "tester_id", "test_passed",
                       "update_date", "remark"],
            "labels": {"zh": "测试", "en": "Verification"},
        }],
        "fields": [
            {
                "key": "verify_method", "kind": "textarea", "storage": "column",
                "labels": {"zh": "验证方法", "en": "Verification method"},
            },
            {
                "key": "verify_criteria", "kind": "textarea", "storage": "column",
                "labels": {"zh": "验证标准", "en": "Acceptance criteria"},
            },
            {
                "key": "tester_id", "kind": "user", "storage": "column",
                "labels": {"zh": "测试人", "en": "Tester"},
            },
            {
                "key": "test_passed", "kind": "boolean", "storage": "column",
                "labels": {"zh": "是否通过", "en": "Passed"},
            },
            {
                "key": "update_date", "kind": "date", "storage": "column",
                "labels": {"zh": "更新日期", "en": "Update date"},
            },
            {
                "key": "remark", "kind": "textarea", "storage": "column",
                "labels": {"zh": "备注", "en": "Remark"},
            },
        ],
    },
}


def expand_packs(definition: dict[str, Any]) -> dict[str, Any]:
    """Merge ``include_packs`` into roles/sections/fields; unknown packs raise."""
    packs = list(definition.pop("include_packs", None) or [])
    if not packs:
        return definition
    role_keys = {
        str(r.get("key") if isinstance(r, dict) else r)
        for r in (definition.get("roles") or [])
    }
    section_keys = {str(s.get("key") or "") for s in (definition.get("sections") or [])}
    field_keys = {str(f.get("key") or "") for f in (definition.get("fields") or [])}
    roles = list(definition.get("roles") or [])
    sections = list(definition.get("sections") or [])
    fields = list(definition.get("fields") or [])
    for name in packs:
        pack = FIELD_PACKS.get(str(name))
        if pack is None:
            raise ValueError(f"未知字段包：{name}")
        for role in pack.get("roles") or []:
            key = str(role.get("key") if isinstance(role, dict) else role)
            if key and key not in role_keys:
                roles.append(deepcopy(role) if isinstance(role, dict) else {"key": role})
                role_keys.add(key)
        for section in pack.get("sections") or []:
            key = str(section.get("key") or "")
            if key and key not in section_keys:
                # Drop fields already owned by the template so ownership stays unique.
                owned = [f for f in (section.get("fields") or []) if f not in field_keys]
                if not owned:
                    continue
                row = deepcopy(section)
                row["fields"] = owned
                sections.append(row)
                section_keys.add(key)
        for field in pack.get("fields") or []:
            key = str(field.get("key") or "")
            if key and key not in field_keys:
                fields.append(deepcopy(field))
                field_keys.add(key)
    definition["roles"] = roles
    definition["sections"] = sections
    definition["fields"] = fields
    # Packs are expanded into a self-contained definition; do not re-store include_packs.
    return definition


def validate_definition(raw: dict[str, Any]) -> dict[str, Any]:
    definition = expand_packs(deepcopy(raw))
    if int(definition.get("schema_version", 0)) != 1:
        raise ValueError("模板 schema_version 必须为 1")
    key = str(definition.get("key") or "")
    if not key or not key.replace("_", "").replace("-", "").isalnum():
        raise ValueError("模板 key 无效")
    roles = [str(r.get("key") if isinstance(r, dict) else r) for r in
             definition.get("roles") or []]
    if not roles or len(roles) != len(set(roles)):
        raise ValueError("模板角色不能为空且不能重复")
    if "admin" not in roles:
        raise ValueError("模板必须包含 admin 角色")
    sections = definition.get("sections") or []
    section_keys = [str(s.get("key") or "") for s in sections]
    if not section_keys or len(section_keys) != len(set(section_keys)):
        raise ValueError("模板分区不能为空且不能重复")
    fields = definition.get("fields") or []
    field_keys = [str(f.get("key") or "") for f in fields]
    if any(not key or not key.replace("_", "").isalnum() for key in field_keys):
        raise ValueError("模板字段 key 无效")
    if len(field_keys) != len(set(field_keys)):
        raise ValueError("模板字段不能重复")
    aliases = [
        str(alias) for field in fields for alias in (field.get("aliases") or [])
    ]
    if set(aliases) & set(field_keys) or len(aliases) != len(set(aliases)):
        raise ValueError("字段别名不能与字段 key 冲突或重复")
    known = set(field_keys) | set(definition.get("general_fields") or [])
    claimed: list[str] = []
    for section in sections:
        if section.get("role") and section["role"] not in roles:
            raise ValueError(f"分区 {section['key']} 引用了未知角色")
        for issue_type in section.get("issue_types") or ["requirement"]:
            if issue_type not in ISSUE_TYPES:
                raise ValueError(f"分区 {section['key']} 的问题类型无效")
        claimed.extend(str(v) for v in section.get("fields") or [])
    if len(claimed) != len(set(claimed)) or set(claimed) != set(field_keys):
        raise ValueError("每个模板字段必须且只能属于一个分区")
    for field in fields:
        name = str(field.get("key") or "")
        kind = str(field.get("kind") or "text")
        storage = str(field.get("storage") or "extra")
        if kind not in FIELD_KINDS or storage not in STORAGES:
            raise ValueError(f"字段 {name} 的类型或存储方式无效")
        if storage == "column" and name not in COLUMN_FIELDS and name != "machine_ids":
            raise ValueError(f"字段 {name} 不能映射到原生列")
        if kind == "select" and not field.get("options"):
            raise ValueError(f"字段 {name} 的选项不能为空")
        if kind == "master_ref" and field.get("master_kind") not in MASTER_KINDS:
            raise ValueError(f"字段 {name} 的主数据类型无效")
        if not isinstance(field.get("excel", True), bool) or not isinstance(
            field.get("report", False), bool
        ):
            raise ValueError(f"字段 {name} 的 Excel/报表配置无效")
    statuses = definition.get("statuses") or []
    status_names = [str(s.get("name") or "") for s in statuses]
    if not status_names or len(status_names) != len(set(status_names)):
        raise ValueError("模板状态不能为空且不能重复")
    categories = {str(s.get("category")) for s in statuses}
    if not categories <= {"todo", "doing", "done"} or "done" not in categories:
        raise ValueError("模板状态分类无效或缺少完成状态")
    workflows = definition.get("workflows") or {}
    for issue_type in ISSUE_TYPES:
        rules = workflows.get(issue_type)
        if not isinstance(rules, dict):
            raise ValueError(f"缺少 {issue_type} 工作流")
        for source, targets in rules.items():
            if source not in status_names or any(t not in status_names for t in targets):
                raise ValueError(f"{issue_type} 工作流引用了未知状态")
        used = set(rules) | {target for targets in rules.values() for target in targets}
        if not used:
            raise ValueError(f"{issue_type} 工作流不能为空")
        first = next((name for name in status_names if name in used), None)
        reachable = {first} if first else set()
        queue = [first] if first else []
        while queue:
            source = queue.pop()
            for target in rules.get(source, []):
                if target not in reachable:
                    reachable.add(target)
                    queue.append(target)
        done = {
            str(status["name"]) for status in statuses
            if status.get("category") == "done"
        }
        if not reachable & done:
            raise ValueError(f"{issue_type} 工作流无法到达完成状态")
    defaults = definition.get("defaults") or {}
    for setting in ("test_status", "pass_status", "fail_return_status", "dev_complete_status"):
        value = defaults.get(setting)
        if value and value not in status_names:
            raise ValueError(f"{setting} 引用了未知状态")
    test_values = [defaults.get(key) for key in
                   ("test_status", "pass_status", "fail_return_status")]
    if any(test_values) and not all(test_values):
        raise ValueError("测试、通过和失败状态必须同时配置")
    if all(test_values):
        test_status, pass_status, fail_status = (str(value) for value in test_values)
        for issue_type, rules in workflows.items():
            if test_status in rules:
                targets = set(rules[test_status])
                if not {pass_status, fail_status} <= targets:
                    raise ValueError(f"{issue_type} 测试状态必须可流转到通过和失败状态")
    for column in (definition.get("list_layout") or {}).get("columns") or []:
        if column.get("field") not in known | LIST_PSEUDO_COLUMNS:
            raise ValueError(f"列表引用了未知字段：{column.get('field')}")
        if int(column.get("width") or 0) <= 0:
            raise ValueError("列表列宽必须大于 0")
        group = column.get("section")
        if group and group not in {*section_keys, profiles.GENERAL}:
            raise ValueError(f"列表列引用了未知分区：{group}")
    validate_default_gates(definition, roles, known)
    return definition


def validate_default_gates(definition: dict[str, Any], roles: list[str], known: set[str]) -> None:
    gates = definition.get("default_gates") or {}
    if not isinstance(gates, dict):
        raise ValueError("默认门禁配置无效")
    status_names = {str(s.get("name")) for s in definition.get("statuses") or []}
    for status, raw in gates.items():
        if status not in status_names or not isinstance(raw, dict):
            raise ValueError(f"默认门禁引用了未知状态：{status}")
        for field in raw.get("required_fields") or []:
            if str(field).removeprefix("custom_fields.") not in known:
                raise ValueError(f"默认门禁引用了未知字段：{field}")
        for item in raw.get("approvals") or []:
            if str((item or {}).get("role") or "") not in roles:
                raise ValueError("默认门禁引用了未知角色")


def definition_errors(raw: dict[str, Any]) -> list[str]:
    """Validation problems of a draft; drafts may be saved while incomplete."""
    try:
        validate_definition(raw)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return [str(exc)]
    return []


def ensure_builtin_templates(db: Session) -> None:
    """Seed the three system templates and pin legacy projects, without changing their data."""
    versions: dict[str, DepartmentTemplateVersion] = {}
    for profile in profiles.PROFILES.values():
        row = db.scalar(select(DepartmentTemplate).where(DepartmentTemplate.key == profile.key))
        if row is None:
            now = utcnow()
            row = DepartmentTemplate(
                key=profile.key, label=profile.label, enabled=True, is_system=True,
                description=f"{profile.label}部门内置模板", created_at=now, updated_at=now,
            )
            db.add(row)
            db.flush()
        version = db.scalar(select(DepartmentTemplateVersion).where(
            DepartmentTemplateVersion.template_id == row.id,
            DepartmentTemplateVersion.version == 1,
        ))
        if version is None:
            definition = validate_definition(profiles.builtin_definition(profile))
            version = DepartmentTemplateVersion(
                template_id=row.id, version=1, status="published", definition=definition,
                published_at=utcnow(),
            )
            db.add(version)
            db.flush()
        if row.current_version_id is None:
            row.current_version_id = version.id
        versions[profile.key] = version
    db.flush()
    for project in db.scalars(select(Project).where(Project.template_version_id.is_(None))):
        key = (project.settings or {}).get("profile") or profiles.DEFAULT_PROFILE
        project.template_version_id = versions.get(key, versions[profiles.DEFAULT_PROFILE]).id
    db.flush()


def catalog(db: Session, *, enabled_only: bool = True) -> list[DepartmentTemplate]:
    stmt = select(DepartmentTemplate).order_by(DepartmentTemplate.id)
    if enabled_only:
        stmt = stmt.where(DepartmentTemplate.enabled.is_(True))
    return list(db.scalars(stmt))


def published_version(db: Session, key: str) -> DepartmentTemplateVersion | None:
    template = db.scalar(select(DepartmentTemplate).where(
        DepartmentTemplate.key == key, DepartmentTemplate.enabled.is_(True)
    ))
    return template.current_version if template else None


def field_def(profile: profiles.Profile, key: str) -> dict[str, Any] | None:
    definition = profile.definition or profiles.builtin_definition(profile)
    return next((f for f in definition.get("fields") or [] if f.get("key") == key), None)


def get_value(issue: Issue, field: dict[str, Any] | str) -> Any:
    raw = field_def(profiles.profile_of(issue.project), field) if isinstance(field, str) else field
    if raw is None:
        return None
    key = str(raw["key"])
    if raw.get("storage") == "column":
        if key == "machine_ids":
            return [link.machine_id for link in issue.machine_links]
        return getattr(issue, key, None)
    return (issue.extra or {}).get(key)


def set_value(issue: Issue, field: dict[str, Any], value: Any) -> None:
    key = str(field["key"])
    if field.get("storage") == "column":
        if key == "machine_ids":
            raise ValueError("machine_ids 必须通过机台关联服务更新")
        setattr(issue, key, value)
        return
    issue.extra = {**(issue.extra or {}), key: value}


def convert_value(value: Any, target: dict[str, Any]) -> Any:
    if value in (None, ""):
        return value
    kind = target.get("kind")
    if kind == "number":
        return float(value)
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "是", "通过"}
    if kind == "date":
        return value.isoformat() if isinstance(value, date) else str(value)[:10]
    if kind == "select" and str(value) not in (target.get("options") or []):
        raise ValueError(f"{target['key']} 不接受该选项")
    return value


def next_version(db: Session, template_id: int) -> int:
    return int(db.scalar(select(func.max(DepartmentTemplateVersion.version)).where(
        DepartmentTemplateVersion.template_id == template_id
    )) or 0) + 1


def recommendations(db: Session, org_unit_id: int | None) -> list[DepartmentTemplate]:
    return [template for template, _unit in recommendation_details(db, org_unit_id)]


def recommendation_details(
    db: Session, org_unit_id: int | None
) -> list[tuple[DepartmentTemplate, OrgUnit]]:
    if not org_unit_id:
        return []
    chain: list[OrgUnit] = []
    current = db.get(OrgUnit, org_unit_id)
    while current is not None:
        chain.append(current)
        current = db.get(OrgUnit, current.parent_id) if current.parent_id else None
    seen: set[int] = set()
    out: list[tuple[DepartmentTemplate, OrgUnit]] = []
    for unit in chain:
        rows = db.scalars(
            select(OrgUnitTemplateRecommendation)
            .where(OrgUnitTemplateRecommendation.org_unit_id == unit.id)
            .order_by(OrgUnitTemplateRecommendation.sort_order,
                      OrgUnitTemplateRecommendation.id)
        )
        for row in rows:
            if row.template_id not in seen and row.template.enabled:
                seen.add(row.template_id)
                out.append((row.template, unit))
    return out
