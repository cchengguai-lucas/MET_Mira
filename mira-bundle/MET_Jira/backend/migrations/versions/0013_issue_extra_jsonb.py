"""use JSONB and a GIN index for template fields

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-12 18:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "issues",
        "extra",
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="extra::jsonb",
        existing_nullable=False,
    )
    op.create_index(
        "ix_issues_extra_gin", "issues", ["extra"],
        unique=False, postgresql_using="gin",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_index("ix_issues_extra_gin", table_name="issues")
    op.alter_column(
        "issues",
        "extra",
        type_=sa.JSON(),
        postgresql_using="extra::json",
        existing_nullable=False,
    )
