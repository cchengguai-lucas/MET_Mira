"""schedule baselines, risk events and planning dates

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-27 10:25:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("plan_start_date", sa.Date()))
    op.add_column("projects", sa.Column("target_date", sa.Date()))
    op.add_column("issues", sa.Column("planned_start_date", sa.Date()))
    op.add_column(
        "issue_links",
        sa.Column("lag_days", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "project_schedule_baselines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "version",
            name="uq_project_schedule_baselines_project_version",
        ),
    )
    op.create_index(
        "ix_project_schedule_baselines_project_id",
        "project_schedule_baselines",
        ["project_id"],
    )
    op.create_index(
        "ix_project_schedule_baselines_active",
        "project_schedule_baselines",
        ["project_id", "active"],
    )

    op.create_table(
        "schedule_risk_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column(
            "source_issue_id",
            sa.Integer(),
            sa.ForeignKey("issues.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "acknowledged_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("snoozed_until", sa.DateTime(timezone=True)),
        sa.Column("decision", sa.String(length=16)),
        sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "decided_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "project_id",
            "fingerprint",
            name="uq_schedule_risk_events_project_fingerprint",
        ),
    )
    op.create_index(
        "ix_schedule_risk_events_project_id",
        "schedule_risk_events",
        ["project_id"],
    )
    op.create_index(
        "ix_schedule_risk_events_source_issue_id",
        "schedule_risk_events",
        ["source_issue_id"],
    )
    op.create_index(
        "ix_schedule_risk_events_project_status",
        "schedule_risk_events",
        ["project_id", "status", "severity"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_risk_events_project_status", table_name="schedule_risk_events"
    )
    op.drop_index(
        "ix_schedule_risk_events_source_issue_id", table_name="schedule_risk_events"
    )
    op.drop_index(
        "ix_schedule_risk_events_project_id", table_name="schedule_risk_events"
    )
    op.drop_table("schedule_risk_events")
    op.drop_index(
        "ix_project_schedule_baselines_active",
        table_name="project_schedule_baselines",
    )
    op.drop_index(
        "ix_project_schedule_baselines_project_id",
        table_name="project_schedule_baselines",
    )
    op.drop_table("project_schedule_baselines")
    op.drop_column("issue_links", "lag_days")
    op.drop_column("issues", "planned_start_date")
    op.drop_column("projects", "target_date")
    op.drop_column("projects", "plan_start_date")
