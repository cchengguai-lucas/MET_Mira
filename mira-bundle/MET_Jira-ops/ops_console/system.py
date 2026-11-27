"""Service, host and storage probes (systemd --user, HTTP health, /proc, disk)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import ENVS, INFRA_UNITS, LLM_BASE_URL, PGDATA, PRESENCE_ROOT

_SIZE_CACHE: dict[str, tuple[float, int]] = {}
_SIZE_TTL = 300.0


def unit_status(unit: str) -> dict[str, Any]:
    props = "ActiveState,SubState,ActiveEnterTimestampMonotonic,NRestarts,MemoryCurrent,MainPID"
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", props],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"unit": unit, "state": "unknown"}
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    since_mono = int(values.get("ActiveEnterTimestampMonotonic") or 0)
    uptime = None
    if since_mono and values.get("ActiveState") == "active":
        uptime = max(0, int(time.monotonic() - since_mono / 1_000_000))
    memory = values.get("MemoryCurrent", "")
    return {
        "unit": unit,
        "state": values.get("ActiveState", "unknown"),
        "sub_state": values.get("SubState", ""),
        "uptime_seconds": uptime,
        "restarts": int(values.get("NRestarts") or 0),
        "memory_bytes": int(memory) if memory.isdigit() else None,
        "pid": int(values.get("MainPID") or 0) or None,
    }


def http_probe(url: str, timeout: float = 3.0) -> tuple[bool, int | None]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            ok = 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False, None
    return ok, round((time.perf_counter() - started) * 1000)


def llm_probe() -> tuple[bool, int | None]:
    return http_probe(f"{LLM_BASE_URL}/models", timeout=5.0)


def services() -> list[dict[str, Any]]:
    out = []
    for env in ENVS.values():
        status = unit_status(env.unit)
        ok, latency = http_probe(env.health_url)
        out.append({**status, "key": env.key, "label": f"MET Jira {env.label} :{env.port}",
                    "url": env.public_url, "http_ok": ok, "latency_ms": latency})
    for unit, label in INFRA_UNITS:
        status = unit_status(unit)
        out.append({**status, "key": unit, "label": label, "http_ok": None, "latency_ms": None})
    return out


def dir_size(path: Path) -> int:
    key = str(path)
    cached = _SIZE_CACHE.get(key)
    if cached and time.time() - cached[0] < _SIZE_TTL:
        return cached[1]
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    _SIZE_CACHE[key] = (time.time(), total)
    return total


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, rest = line.split(":", 1)
            values[name] = int(rest.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return values


def host() -> dict[str, Any]:
    mem = _meminfo()
    load = os.getloadavg()
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        uptime = None
    disks = []
    seen: set[int] = set()
    for label, path in (("System disk /", Path("/")), ("Data disk /data", Path("/data"))):
        try:
            if not path.exists() or path.stat().st_dev in seen:
                continue
            seen.add(path.stat().st_dev)
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        disks.append({"label": label, "total": usage.total, "used": usage.used, "free": usage.free})
    storage = [
        {"label": f"{e.label} attachments & imports", "path": str(e.data_dir),
         "bytes": dir_size(e.data_dir)}
        for e in ENVS.values()
    ]
    storage.append({"label": "PostgreSQL data directory", "path": str(PGDATA),
                    "bytes": dir_size(PGDATA)})
    storage.append({"label": "Presence heartbeats & events", "path": str(PRESENCE_ROOT),
                    "bytes": dir_size(PRESENCE_ROOT)})
    return {
        "cpu_count": os.cpu_count(),
        "load": [round(v, 2) for v in load],
        "mem_total": mem.get("MemTotal"),
        "mem_available": mem.get("MemAvailable"),
        "uptime_seconds": uptime,
        "disks": disks,
        "storage": storage,
    }
