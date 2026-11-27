"""project cartoon icon key

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-15 14:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("icon", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "icon")
