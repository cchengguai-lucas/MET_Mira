"""Per-user notification preferences (in-app + email + WeCom), stored on ``User.preferences``."""

from __future__ import annotations

from typing import Any

from ..models import Issue, User

DEFAULT_NOTIFICATIONS: dict[str, Any] = {
    "in_app": True,
    "email": True,
    "email_as_assignee": True,
    "email_as_reporter": True,
    "email_as_watcher": True,
    "email_own_changes": False,
    "email_format": "html",
    "batch_minutes": 0,
    # WeCom push (SLA etc.); matched by the account email on the WeCom bridge.
    "wecom": True,
}

_BOOL_KEYS = (
    "in_app",
    "email",
    "email_as_assignee",
    "email_as_reporter",
    "email_as_watcher",
    "email_own_changes",
    "wecom",
)
_BATCH_ALLOWED = frozenset({0, 3, 10, 30, 60})


def defaults() -> dict[str, Any]:
    return dict(DEFAULT_NOTIFICATIONS)


def of(user: User | None) -> dict[str, Any]:
    raw = ((user.preferences or {}).get("notifications") if user else None) or {}
    out = defaults()
    if not isinstance(raw, dict):
        return out
    for key in _BOOL_KEYS:
        if key in raw:
            out[key] = bool(raw[key])
    if raw.get("email_format") in ("html", "text"):
        out["email_format"] = raw["email_format"]
    try:
        batch = int(raw.get("batch_minutes", out["batch_minutes"]))
    except (TypeError, ValueError):
        batch = 0
    out["batch_minutes"] = batch if batch in _BATCH_ALLOWED else 0
    return out


def validate(raw: Any) -> dict[str, Any]:
    """Normalize a notifications payload or raise ``ValueError``."""
    if not isinstance(raw, dict):
        raise ValueError("通知设置格式不正确")
    out = of(None)
    for key in _BOOL_KEYS:
        if key in raw:
            out[key] = bool(raw[key])
    if "email_format" in raw:
        if raw["email_format"] not in ("html", "text"):
            raise ValueError("邮件格式无效")
        out["email_format"] = raw["email_format"]
    if "batch_minutes" in raw:
        try:
            batch = int(raw["batch_minutes"])
        except (TypeError, ValueError) as error:
            raise ValueError("合并发送间隔无效") from error
        if batch not in _BATCH_ALLOWED:
            raise ValueError("合并发送间隔无效")
        out["batch_minutes"] = batch
    return out


def wants_in_app(user: User) -> bool:
    return bool(of(user)["in_app"])


def wants_email(user: User, issue: Issue | None, actor: User | None) -> bool:
    prefs = of(user)
    if not prefs["email"]:
        return False
    if actor is not None and actor.id == user.id:
        return bool(prefs["email_own_changes"])
    if issue is None:
        return True
    if issue.assignee_id == user.id:
        return bool(prefs["email_as_assignee"])
    if issue.reporter_id == user.id:
        return bool(prefs["email_as_reporter"])
    return bool(prefs["email_as_watcher"])


def wants_wecom(user: User) -> bool:
    """Whether SLA / system WeCom pushes should target this user."""
    return bool(of(user)["wecom"])
