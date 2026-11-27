from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, utcnow
from ..deps import current_user, get_issue, issue_by_id, require_writable_issue
from ..models import Comment, User
from ..schemas import CommentIn
from ..serialize import comment_out
from ..services import permissions
from ..services.issues import add_comment

router = APIRouter(prefix="/api", tags=["comments"])


@router.get("/issues/{key}/comments")
def list_comments(key: str, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    rows = db.scalars(
        select(Comment)
        .options(selectinload(Comment.author))
        .where(Comment.issue_id == issue.id)
        .order_by(Comment.id)
    )
    return {"comments": [comment_out(c) for c in rows]}


@router.post("/issues/{key}/comments")
def create_comment(key: str, body: CommentIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    require_writable_issue(db, issue, user)
    comment = add_comment(db, issue, user, body.body)
    db.commit()
    db.refresh(comment)
    return comment_out(comment)


def _own(db: Session, comment_id: int, user: User) -> Comment:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(404, "评论不存在")
    issue = issue_by_id(db, comment.issue_id, user)
    if issue is None:
        raise HTTPException(404, "问题不存在")
    require_writable_issue(db, issue, user)
    if comment.author_id != user.id:
        roles = permissions.project_roles(db, issue.project_id, user) if issue else set()
        if not permissions.is_project_admin(user, roles):
            raise HTTPException(403, "只能修改自己的评论")
    return comment


@router.put("/comments/{comment_id}")
def edit_comment(comment_id: int, body: CommentIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    comment = _own(db, comment_id, user)
    comment.body = body.body.strip()
    comment.updated_at = utcnow()
    db.commit()
    return comment_out(comment)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    comment = _own(db, comment_id, user)
    db.delete(comment)
    db.commit()
    return {"ok": True}
