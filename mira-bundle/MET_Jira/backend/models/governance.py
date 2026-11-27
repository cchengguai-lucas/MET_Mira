from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, utcnow
from .org import User


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index(
            "uq_approval_requests_issue_target_pending",
            "issue_id",
            "target_status_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), index=True
    )
    target_status_id: Mapped[int] = mapped_column(ForeignKey("statuses.id"), index=True)
    requested_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    requirements: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    comment: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requested_by: Mapped[User | None] = relationship()


class MasterAsset(Base):
    __tablename__ = "master_assets"
    __table_args__ = (UniqueConstraint("kind", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # equipment | line | material | service
    kind: Mapped[str] = mapped_column(String(24), index=True)
    code: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectAsset(Base):
    __tablename__ = "project_assets"
    __table_args__ = (UniqueConstraint("project_id", "asset_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("master_assets.id", ondelete="CASCADE"), index=True
    )
    local_machine_id: Mapped[int | None] = mapped_column(
        ForeignKey("machines.id", ondelete="SET NULL"), index=True
    )
    alias: Mapped[str] = mapped_column(String(200), default="")

    asset: Mapped[MasterAsset] = relationship()
