"""record department template governance actions

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-12 21:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "department_template_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["department_templates.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["department_template_versions.id"],
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_department_template_audits_template_id",
                    "department_template_audits", ["template_id"])
    op.create_index("ix_department_template_audits_action",
                    "department_template_audits", ["action"])
    op.create_index("ix_department_template_audits_created_at",
                    "department_template_audits", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_department_template_audits_created_at",
                  table_name="department_template_audits")
    op.drop_index("ix_department_template_audits_action",
                  table_name="department_template_audits")
    op.drop_index("ix_department_template_audits_template_id",
                  table_name="department_template_audits")
    op.drop_table("department_template_audits")
