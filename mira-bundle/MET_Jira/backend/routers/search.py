from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, utcnow
from ..deps import current_user
from ..models import Issue, Project, ProjectMember, SavedFilter, User
from ..schemas import FilterCopyIn, SavedFilterIn, SavedFilterUpdate
from ..serialize import iso, user_brief
from ..services import permissions, subtasks
from ..services.search import (
    FIELD_ALIASES,
    VIEW_FIELDS,
    JqlError,
    cached_search_facets,
    search_issues,
    view_conditions,
)

router = APIRouter(prefix="/api", tags=["search"])


def parse_view(raw: str) -> dict[str, list[str]]:
    """``view`` query parameter: JSON object of view field → selected values."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise HTTPException(400, "视图筛选格式错误") from error
    if not isinstance(data, dict):
        raise HTTPException(400, "视图筛选格式错误")
    return {k: [str(v) for v in vals] for k, vals in data.items()
            if k in VIEW_FIELDS and isinstance(vals, list)}


@router.get("/search")
def search(
    jql: str = "",
    view: str = "",
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        extra = view_conditions(parse_view(view), user_id=user.id)
        total, rows = search_issues(db, jql, user_id=user.id, limit=limit, offset=offset,
                                    extra=extra)
    except JqlError as error:
        raise HTTPException(400, f"查询语法错误：{error}") from error
    return {"total": total, "issues": subtasks.briefs(db, rows)}


@router.get("/search/facets")
def facets(jql: str = "", view: str = "", user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> dict:
    try:
        return cached_search_facets(db, jql, parse_view(view), user_id=user.id)
    except JqlError as error:
        raise HTTPException(400, f"查询语法错误：{error}") from error


@router.get("/search/quick")
def quick(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Command palette: match issue keys / titles and people."""
    q = q.strip()
    if not q:
        return {"issues": [], "users": []}
    like = f"%{q}%"
    visible = permissions.visible_issue_clause(db, user)
    issues = db.scalars(
        select(Issue)
        .options(selectinload(Issue.status))
        .where(or_(Issue.key.ilike(like), Issue.title.ilike(like)),
               *([visible] if visible is not None else []))
        .order_by(Issue.updated_at.desc())
        .limit(8)
    )
    users = db.scalars(
        select(User)
        .where(User.enabled.is_(True),
               or_(User.display_name.ilike(like), User.username.ilike(like)))
        .limit(5)
    )
    return {
        "issues": [
            {"key": i.key, "title": i.title, "type": i.type,
             "status": {"name": i.status.name, "color": i.status.color}}
            for i in issues
        ],
        "users": [user_brief(u) for u in users],
    }


@router.get("/search/fields")
def fields(_user: User = Depends(current_user)) -> dict:
    return {"fields": sorted({v for v in FIELD_ALIASES.values()}),
            "aliases": FIELD_ALIASES}


FILTER_LOADS = (
    selectinload(SavedFilter.owner),
    selectinload(SavedFilter.share_project),
    selectinload(SavedFilter.source).selectinload(SavedFilter.owner),
)


def member_project_ids(db: Session, user: User) -> set[int]:
    ids = set(db.scalars(select(ProjectMember.project_id)
                         .where(ProjectMember.user_id == user.id)))
    return ids | set(db.scalars(select(Project.id).where(Project.lead_id == user.id)))


def shared_clause(db: Session, user: User) -> ColumnElement[bool]:
    """Shared filters this user may browse: everyone-scope, their projects', and their own."""
    shared = SavedFilter.scope != "private"
    if user.is_admin:
        return shared
    return and_(shared, or_(
        SavedFilter.scope == "everyone",
        SavedFilter.owner_id == user.id,
        and_(SavedFilter.scope == "project",
             SavedFilter.share_project_id.in_(member_project_ids(db, user))),
    ))


