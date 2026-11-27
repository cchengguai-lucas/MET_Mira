from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..deps import current_user
from ..models import Issue, Notification, User
from ..serialize import iso, user_brief

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ReadIn(BaseModel):
    ids: list[int] | None = None
    all: bool = False


@router.get("/unread-count")
def unread_count(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    unread = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.read.is_(False)
        )
    )
    return {"unread": int(unread or 0)}


@router.get("")
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(Notification)
        .options(selectinload(Notification.actor))
        .where(Notification.user_id == user.id)
    )
    if unread_only:
        stmt = stmt.where(Notification.read.is_(False))
    rows = list(db.scalars(stmt.order_by(Notification.id.desc()).limit(limit)))
    keys = dict(
        db.execute(
            select(Issue.id, Issue.key).where(
                Issue.id.in_([r.issue_id for r in rows if r.issue_id])
            )
        ).all()
    )
    unread = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.read.is_(False)
        )
    )
    return {
        "unread": unread,
        "notifications": [
            {
                "id": r.id,
                "kind": r.kind,
                "message": r.message,
                "read": r.read,
                "actor": user_brief(r.actor),
                "issue_key": keys.get(r.issue_id),
                "created_at": iso(r.created_at),
            }
            for r in rows
        ],
    }


@router.post("/read")
def mark_read(body: ReadIn, user: User = Depends(current_user),
              db: Session = Depends(get_db)) -> dict:
    stmt = update(Notification).where(Notification.user_id == user.id)
    if not body.all:
        stmt = stmt.where(Notification.id.in_(body.ids or []))
    db.execute(stmt.values(read=True))
    db.commit()
    return {"ok": True}
