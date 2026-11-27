"""shared filter library: sharing scope, description and copy lineage

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12 10:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('saved_filters', sa.Column('description', sa.Text(), nullable=False,
                                             server_default=''))
    op.add_column('saved_filters', sa.Column('scope', sa.String(length=16), nullable=False,
                                             server_default='private'))
    op.add_column('saved_filters', sa.Column('share_project_id', sa.Integer(), nullable=True))
    op.add_column('saved_filters', sa.Column('source_id', sa.Integer(), nullable=True))
    op.add_column('saved_filters', sa.Column('updated_at', sa.DateTime(timezone=True),
                                             nullable=True))
    op.create_foreign_key('fk_saved_filters_share_project', 'saved_filters', 'projects',
                          ['share_project_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_saved_filters_source', 'saved_filters', 'saved_filters',
                          ['source_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_saved_filters_scope', 'saved_filters', ['scope'])
    op.create_index('ix_saved_filters_source_id', 'saved_filters', ['source_id'])
    op.execute("UPDATE saved_filters SET scope = 'everyone' WHERE shared")
    op.drop_column('saved_filters', 'shared')


def downgrade() -> None:
    op.add_column('saved_filters', sa.Column('shared', sa.Boolean(), nullable=False,
                                             server_default=sa.false()))
    op.execute("UPDATE saved_filters SET shared = TRUE WHERE scope <> 'private'")
    op.drop_index('ix_saved_filters_source_id', table_name='saved_filters')
    op.drop_index('ix_saved_filters_scope', table_name='saved_filters')
    op.drop_constraint('fk_saved_filters_source', 'saved_filters', type_='foreignkey')
    op.drop_constraint('fk_saved_filters_share_project', 'saved_filters', type_='foreignkey')
    for column in ('updated_at', 'source_id', 'share_project_id', 'scope', 'description'):
        op.drop_column('saved_filters', column)
