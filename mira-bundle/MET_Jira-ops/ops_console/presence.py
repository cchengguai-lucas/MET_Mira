"""Read MET Jira presence seats and usage events from the shared presence directory."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import ONLINE_TTL_SECONDS, PRESENCE_ROOT, TZ

PAGE_LABELS = {
    "home": "My work",
    "projects": "Projects",
    "board": "Board",
    "backlog": "Backlog",
    "list": "List",
    "reports": "Reports",
    "settings": "Project settings",
    "issue": "Issue detail",
    "search": "Search",
    "org": "Org chart",
    "person": "Profile",
    "admin": "Admin",
    "login": "Login",
}


def online(env: str, *, root: Path | None = None, ttl: int = ONLINE_TTL_SECONDS,
           now: float | None = None) -> list[dict[str, Any]]:
    """Seats seen within ``ttl`` seconds, newest first. Expired files are left alone."""
    directory = (root or PRESENCE_ROOT) / env
    if not directory.is_dir():
        return []
    now = time.time() if now is None else now
    seats: list[dict[str, Any]] = []
    for path in directory.glob("user_*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            user_id = int(raw["user_id"])
            last_seen = float(raw["last_seen"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        age = now - last_seen
        if age > ttl:
            continue
        page = str(raw.get("page") or "")
        seats.append({
            "user_id": user_id,
            "username": str(raw.get("username") or ""),
            "label": str(raw.get("label") or f"user-{user_id}"),
            "page": page,
            "page_label": PAGE_LABELS.get(page, page or "—"),
            "last_seen": last_seen,
            "age": round(max(0.0, age)),
        })
    return sorted(seats, key=lambda s: s["last_seen"], reverse=True)


def read_events(days: int, *, root: Path | None = None,
                today: date | None = None) -> list[dict[str, Any]]:
    directory = (root or PRESENCE_ROOT) / "events"
    today = today or datetime.now(TZ).date()
    events: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        path = directory / f"{(today - timedelta(days=offset)).isoformat()}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def event_summary(env: str, days: int, **kwargs: Any) -> dict[str, Any]:
    events = [e for e in read_events(days, **kwargs) if e.get("deploy_env") == env]
    per_day: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        day = datetime.fromtimestamp(float(event.get("ts") or 0), TZ).date().isoformat()
        per_day[day][str(event.get("action"))] += 1
    totals = Counter(str(e.get("action")) for e in events)
    recent = [
        {k: e.get(k) for k in ("ts", "label", "action", "page", "kind")}
        for e in sorted(events, key=lambda e: e.get("ts") or 0, reverse=True)[:30]
    ]
    return {
        "per_day": {day: dict(counts) for day, counts in sorted(per_day.items())},
        "totals": dict(totals),
        "recent": recent,
    }
