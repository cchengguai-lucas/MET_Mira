"""project cover image

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-15 14:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("cover_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "cover_key")
