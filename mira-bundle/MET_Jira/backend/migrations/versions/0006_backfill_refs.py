"""activity log ids for sprint, people and issue links written before ids were logged

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10 16:00:00
"""

from __future__ import annotations

from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

# (fields, joined table, condition matching the logged display value)
MATCHES = [
    ("('sprint_id')", 'sprints AS t', "t.project_id = i.project_id AND t.name = a.{side}_value"),
    ("('assignee_id', 'tester_id', 'software_owner_id')", 'users AS t',
     "t.display_name = a.{side}_value"),
    ("('parent_id', 'epic_id')", 'issues AS t',
     "t.project_id = i.project_id AND t.key = a.{side}_value"),
]


def upgrade() -> None:
    for fields, table, condition in MATCHES:
        for side in ('old', 'new'):
            op.execute(f"""
                UPDATE activity_logs AS a SET {side}_ref = t.id
                FROM issues AS i, {table}
                WHERE a.field IN {fields} AND a.issue_id = i.id
                  AND a.{side}_ref IS NULL AND a.{side}_value IS NOT NULL
                  AND {condition.format(side=side)}
            """)


def downgrade() -> None:
    pass
