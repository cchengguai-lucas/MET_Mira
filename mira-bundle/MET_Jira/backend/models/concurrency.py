from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"
    __table_args__ = (
        Index(
            "ix_rate_limit_events_subject_time",
            "kind",
            "subject_hash",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    subject_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AiSlotLease(Base):
    __tablename__ = "ai_slot_leases"
    __table_args__ = (
        Index("ix_ai_slot_leases_subject_expires", "subject_hash", "expires_at"),
    )

    token: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_hash: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(128))
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
