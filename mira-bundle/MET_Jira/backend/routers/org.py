from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, current_user_or_external, get_project, require_admin
from ..models import ActivityLog, Issue, OrgUnit, ProjectMember, Status, User
from ..schemas import OrgUnitUpdate
from ..serialize import (
    assigned_open_issue_out,
    attach_custom_field_users,
    issue_brief,
    issue_query,
    org_unit_out,
    user_brief,
    user_full,
)
from ..services import avatars, permissions, profiles
from ..services.software import descendant_ids as _descendant_ids
from ..services.software import software_unit_ids

router = APIRouter(prefix="/api", tags=["org"])


def _public_base(request: Request) -> str:
    return get_settings().public_url or str(request.base_url).rstrip("/")


def _user_by_username(db: Session, username: str) -> User:
    person = db.scalar(
        select(User)
        .options(selectinload(User.org_unit), selectinload(User.manager))
        .where(User.username == username)
    )
    if person is None:
        raise HTTPException(404, "用户不存在")
    return person


@router.get("/org/tree")
def org_tree(
    include_hidden: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    units = list(
        db.scalars(
            select(OrgUnit).where(OrgUnit.removed.is_(False)).order_by(
                OrgUnit.level, OrgUnit.sort_order, OrgUnit.name
            )
        )
    )
    if not (include_hidden and user.is_admin):
        hidden_roots = [u.id for u in units if u.hidden]
        hidden: set[int] = set()
        for root in hidden_roots:
            hidden |= _descendant_ids(units, root)
        units = [u for u in units if u.id not in hidden]
    direct = dict(
        db.execute(
            select(User.org_unit_id, func.count(User.id))
            .where(User.enabled.is_(True))
            .group_by(User.org_unit_id)
        ).all()
    )
    totals: dict[int, int] = {}
    for unit in units:
        totals[unit.id] = sum(direct.get(i, 0) for i in _descendant_ids(units, unit.id))
    unassigned = direct.get(None, 0)
    return {
        "units": [
            {**org_unit_out(u, direct.get(u.id, 0)), "total_count": totals.get(u.id, 0)}
            for u in units
        ],
        "unassigned_count": unassigned,
        "user_count": sum(direct.values()),
    }


@router.get("/org/units/{unit_id}")
def org_unit_detail(
    unit_id: int, _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    unit = db.get(OrgUnit, unit_id)
    if unit is None:
        raise HTTPException(404, "组织不存在")
    units = list(db.scalars(select(OrgUnit).where(OrgUnit.removed.is_(False))))
    ids = _descendant_ids(units, unit.id)
    members = list(
        db.scalars(
            select(User)
            .options(selectinload(User.manager), selectinload(User.org_unit))
            .where(User.org_unit_id == unit.id)
            .order_by(User.enabled.desc(), User.display_name)
        )
    )
    member_ids = select(User.id).where(User.org_unit_id.in_(ids))
    open_rows = db.execute(
        select(Status.category, func.count(Issue.id))
        .join(Status, Status.id == Issue.status_id)
        .where(Issue.assignee_id.in_(member_ids))
        .group_by(Status.category)
    ).all()
    children = [u for u in units if u.parent_id == unit.id]
    children.sort(key=lambda u: (u.sort_order, u.name))
    return {
        "unit": org_unit_out(unit, len(members)),
        "breadcrumb": _breadcrumb(db, unit),
        "children": [org_unit_out(c) for c in children],
        "members": [user_full(m) for m in members],
        "subtree_member_count": db.scalar(
            select(func.count(User.id)).where(User.org_unit_id.in_(ids), User.enabled.is_(True))
        ),
        "issue_stats": {category: count for category, count in open_rows},
    }


def _breadcrumb(db: Session, unit: OrgUnit) -> list[dict]:
    chain = []
    node: OrgUnit | None = unit
    guard = 0
    while node is not None and guard < 64:
        chain.append({"id": node.id, "label": node.label})
        node = db.get(OrgUnit, node.parent_id) if node.parent_id else None
        guard += 1
    return list(reversed(chain))


@router.put("/org/units/{unit_id}")
def update_unit(
    unit_id: int,
    body: OrgUnitUpdate,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    from ..services.org_sync import refresh_paths

    unit = db.get(OrgUnit, unit_id)
    if unit is None:
        raise HTTPException(404, "组织不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(unit, field, value or None if field == "display_name" else value)
    db.flush()
    refresh_paths(db)
    db.commit()
    return org_unit_out(unit)


_PERSON_FIELDS = {"assignee_id", "tester_id", "member"}


def _project_people(db: Session, project_id: int, roles: set[str]):
    stmt = select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)
    if roles:
        stmt = stmt.where(ProjectMember.role.in_(roles))
    return stmt


@router.get("/users")
def list_users(
    q: str = "",
    org_unit_id: int | None = None,
    descendants: bool = True,
    include_disabled: bool = False,
    software_of: str | None = Query(None, description="项目 Key：只返回该项目软件部门的人员"),
    project: str | None = Query(None, description="项目 Key：按单据字段限制为项目成员"),
    field: str | None = Query(None, description="assignee_id、tester_id 或 member"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    if field and field not in _PERSON_FIELDS:
        raise HTTPException(400, "未知的人员范围")
    if field and not project:
        raise HTTPException(400, "请指定项目")
    stmt = select(User).options(selectinload(User.org_unit), selectinload(User.manager))
    if software_of:
        unit_ids = software_unit_ids(db, get_project(db, software_of, user))
        if unit_ids:
            stmt = stmt.where(User.org_unit_id.in_(unit_ids))
    elif project and field:
        target = get_project(db, project, user)
        if field == "member":
            roles = set()
        else:
            roles = profiles.person_roles(profiles.profile_of(target), field)
        pool = _project_people(db, target.id, roles)
        if roles:
            enabled = db.scalar(
                select(func.count()).select_from(User).where(
                    User.id.in_(pool), User.enabled.is_(True),
                )
            )
            if not enabled:
                pool = _project_people(db, target.id, set())
        stmt = stmt.where(User.id.in_(pool))
    if not include_disabled:
        stmt = stmt.where(User.enabled.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                User.username.ilike(like),
                User.display_name.ilike(like),
                User.email.ilike(like),
                User.title.ilike(like),
                User.department.ilike(like),
            )
        )
    if org_unit_id is not None:
        if descendants:
            units = list(db.scalars(select(OrgUnit)))
            stmt = stmt.where(User.org_unit_id.in_(_descendant_ids(units, org_unit_id)))
        else:
            stmt = stmt.where(User.org_unit_id == org_unit_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(User.display_name).offset(offset).limit(limit))
    return {"total": total, "users": [user_full(u) for u in rows]}


@router.get("/users/{user_id}/avatar")
def user_avatar(
    user_id: int, _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> FileResponse:
    person = db.get(User, user_id)
    path = avatars.avatar_path(user_id, person.avatar_key) if person and person.avatar_key else None
    if path is None or not path.is_file():
        raise HTTPException(404, "未设置头像")
    # URLs carry ?v=<avatar_key>, so a given URL never changes content.
    return FileResponse(path, media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/users/{username}/assigned-open")
def user_assigned_open(
    username: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User | None = Depends(current_user_or_external),
    db: Session = Depends(get_db),
) -> dict:
    """Unfinished issues assigned to ``username`` (status category != done).

    Returns full issue payloads plus absolute ``url`` and ``assigned_at``.
    Auth: session cookie, or ``Authorization: Bearer`` / ``X-Api-Key`` matching
    ``METJIRA_EXTERNAL_API_TOKEN`` (token callers see all projects; cookie
    callers keep visibility filters).
    """
    person = _user_by_username(db, username)
    visible = permissions.visible_issue_clause(db, user)
    filters = [Issue.assignee_id == person.id, Status.category != "done"]
    if visible is not None:
        filters.insert(0, visible)
    total = db.scalar(
        select(func.count())
        .select_from(Issue)
        .join(Status, Status.id == Issue.status_id)
        .where(*filters)
    ) or 0
    rows = list(
        db.scalars(
            issue_query()
            .join(Status, Status.id == Issue.status_id)
            .where(*filters)
            .order_by(Issue.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    assigned_at_by_issue: dict[int, object] = {}
    if rows:
        assigned_at_by_issue = dict(
            db.execute(
                select(ActivityLog.issue_id, func.max(ActivityLog.created_at))
                .where(
                    ActivityLog.issue_id.in_([issue.id for issue in rows]),
                    ActivityLog.field == "assignee_id",
                    ActivityLog.new_ref == person.id,
                )
                .group_by(ActivityLog.issue_id)
            ).all()
        )
    base = _public_base(request)
    payloads = [
        assigned_open_issue_out(
            issue,
            base_url=base,
            assigned_at=assigned_at_by_issue.get(issue.id),
        )
        for issue in rows
    ]
    attach_custom_field_users(db, rows, payloads)
    return {
        "user": user_brief(person),
        "total": total,
        "limit": limit,
        "offset": offset,
        "issues": payloads,
    }


@router.get("/users/{username}")
def user_profile(
    username: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    person = _user_by_username(db, username)
    chain = []
    manager = person.manager
    guard = 0
    while manager is not None and guard < 20:
        chain.append(user_brief(manager))
        manager = manager.manager
        guard += 1
    reports = db.scalars(
        select(User).where(User.manager_id == person.id, User.enabled.is_(True))
        .order_by(User.display_name)
    )
    visible = permissions.visible_issue_clause(db, user)
    scope = [visible] if visible is not None else []
    assigned = db.scalars(
        issue_query()
        .where(*scope)
        .join(Status, Status.id == Issue.status_id)
        .where(Issue.assignee_id == person.id, Status.category != "done")
        .order_by(Issue.updated_at.desc())
        .limit(50)
    )
    reported = db.scalars(
        issue_query().where(Issue.reporter_id == person.id, *scope)
        .order_by(Issue.created_at.desc()).limit(20)
    )
    testing = db.scalars(
        issue_query()
        .where(*scope)
        .join(Status, Status.id == Issue.status_id)
        .where(Issue.tester_id == person.id, Status.category != "done")
        .order_by(Issue.updated_at.desc())
        .limit(30)
    )
    return {
        "user": user_full(person),
        "manager_chain": chain,
        "direct_reports": [user_brief(r) for r in reports],
        "assigned": [issue_brief(i) for i in assigned],
        "reported": [issue_brief(i) for i in reported],
        "testing": [issue_brief(i) for i in testing],
    }


@router.get("/org/reporting")
def reporting_lines(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    users = db.scalars(
        select(User).options(selectinload(User.org_unit)).where(User.enabled.is_(True))
    )
    return {
        "nodes": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "title": u.title,
                "manager_id": u.manager_id,
                "org_path": u.org_unit.path if u.org_unit else None,
            }
            for u in users
        ]
    }
