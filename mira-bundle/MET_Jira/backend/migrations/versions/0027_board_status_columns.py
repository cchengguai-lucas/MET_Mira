"""give every existing status its own board column when none maps it

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-03 09:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

projects = sa.table("projects", sa.column("id", sa.Integer))
statuses = sa.table(
    "statuses",
    sa.column("id", sa.Integer),
    sa.column("project_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("sort_order", sa.Integer),
)
boards = sa.table(
    "boards",
    sa.column("id", sa.Integer),
    sa.column("project_id", sa.Integer),
    sa.column("columns", sa.JSON),
)


def _column_signature(columns: list | None) -> list[tuple]:
    return [
        (column.get("name"), tuple(column.get("status_ids") or []), column.get("wip"))
        for column in (columns or [])
    ]


def upgrade() -> None:
    from backend.services.workflow import ensure_status_columns

    conn = op.get_bind()
    project_ids = [row.id for row in conn.execute(sa.select(projects.c.id)).all()]
    for project_id in project_ids:
        rows = conn.execute(
            sa.select(statuses.c.id, statuses.c.name)
            .where(statuses.c.project_id == project_id)
            .order_by(statuses.c.sort_order, statuses.c.id)
        ).all()
        pairs = [(row.id, row.name) for row in rows]
        for board_id, columns in conn.execute(
            sa.select(boards.c.id, boards.c.columns).where(boards.c.project_id == project_id)
        ).all():
            updated = ensure_status_columns(columns, pairs)
            if _column_signature(updated) != _column_signature(columns):
                conn.execute(
                    boards.update().where(boards.c.id == board_id).values(columns=updated)
                )


def downgrade() -> None:
    """Added columns are indistinguishable from ones an admin created later."""
