"""Ops login: MET Jira local admins or authorized MET AD accounts, plus a signed cookie."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from . import ldap_auth
from .config import ENVS, OPS_USERS, SESSION_MAX_AGE
from .pg import connect


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_password(password: str, stored: str | None) -> bool:
    """Mirror of MET Jira's ``pbkdf2$<salt>$<digest>`` (sha256, 240k rounds)."""
    if not stored or not stored.startswith("pbkdf2$"):
        return False
    try:
        _, salt, digest = stored.split("$", 2)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), _decode(salt), 240_000)
        return hmac.compare_digest(candidate, _decode(digest))
    except (ValueError, TypeError):
        return False


class LoginError(Exception):
    pass


def _lookup(username: str) -> dict[str, Any] | None:
    with connect(ENVS["production"]) as conn:
        return conn.execute(
            """
            select id, username, display_name, password_hash, auth_source, is_admin, enabled
            from users where lower(username) = lower(%s)
            """,
            (username,),
        ).fetchone()


def ad_allowed(username: str, row: dict[str, Any] | None, allowlist: frozenset[str]) -> bool:
    """AD accounts get in when allowlisted, or when they are enabled MET Jira system admins."""
    if username.lower() in allowlist:
        return True
    return bool(row and row["auth_source"] == "ldap" and row["is_admin"] and row["enabled"])


def authenticate(username: str, password: str) -> dict[str, Any]:
    """Local MET Jira admins by password hash; otherwise MET AD for authorized accounts."""
    username = username.strip()
    sam = username.split("@", 1)[0].split("\\")[-1]
    try:
        row = _lookup(sam)
    except Exception:  # noqa: BLE001 - AD login must still work if the app DB is down
        row = None

    if row is not None and row["auth_source"] == "local":
        if not verify_password(password, row["password_hash"]):
            raise LoginError("Incorrect username or password.")
        if not row["is_admin"] or not row["enabled"]:
            raise LoginError(
                "This local account is not a MET Jira admin and cannot sign in to Ops."
            )
        return {"id": row["id"], "username": row["username"],
                "display_name": row["display_name"], "via": "local"}

    if not ldap_auth.load_settings().enabled:
        raise LoginError("MET AD is not configured. Only MET Jira local admins can sign in.")
    if not ad_allowed(sam, row, OPS_USERS):
        raise LoginError(
            "This AD account is not authorized for Ops "
            "(it must be in METJIRA_OPS_USERS or be a MET Jira admin)."
        )
    profile = ldap_auth.authenticate(sam, password)
    if profile is None:
        raise LoginError("Incorrect AD username or password.")
    return {
        "id": row["id"] if row else None,
        "username": profile.username,
        "display_name": profile.display_name,
        "via": "ldap",
    }


def session_secret() -> str:
    secret = os.environ.get("METJIRA_OPS_SECRET", "").strip()
    if secret:
        return secret
    seed = os.environ.get("USER", "") + os.environ.get("HOSTNAME", "")
    return hashlib.sha256(f"met-jira-ops|{seed}".encode()).hexdigest()


def issue_session(user: dict[str, Any], secret: str, ttl: int = SESSION_MAX_AGE) -> str:
    payload = {**user, "exp": int(time.time()) + ttl}
    encoded = _encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


def parse_session(token: str | None, secret: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    try:
        payload, supplied = token.split(".", 1)
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(supplied)):
            return None
        data = json.loads(_decode(payload))
        if not isinstance(data, dict) or int(data["exp"]) < int(time.time()):
            return None
        return data
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
