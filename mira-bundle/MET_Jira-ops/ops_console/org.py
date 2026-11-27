"""Department view of MET Jira users, based on the AD org tree synced into ``org_units``."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from .config import AppEnv
from .pg import connect

UNASSIGNED = "Unassigned"
LOCAL = "Local account"
_CACHE: dict[str, tuple[float, dict[int, dict[str, Any]]]] = {}
CACHE_SECONDS = 300


def split_path(path: str | None) -> tuple[str, str]:
    """``MET / ETCH / ICP / Product`` -> (``ETCH``, ``ETCH / ICP / Product``)."""
    parts = [p.strip() for p in (path or "").split(" / ") if p.strip()]
    if len(parts) > 1:
        parts = parts[1:]
    if not parts:
        return UNASSIGNED, UNASSIGNED
    return parts[0], " / ".join(parts)


def directory(env: AppEnv, now: float | None = None) -> dict[int, dict[str, Any]]:
    now = time.time() if now is None else now
    cached = _CACHE.get(env.key)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    with connect(env) as conn:
        rows = conn.execute(
            """
            select u.id, u.username, u.display_name, u.enabled, u.auth_source, o.path
            from users u left join org_units o on o.id = u.org_unit_id
            """
        ).fetchall()
    people = {}
    for row in rows:
        if row["auth_source"] == "local":
            dept, team = LOCAL, LOCAL
        else:
            dept, team = split_path(row["path"])
        people[int(row["id"])] = {
            "name": row["display_name"] or row["username"],
            "enabled": bool(row["enabled"]),
            "ad": row["auth_source"] == "ldap",
            "dept": dept,
            "team": team,
        }
    _CACHE[env.key] = (now, people)
    return people


def headcount(people: dict[int, dict[str, Any]]) -> Counter:
    return Counter(p["dept"] for p in people.values() if p["ad"] and p["enabled"])


def usage_by_dept(totals: list[dict[str, Any]], people: dict[int, dict[str, Any]]) -> list[dict]:
    """Aggregate per-user dwell time into departments, with coverage against AD headcount."""
    heads = headcount(people)
    agg: dict[str, dict[str, Any]] = {}
    for row in totals:
        person = people.get(int(row["user_id"]))
        dept = person["dept"] if person else UNASSIGNED
        item = agg.setdefault(dept, {"dept": dept, "users": 0, "seconds": 0})
        item["users"] += 1
        item["seconds"] += int(row["seconds"] or 0)
    out = []
    for dept, item in agg.items():
        total = heads.get(dept, 0)
        out.append({**item, "headcount": total,
                    "coverage": (item["users"] / total) if total else None})
    return sorted(out, key=lambda r: r["seconds"], reverse=True)


def issues_by_dept(reporters: list[dict[str, Any]],
                   people: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for row in reporters:
        person = people.get(int(row["user_id"]))
        dept = person["dept"] if person else UNASSIGNED
        item = agg.setdefault(dept, {"dept": dept, "total": 0, "open": 0, "recent": 0,
                                     "people": 0})
        item["people"] += 1
        for key in ("total", "open", "recent"):
            item[key] += int(row[key] or 0)
    return sorted(agg.values(), key=lambda r: r["total"], reverse=True)


def annotate(rows: list[dict[str, Any]], people: dict[int, dict[str, Any]],
             key: str = "user_id") -> list[dict[str, Any]]:
    for row in rows:
        person = people.get(int(row[key])) if row.get(key) is not None else None
        row["team"] = person["team"] if person else None
    return rows
