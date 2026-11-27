"""Sub-task progress shown on parent issues in boards, backlog and lists."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import Issue, Status
from ..serialize import attach_custom_field_users, issue_brief, issue_card
from .workflow import REJECTED


def progress(db: Session, parent_ids: Iterable[int]) -> dict[int, dict[str, int]]:
    ids = list(set(parent_ids))
    if not ids:
        return {}
    completed = (Status.category == "done") & (Status.name != REJECTED)
    rows = db.execute(
        select(Issue.parent_id, func.count(Issue.id),
               func.sum(case((completed, 1), else_=0)))
        .join(Status, Status.id == Issue.status_id)
        .where(Issue.parent_id.in_(ids))
        .group_by(Issue.parent_id)
    )
    return {pid: {"done": int(done or 0), "total": int(total)} for pid, total, done in rows}


def _with_progress(db: Session, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = progress(db, (b["id"] for b in out if b["type"] != "subtask"))
    for brief in out:
        if brief["id"] in counts:
            brief["subtasks"] = counts[brief["id"]]
    return out


def briefs(db: Session, issues: Iterable[Issue]) -> list[dict[str, Any]]:
    """``issue_brief`` plus ``subtasks: {done, total}`` on issues that have sub-tasks."""
    rows = list(issues)
    payloads = attach_custom_field_users(db, rows, [issue_brief(i) for i in rows])
    return _with_progress(db, payloads)


def cards(db: Session, issues: Iterable[Issue]) -> list[dict[str, Any]]:
    """Lean board/backlog cards plus sub-task progress badges."""
    return _with_progress(db, [issue_card(i) for i in issues])
