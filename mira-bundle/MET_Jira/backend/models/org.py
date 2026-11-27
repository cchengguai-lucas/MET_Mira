from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, utcnow


class OrgUnit(Base):
    __tablename__ = "org_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    dn: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("org_units.id", ondelete="SET NULL"), index=True
    )
    path: Mapped[str] = mapped_column(String(1024), default="")
    level: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parent: Mapped[OrgUnit | None] = relationship(remote_side="OrgUnit.id")

    @property
    def label(self) -> str:
        return self.display_name or self.name


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    native_name: Mapped[str | None] = mapped_column(String(200), index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    title: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(64))
    dn: Mapped[str | None] = mapped_column(String(512), index=True)
    manager_dn: Mapped[str | None] = mapped_column(String(512))
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    org_unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("org_units.id", ondelete="SET NULL"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    can_create_projects: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_source: Mapped[str] = mapped_column(String(16), default="ldap")
    password_hash: Mapped[str | None] = mapped_column(String(256))
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    avatar_key: Mapped[str | None] = mapped_column(String(64))
    avatar_color: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    org_unit: Mapped[OrgUnit | None] = relationship()
    manager: Mapped[User | None] = relationship(remote_side="User.id")


class OrgSyncRun(Base):
    __tablename__ = "org_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diff: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
