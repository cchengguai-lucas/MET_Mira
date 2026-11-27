"""user avatar

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 12:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('avatar_key', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('avatar_color', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'avatar_color')
    op.drop_column('users', 'avatar_key')
