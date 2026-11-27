from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, utcnow


class DepartmentTemplate(Base):
    __tablename__ = "department_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "department_template_versions.id",
            name="fk_department_templates_current_version",
            ondelete="SET NULL",
            use_alter=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list[DepartmentTemplateVersion]] = relationship(
        back_populates="template",
        foreign_keys="DepartmentTemplateVersion.template_id",
        cascade="all, delete-orphan",
        order_by="DepartmentTemplateVersion.version",
    )
    current_version: Mapped[DepartmentTemplateVersion | None] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )


class DepartmentTemplateVersion(Base):
    __tablename__ = "department_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("department_templates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_from_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("department_template_versions.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    published_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    template: Mapped[DepartmentTemplate] = relationship(
        back_populates="versions", foreign_keys=[template_id]
    )


class DepartmentTemplateAudit(Base):
    __tablename__ = "department_template_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("department_templates.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("department_template_versions.id", ondelete="SET NULL")
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class OrgUnitTemplateRecommendation(Base):
    __tablename__ = "org_unit_template_recommendations"
    __table_args__ = (UniqueConstraint("org_unit_id", "template_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_unit_id: Mapped[int] = mapped_column(
        ForeignKey("org_units.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("department_templates.id", ondelete="CASCADE"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")

    template: Mapped[DepartmentTemplate] = relationship()


class ProjectTemplateMigration(Base):
    __tablename__ = "project_template_migrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("department_template_versions.id", ondelete="SET NULL")
    )
    target_version_id: Mapped[int] = mapped_column(
        ForeignKey("department_template_versions.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), default="previewed", index=True)
    preview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
