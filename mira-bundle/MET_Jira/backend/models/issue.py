from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, utcnow
from .org import User
from .project import Machine, Project, Sprint, Status

ISSUE_TYPES = ("requirement", "bug", "task", "story", "epic", "subtask")
PRIORITIES = ("highest", "high", "medium", "low", "lowest")


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        Index("ix_issues_project_sprint", "project_id", "sprint_id"),
        Index("ix_issues_project_rank", "project_id", "rank"),
        Index("ix_issues_project_updated", "project_id", "updated_at"),
        Index("ix_issues_project_resolved", "project_id", "resolved_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(16), default="requirement", index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    status_id: Mapped[int] = mapped_column(ForeignKey("statuses.id"), index=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    reporter_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    tester_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="SET NULL"), index=True
    )
    epic_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="SET NULL"), index=True
    )
    sprint_id: Mapped[int | None] = mapped_column(
        ForeignKey("sprints.id", ondelete="SET NULL"), index=True
    )
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    planned_start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    story_points: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sla_state: Mapped[str] = mapped_column(String(16), default="ok", index=True)
    sla_notified_level: Mapped[int] = mapped_column(Integer, default=0)
    sla_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_paused_hours: Mapped[float] = mapped_column(Float, default=0.0)
    import_batch_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # 需求提出人填写
    project_name: Mapped[str] = mapped_column(String(200), default="")
    # 电气组填写
    update_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    involves_software: Mapped[bool | None] = mapped_column(Boolean)
    software_owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    verify_method: Mapped[str] = mapped_column(Text, default="")
    verify_criteria: Mapped[str] = mapped_column(Text, default="")
    # 软件组填写。快照只读，不参与流转。
    software_ready: Mapped[bool | None] = mapped_column(Boolean)
    software_note: Mapped[str] = mapped_column(Text, default="")
    software_jira_key: Mapped[str] = mapped_column(String(64), default="")
    software_jira_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    # 方案确认。快照是上次发起确认时的方案说明，不能由客户端直接改。
    scheme_note: Mapped[str] = mapped_column(Text, default="")
    electrical_scheme_ok: Mapped[bool | None] = mapped_column(Boolean)
    electrical_scheme_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    process_scheme_ok: Mapped[bool | None] = mapped_column(Boolean)
    process_scheme_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    software_scheme_ok: Mapped[bool | None] = mapped_column(Boolean)
    software_scheme_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    scheme_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    scheme_confirmed_note: Mapped[str] = mapped_column(Text, default="")
    # 测试人员填写
    update_date: Mapped[date | None] = mapped_column(Date)
    test_passed: Mapped[bool | None] = mapped_column(Boolean)
    remark: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )

    project: Mapped[Project] = relationship()
    status: Mapped[Status] = relationship()
    reporter: Mapped[User | None] = relationship(foreign_keys=[reporter_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    tester: Mapped[User | None] = relationship(foreign_keys=[tester_id])
    software_owner: Mapped[User | None] = relationship(foreign_keys=[software_owner_id])
    electrical_scheme_by: Mapped[User | None] = relationship(foreign_keys=[electrical_scheme_by_id])
    process_scheme_by: Mapped[User | None] = relationship(foreign_keys=[process_scheme_by_id])
    software_scheme_by: Mapped[User | None] = relationship(foreign_keys=[software_scheme_by_id])
    sprint: Mapped[Sprint | None] = relationship()
    parent: Mapped[Issue | None] = relationship(
        remote_side="Issue.id", foreign_keys=[parent_id]
    )
    epic: Mapped[Issue | None] = relationship(remote_side="Issue.id", foreign_keys=[epic_id])
    machine_links: Mapped[list[IssueMachine]] = relationship(
        cascade="all, delete-orphan", back_populates="issue"
    )

    @property
    def machines(self) -> list[Machine]:
        return [link.machine for link in self.machine_links]


class IssueMachine(Base):
    __tablename__ = "issue_machines"
    __table_args__ = (UniqueConstraint("issue_id", "machine_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), index=True
    )

    issue: Mapped[Issue] = relationship(back_populates="machine_links")
    machine: Mapped[Machine] = relationship()
