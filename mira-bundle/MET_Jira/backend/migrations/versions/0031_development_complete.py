"""Electrical flow: development complete between development and testing.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-08 11:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

DEV_COMPLETE = "开发完成"
DEV_COLOR = "#5ac8fa"

projects = sa.table(
    "projects",
    sa.column("id", sa.Integer),
    sa.column("settings", sa.JSON),
)
statuses = sa.table(
    "statuses",
    sa.column("id", sa.Integer),
    sa.column("project_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("category", sa.String),
    sa.column("color", sa.String),
    sa.column("sort_order", sa.Integer),
)
boards = sa.table(
    "boards",
    sa.column("id", sa.Integer),
    sa.column("project_id", sa.Integer),
    sa.column("columns", sa.JSON),
)
templates = sa.table(
    "department_templates",
    sa.column("id", sa.Integer),
    sa.column("key", sa.String),
)
versions = sa.table(
    "department_template_versions",
    sa.column("id", sa.Integer),
    sa.column("template_id", sa.Integer),
    sa.column("definition", sa.JSON),
)
issues = sa.table(
    "issues",
    sa.column("id", sa.Integer),
    sa.column("status_id", sa.Integer),
)


def _column_signature(columns: list | None) -> list[tuple]:
    return [
        (column.get("name"), tuple(column.get("status_ids") or []), column.get("wip"))
        for column in (columns or [])
    ]


def _splice_map(raw: dict | None) -> tuple[dict, bool]:
    from backend.services.workflow import splice_development_complete

    if not isinstance(raw, dict):
        return raw or {}, False
    changed = False
    updated: dict = {}
    for key, rules in raw.items():
        if not isinstance(rules, dict):
            updated[key] = rules
            continue
        spliced = splice_development_complete(rules)
        if spliced is None:
            updated[key] = rules
        else:
            updated[key] = spliced
            changed = True
    return updated, changed


def _insert_status_def(rows: list) -> list:
    if any(isinstance(row, dict) and row.get("name") == DEV_COMPLETE for row in rows):
        return rows
    insert_at = next(
        (index + 1 for index, row in enumerate(rows)
         if isinstance(row, dict) and row.get("name") == "研发中"),
        None,
    )
    if insert_at is None:
        return rows
    added = {"name": DEV_COMPLETE, "category": "doing", "color": DEV_COLOR}
    return [*rows[:insert_at], added, *rows[insert_at:]]


def _upgrade_templates(conn) -> None:
    template_ids = [
        row.id for row in conn.execute(
            sa.select(templates.c.id).where(templates.c.key == "electrical")
        ).all()
    ]
    if not template_ids:
        return
    for version_id, definition in conn.execute(
        sa.select(versions.c.id, versions.c.definition).where(
            versions.c.template_id.in_(template_ids)
        )
    ).all():
        if not isinstance(definition, dict):
            continue
        workflows, changed = _splice_map(definition.get("workflows") or {})
        if not changed:
            continue
        definition = dict(definition)
        definition["workflows"] = workflows
        definition["statuses"] = _insert_status_def(list(definition.get("statuses") or []))
        conn.execute(
            versions.update().where(versions.c.id == version_id).values(definition=definition)
        )


def _add_status(conn, project_id: int) -> int:
    return conn.execute(
        statuses.insert().values(
            project_id=project_id, name=DEV_COMPLETE, category="doing",
            color=DEV_COLOR, sort_order=0,
        ).returning(statuses.c.id)
    ).scalar_one()


def upgrade() -> None:
    from backend.services.workflow import ensure_status_columns

    conn = op.get_bind()
    _upgrade_templates(conn)
    for project_id, settings in conn.execute(
        sa.select(projects.c.id, projects.c.settings)
    ).all():
        stored = settings if isinstance(settings, dict) else {}
        if stored.get("profile") != "electrical":
            continue
        transitions, changed = _splice_map(stored.get("transitions") or {})
        if not changed:
            continue
        rows = conn.execute(
            sa.select(statuses.c.id, statuses.c.name)
            .where(statuses.c.project_id == project_id)
            .order_by(statuses.c.sort_order, statuses.c.id)
        ).all()
        order = [(row.id, row.name) for row in rows]
        names = {name for _, name in order}
        if DEV_COMPLETE not in names and "研发中" in names:
            new_id = _add_status(conn, project_id)
            anchor = next(index for index, (_, name) in enumerate(order) if name == "研发中")
            order.insert(anchor + 1, (new_id, DEV_COMPLETE))
        for index, (sid, _) in enumerate(order):
            conn.execute(statuses.update().where(statuses.c.id == sid).values(sort_order=index))
        settings = {**stored, "transitions": transitions}
        conn.execute(projects.update().where(projects.c.id == project_id).values(settings=settings))
        for board_id, columns in conn.execute(
            sa.select(boards.c.id, boards.c.columns).where(boards.c.project_id == project_id)
        ).all():
            updated = ensure_status_columns(columns, order)
            if _column_signature(updated) != _column_signature(columns):
                conn.execute(
                    boards.update().where(boards.c.id == board_id).values(columns=updated)
                )


def downgrade() -> None:
    """Drop the status only when no issue still uses it, and restore the direct test edge."""
    conn = op.get_bind()
    for project_id, settings in conn.execute(
        sa.select(projects.c.id, projects.c.settings)
    ).all():
        stored = settings if isinstance(settings, dict) else {}
        transitions = stored.get("transitions") or {}
        if not isinstance(transitions, dict):
            continue
        restored = {}
        changed = False
        for key, rules in transitions.items():
            if (
                isinstance(rules, dict)
                and rules.get(DEV_COMPLETE) == ["测试中", "研发中"]
                and isinstance(rules.get("研发中"), list)
                and rules["研发中"][:1] == [DEV_COMPLETE]
            ):
                developing = [name for name in rules["研发中"] if name != DEV_COMPLETE]
                restored[key] = {
                    **{name: targets for name, targets in rules.items() if name != DEV_COMPLETE},
                    "研发中": ["测试中", *developing],
                }
                changed = True
            else:
                restored[key] = rules
        if changed:
            conn.execute(
                projects.update().where(projects.c.id == project_id).values(
                    settings={**stored, "transitions": restored}
                )
            )
        row = conn.execute(
            sa.select(statuses.c.id).where(
                statuses.c.project_id == project_id, statuses.c.name == DEV_COMPLETE,
            )
        ).first()
        if row is None:
            continue
        used = conn.execute(
            sa.select(issues.c.id).where(issues.c.status_id == row.id).limit(1)
        ).first()
        if used is not None:
            continue
        for board_id, columns in conn.execute(
            sa.select(boards.c.id, boards.c.columns).where(boards.c.project_id == project_id)
        ).all():
            kept = []
            for column in columns or []:
                ids = [sid for sid in (column.get("status_ids") or []) if sid != row.id]
                if ids:
                    kept.append({**column, "status_ids": ids})
            conn.execute(boards.update().where(boards.c.id == board_id).values(columns=kept))
        conn.execute(statuses.delete().where(statuses.c.id == row.id))
