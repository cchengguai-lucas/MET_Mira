"""composite indexes for board / list / kanban hot paths

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-15 15:30:00
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_issues_project_sprint", "issues", ["project_id", "sprint_id"])
    op.create_index("ix_issues_project_rank", "issues", ["project_id", "rank"])
    op.create_index("ix_issues_project_updated", "issues", ["project_id", "updated_at"])
    op.create_index("ix_issues_project_resolved", "issues", ["project_id", "resolved_at"])


def downgrade() -> None:
    op.drop_index("ix_issues_project_resolved", table_name="issues")
    op.drop_index("ix_issues_project_updated", table_name="issues")
    op.drop_index("ix_issues_project_rank", table_name="issues")
    op.drop_index("ix_issues_project_sprint", table_name="issues")
