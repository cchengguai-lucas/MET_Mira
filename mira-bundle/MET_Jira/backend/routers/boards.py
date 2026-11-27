from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..deps import check_project, current_user, get_project
from ..models import Board, Issue, Project, Sprint, Status, User
from ..schemas import BoardUpdate
from ..serialize import sprint_out, status_out
from ..services import permissions, reports, subtasks
from ..services.search import JqlError, compile_jql, search_issues

router = APIRouter(prefix="/api", tags=["boards"])

DONE_VISIBLE_DAYS = 14


def board_out(board: Board) -> dict:
    return {
        "id": board.id,
        "project_id": board.project_id,
        "name": board.name,
        "kind": board.kind,
        "columns": board.columns or [],
        "swimlane": board.swimlane,
        "quick_filters": board.quick_filters or [],
        "filter_jql": board.filter_jql,
    }


def _project(db: Session, board: Board) -> Project:
    project = db.get(Project, board.project_id)
    assert project is not None
    return project


def _board(db: Session, board_id: int, user: User) -> Board:
    board = db.get(Board, board_id)
    if board is None:
        raise HTTPException(404, "看板不存在")
    check_project(db, _project(db, board), user)
    return board


@router.get("/projects/{key}/boards")
def list_boards(key: str, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    boards = db.scalars(select(Board).where(Board.project_id == project.id).order_by(Board.id))
    return {"boards": [board_out(b) for b in boards]}


@router.get("/boards/{board_id}")
def board_data(board_id: int, jql: str = "", user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    board = _board(db, board_id, user)
    statuses = list(
        db.scalars(
            select(Status).where(Status.project_id == board.project_id).order_by(Status.sort_order)
        )
    )
    extra = [Issue.project_id == board.project_id, Issue.type != "epic"]
    sprint = None
    if board.kind == "scrum":
        sprint = db.scalar(
            select(Sprint).where(Sprint.project_id == board.project_id, Sprint.state == "active")
            .order_by(Sprint.id)
        )
        extra.append(Issue.sprint_id == (sprint.id if sprint else -1))
    else:
        done_ids = [s.id for s in statuses if s.category == "done"]
        cutoff = utcnow() - timedelta(days=DONE_VISIBLE_DAYS)
        extra.append(
            or_(Issue.status_id.notin_(done_ids or [-1]), Issue.resolved_at.is_(None),
                Issue.resolved_at >= cutoff)
        )
    try:
        if board.filter_jql.strip():
            where, _ = compile_jql(board.filter_jql, user_id=user.id)
            extra.append(where)
        _, rows = search_issues(db, jql, user_id=user.id, limit=800, extra=extra,
                                default_order=[Issue.rank])
    except JqlError as error:
        raise HTTPException(400, f"查询语法错误：{error}") from error
    roles = permissions.project_roles(db, board.project_id, user)
    since = reports.status_since(db, rows)
    return {
        "board": board_out(board),
        "statuses": [status_out(s) for s in statuses],
        "sprint": sprint_out(sprint),
        "issues": subtasks.cards(db, rows),
        "can_configure": permissions.is_project_admin(user, roles),
        "aging": {
            "warn": reports.AGING_WARN_DAYS,
            "danger": reports.AGING_DANGER_DAYS,
            "since": {str(issue_id): when.isoformat() for issue_id, when in since.items()},
        },
    }


@router.put("/boards/{board_id}")
def update_board(board_id: int, body: BoardUpdate, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    board = _board(db, board_id, user)
    data = body.model_dump(exclude_unset=True)
    if not set(data) <= {"swimlane"}:
        check_project(db, _project(db, board), user, "admin")
    if data.get("filter_jql"):
        try:
            compile_jql(data["filter_jql"], user_id=user.id)
        except JqlError as error:
            raise HTTPException(400, f"查询语法错误：{error}") from error
    for field, value in data.items():
        setattr(board, field, value)
    db.commit()
    return board_out(board)


@router.post("/projects/{key}/boards")
def create_board(key: str, body: BoardUpdate, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    statuses = db.scalars(
        select(Status).where(Status.project_id == project.id).order_by(Status.sort_order)
    )
    board = Board(
        project_id=project.id,
        name=body.name or f"{project.name} 看板",
        kind="kanban",
        columns=body.columns
        or [{"name": s.name, "status_ids": [s.id], "wip": None} for s in statuses],
        swimlane=body.swimlane or "none",
        quick_filters=body.quick_filters or [],
        filter_jql=body.filter_jql or "",
    )
    db.add(board)
    db.commit()
    return board_out(board)
