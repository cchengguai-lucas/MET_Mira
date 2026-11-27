"""give seeded template roles, built-in fields and sections readable display names

The first seed stored role keys (``requester``) and built-in field keys (``title``) as their
display labels. Only labels that still equal the raw key (or an empty/duplicated English label)
are replaced, so names an administrator has edited are kept. Keys and rules are untouched.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-13 12:40:00
"""

from __future__ import annotations

from copy import deepcopy

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

ROLE_LABELS = {
    "admin": ("项目管理员", "Project admin"),
    "requester": ("需求提出人", "Requester"),
    "electrical": ("电气组", "Electrical team"),
    "tester": ("测试人员", "Tester"),
    "mechanical": ("机械设计组", "Mechanical design"),
    "production": ("生产执行组", "Production team"),
    "quality": ("质量确认", "Quality"),
    "member": ("成员", "Member"),
}
FIELD_LABELS = {
    "title": ("标题", "Title"),
    "description": ("描述", "Description"),
    "project_name": ("项目名称", "Project name"),
    "machine_ids": ("机台", "Machines"),
    "update_scope": ("更新涉及", "Update scope"),
    "involves_software": ("是否涉及软件", "Involves software"),
    "software_owner_id": ("软件负责人", "Software owner"),
    "verify_method": ("验证方法", "Verification method"),
    "verify_criteria": ("验证标准", "Acceptance criteria"),
    "update_date": ("更新日期", "Update date"),
    "tester_id": ("测试人", "Tester"),
    "test_passed": ("是否通过", "Passed"),
    "remark": ("备注", "Remarks"),
}
TEMPLATE_LABELS = {
    "electrical": ("电气", "Electrical"),
    "mechanical": ("机械", "Mechanical"),
    "production": ("生产", "Production"),
}
SECTION_EN = {
    "需求方填写": "Requester",
    "电气组填写": "Electrical team",
    "测试方填写": "Tester",
    "机械设计组填写": "Mechanical design",
    "工艺/装配确认": "Process / assembly check",
    "问题提出人填写": "Reporter",
    "生产执行组填写": "Production team",
    "质量确认": "Quality check",
}

versions = sa.table(
    "department_template_versions",
    sa.column("id", sa.Integer),
    sa.column("definition", sa.JSON),
)


def _relabel(items: list, names: dict, key_attr: str = "key") -> bool:
    changed = False
    for item in items:
        if not isinstance(item, dict) or item.get(key_attr) not in names:
            continue
        key = item[key_attr]
        zh, en = names[key]
        labels = dict(item.get("labels") or {})
        original_zh = labels.get("zh")
        if original_zh in (None, "", key):
            labels["zh"] = zh
            changed = True
        if labels.get("en") in (None, "", key, original_zh):
            labels["en"] = en
            changed = True
        item["labels"] = labels
    return changed


def upgrade() -> None:
    bind = op.get_bind()
    for row in bind.execute(sa.select(versions.c.id, versions.c.definition)).all():
        definition = deepcopy(row.definition) if isinstance(row.definition, dict) else None
        if not definition:
            continue
        changed = _relabel([definition], TEMPLATE_LABELS)
        changed = _relabel(definition.get("roles") or [], ROLE_LABELS) or changed
        builtin = [f for f in definition.get("fields") or []
                   if isinstance(f, dict) and f.get("kind") == "builtin"]
        changed = _relabel(builtin, FIELD_LABELS) or changed
        for section in definition.get("sections") or []:
            labels = dict(section.get("labels") or {})
            english = SECTION_EN.get(labels.get("zh") or "")
            if english and labels.get("en") in (None, "", labels.get("zh")):
                labels["en"] = english
                section["labels"] = labels
                changed = True
        if changed:
            bind.execute(versions.update().where(versions.c.id == row.id)
                         .values(definition=definition))


def downgrade() -> None:
    """Display names only; nothing to undo."""
