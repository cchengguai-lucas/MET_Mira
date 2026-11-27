"""clear test results that contradict the issue status

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12 15:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

REOPENED = "重新打开"
TEST_FAILED = "测试未通过"
REJECTED = "已驳回"

projects = sa.table("projects", sa.column("id", sa.Integer), sa.column("settings", sa.JSON))
statuses = sa.table(
    "statuses",
    sa.column("id", sa.Integer),
    sa.column("project_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("category", sa.String),
)
issues = sa.table(
    "issues",
    sa.column("id", sa.Integer),
    sa.column("status_id", sa.Integer),
    sa.column("test_passed", sa.Boolean),
)


def upgrade() -> None:
    """A pass may only remain on a completed or reopened issue, a failure only on the fail
    status or a reopened issue; anything else is a leftover from an earlier round."""
    conn = op.get_bind()
    for project_id, settings in conn.execute(sa.select(projects.c.id, projects.c.settings)).all():
        fail_names = {TEST_FAILED, (settings or {}).get("fail_return_status")}
        rows = conn.execute(
            sa.select(statuses.c.id, statuses.c.name, statuses.c.category)
            .where(statuses.c.project_id == project_id)
        ).all()
        keep_pass = [r.id for r in rows if r.name == REOPENED
                     or (r.category == "done" and r.name != REJECTED)]
        keep_fail = [r.id for r in rows if r.name == REOPENED or r.name in fail_names]
        project_statuses = [r.id for r in rows]
        if not project_statuses:
            continue
        for passed, keep in ((True, keep_pass), (False, keep_fail)):
            conn.execute(
                issues.update()
                .where(issues.c.status_id.in_(project_statuses),
                       issues.c.test_passed.is_(passed),
                       issues.c.status_id.notin_(keep or [-1]))
                .values(test_passed=None)
            )


def downgrade() -> None:
    pass
