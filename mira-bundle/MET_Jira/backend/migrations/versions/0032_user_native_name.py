"""Store the directory Chinese name used to match software Jira people.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-08 13:05:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("native_name", sa.String(length=200), nullable=True))
    op.create_index("ix_users_native_name", "users", ["native_name"])


def downgrade() -> None:
    op.drop_index("ix_users_native_name", table_name="users")
    op.drop_column("users", "native_name")
