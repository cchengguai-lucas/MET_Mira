from __future__ import annotations

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import is_postgres
from ..models import ApprovalRequest, Issue, Project, Sprint
from .issues import IssueError

Versioned = TypeVar("Versioned", Issue, ApprovalRequest, Sprint)


def stale_write() -> IssueError:
    return IssueError(
        "内容已被其他人更新；当前编辑内容已保留，请刷新后重试",
        409,
        code="stale_write",
    )


def lock_versioned(
    db: Session,
    model: type[Versioned],
    row_id: int,
    expected_version: int | None = None,
) -> Versioned:
    stmt = (
        select(model)
        .where(model.id == row_id)
        .execution_options(populate_existing=True)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update()
    row = db.scalars(stmt).one()
    if is_postgres(db):
        # Refresh attributes/previously eager-loaded relationships after a lock wait;
        # the identity map may still contain the pre-lock view read for authorization.
        db.refresh(row)
    if expected_version is not None and row.lock_version != expected_version:
        raise stale_write()
    return row


def lock_project(db: Session, project_id: int) -> Project:
    stmt = (
        select(Project)
        .where(Project.id == project_id)
        .execution_options(populate_existing=True)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update()
    return db.scalars(stmt).one()
