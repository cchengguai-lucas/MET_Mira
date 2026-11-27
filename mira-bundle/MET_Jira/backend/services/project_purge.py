"""Permanent deletion of an archived project and everything that belongs to it."""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Attachment, Board, Comment, Issue, Project, Sprint, User
from . import covers

LOGGER = logging.getLogger(__name__)


def purge_counts(db: Session, project: Project) -> dict[str, int]:
    issue_ids = select(Issue.id).where(Issue.project_id == project.id)
    return {
        "issues": db.scalar(select(func.count()).select_from(issue_ids.subquery())) or 0,
        "comments": db.scalar(select(func.count(Comment.id))
                              .where(Comment.issue_id.in_(issue_ids))) or 0,
        "attachments": db.scalar(select(func.count(Attachment.id))
                                 .where(Attachment.issue_id.in_(issue_ids))) or 0,
        "boards": db.scalar(select(func.count(Board.id))
                            .where(Board.project_id == project.id)) or 0,
        "sprints": db.scalar(select(func.count(Sprint.id))
                             .where(Sprint.project_id == project.id)) or 0,
    }


def purge_project(db: Session, project: Project, actor: User) -> dict[str, int]:
    """Delete the project with its issues, comments, attachments, boards and sprints.

    Commits, then removes the attachment files; returns what was deleted.
    """
    counts = purge_counts(db, project)
    issue_ids = select(Issue.id).where(Issue.project_id == project.id)
    stored = list(db.scalars(select(Attachment.stored_name)
                             .where(Attachment.issue_id.in_(issue_ids))))
    cover_key = project.cover_key
    project_id = project.id
    outside = Issue.project_id != project.id
    db.execute(update(Issue).where(outside, Issue.parent_id.in_(issue_ids), Issue.type == "subtask")
               .values(parent_id=None, type="task"))
    db.execute(update(Issue).where(outside, Issue.parent_id.in_(issue_ids))
               .values(parent_id=None))
    db.execute(update(Issue).where(outside, Issue.epic_id.in_(issue_ids)).values(epic_id=None))
    db.execute(delete(Issue).where(Issue.project_id == project.id))
    key = project.key
    db.execute(delete(Project).where(Project.id == project.id))
    db.commit()
    db.expunge_all()
    folder = get_settings().attachments_dir
    for name in stored:
        (folder / name).unlink(missing_ok=True)
    if cover_key:
        covers.cover_path(project_id, cover_key).unlink(missing_ok=True)
    LOGGER.warning("project %s permanently deleted by %s: %s", key, actor.username, counts)
    return counts
