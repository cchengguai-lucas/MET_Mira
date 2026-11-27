"""software readiness fields and a reserved Jira snapshot

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-03 15:50:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    snapshot = (
        postgresql.JSONB(astext_type=sa.Text())
        if op.get_bind().dialect.name == "postgresql"
        else sa.JSON()
    )
    op.add_column("issues", sa.Column("software_ready", sa.Boolean(), nullable=True))
    op.add_column(
        "issues",
        sa.Column("software_note", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "issues",
        sa.Column("software_jira_key", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column("issues", sa.Column("software_jira_snapshot", snapshot, nullable=True))


def downgrade() -> None:
    op.drop_column("issues", "software_jira_snapshot")
    op.drop_column("issues", "software_jira_key")
    op.drop_column("issues", "software_note")
    op.drop_column("issues", "software_ready")
