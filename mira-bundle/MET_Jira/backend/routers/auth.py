from __future__ import annotations

import json
import re

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings, login_allowed
from ..db import get_db, utcnow
from ..deps import current_user, signer
from ..models import User
from ..schemas import AvatarColorIn, LoginIn, PasswordChangeIn
from ..security import hash_password, verify_password
from ..serialize import user_full
from ..services import avatars, presence, ratelimit
from ..services.ldap_auth import authenticate_ldap, ldap_enabled, load_ldap_settings
from ..services.org_sync import upsert_login_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_NOT_ALLOWED = "预发环境仅允许指定账号登录"
REMEMBER_SECONDS = 7 * 24 * 3600


def auth_mode() -> str:
    mode = get_settings().auth_mode
    if mode in {"ldap", "local"}:
        return mode
    return "ldap" if ldap_enabled() else "local"


@router.get("/config")
def auth_config() -> dict:
    settings = load_ldap_settings()
    return {
        "mode": auth_mode(),
        "ldap_label": settings.label if settings.enabled else None,
        "local_fallback": get_settings().auth_mode != "ldap",
        "deploy_env": get_settings().deploy_env,
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)) -> dict:
    username = body.username.strip()
    ip = request.client.host if request.client else "unknown"
    if not login_allowed(username):
        raise HTTPException(403, LOGIN_NOT_ALLOWED)
    ratelimit.check_login(ip, username)
    user: User | None = None
    local = db.scalar(select(User).where(User.username == username, User.auth_source == "local"))
    if local is not None and get_settings().auth_mode != "ldap":
        if verify_password(body.password, local.password_hash) and local.enabled:
            user = local
            user.last_login_at = utcnow()
    elif auth_mode() == "ldap" or ldap_enabled():
        profile = authenticate_ldap(username, body.password)
        if profile is not None and not profile.enabled:
            ratelimit.login_failed(ip, username)
            raise HTTPException(403, "该目录账号已停用")
        if profile is not None:
            try:
                user = upsert_login_user(db, profile)
            except Exception as error:
                from ..services.org_sync import OrgSyncError
                if isinstance(error, OrgSyncError):
                    ratelimit.login_failed(ip, username)
                    raise HTTPException(403, str(error)) from error
                raise
    if user is None:
        ratelimit.login_failed(ip, username)
        raise HTTPException(401, "用户名或密码错误")
    if not user.enabled:
        ratelimit.login_failed(ip, username)
        raise HTTPException(403, "账号已停用")
    ratelimit.login_succeeded(ip, username)
    db.commit()
    settings = get_settings()
    ttl = REMEMBER_SECONDS if body.remember else settings.session_hours * 3600
    response.set_cookie(
        settings.cookie_name,
        signer(ttl).issue(user.id),
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    presence.record_event(user, "login", kind=user.auth_source)
    presence.touch(user, "login", force=True)
    return {"user": user_full(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    user_id = signer().verify(request.cookies.get(settings.cookie_name))
    user = db.get(User, user_id) if user_id is not None else None
    if user is not None:
        presence.leave(user)
        presence.record_event(user, "logout")
    response.delete_cookie(settings.cookie_name)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from ..services import schedule_cascade

    return {
        "user": user_full(user),
        "preferences": user.preferences or {},
        "portfolio_access": schedule_cascade.has_portfolio_access(db, user),
        "default_portfolio_view": schedule_cascade.default_portfolio_view(db, user),
    }


@router.post("/me/password")
def change_password(
    body: PasswordChangeIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    if user.auth_source != "local" or not user.password_hash:
        raise HTTPException(400, "AD 账号的密码请在域控中修改")
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "当前密码不正确")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    data = await file.read(avatars.MAX_BYTES + 1)
    old_key = user.avatar_key
    try:
        key = await to_thread.run_sync(avatars.stage, user.id, data)
    except avatars.AvatarError as exc:
        raise HTTPException(400, str(exc)) from exc
    user.avatar_key = key
    try:
        db.commit()
    except Exception:
        db.rollback()
        await to_thread.run_sync(
            avatars.avatar_path(user.id, key).unlink, True
        )
        raise
    if old_key:
        await to_thread.run_sync(avatars.avatar_path(user.id, old_key).unlink, True)
    return {"user": user_full(user)}


@router.delete("/me/avatar")
def delete_avatar(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    avatars.clear(user)
    db.commit()
    return {"user": user_full(user)}


@router.put("/me/avatar-color")
def update_avatar_color(
    body: AvatarColorIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    try:
        avatars.set_color(user, body.color)
    except avatars.AvatarError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"user": user_full(user)}


_PREF_KEYS = frozenset({"shortcuts", "notifications", "pinned_projects"})
_PINNED_PROJECT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}$")


def _validate_pinned_projects(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 40:
        raise ValueError("置顶项目格式不正确")
    keys: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("置顶项目格式不正确")
        key = item.strip().upper()
        if not _PINNED_PROJECT_RE.fullmatch(key):
            raise ValueError("置顶项目 Key 无效")
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


@router.put("/me/preferences")
def update_preferences(
    body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    from ..services import notify_prefs

    unknown = set(body) - _PREF_KEYS
    if unknown:
        raise HTTPException(400, f"不支持的偏好项：{', '.join(sorted(unknown))}")
    shortcuts = body.get("shortcuts")
    if shortcuts is not None and not (
        isinstance(shortcuts, dict) and len(shortcuts) <= 64
        and all(isinstance(k, str) and len(k) <= 32 and isinstance(v, str) and len(v) <= 32
                for k, v in shortcuts.items())
    ):
        raise HTTPException(400, "快捷键设置格式不正确")
    patch = {k: body[k] for k in _PREF_KEYS if k in body}
    if "notifications" in patch:
        try:
            patch["notifications"] = notify_prefs.validate(patch["notifications"])
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    if "pinned_projects" in patch:
        try:
            patch["pinned_projects"] = _validate_pinned_projects(patch["pinned_projects"])
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    merged = {**(user.preferences or {}), **patch}
    if len(json.dumps(merged, ensure_ascii=False)) > 16_384:
        raise HTTPException(400, "偏好设置过大")
    user.preferences = merged
    db.commit()
    return {"preferences": user.preferences}
