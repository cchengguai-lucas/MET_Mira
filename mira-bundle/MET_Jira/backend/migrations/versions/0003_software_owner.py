"""issue software owner

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 14:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('issues', sa.Column('software_owner_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_issues_software_owner_id_users', 'issues', 'users',
        ['software_owner_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(op.f('ix_issues_software_owner_id'), 'issues', ['software_owner_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_issues_software_owner_id'), table_name='issues')
    op.drop_constraint('fk_issues_software_owner_id_users', 'issues', type_='foreignkey')
    op.drop_column('issues', 'software_owner_id')
