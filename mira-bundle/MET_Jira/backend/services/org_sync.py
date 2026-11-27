"""Synchronise the MET organisation (OU tree, people, reporting lines) from Active Directory."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import OrgSyncRun, OrgUnit, User
from .ldap_auth import (
    LdapProfile,
    LdapSettings,
    directory_native_name,
    load_ldap_settings,
    make_server,
    normalize_username,
    parent_dn,
    profile_from_entry,
    rdn_value,
    split_dn,
    user_attrs,
)

LOGGER = logging.getLogger(__name__)
DIFF_LIMIT = 300
_sync_lock = threading.Lock()


class OrgSyncError(RuntimeError):
    pass


@dataclass
class Directory:
    ou_dns: list[str] = field(default_factory=list)
    profiles: list[LdapProfile] = field(default_factory=list)


class _Entry:
    """Adapter so paged-search dicts look like ldap3 Entry objects."""

    def __init__(self, dn: str, attrs: dict[str, Any]):
        self.entry_dn = dn
        self._attrs = {key.lower(): value for key, value in attrs.items()}

    def __getitem__(self, name: str) -> SimpleNamespace:
        value = self._attrs.get(name.lower())
        if value is None or value == "" or value == []:
            values: list[Any] = []
        elif isinstance(value, list):
            values = value
        else:
            values = [value]
        return SimpleNamespace(values=values)


def fetch_directory(settings: LdapSettings | None = None) -> Directory:
    settings = settings or load_ldap_settings()
    if not settings.enabled:
        raise OrgSyncError("LDAP 未启用，请检查 configs/ldap.env")
    if not settings.can_sync:
        raise OrgSyncError(
            "缺少服务账号密码：请在 configs/ldap.env 中配置 LDAP_APP_DN 与 LDAP_APP_PASSWORD"
        )
    from ldap3 import SUBTREE, Connection

    directory = Directory()
    with Connection(
        make_server(settings),
        user=settings.app_dn,
        password=settings.app_password,
        auto_bind=True,
        receive_timeout=30,
    ) as conn:
        for item in conn.extend.standard.paged_search(
            settings.search_base,
            "(objectClass=organizationalUnit)",
            search_scope=SUBTREE,
            attributes=["ou"],
            paged_size=500,
            generator=True,
        ):
            if item.get("type") == "searchResEntry":
                directory.ou_dns.append(item["dn"])
        user_filter = "(&(objectClass=user)(objectCategory=person))"
        extra = settings.search_filters.strip()
        if extra:
            user_filter = f"(&{user_filter}{extra if extra.startswith('(') else f'({extra})'})"
        for item in conn.extend.standard.paged_search(
            settings.search_base,
            user_filter,
            search_scope=SUBTREE,
            attributes=user_attrs(settings),
            paged_size=500,
            generator=True,
        ):
            if item.get("type") != "searchResEntry":
                continue
            entry = _Entry(item["dn"], dict(item.get("attributes") or {}))
            profile = profile_from_entry(entry, "", settings)
            if profile is not None:
                directory.profiles.append(profile)
    return directory


def probe_directory(settings: LdapSettings | None = None) -> dict[str, Any]:
    """Read-only bind and one-entry searches; never returns credentials or directory records."""
    settings = settings or load_ldap_settings()
    result: dict[str, Any] = {
        "enabled": settings.enabled,
        "configured": settings.can_sync,
        "bind_ok": False,
        "search_ok": False,
    }
    if not settings.can_sync:
        result["error"] = "缺少服务账号密码"
        return result
    if not settings.enabled:
        result["error"] = "LDAP 未启用"
        return result
    from ldap3 import BASE, SUBTREE, Connection

    started = time.perf_counter()
    try:
        with Connection(
            make_server(settings), user=settings.app_dn, password=settings.app_password,
            auto_bind=True, receive_timeout=10,
        ) as conn:
            result["bind_ok"] = True
            result["base_ok"] = conn.search(
                settings.search_base, "(objectClass=*)", search_scope=BASE,
                attributes=["distinguishedName"], size_limit=1,
            )
            result["search_ok"] = conn.search(
                settings.search_base, "(objectClass=organizationalUnit)",
                search_scope=SUBTREE, attributes=["ou"], size_limit=1,
            )
    except Exception as error:  # noqa: BLE001 - expose the type, never LDAP details
        LOGGER.warning("LDAP probe failed: %s", error)
        result["error"] = f"{type(error).__name__}: 连接或查询失败"
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def _key(dn: str | None) -> str:
    return ",".join(part.casefold() for part in split_dn(dn))


def _is_admin(username: str) -> bool:
    return username.lower() in get_settings().ad_admins


def _path_of(ou: OrgUnit, by_id: dict[int, OrgUnit]) -> tuple[str, int]:
    names: list[str] = []
    node: OrgUnit | None = ou
    guard = 0
    while node is not None and guard < 64:
        names.append(node.label)
        node = by_id.get(node.parent_id) if node.parent_id else None
        guard += 1
    names.reverse()
    return " / ".join(names), len(names) - 1


def refresh_paths(db: Session) -> None:
    units = list(db.scalars(select(OrgUnit)))
    by_id = {ou.id: ou for ou in units}
    for ou in units:
        ou.path, ou.level = _path_of(ou, by_id)


def ensure_ou_chain(db: Session, dn: str | None) -> OrgUnit | None:
    """Create the OU ancestors of ``dn`` (down to the search base) and return the leaf."""
    if not dn:
        return None
    settings = load_ldap_settings()
    base_key = _key(settings.search_base)
    chain: list[str] = []
    current = parent_dn(dn)
    while current:
        first = split_dn(current)[0] if split_dn(current) else ""
        if not first.lower().startswith("ou="):
            break
        chain.append(current)
        if _key(current) == base_key:
            break
        current = parent_dn(current)
    if not chain:
        return None
    chain.reverse()
    parent: OrgUnit | None = None
    leaf: OrgUnit | None = None
    created = False
    for ou_dn in chain:
        ou = db.scalar(select(OrgUnit).where(OrgUnit.dn == ou_dn))
        if ou is None:
            ou = OrgUnit(dn=ou_dn, name=rdn_value(ou_dn), parent_id=parent.id if parent else None)
            db.add(ou)
            db.flush()
            created = True
        parent = leaf = ou
    if created:
        refresh_paths(db)
    return leaf


def upsert_login_user(db: Session, profile: LdapProfile) -> User:
    if not profile.enabled:
        raise OrgSyncError("该目录账号已停用")
    user = db.scalar(select(User).where(User.username == profile.username))
    # Never convert a local account into LDAP via login — usernames must stay distinct.
    if user is not None and user.auth_source == "local":
        raise OrgSyncError("同名本地账号已存在，无法用目录登录")
    org = ensure_ou_chain(db, profile.dn)
    if user is None:
        user = User(username=profile.username, display_name=profile.display_name)
        db.add(user)
    user.display_name = profile.display_name
    user.native_name = profile.native_name
    user.email = profile.email
    user.dn = profile.dn
    user.title = profile.title or user.title
    user.department = profile.department or user.department
    user.phone = profile.phone or user.phone
    user.manager_dn = profile.manager_dn or user.manager_dn
    user.auth_source = "ldap"
    user.enabled = True
    if org is not None:
        user.org_unit_id = org.id
    # Directory list is the source of truth for LDAP admin promotion/demotion.
    user.is_admin = _is_admin(profile.username)
    if user.manager_dn:
        manager = db.scalar(select(User).where(User.dn == user.manager_dn))
        if manager is not None and manager.id != user.id:
            user.manager_id = manager.id
    user.last_login_at = utcnow()
    db.flush()
    return user


def _cap(items: list[Any]) -> list[Any]:
    return items[:DIFF_LIMIT]


def apply_directory(db: Session, directory: Directory) -> tuple[dict[str, int], dict[str, Any]]:
    now = utcnow()
    diff: dict[str, list[Any]] = {
        "ou_added": [],
        "ou_removed": [],
        "users_added": [],
        "users_moved": [],
        "users_disabled": [],
        "users_enabled": [],
        "users_skipped_local": [],
        "admins_promoted": [],
        "admins_demoted": [],
    }

    existing_ous = {_key(ou.dn): ou for ou in db.scalars(select(OrgUnit))}
    seen_ou: set[str] = set()
    for dn in sorted(directory.ou_dns, key=lambda d: len(split_dn(d))):
        key = _key(dn)
        if key in seen_ou:
            continue
        ou = existing_ous.get(key)
        if ou is None:
            ou = OrgUnit(dn=dn, name=rdn_value(dn))
            db.add(ou)
            existing_ous[key] = ou
            diff["ou_added"].append(dn)
        ou.name = rdn_value(dn)
        ou.removed = False
        ou.synced_at = now
        seen_ou.add(key)
    db.flush()
    for key, ou in existing_ous.items():
        if key in seen_ou:
            parent = existing_ous.get(_key(parent_dn(ou.dn)))
            ou.parent_id = parent.id if parent is not None and parent is not ou else None
        elif not ou.removed:
            ou.removed = True
            diff["ou_removed"].append(ou.path or ou.dn)
    db.flush()
    refresh_paths(db)
    path_by_id = {ou.id: ou.path for ou in existing_ous.values()}

    users = {user.username: user for user in db.scalars(select(User))}
    seen_users: set[str] = set()
    for profile in directory.profiles:
        seen_users.add(profile.username)
        org = existing_ous.get(_key(parent_dn(profile.dn or "")))
        org_id = org.id if org is not None else None
        user = users.get(profile.username)
        # Never overwrite local accounts — keep usernames and auth_source distinct.
        if user is not None and user.auth_source == "local":
            diff.setdefault("users_skipped_local", []).append(
                {"username": user.username, "name": user.display_name}
            )
            continue
        if user is None:
            user = User(username=profile.username, display_name=profile.display_name)
            db.add(user)
            users[profile.username] = user
            diff["users_added"].append(
                {"username": profile.username, "name": profile.display_name,
                 "org": path_by_id.get(org_id)}
            )
        else:
            if user.org_unit_id != org_id and user.synced_at is not None:
                diff["users_moved"].append(
                    {"username": user.username, "name": profile.display_name,
                     "from": path_by_id.get(user.org_unit_id), "to": path_by_id.get(org_id)}
                )
            if user.enabled and not profile.enabled:
                diff["users_disabled"].append(
                    {"username": user.username, "name": profile.display_name}
                )
            elif not user.enabled and profile.enabled:
                diff["users_enabled"].append(
                    {"username": user.username, "name": profile.display_name}
                )
            was_admin = user.is_admin
            will_admin = _is_admin(profile.username)
            if was_admin and not will_admin:
                diff.setdefault("admins_demoted", []).append(
                    {"username": user.username, "name": profile.display_name}
                )
            elif not was_admin and will_admin:
                diff.setdefault("admins_promoted", []).append(
                    {"username": user.username, "name": profile.display_name}
                )
        user.display_name = profile.display_name
        user.native_name = profile.native_name
        user.email = profile.email
        user.dn = profile.dn
        user.title = profile.title
        user.department = profile.department
        user.phone = profile.phone
        user.manager_dn = profile.manager_dn
        user.org_unit_id = org_id
        user.enabled = profile.enabled
        user.auth_source = "ldap"
        user.synced_at = now
        user.is_admin = _is_admin(profile.username)
    db.flush()

    by_dn = {_key(user.dn): user for user in users.values() if user.dn}
    for user in users.values():
        if user.auth_source != "ldap":
            continue
        if user.username in seen_users:
            manager = by_dn.get(_key(user.manager_dn)) if user.manager_dn else None
            user.manager_id = manager.id if manager is not None and manager is not user else None
        elif user.enabled and directory.profiles:
            user.enabled = False
            diff["users_disabled"].append({"username": user.username, "name": user.display_name})

    stats = {
        "ous": len(seen_ou),
        "users": len(seen_users),
        "enabled_users": sum(1 for p in directory.profiles if p.enabled),
        **{f"{name}_count": len(items) for name, items in diff.items()},
    }
    return stats, {name: _cap(items) for name, items in diff.items()}


def run_org_sync(
    db: Session,
    *,
    trigger: str = "manual",
    user_id: int | None = None,
    directory: Directory | None = None,
) -> OrgSyncRun:
    if not _sync_lock.acquire(blocking=False):
        raise OrgSyncError("已有同步任务正在运行")
    run = OrgSyncRun(trigger=trigger, status="running", triggered_by_id=user_id)
    db.add(run)
    db.commit()
    try:
        directory = directory or fetch_directory()
        stats, diff = apply_directory(db, directory)
        run.stats, run.diff, run.status = stats, diff, "success"
    except Exception as error:  # noqa: BLE001 - persist failure for the admin page
        db.rollback()
        LOGGER.warning("Org sync failed: %s", error)
        run = db.get(OrgSyncRun, run.id) or run
        run.status = "failed"
        run.error = str(error)
    finally:
        run.finished_at = utcnow()
        db.commit()
        _sync_lock.release()
    return run


def fetch_native_names(settings: LdapSettings | None = None) -> dict[str, str | None]:
    """Map each directory username to the Chinese name stored in description."""
    settings = settings or load_ldap_settings()
    if not settings.can_sync:
        raise OrgSyncError("缺少服务账号密码")
    from ldap3 import SUBTREE, Connection

    names: dict[str, str | None] = {}
    with Connection(
        make_server(settings), user=settings.app_dn, password=settings.app_password,
        auto_bind=True, receive_timeout=30,
    ) as conn:
        pages = conn.extend.standard.paged_search(
            settings.search_base,
            "(&(objectClass=user)(objectCategory=person))",
            search_scope=SUBTREE,
            attributes=["sAMAccountName", "description"],
            paged_size=500,
            generator=True,
        )
        for item in pages:
            if item.get("type") != "searchResEntry":
                continue
            attrs = item.get("attributes") or {}
            sam = attrs.get("sAMAccountName") or ""
            desc = attrs.get("description") or ""
            if isinstance(sam, list):
                sam = sam[0] if sam else ""
            if isinstance(desc, list):
                desc = desc[0] if desc else ""
            username = normalize_username(str(sam).strip())
            if username:
                names[username] = directory_native_name(str(desc) if desc else None)
    return names


def apply_native_names(db: Session, names: dict[str, str | None]) -> int:
    """Copy directory Chinese names onto LDAP users. Local accounts stay unchanged."""
    updated = 0
    users = db.scalars(select(User).where(User.auth_source == "ldap")).all()
    for user in users:
        if user.username not in names:
            continue
        new = names[user.username]
        if user.native_name != new:
            user.native_name = new
            updated += 1
    return updated