def copy_counts(db: Session, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    return dict(db.execute(
        select(SavedFilter.source_id, func.count())
        .where(SavedFilter.source_id.in_(ids))
        .group_by(SavedFilter.source_id)
    ).all())


def filter_out(f: SavedFilter, user: User, copies: dict[int, int]) -> dict:
    project = f.share_project
    source = f.source
    return {
        "id": f.id,
        "name": f.name,
        "jql": f.jql,
        "description": f.description or "",
        "scope": f.scope,
        "project": {"key": project.key, "name": project.name} if project else None,
        "source": ({"id": source.id, "name": source.name, "owner": user_brief(source.owner)}
                   if source else None),
        "copies": copies.get(f.id, 0),
        "owner": user_brief(f.owner),
        "mine": f.owner_id == user.id,
        "created_at": iso(f.created_at),
        "updated_at": iso(f.updated_at or f.created_at),
    }


def check_jql(db: Session, jql: str, user: User) -> None:
    try:
        search_issues(db, jql, user_id=user.id, limit=1)
    except JqlError as error:
        raise HTTPException(400, f"查询语法错误：{error}") from error


def resolve_scope(db: Session, user: User, scope: str,
                  project_key: str | None) -> tuple[str, int | None]:
    if scope != "project":
        return scope, None
    if not project_key:
        raise HTTPException(400, "请选择要共享的项目")
    project = db.scalar(select(Project).where(Project.key == project_key.upper()))
    if project is None:
        raise HTTPException(404, "项目不存在")
    if not user.is_admin and project.id not in member_project_ids(db, user):
        raise HTTPException(403, "只能共享给你所在的项目")
    return scope, project.id


def owned_filter(db: Session, filter_id: int) -> SavedFilter:
    row = db.scalar(select(SavedFilter).options(*FILTER_LOADS)
                    .where(SavedFilter.id == filter_id))
    if row is None:
        raise HTTPException(404)
    return row


@router.get("/filters")
def list_filters(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The user's own library (shared filters of others live under /filters/shared)."""
    rows = list(db.scalars(
        select(SavedFilter).options(*FILTER_LOADS)
        .where(SavedFilter.owner_id == user.id)
        .order_by(SavedFilter.name)
    ))
    copies = copy_counts(db, [f.id for f in rows])
    return {"filters": [filter_out(f, user, copies) for f in rows]}


@router.get("/filters/shared")
def shared_filters(
    q: str = "",
    project: str = "",
    sort: Literal["popular", "new"] = "popular",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(SavedFilter).options(*FILTER_LOADS).where(shared_clause(db, user))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(SavedFilter.name.ilike(like), SavedFilter.description.ilike(like),
                              SavedFilter.jql.ilike(like)))
    if project == "everyone":
        stmt = stmt.where(SavedFilter.scope == "everyone")
    elif project:
        stmt = stmt.join(Project, Project.id == SavedFilter.share_project_id).where(
            SavedFilter.scope == "project", Project.key == project.upper())
    rows = list(db.scalars(stmt))
    copies = copy_counts(db, [f.id for f in rows])
    saved = set(db.scalars(select(SavedFilter.source_id).where(
        SavedFilter.owner_id == user.id, SavedFilter.source_id.is_not(None))))

    def recency(f: SavedFilter) -> float:
        return (f.updated_at or f.created_at).timestamp()

    if sort == "popular":
        rows.sort(key=lambda f: (copies.get(f.id, 0), recency(f)), reverse=True)
    else:
        rows.sort(key=recency, reverse=True)
    return {"filters": [{**filter_out(f, user, copies), "saved": f.id in saved} for f in rows]}


@router.post("/filters")
def create_filter(body: SavedFilterIn, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    check_jql(db, body.jql, user)
    scope, project_id = resolve_scope(db, user, body.scope, body.project_key)
    row = SavedFilter(owner_id=user.id, name=body.name.strip(), jql=body.jql,
                      description=body.description.strip(), scope=scope,
                      share_project_id=project_id)
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.patch("/filters/{filter_id}")
def update_filter(filter_id: int, body: SavedFilterUpdate, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    row = owned_filter(db, filter_id)
    fields = body.model_fields_set
    if row.owner_id != user.id:
        # Admins may take a shared filter down, nothing more.
        if not (user.is_admin and fields <= {"scope", "project_key"} and body.scope == "private"):
            raise HTTPException(403, "只能编辑自己的筛选器")
    if body.name is not None:
        row.name = body.name.strip()
    if body.description is not None:
        row.description = body.description.strip()
    if body.jql is not None and body.jql != row.jql:
        check_jql(db, body.jql, user)
        row.jql = body.jql
    if "scope" in fields or "project_key" in fields:
        scope = body.scope or row.scope
        key = body.project_key if "project_key" in fields else (
            row.share_project.key if row.share_project else None)
        row.scope, row.share_project_id = resolve_scope(db, user, scope, key)
    row.updated_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/filters/{filter_id}/copy")
def copy_filter(filter_id: int, body: FilterCopyIn | None = None,
                user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(select(SavedFilter).where(
        SavedFilter.id == filter_id,
        or_(SavedFilter.owner_id == user.id, shared_clause(db, user))))
    if row is None:
        raise HTTPException(404, "筛选器不存在或未共享给你")
    taken = set(db.scalars(select(SavedFilter.name).where(SavedFilter.owner_id == user.id)))
    base = ((body.name if body and body.name else None) or row.name).strip()
    name, n = base, 1
    while name in taken:
        suffix = " (副本)" if n == 1 else f" (副本 {n})"
        name = base[: 120 - len(suffix)] + suffix
        n += 1
    copy = SavedFilter(owner_id=user.id, name=name, jql=row.jql, description=row.description,
                       scope="private", source_id=row.id)
    db.add(copy)
    db.commit()
    return {"id": copy.id, "name": copy.name}


@router.delete("/filters/{filter_id}")
def delete_filter(filter_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    row = db.get(SavedFilter, filter_id)
    if row is None:
        raise HTTPException(404)
    if row.owner_id != user.id and not user.is_admin:
        raise HTTPException(403, "只能删除自己的筛选器")
    db.execute(update(SavedFilter).where(SavedFilter.source_id == row.id).values(source_id=None))
    db.delete(row)
    db.commit()
    return {"ok": True}
