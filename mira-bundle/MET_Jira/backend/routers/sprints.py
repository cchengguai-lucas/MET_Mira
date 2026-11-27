from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..deps import check_project, current_user, get_project
from ..models import Issue, Project, Sprint, Status, User
from ..schemas import SprintComplete, SprintIn, SprintUpdate
from ..serialize import issue_query, sprint_out
from ..services import concurrency, subtasks, workflow
from ..services.issues import log

router = APIRouter(prefix="/api", tags=["sprints"])


def _sprint(db: Session, sprint_id: int, user: User, need: str = "view") -> Sprint:
    sprint = db.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(404, "冲刺不存在")
    project = db.get(Project, sprint.project_id)
    assert project is not None
    check_project(db, project, user, need)
    return sprint


@router.get("/projects/{key}/backlog")
def backlog(key: str, user: User = Depends(current_user),
            db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    sprints = list(
        db.scalars(
            select(Sprint)
            .where(Sprint.project_id == project.id, Sprint.state != "closed")
            .order_by(Sprint.state.desc(), Sprint.id)
        )
    )
    sprints.sort(key=lambda s: (0 if s.state == "active" else 1, s.id))
    done_ids = select(Status.id).where(Status.project_id == project.id,
                                       Status.category == "done")
    issues = list(
        db.scalars(
            issue_query()
            .where(
                Issue.project_id == project.id,
                Issue.type != "epic",
                (Issue.sprint_id.is_not(None)) | (Issue.status_id.notin_(done_ids)),
            )
            .order_by(Issue.rank)
        )
    )
    open_ids = {s.id for s in sprints}
    by_sprint: dict[int, list] = {s.id: [] for s in sprints}
    backlog_items = []
    for brief in subtasks.cards(db, issues):
        if brief["sprint_id"] in open_ids:
            by_sprint[brief["sprint_id"]].append(brief)
        elif brief["status"]["category"] != "done":
            backlog_items.append(brief)
    by_sprint = {sid: _nest(items) for sid, items in by_sprint.items()}
    backlog_items = _nest(backlog_items)
    return {
        "sprints": [{**(sprint_out(s) or {}), "issues": by_sprint[s.id]} for s in sprints],
        "backlog": backlog_items,
    }


def _nest(items: list[dict]) -> list[dict]:
    """Sub-tasks travel with their parent, so they are listed only when the parent is not."""
    present = {i["id"] for i in items}
    return [i for i in items if not (i["parent"] and i["parent"]["id"] in present)]


@router.get("/projects/{key}/sprints")
def list_sprints(key: str, include_closed: bool = True, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    stmt = select(Sprint).where(Sprint.project_id == project.id)
    if not include_closed:
        stmt = stmt.where(Sprint.state != "closed")
    return {"sprints": [sprint_out(s) for s in db.scalars(stmt.order_by(Sprint.id.desc()))]}


@router.post("/projects/{key}/sprints")
def create_sprint(key: str, body: SprintIn, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "member")
    count = db.scalar(select(func.count(Sprint.id)).where(Sprint.project_id == project.id)) or 0
    sprint = Sprint(project_id=project.id, name=body.name or f"{project.key} 冲刺 {count + 1}",
                    goal=body.goal, start_date=body.start_date, end_date=body.end_date)
    db.add(sprint)
    db.commit()
    return sprint_out(sprint) or {}


@router.put("/sprints/{sprint_id}")
def update_sprint(sprint_id: int, body: SprintUpdate, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    sprint = _sprint(db, sprint_id, user, "member")
    sprint = concurrency.lock_versioned(db, Sprint, sprint.id, body.expected_version)
    for field, value in body.model_dump(
        exclude_unset=True,
        exclude={"expected_version"},
    ).items():
        setattr(sprint, field, value)
    sprint.lock_version += 1
    db.commit()
    return sprint_out(sprint) or {}


@router.post("/sprints/{sprint_id}/start")
def start_sprint(sprint_id: int, body: SprintUpdate, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    sprint = _sprint(db, sprint_id, user, "member")
    concurrency.lock_project(db, sprint.project_id)
    sprint = concurrency.lock_versioned(db, Sprint, sprint.id, body.expected_version)
    active = db.scalar(
        select(Sprint.id).where(Sprint.project_id == sprint.project_id, Sprint.state == "active")
    )
    if active is not None:
        raise HTTPException(409, "已有进行中的冲刺，请先完成该冲刺")
    if sprint.state != "future":
        raise HTTPException(409, "只能开始尚未开始的冲刺")
    for field, value in body.model_dump(
        exclude_unset=True,
        exclude={"expected_version"},
    ).items():
        setattr(sprint, field, value)
    issues = list(db.scalars(select(Issue).where(Issue.sprint_id == sprint.id)))
    sprint.state = "active"
    sprint.lock_version += 1
    sprint.started_at = utcnow()
    sprint.stats = {
        "committed_count": len(issues),
        "committed_points": sum(i.story_points or 0 for i in issues),
        "committed_keys": [i.key for i in issues],
    }
    db.commit()
    return sprint_out(sprint) or {}


@router.post("/sprints/{sprint_id}/complete")
def complete_sprint(sprint_id: int, body: SprintComplete, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    sprint = _sprint(db, sprint_id, user, "member")
    concurrency.lock_project(db, sprint.project_id)
    sprint = concurrency.lock_versioned(db, Sprint, sprint.id, body.expected_version)
    if sprint.state != "active":
        raise HTTPException(409, "只能完成进行中的冲刺")
    target = None
    if body.move_open_to is not None:
        target = _sprint(db, body.move_open_to, user)
        if target.project_id != sprint.project_id or target.state == "closed":
            raise HTTPException(400, "目标冲刺无效")
    issues = list(db.scalars(issue_query().where(Issue.sprint_id == sprint.id)))
    completed = [i for i in issues if workflow.is_completed(i.status)]
    open_items = [i for i in issues if not workflow.is_closed(i.status)]
    for issue in open_items:
        issue.sprint_id = target.id if target else None
        log(db, issue, user, "updated", field="sprint_id", old=sprint.id,
            new=target.id if target else None)
    sprint.state = "closed"
    sprint.lock_version += 1
    sprint.completed_at = utcnow()
    sprint.stats = {
        **(sprint.stats or {}),
        "completed_count": len(completed),
        "completed_points": sum(i.story_points or 0 for i in completed),
        "incomplete_count": len(open_items),
        "completed_keys": [i.key for i in completed],
        "rejected_count": sum(1 for i in issues if workflow.is_rejected(i.status)),
    }
    db.commit()
    return sprint_out(sprint) or {}


@router.delete("/sprints/{sprint_id}")
def delete_sprint(sprint_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    sprint = _sprint(db, sprint_id, user, "member")
    if sprint.state == "active":
        raise HTTPException(409, "进行中的冲刺不能删除")
    for issue in db.scalars(select(Issue).where(Issue.sprint_id == sprint.id)):
        issue.sprint_id = None
    db.delete(sprint)
    db.commit()
    return {"ok": True}
