"""store the chart data of AI reports so they can be rendered as HTML with charts

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-13 16:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_reports", sa.Column("metrics", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_reports", "metrics")
