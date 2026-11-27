"""Three-party scheme confirmation on electrical requirements.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-06 11:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "issues",
        sa.Column("scheme_note", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("issues", sa.Column("electrical_scheme_ok", sa.Boolean(), nullable=True))
    op.add_column("issues", sa.Column("process_scheme_ok", sa.Boolean(), nullable=True))
    op.add_column("issues", sa.Column("software_scheme_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "issues",
        sa.Column("scheme_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "issues",
        sa.Column("scheme_confirmed_note", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("issues", "scheme_confirmed_note")
    op.drop_column("issues", "scheme_locked")
    op.drop_column("issues", "software_scheme_ok")
    op.drop_column("issues", "process_scheme_ok")
    op.drop_column("issues", "electrical_scheme_ok")
    op.drop_column("issues", "scheme_note")
