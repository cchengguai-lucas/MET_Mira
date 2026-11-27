"""Who signed each scheme confirmation.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-06 11:50:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_COLUMNS = (
    "electrical_scheme_by_id",
    "process_scheme_by_id",
    "software_scheme_by_id",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("issues", sa.Column(name, sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_issues_{name}", "issues", "users", [name], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_constraint(f"fk_issues_{name}", "issues", type_="foreignkey")
        op.drop_column("issues", name)
