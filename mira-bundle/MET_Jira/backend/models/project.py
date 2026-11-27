from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, utcnow
from .org import User
from .template import DepartmentTemplateVersion

PROJECT_ROLES = ("admin", "requester", "electrical", "tester", "member")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    plan_start_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)
    issue_seq: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[str] = mapped_column(String(16), default="#0a84ff")
    # Preset cartoon badge id (OW2-style player-icon vibe); null = key initials.
    icon: Mapped[str | None] = mapped_column(String(32))
    cover_key: Mapped[str | None] = mapped_column(String(64))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    template_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("department_template_versions.id", ondelete="SET NULL"), index=True
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lead: Mapped[User | None] = relationship()
    template_version: Mapped[DepartmentTemplateVersion | None] = relationship(
        foreign_keys=[template_version_id]
    )
    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    statuses: Mapped[list[Status]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Status.sort_order"
    )
    machine_groups: Mapped[list[MachineGroup]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="MachineGroup.sort_order",
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Status(Base):
    __tablename__ = "statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16), default="todo")  # todo | doing | done
    color: Mapped[str] = mapped_column(String(16), default="#8e8e93")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="statuses")


class MachineGroup(Base):
    __tablename__ = "machine_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="machine_groups")
    machines: Mapped[list[Machine]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="Machine.sort_order"
    )


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("machine_groups.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped[MachineGroup] = relationship(back_populates="machines")


class Sprint(Base):
    __tablename__ = "sprints"
    __table_args__ = (
        Index(
            "uq_sprints_project_active",
            "project_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    goal: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(16), default="future")  # future | active | closed
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16), default="kanban")  # kanban | scrum
    # [{"name": str, "status_ids": [int], "wip": int | None}]
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    swimlane: Mapped[str] = mapped_column(String(32), default="none")
    quick_filters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    filter_jql: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
