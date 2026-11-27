"""reopened and test-failed statuses

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

REOPENED = "重新打开"
TEST_FAILED = "测试未通过"

# Built-in flows before this revision; stored copies of them are dropped so the new defaults apply.
OLD_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "requirement": {
        "待评估": ["方案讨论", "已驳回"],
        "方案讨论": ["研发中", "待评估", "已驳回"],
        "研发中": ["测试中", "方案讨论"],
        "测试中": ["已完成", "研发中"],
        "已完成": ["研发中"],
        "已驳回": ["待评估"],
    },
    "bug": {
        "待评估": ["研发中", "已驳回"],
        "研发中": ["测试中", "待评估"],
        "测试中": ["已完成", "研发中"],
        "已完成": ["研发中"],
        "已驳回": ["待评估"],
    },
    "story": {
        "待评估": ["研发中"],
        "研发中": ["测试中", "待评估"],
        "测试中": ["已完成", "研发中"],
        "已完成": ["研发中"],
    },
    "task": {"待评估": ["研发中"], "研发中": ["已完成", "待评估"], "已完成": ["研发中"]},
    "subtask": {"待评估": ["研发中", "已完成"], "研发中": ["已完成", "待评估"], "已完成": ["研发中"]},
    "epic": {
        "待评估": ["研发中", "已驳回"],
        "研发中": ["已完成", "待评估"],
        "已完成": ["研发中"],
        "已驳回": ["待评估"],
    },
}

projects = sa.table("projects", sa.column("id", sa.Integer), sa.column("settings", sa.JSON))
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
    "boards", sa.column("id", sa.Integer), sa.column("project_id", sa.Integer),
    sa.column("columns", sa.JSON),
)


def _add_status(conn, project_id: int, name: str, category: str, color: str) -> int:
    return conn.execute(
        statuses.insert()
        .values(project_id=project_id, name=name, category=category, color=color, sort_order=0)
        .returning(statuses.c.id)
    ).scalar_one()


def _add_column(columns: list[dict], status_id: int, name: str, anchor: int | None,
                after: bool) -> list[dict]:
    if any(status_id in (c.get("status_ids") or []) for c in columns):
        return columns
    index = next((i for i, c in enumerate(columns) if anchor in (c.get("status_ids") or [])), None)
    if index is None:
        return columns
    column = {"name": name, "status_ids": [status_id], "wip": None}
    position = index + 1 if after else index
    return [*columns[:position], column, *columns[position:]]


def upgrade() -> None:
    conn = op.get_bind()
    for project_id, settings in conn.execute(sa.select(projects.c.id, projects.c.settings)).all():
        rows = conn.execute(
            sa.select(statuses.c.id, statuses.c.name)
            .where(statuses.c.project_id == project_id)
            .order_by(statuses.c.sort_order, statuses.c.id)
        ).all()
        order = [(r.id, r.name) for r in rows]
        ids = {name: sid for sid, name in order}
        added: dict[str, int] = {}
        if REOPENED not in ids:
            added[REOPENED] = _add_status(conn, project_id, REOPENED, "todo", "#32ade6")
            anchor = next((i for i, (_, n) in enumerate(order) if n == "研发中"), len(order))
            order.insert(anchor, (added[REOPENED], REOPENED))
        if TEST_FAILED not in ids and "测试中" in ids:
            added[TEST_FAILED] = _add_status(conn, project_id, TEST_FAILED, "doing", "#ff375f")
            anchor = next(i for i, (_, n) in enumerate(order) if n == "测试中")
            order.insert(anchor + 1, (added[TEST_FAILED], TEST_FAILED))
        for index, (sid, _) in enumerate(order):
            conn.execute(statuses.update().where(statuses.c.id == sid).values(sort_order=index))

        if added:
            for board_id, columns in conn.execute(
                sa.select(boards.c.id, boards.c.columns).where(boards.c.project_id == project_id)
            ).all():
                updated = list(columns or [])
                if REOPENED in added:
                    updated = _add_column(updated, added[REOPENED], REOPENED, ids.get("研发中"),
                                          after=False)
                if TEST_FAILED in added:
                    updated = _add_column(updated, added[TEST_FAILED], TEST_FAILED,
                                          ids.get("测试中"), after=True)
                if updated != columns:
                    conn.execute(boards.update().where(boards.c.id == board_id)
                                 .values(columns=updated))

        settings = dict(settings or {})
        stored = dict(settings.get("transitions") or {})
        kept = {t: rules for t, rules in stored.items() if rules != OLD_DEFAULTS.get(t)}
        changed = kept != stored
        settings["transitions"] = kept
        if settings.get("fail_return_status") == "研发中" and (
            TEST_FAILED in added or TEST_FAILED in ids
        ):
            settings["fail_return_status"] = TEST_FAILED
            changed = True
        if changed:
            conn.execute(projects.update().where(projects.c.id == project_id)
                         .values(settings=settings))


def downgrade() -> None:
    pass
