"""SLA clock start and paused time, so reopening and paused statuses keep fair deadlines

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-13 17:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issues", sa.Column("sla_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("issues", sa.Column("sla_paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("issues", sa.Column("sla_paused_hours", sa.Float(), nullable=False,
                                      server_default="0"))
    op.execute("UPDATE issues SET sla_started_at = created_at")
    op.execute(
        "UPDATE issues SET sla_state = 'completed' "
        "WHERE status_id IN (SELECT id FROM statuses WHERE category = 'done')"
    )
    op.execute(
        "UPDATE issues SET sla_state = 'ok' WHERE sla_state = 'completed' "
        "AND status_id IN (SELECT id FROM statuses WHERE category <> 'done')"
    )


def downgrade() -> None:
    op.drop_column("issues", "sla_paused_hours")
    op.drop_column("issues", "sla_paused_at")
    op.drop_column("issues", "sla_started_at")
