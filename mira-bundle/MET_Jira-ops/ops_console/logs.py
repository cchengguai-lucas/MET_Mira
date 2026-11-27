"""Request and error statistics parsed from the services' user journal (uvicorn access log)."""

from __future__ import annotations

import re
import subprocess
import time
from collections import Counter
from collections.abc import Iterable
from typing import Any

ACCESS_RE = re.compile(
    r'INFO:\s+(?P<client>[\d.]+):\d+ - '
    r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<status>\d{3})'
)
ERROR_RE = re.compile(r"\b(ERROR|CRITICAL|Traceback|Exception)\b")
WARNING_RE = re.compile(r"\bWARNING\b")
START_RE = re.compile(r"Started .*MET Jira")
_ID_RE = re.compile(r"/(?:[A-Z][A-Z0-9]+-\d+|\d+)(?=/|$)")
_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 30.0


def journal_lines(unit: str, hours: int) -> list[tuple[float, str]]:
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", unit, "--since", f"-{hours}h",
             "-o", "short-unix", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = []
    for raw in result.stdout.splitlines():
        stamp, _, rest = raw.partition(" ")
        try:
            lines.append((float(stamp), rest))
        except ValueError:
            if lines:
                lines.append((lines[-1][0], raw))
    return lines


def normalize_path(path: str) -> str:
    path = path.split("?", 1)[0]
    if not path.startswith("/api/"):
        return "/assets/*" if path.startswith("/assets/") else "(pages)"
    return _ID_RE.sub("/:id", path)


def summarize(lines: Iterable[tuple[float, str]], hours: int,
              now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    bucket = 300 if hours <= 6 else 3600
    first = int((now - hours * 3600) // bucket) * bucket
    per_bucket: dict[int, list[int]] = {b: [0, 0, 0] for b in range(first, int(now) + 1, bucket)}
    statuses: Counter[str] = Counter()
    endpoints: Counter[str] = Counter()
    error_paths: Counter[str] = Counter()
    clients: set[str] = set()
    errors: list[dict[str, Any]] = []
    starts: list[float] = []
    warnings = 0
    for ts, text in lines:
        match = ACCESS_RE.search(text)
        if match:
            status = int(match["status"])
            b = int(ts // bucket) * bucket
            slot = per_bucket.setdefault(b, [0, 0, 0])
            slot[0] += 1
            if status >= 500:
                slot[2] += 1
                error_paths[normalize_path(match["path"])] += 1
            elif status >= 400:
                slot[1] += 1
            statuses[f"{status // 100}xx"] += 1
            endpoints[f'{match["method"]} {normalize_path(match["path"])}'] += 1
            clients.add(match["client"])
            continue
        if START_RE.search(text):
            starts.append(ts)
        elif ERROR_RE.search(text):
            message = text.split(": ", 1)[-1].strip()
            if errors and errors[-1]["ts"] == ts:
                errors[-1]["text"] += "\n" + message
            else:
                errors.append({"ts": ts, "text": message})
        elif WARNING_RE.search(text):
            warnings += 1
    total = sum(statuses.values())
    return {
        "hours": hours,
        "requests": total,
        "clients": len(clients),
        "status": dict(statuses),
        "error_rate": (statuses.get("5xx", 0) / total) if total else 0.0,
        "series": [[b * 1000, *v] for b, v in sorted(per_bucket.items())],
        "top_endpoints": endpoints.most_common(12),
        "error_endpoints": error_paths.most_common(8),
        "errors": [{"ts": e["ts"], "text": e["text"][:2000]} for e in errors[-25:]][::-1],
        "warnings": warnings,
        "restarts": [s * 1000 for s in starts],
    }


def unit_summary(unit: str, hours: int = 24) -> dict[str, Any]:
    key = (unit, hours)
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    data = summarize(journal_lines(unit, hours), hours)
    _CACHE[key] = (time.time(), data)
    return data
