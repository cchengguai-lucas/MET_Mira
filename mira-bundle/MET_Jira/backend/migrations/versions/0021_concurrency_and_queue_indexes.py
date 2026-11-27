"""concurrency versions, partial uniqueness and queue indexes

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-21 15:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def _format_duplicates(rows: list[sa.RowMapping], keys: tuple[str, ...]) -> str:
    return ", ".join(
        "/".join(f"{key}={row[key]}" for key in keys) + f" (count={row['count']})"
        for row in rows
    )


def _reject_duplicates() -> None:
    bind = op.get_bind()
    active = list(
        bind.execute(
            sa.text(
                """
                SELECT project_id, count(*) AS count
                FROM sprints
                WHERE state = 'active'
                GROUP BY project_id
                HAVING count(*) > 1
                ORDER BY project_id
                LIMIT 20
                """
            )
        ).mappings()
    )
    if active:
        details = _format_duplicates(active, ("project_id",))
        raise RuntimeError(
            "Migration 0021 blocked: duplicate active sprints exist; "
            f"resolve them before retrying: {details}"
        )

    pending = list(
        bind.execute(
            sa.text(
                """
                SELECT issue_id, target_status_id, count(*) AS count
                FROM approval_requests
                WHERE status = 'pending'
                GROUP BY issue_id, target_status_id
                HAVING count(*) > 1
                ORDER BY issue_id, target_status_id
                LIMIT 20
                """
            )
        ).mappings()
    )
    if pending:
        details = _format_duplicates(pending, ("issue_id", "target_status_id"))
        raise RuntimeError(
            "Migration 0021 blocked: duplicate pending approval requests exist; "
            f"resolve them before retrying: {details}"
        )


def upgrade() -> None:
    _reject_duplicates()
    op.add_column(
        "issues",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "sprints",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "approval_requests",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "uq_sprints_project_active",
        "sprints",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "uq_approval_requests_issue_target_pending",
        "approval_requests",
        ["issue_id", "target_status_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_notifications_user_id_id",
        "notifications",
        ["user_id", "id"],
    )
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", "id"],
        postgresql_where=sa.text("read = false"),
    )
    op.create_index(
        "ix_activity_logs_issue_created",
        "activity_logs",
        ["issue_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_logs_issue_created", table_name="activity_logs")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_user_id_id", table_name="notifications")
    op.drop_index(
        "uq_approval_requests_issue_target_pending",
        table_name="approval_requests",
    )
    op.drop_index("uq_sprints_project_active", table_name="sprints")
    op.drop_column("approval_requests", "lock_version")
    op.drop_column("sprints", "lock_version")
    op.drop_column("issues", "lock_version")
