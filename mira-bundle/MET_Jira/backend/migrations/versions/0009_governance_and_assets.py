"""workflow governance, shared assets and SLA state

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12 12:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issues", sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("issues", sa.Column("sla_state", sa.String(16), nullable=False,
                                      server_default="ok"))
    op.add_column("issues", sa.Column("sla_notified_level", sa.Integer(), nullable=False,
                                      server_default="0"))
    op.create_index("ix_issues_sla_due_at", "issues", ["sla_due_at"])
    op.create_index("ix_issues_sla_state", "issues", ["sla_state"])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_id", sa.Integer(), sa.ForeignKey("issues.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("target_status_id", sa.Integer(), sa.ForeignKey("statuses.id"), nullable=False),
        sa.Column("requested_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requirements", sa.JSON(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("comment", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_approval_requests_issue_id", "approval_requests", ["issue_id"])
    op.create_index("ix_approval_requests_target_status_id", "approval_requests",
                    ["target_status_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])

    op.create_table(
        "master_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("kind", "code"),
    )
    op.create_index("ix_master_assets_kind", "master_assets", ["kind"])
    op.create_index("ix_master_assets_active", "master_assets", ["active"])
    op.create_table(
        "project_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.Integer(),
                  sa.ForeignKey("master_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("local_machine_id", sa.Integer(),
                  sa.ForeignKey("machines.id", ondelete="SET NULL")),
        sa.Column("alias", sa.String(200), nullable=False, server_default=""),
        sa.UniqueConstraint("project_id", "asset_id"),
    )
    op.create_index("ix_project_assets_project_id", "project_assets", ["project_id"])
    op.create_index("ix_project_assets_asset_id", "project_assets", ["asset_id"])
    op.create_index("ix_project_assets_local_machine_id", "project_assets",
                    ["local_machine_id"])


def downgrade() -> None:
    op.drop_table("project_assets")
    op.drop_table("master_assets")
    op.drop_table("approval_requests")
    op.drop_index("ix_issues_sla_state", table_name="issues")
    op.drop_index("ix_issues_sla_due_at", table_name="issues")
    op.drop_column("issues", "sla_notified_level")
    op.drop_column("issues", "sla_state")
    op.drop_column("issues", "sla_due_at")
