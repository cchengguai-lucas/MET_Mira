"""normalize built-in template master-data fields

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-12 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    templates = sa.table(
        "department_templates",
        sa.column("id", sa.Integer),
        sa.column("key", sa.String),
    )
    versions = sa.table(
        "department_template_versions",
        sa.column("id", sa.Integer),
        sa.column("template_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("definition", sa.JSON),
    )
    rows = connection.execute(
        sa.select(versions.c.id, templates.c.key, versions.c.definition)
        .select_from(versions.join(templates, versions.c.template_id == templates.c.id))
        .where(versions.c.version == 1, templates.c.key.in_(["mechanical", "production"]))
    )
    for row in rows:
        definition = dict(row.definition)
        defaults = dict(definition.get("defaults") or {})
        if not defaults.get("test_status"):
            defaults["pass_status"] = None
            defaults["fail_return_status"] = None
        definition["defaults"] = defaults
        target = ("material", "material") if row.key == "mechanical" else ("line", "line")
        for field in definition.get("fields") or []:
            if field.get("key") == target[0]:
                field.update({
                    "kind": "master_ref",
                    "storage": "master_ref",
                    "master_kind": target[1],
                    "report": True,
                })
        connection.execute(
            versions.update().where(versions.c.id == row.id).values(definition=definition)
        )


def downgrade() -> None:
    connection = op.get_bind()
    templates = sa.table(
        "department_templates",
        sa.column("id", sa.Integer),
        sa.column("key", sa.String),
    )
    versions = sa.table(
        "department_template_versions",
        sa.column("id", sa.Integer),
        sa.column("template_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("definition", sa.JSON),
    )
    rows = connection.execute(
        sa.select(versions.c.id, templates.c.key, versions.c.definition)
        .select_from(versions.join(templates, versions.c.template_id == templates.c.id))
        .where(versions.c.version == 1, templates.c.key.in_(["mechanical", "production"]))
    )
    for row in rows:
        definition = dict(row.definition)
        key = "material" if row.key == "mechanical" else "line"
        for field in definition.get("fields") or []:
            if field.get("key") == key:
                field.update({
                    "kind": "text", "storage": "extra",
                    "master_kind": None, "report": False,
                })
        connection.execute(
            versions.update().where(versions.c.id == row.id).values(definition=definition)
        )
