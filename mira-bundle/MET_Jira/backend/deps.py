from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings, login_allowed
from .db import get_db, session_factory
from .models import Issue, Project, User
from .security import SessionSigner
from .serialize import issue_query
from .services import permissions


def signer(ttl: int | None = None) -> SessionSigner:
    settings = get_settings()
    return SessionSigner(settings.secret, settings.session_hours * 3600 if ttl is None else ttl)


def _request_api_token(request: Request) -> str | None:
    """Bearer Authorization or X-Api-Key header value, if present."""
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    key = (request.headers.get("x-api-key") or "").strip()
    return key or None


def external_api_token_ok(request: Request) -> bool:
    """True when a configured METJIRA_EXTERNAL_API_TOKEN matches the request."""
    expected = get_settings().external_api_token
    supplied = _request_api_token(request)
    if not expected or not supplied or len(supplied) != len(expected):
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = signer().verify(request.cookies.get(get_settings().cookie_name))
    if user_id is None:
        raise HTTPException(401, "未登录或会话已过期")
    user = db.get(User, user_id)
    if user is None or not user.enabled:
        raise HTTPException(401, "账号不可用")
    if not login_allowed(user.username):
        raise HTTPException(401, "预发环境仅允许指定账号登录")
    return user


def current_user_or_external(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Session cookie user, or ``None`` when a valid external API token is presented.

    ``None`` means the caller is a machine client with full read visibility for
    the endpoints that opt into this dependency (no private-project filter).
    """
    if external_api_token_ok(request):
        return None
    return current_user(request, db)


def current_user_id(request: Request) -> int:
    """Authenticate in a self-contained short session for streaming endpoints."""
    user_id = signer().verify(request.cookies.get(get_settings().cookie_name))
    if user_id is None:
        raise HTTPException(401, "未登录或会话已过期")
    with session_factory()() as db:
        user = db.get(User, user_id)
        if user is None or not user.enabled:
            raise HTTPException(401, "账号不可用")
        if not login_allowed(user.username):
            raise HTTPException(401, "预发环境仅允许指定账号登录")
        return user.id


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


def require_project_creator(user: User = Depends(current_user)) -> User:
    if not permissions.can_create_projects(user):
        raise HTTPException(403, "需要项目管理员权限才能创建项目")
    return user


ACCESS_DENIED = {
    "view": "仅项目成员可以查看此项目",
    "member": "仅项目成员可以执行此操作",
    "admin": "需要项目管理员权限",
}


def check_project(db: Session, project: Project, user: User, need: str = "view") -> set[str]:
    """The one project access check: ``view`` (visibility), ``member`` or ``admin``."""
    roles = permissions.project_roles(db, project.id, user)
    if need == "admin":
        allowed = permissions.is_project_admin(user, roles)
    elif need == "member":
        allowed = user.is_admin or bool(roles)
    else:
        allowed = permissions.can_view_project(user, project, roles)
    if not allowed:
        raise HTTPException(403, ACCESS_DENIED[need])
    return roles


def get_project(db: Session, key_or_id: str | int, user: User, need: str = "view") -> Project:
    if isinstance(key_or_id, int) or str(key_or_id).isdigit():
        project = db.get(Project, int(key_or_id))
    else:
        project = db.scalar(select(Project).where(Project.key == str(key_or_id).upper()))
    if project is None:
        raise HTTPException(404, "项目不存在")
    check_project(db, project, user, need)
    return project


def check_issue(db: Session, issue: Issue, user: User) -> set[str]:
    roles = permissions.project_roles(db, issue.project_id, user)
    if not permissions.can_view_issue(user, issue, issue.project, roles):
        raise HTTPException(403, ACCESS_DENIED["view"])
    return roles


def require_writable_project(db: Session, project: Project, user: User) -> set[str]:
    """Member access plus archived guard for create / mutate routes."""
    roles = check_project(db, project, user, need="member")
    if project.archived:
        raise HTTPException(403, "归档项目不可修改")
    return roles


def require_writable_issue(db: Session, issue: Issue, user: User) -> set[str]:
    roles = check_issue(db, issue, user)
    project = issue.project
    if not permissions.can_mutate_issue(user, issue, project, roles):
        if project.archived:
            raise HTTPException(403, "归档项目不可修改")
        raise HTTPException(403, ACCESS_DENIED["member"])
    return roles


def get_issue(db: Session, key: str, user: User) -> Issue:
    stmt = issue_query()
    stmt = stmt.where(Issue.id == int(key)) if key.isdigit() else stmt.where(
        Issue.key == key.upper()
    )
    issue = db.scalars(stmt).first()
    if issue is None:
        raise HTTPException(404, "问题不存在")
    check_issue(db, issue, user)
    return issue


def issue_by_id(db: Session, issue_id: int | None, user: User) -> Issue | None:
    issue = db.get(Issue, issue_id) if issue_id is not None else None
    if issue is not None:
        check_issue(db, issue, user)
    return issue
