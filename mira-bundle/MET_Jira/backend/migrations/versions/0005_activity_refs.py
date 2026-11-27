"""activity log ids for id-valued fields

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10 15:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('activity_logs', sa.Column('old_ref', sa.Integer(), nullable=True))
    op.add_column('activity_logs', sa.Column('new_ref', sa.Integer(), nullable=True))
    # Status history so far only kept names; match them to the project's current statuses.
    for side in ('old', 'new'):
        op.execute(f"""
            UPDATE activity_logs AS a SET {side}_ref = s.id
            FROM issues AS i, statuses AS s
            WHERE a.field = 'status_id' AND a.issue_id = i.id
              AND s.project_id = i.project_id AND s.name = a.{side}_value
        """)


def downgrade() -> None:
    op.drop_column('activity_logs', 'new_ref')
    op.drop_column('activity_logs', 'old_ref')
