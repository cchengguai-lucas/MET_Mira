"""rename default scrum boards to the Chinese term

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-10 20:00:00
"""

from __future__ import annotations

from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE boards AS b SET name = p.name || ' 冲刺看板' FROM projects AS p "
        "WHERE b.project_id = p.id AND b.kind = 'scrum' AND b.name = p.name || ' Sprint'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE boards AS b SET name = p.name || ' Sprint' FROM projects AS p "
        "WHERE b.project_id = p.id AND b.kind = 'scrum' AND b.name = p.name || ' 冲刺看板'"
    )
