"""HMAC-signed, expiring session cookies and local password hashing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True, slots=True)
class SessionSigner:
    secret: str
    ttl: int

    def issue(self, user_id: int) -> str:
        payload: dict[str, Any] = {"uid": user_id, "exp": int(time.time()) + self.ttl}
        encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).digest()
        return f"{encoded}.{_encode(signature)}"

    def verify(self, token: str | None) -> int | None:
        if not token:
            return None
        try:
            payload, supplied = token.split(".", 1)
            expected = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _decode(supplied)):
                return None
            data: dict[str, Any] = json.loads(_decode(payload))
            if int(data["exp"]) < int(time.time()):
                return None
            return int(data["uid"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("pbkdf2$"):
        return False
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), _decode(salt), 240_000)
    return hmac.compare_digest(candidate, _decode(digest))
