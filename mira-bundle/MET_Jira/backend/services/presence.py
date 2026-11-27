"""Online seats and usage events for the MET Jira Ops console.

One JSON file per user under ``PRESENCE_ROOT/{production|staging}/`` and an append-only
``PRESENCE_ROOT/events/YYYY-MM-DD.jsonl``. The Ops console reads these files; it never imports
this package. Writes must never break a request, so every failure is swallowed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..models import User
from .storage import atomic_write

LOGGER = logging.getLogger(__name__)
STATS_TZ = ZoneInfo("Asia/Shanghai")
MIN_WRITE_INTERVAL = 55.0
_MAX_LABEL = 40
_MAX_PAGE = 40
_state: dict[tuple[str, str, int], tuple[float, str]] = {}
_state_lock = threading.Lock()


def presence_root() -> Path:
    return Path(
        os.environ.get("METJIRA_PRESENCE_ROOT", "~/.met_jira_ops/presence")
    ).expanduser()


def _env_dir() -> Path:
    path = presence_root() / get_settings().deploy_env
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_page(page: str | None) -> str:
    raw = str(page or "").strip().lower().replace(" ", "_")
    return raw[:_MAX_PAGE]


def _label(user: User) -> str:
    return (user.display_name or user.username or f"user-{user.id}")[:_MAX_LABEL]


def touch(user: User, page: str | None = None, *, force: bool = False) -> None:
    """Refresh heartbeat from process memory; never read the old JSON on the request path."""
    try:
        path = _env_dir() / f"user_{int(user.id)}.json"
        now = time.time()
        cache_key = (str(presence_root()), get_settings().deploy_env, int(user.id))
        with _state_lock:
            previous_seen, previous_page = _state.get(cache_key, (0.0, ""))
            page_id = normalize_page(page) or previous_page
            if not force and now - previous_seen < MIN_WRITE_INTERVAL and page_id == previous_page:
                return
            _state[cache_key] = (now, page_id)
        payload = {
            "user_id": int(user.id),
            "username": user.username,
            "label": _label(user),
            "last_seen": now,
            "page": page_id,
        }
        atomic_write(path, json.dumps(payload, ensure_ascii=False).encode())
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("presence write failed: %r", exc)


def leave(user: User) -> None:
    try:
        with _state_lock:
            _state.pop(
                (str(presence_root()), get_settings().deploy_env, int(user.id)),
                None,
            )
        (_env_dir() / f"user_{int(user.id)}.json").unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("presence clear failed: %r", exc)


def record_event(user: User | None, action: str, *, page: str = "", kind: str = "") -> None:
    if user is None:
        return
    try:
        now = time.time()
        directory = presence_root() / "events"
        directory.mkdir(parents=True, exist_ok=True)
        day = datetime.fromtimestamp(now, tz=STATS_TZ).date().isoformat()
        payload = {
            "ts": round(now, 3),
            "deploy_env": get_settings().deploy_env,
            "user_id": int(user.id),
            "label": _label(user),
            "action": action[:40],
            "page": normalize_page(page),
            "kind": kind[:40],
        }
        with open(directory / f"{day}.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("presence event failed: %r", exc)
