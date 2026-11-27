"""shared rate limits, AI leases and import idempotency

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-21 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_rate_limit_events_subject_time",
        "rate_limit_events",
        ["kind", "subject_hash", "occurred_at"],
    )
    op.create_table(
        "ai_slot_leases",
        sa.Column("token", sa.String(length=36), primary_key=True),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_slot_leases_subject_expires",
        "ai_slot_leases",
        ["subject_hash", "expires_at"],
    )
    op.add_column("import_batches", sa.Column("token", sa.String(length=32)))
    op.add_column(
        "import_batches",
        sa.Column("result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_unique_constraint("uq_import_batches_token", "import_batches", ["token"])


def downgrade() -> None:
    op.drop_constraint("uq_import_batches_token", "import_batches", type_="unique")
    op.drop_column("import_batches", "result")
    op.drop_column("import_batches", "token")
    op.drop_index("ix_ai_slot_leases_subject_expires", table_name="ai_slot_leases")
    op.drop_table("ai_slot_leases")
    op.drop_index("ix_rate_limit_events_subject_time", table_name="rate_limit_events")
    op.drop_table("rate_limit_events")
