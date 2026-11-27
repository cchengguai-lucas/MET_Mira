"""versioned department templates

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-12 15:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "department_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_department_templates_key", "department_templates", ["key"], unique=True)
    op.create_index("ix_department_templates_enabled", "department_templates", ["enabled"])
    op.create_table(
        "department_template_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey(
            "department_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_from_version_id", sa.Integer(), sa.ForeignKey(
            "department_template_versions.id", ondelete="SET NULL")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey(
            "users.id", ondelete="SET NULL")),
        sa.Column("published_by_id", sa.Integer(), sa.ForeignKey(
            "users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("template_id", "version"),
    )
    op.create_index("ix_department_template_versions_template_id",
                    "department_template_versions", ["template_id"])
    op.create_index("ix_department_template_versions_status",
                    "department_template_versions", ["status"])
    op.create_foreign_key(
        "fk_department_templates_current_version",
        "department_templates", "department_template_versions",
        ["current_version_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("projects", sa.Column("template_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_projects_template_version", "projects", "department_template_versions",
        ["template_version_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_projects_template_version_id", "projects", ["template_version_id"])
    op.create_table(
        "org_unit_template_recommendations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_unit_id", sa.Integer(), sa.ForeignKey(
            "org_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey(
            "department_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("org_unit_id", "template_id"),
    )
    op.create_index("ix_org_unit_template_recommendations_org_unit_id",
                    "org_unit_template_recommendations", ["org_unit_id"])
    op.create_index("ix_org_unit_template_recommendations_template_id",
                    "org_unit_template_recommendations", ["template_id"])
    op.create_table(
        "project_template_migrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey(
            "projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version_id", sa.Integer(), sa.ForeignKey(
            "department_template_versions.id", ondelete="SET NULL")),
        sa.Column("target_version_id", sa.Integer(), sa.ForeignKey(
            "department_template_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="previewed"),
        sa.Column("preview", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey(
            "users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_project_template_migrations_project_id",
                    "project_template_migrations", ["project_id"])
    op.create_index("ix_project_template_migrations_status",
                    "project_template_migrations", ["status"])
def downgrade() -> None:
    op.drop_table("project_template_migrations")
    op.drop_table("org_unit_template_recommendations")
    op.drop_index("ix_projects_template_version_id", table_name="projects")
    op.drop_constraint("fk_projects_template_version", "projects", type_="foreignkey")
    op.drop_column("projects", "template_version_id")
    op.drop_constraint("fk_department_templates_current_version",
                       "department_templates", type_="foreignkey")
    op.drop_table("department_template_versions")
    op.drop_table("department_templates")
