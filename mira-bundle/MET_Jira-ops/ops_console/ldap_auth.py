"""MET AD authentication for Ops login (service-account search, then bind as the user)."""

from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import LDAP_ENV_FILES

LOGGER = logging.getLogger("met_jira_ops.ldap")


@dataclass(frozen=True)
class LdapSettings:
    enabled: bool
    label: str
    host: str
    port: int
    use_tls: bool
    validate_cert: bool
    app_dn: str
    app_password: str
    search_base: str
    attr_username: str
    attr_name: str
    attr_mail: str
    upn_suffix: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class LdapUser:
    username: str
    display_name: str
    email: str | None
    department: str | None


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _bool(raw: str, default: bool = False) -> bool:
    return default if not raw.strip() else raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def load_settings() -> LdapSettings:
    merged: dict[str, str] = {}
    sources = []
    for path in LDAP_ENV_FILES:
        values = _read_env_file(path)
        if values:
            merged.update(values)
            sources.append(str(path))

    def get(key: str, default: str = "") -> str:
        return (os.environ.get(key) or merged.get(key) or default).strip()

    host, base = get("LDAP_SERVER_HOST"), get("LDAP_SEARCH_BASE")
    app_dn = get("LDAP_APP_DN")
    try:
        port = int(get("LDAP_SERVER_PORT", "389"))
    except ValueError:
        port = 389
    return LdapSettings(
        enabled=_bool(get("ENABLE_LDAP"), default=bool(host and base)) and bool(host and base),
        label=get("LDAP_SERVER_LABEL", "MET AD"),
        host=host,
        port=port,
        use_tls=_bool(get("LDAP_USE_TLS")),
        validate_cert=_bool(get("LDAP_VALIDATE_CERT")),
        app_dn=app_dn,
        app_password=get("LDAP_APP_PASSWORD"),
        search_base=base,
        attr_username=get("LDAP_ATTRIBUTE_FOR_USERNAME", "sAMAccountName"),
        attr_name=get("LDAP_ATTRIBUTE_FOR_NAME", "displayName"),
        attr_mail=get("LDAP_ATTRIBUTE_FOR_MAIL", "mail"),
        upn_suffix=get("LDAP_UPN_SUFFIX") or (app_dn.rsplit("@", 1)[-1] if "@" in app_dn else ""),
        sources=tuple(sources),
    )


def status() -> dict:
    s = load_settings()
    return {
        "enabled": s.enabled,
        "label": s.label,
        "server": f"{s.host}:{s.port}" if s.host else None,
        "search_base": s.search_base,
        "service_account": s.app_dn or None,
        "service_password": bool(s.app_password),
        "tls": s.use_tls,
        "sources": list(s.sources),
    }


def _first(entry, name: str) -> str | None:
    try:
        values = entry[name].values
    except Exception:  # noqa: BLE001
        return None
    return str(values[0]).strip() or None if values else None


def authenticate(username: str, password: str) -> LdapUser | None:
    """Return the AD profile when the credentials are valid and the account is enabled."""
    settings = load_settings()
    username = username.strip()
    if not (settings.enabled and settings.app_dn and settings.app_password):
        return None
    if not (username and password):
        return None
    from ldap3 import NONE, Connection, Server, Tls
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.conv import escape_filter_chars

    sam = username.split("@", 1)[0].split("\\")[-1]
    tls = None
    if settings.use_tls:
        tls = Tls(validate=ssl.CERT_REQUIRED if settings.validate_cert else ssl.CERT_NONE)
    server = Server(settings.host, port=settings.port, use_ssl=settings.use_tls, get_info=NONE,
                    tls=tls, connect_timeout=8)
    attrs = [settings.attr_username, settings.attr_name, settings.attr_mail, "department",
             "userAccountControl"]
    query = f"(&(objectClass=user)({settings.attr_username}={escape_filter_chars(sam)}))"
    try:
        with Connection(server, user=settings.app_dn, password=settings.app_password,
                        auto_bind=True, receive_timeout=10) as service:
            if not service.search(settings.search_base, query, attributes=attrs, size_limit=1):
                return None
            if not service.entries:
                return None
            entry = service.entries[0]
        uac = _first(entry, "userAccountControl")
        if uac and uac.isdigit() and int(uac) & 0x2:
            return None
        with Connection(server, user=entry.entry_dn, password=password, auto_bind=True,
                        receive_timeout=10) as conn:
            if not conn.bound:
                return None
    except LDAPException as exc:
        LOGGER.info("Ops LDAP login failed for %s: %s", sam, exc)
        return None
    return LdapUser(
        username=(_first(entry, settings.attr_username) or sam).lower(),
        display_name=_first(entry, settings.attr_name) or sam,
        email=_first(entry, settings.attr_mail),
        department=_first(entry, "department"),
    )
