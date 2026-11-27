"""MET Active Directory authentication (same LDAP flow as FileChecker / Interlock Maker)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import ssl
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import ROOT

LOGGER = logging.getLogger(__name__)

LDAP_ENV_CANDIDATES = (
    Path("/data/home/lucas/projects/Eletrical-Area/drawing-review-agent/configs/ldap.env"),
    ROOT / "configs" / "ldap.env",
)

PROFILE_ATTRS = ("title", "department", "manager", "telephoneNumber", "description")


@dataclass(frozen=True, slots=True)
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
    attr_mail: str
    attr_name: str
    search_filters: str
    upn_suffix: str
    netbios_domain: str

    @property
    def can_sync(self) -> bool:
        return bool(self.enabled and self.app_dn and self.app_password)


DEFAULT_COMPANY_OU = "MET"


def company_ou_from_search_base(search_base: str | None) -> str:
    for token in (search_base or "").split(","):
        token = token.strip()
        if token.lower().startswith("ou=") and len(token) > 3:
            return token[3:].strip() or DEFAULT_COMPANY_OU
    return DEFAULT_COMPANY_OU


def split_dn(dn: str | None) -> list[str]:
    """Split a DN into RDNs, honouring escaped commas."""
    parts: list[str] = []
    current = ""
    escape = False
    for ch in dn or "":
        if escape:
            current += ch
            escape = False
        elif ch == "\\":
            current += ch
            escape = True
        elif ch == ",":
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def parent_dn(dn: str) -> str:
    parts = split_dn(dn)
    return ",".join(parts[1:])


def rdn_value(dn: str) -> str:
    parts = split_dn(dn)
    if not parts or "=" not in parts[0]:
        return dn
    return parts[0].split("=", 1)[1].replace("\\", "").strip()


def ou_chain_from_dn(dn: str | None) -> list[str]:
    names: list[str] = []
    for token in split_dn(dn):
        if token.lower().startswith("ou=") and len(token) > 3:
            name = token[3:].replace("\\", "").strip()
            if name:
                names.append(name)
    return names


def org_from_dn(
    dn: str | None, *, company_ou: str = DEFAULT_COMPANY_OU
) -> tuple[str | None, str | None]:
    """Leaf OU is the 小组 (team); remaining OUs are org_unit."""
    ous = ou_chain_from_dn(dn)
    root = (company_ou or DEFAULT_COMPANY_OU).strip()
    while ous and ous[-1].casefold() == root.casefold():
        ous.pop()
    if not ous:
        return None, None
    team = ous[0]
    org_unit = " / ".join(ous[1:]) if len(ous) > 1 else None
    return team, org_unit


@dataclass(frozen=True, slots=True)
class LdapProfile:
    username: str
    display_name: str
    email: str | None
    dn: str | None = None
    title: str | None = None
    department: str | None = None
    manager_dn: str | None = None
    phone: str | None = None
    enabled: bool = True
    native_name: str | None = None


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        LOGGER.warning("Cannot read LDAP env file %s: %s", path, error)
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_get(merged: dict[str, str], key: str, default: str = "") -> str:
    return (os.environ.get(key) or merged.get(key) or default).strip()


@lru_cache(maxsize=1)
def load_ldap_settings() -> LdapSettings:
    merged: dict[str, str] = {}
    for path in LDAP_ENV_CANDIDATES:
        merged.update(_load_env_file(path))

    host = _env_get(merged, "LDAP_SERVER_HOST")
    app_dn = _env_get(merged, "LDAP_APP_DN")
    search_base = _env_get(merged, "LDAP_SEARCH_BASE")
    enabled = _parse_bool(_env_get(merged, "ENABLE_LDAP"), default=bool(host and search_base))
    if enabled and not (host and search_base):
        LOGGER.warning("ENABLE_LDAP=true but LDAP host/search base is incomplete")
        enabled = False
    try:
        port = int(_env_get(merged, "LDAP_SERVER_PORT", "389"))
    except ValueError:
        port = 389
    upn_suffix = _env_get(merged, "LDAP_UPN_SUFFIX")
    if not upn_suffix and "@" in app_dn:
        upn_suffix = app_dn.rsplit("@", 1)[-1]
    return LdapSettings(
        enabled=enabled,
        label=_env_get(merged, "LDAP_SERVER_LABEL", "MET AD") or "MET AD",
        host=host,
        port=port,
        use_tls=_parse_bool(_env_get(merged, "LDAP_USE_TLS")),
        validate_cert=_parse_bool(_env_get(merged, "LDAP_VALIDATE_CERT")),
        app_dn=app_dn,
        app_password=_env_get(merged, "LDAP_APP_PASSWORD"),
        search_base=search_base,
        attr_username=_env_get(merged, "LDAP_ATTRIBUTE_FOR_USERNAME", "sAMAccountName"),
        attr_mail=_env_get(merged, "LDAP_ATTRIBUTE_FOR_MAIL", "mail"),
        attr_name=_env_get(merged, "LDAP_ATTRIBUTE_FOR_NAME", "displayName"),
        search_filters=_env_get(merged, "LDAP_SEARCH_FILTERS"),
        upn_suffix=upn_suffix or "met.com",
        netbios_domain=_env_get(merged, "LDAP_NETBIOS_DOMAIN", "MET") or "MET",
    )


def ldap_enabled() -> bool:
    return load_ldap_settings().enabled


def directory_native_name(value: str | None) -> str | None:
    """Chinese personal name stored in the directory description."""
    text = str(value or "").strip()
    if not text or len(text) > 32:
        return None
    if not any("\u4e00" <= char <= "\u9fff" for char in text):
        return None
    return text


def attr_first(entry: Any, name: str) -> str | None:
    try:
        values = entry[name].values
    except Exception:  # noqa: BLE001 - ldap3 entries expose dynamic attributes
        return None
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None


def normalize_username(sam: str) -> str | None:
    local = re.sub(r"[^a-zA-Z0-9._-]", "_", sam)[:64]
    if local != sam:
        digest = hashlib.sha256(sam.encode()).hexdigest()[:12]
        local = f"{local[:48]}-{digest}"
    return local if len(local) >= 2 else None


def profile_from_entry(entry: Any, username: str, settings: LdapSettings) -> LdapProfile | None:
    sam = attr_first(entry, settings.attr_username) or username
    local_username = normalize_username(sam)
    if local_username is None:
        return None
    uac_raw = attr_first(entry, "userAccountControl")
    enabled = True
    if uac_raw and uac_raw.isdigit():
        enabled = not (int(uac_raw) & 0x2)
    return LdapProfile(
        username=local_username,
        display_name=attr_first(entry, settings.attr_name) or sam,
        email=attr_first(entry, settings.attr_mail),
        dn=getattr(entry, "entry_dn", None),
        title=attr_first(entry, "title"),
        department=attr_first(entry, "department"),
        manager_dn=attr_first(entry, "manager"),
        phone=attr_first(entry, "telephoneNumber"),
        enabled=enabled,
        native_name=directory_native_name(attr_first(entry, "description")),
    )


def user_search_filter(username: str, settings: LdapSettings) -> str:
    from ldap3.utils.conv import escape_filter_chars

    parts = [
        "(objectClass=user)",
        f"({settings.attr_username}={escape_filter_chars(username)})",
    ]
    extra = settings.search_filters.strip()
    if extra:
        parts.append(extra if extra.startswith("(") else f"({extra})")
    return f"(&{''.join(parts)})"


def make_server(settings: LdapSettings):
    from ldap3 import NONE, Server, Tls

    tls = None
    if settings.use_tls:
        tls = Tls(validate=ssl.CERT_REQUIRED if settings.validate_cert else ssl.CERT_NONE)
    return Server(
        settings.host,
        port=settings.port,
        use_ssl=settings.use_tls,
        get_info=NONE,
        tls=tls,
        connect_timeout=8,
    )


def _bind_identities(username: str, settings: LdapSettings) -> tuple[list[str], str]:
    raw = username.strip()
    if "@" in raw:
        return [raw], raw.split("@", 1)[0]
    identities = []
    if settings.upn_suffix:
        identities.append(f"{raw}@{settings.upn_suffix}")
    if settings.netbios_domain:
        identities.append(f"{settings.netbios_domain}\\{raw}")
    return list(dict.fromkeys(identities)), raw


def user_attrs(settings: LdapSettings) -> list[str]:
    return [
        settings.attr_username,
        settings.attr_mail,
        settings.attr_name,
        "userAccountControl",
        *PROFILE_ATTRS,
    ]


def authenticate_ldap(username: str, password: str) -> LdapProfile | None:
    """Verify credentials against MET AD and return a normalized profile."""
    username = (username or "").strip()
    if not username or not password:
        return None
    settings = load_ldap_settings()
    if not settings.enabled:
        return None
    try:
        from ldap3 import Connection
        from ldap3.core.exceptions import LDAPException
    except ImportError:
        LOGGER.error("ldap3 is not installed; cannot authenticate against MET AD")
        return None

    server = make_server(settings)
    attrs = user_attrs(settings)
    identities, sam = _bind_identities(username, settings)
    search_filter = user_search_filter(sam, settings)
    try:
        if settings.app_dn and settings.app_password:
            with Connection(
                server,
                user=settings.app_dn,
                password=settings.app_password,
                auto_bind=True,
                receive_timeout=10,
            ) as service:
                ok = service.search(
                    settings.search_base, search_filter, attributes=attrs, size_limit=1
                )
                if not ok or not service.entries:
                    return None
                entry = service.entries[0]
                user_dn = entry.entry_dn
            with Connection(
                server, user=user_dn, password=password, auto_bind=True, receive_timeout=10
            ) as connection:
                if not connection.bound:
                    return None
            return profile_from_entry(entry, sam, settings)

        last_error: Exception | None = None
        for identity in identities:
            try:
                with Connection(
                    server, user=identity, password=password, auto_bind=True, receive_timeout=10
                ) as connection:
                    if not connection.bound:
                        continue
                    ok = connection.search(
                        settings.search_base, search_filter, attributes=attrs, size_limit=1
                    )
                    if not ok or not connection.entries:
                        return None
                    return profile_from_entry(connection.entries[0], sam, settings)
            except LDAPException as error:
                last_error = error
        if last_error is not None:
            LOGGER.info("LDAP auth failed for %s: %s", sam, last_error)
        return None
    except LDAPException as error:
        LOGGER.info("LDAP auth failed for %s: %s", username, error)
        return None
    except Exception as error:  # noqa: BLE001 - authentication must fail closed
        LOGGER.warning("LDAP unexpected error for %s: %s", username, error)
        return None
