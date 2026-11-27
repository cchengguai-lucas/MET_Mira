#!/usr/bin/env python3
"""Authenticated staging capacity baseline with database/queue safety gates."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from psycopg import connect
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

POOL_TIMEOUT = re.compile(
    r"(QueuePool limit.*timeout|pool[_ ]timeout|timed out waiting for.*connection)",
    re.IGNORECASE,
)


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    return values[max(0, math.ceil(len(values) * quantile) - 1)]


def request_json(
    url: str,
    *,
    cookie: str = "",
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 20,
) -> tuple[int, bytes]:
    headers = {"Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = Request(url, payload, headers, method=method)
    try:
        with build_opener().open(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except (OSError, URLError):
        return 0, b""


def login(base_url: str, username: str, password: str) -> str:
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    request = Request(
        f"{base_url}/api/auth/login",
        json.dumps({"username": username, "password": password}).encode(),
        {"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=15) as response:
        response.read()
    cookie = "; ".join(f"{item.name}={item.value}" for item in jar)
    if not cookie:
        raise RuntimeError("login succeeded without a session cookie")
    return cookie


def direct_database_url(explicit: str | None) -> URL:
    raw = explicit or os.getenv("METJIRA_CAPACITY_DATABASE_URL")
    if not raw:
        raw = load_dotenv(Path(".env")).get("METJIRA_DATABASE_URL")
    if not raw:
        raise RuntimeError("no direct staging database URL was provided")
    url = make_url(raw)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or (url.port or 5432) != 5433
        or url.database != "met_jira_staging"
    ):
        raise RuntimeError("capacity diagnostics require direct met_jira_staging on localhost:5433")
    return url


class Diagnostics:
    def __init__(self, database_url: URL, started_at: datetime) -> None:
        self.url = database_url
        self.started_at = started_at
        self.engine = create_engine(
            database_url,
            poolclass=NullPool,
            connect_args={"application_name": "met-jira-capacity-monitor"},
        )
        self.stop = threading.Event()
        self.samples: list[int] = []
        self.error: str | None = None
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self.stop.wait(0.1):
            try:
                with self.engine.connect() as connection:
                    count = connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND backend_type = 'client backend'"
                        )
                    )
                self.samples.append(int(count or 0))
            except Exception as error:  # noqa: BLE001 - surfaced as a failed safety gate
                self.error = f"{type(error).__name__}: {error}"
                return

    def outbox(self) -> dict[str, Any]:
        with self.engine.connect() as connection:
            statuses = dict(
                connection.execute(
                    text(
                        "SELECT status, count(*) FROM outbox_events "
                        "GROUP BY status ORDER BY status"
                    )
                ).all()
            )
            duplicate_keys = connection.scalar(
                text(
                    "SELECT count(*) FROM ("
                    "SELECT dedupe_key FROM outbox_events WHERE dedupe_key IS NOT NULL "
                    "GROUP BY dedupe_key HAVING count(*) > 1"
                    ") duplicates"
                )
            )
            duplicate_delivery_risk = connection.scalar(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE status = 'succeeded' AND attempts > 1 "
                    "AND completed_at >= :started_at"
                ),
                {"started_at": self.started_at},
            )
        return {
            "status": statuses,
            "backlog": int(statuses.get("pending", 0) + statuses.get("claimed", 0)),
            "duplicate_dedupe_keys": int(duplicate_keys or 0),
            "duplicate_delivery_risk": int(duplicate_delivery_risk or 0),
        }

    def pgbouncer(self) -> dict[str, Any]:
        kwargs = {
            "host": "127.0.0.1",
            "port": 6433,
            "dbname": "pgbouncer",
            "user": self.url.username or "",
            "password": self.url.password or "",
            "connect_timeout": 5,
            "autocommit": True,
            "row_factory": dict_row,
        }
        with connect(**kwargs) as connection:
            stats = list(connection.execute("SHOW STATS"))
            pools = list(connection.execute("SHOW POOLS"))
        return {
            "stats": [
                {key: value for key, value in row.items() if key != "database"}
                | {"database": row["database"]}
                for row in stats
                if row["database"] == self.url.database
            ],
            "pools": [
                dict(row)
                for row in pools
                if row["database"] in {self.url.database, "pgbouncer"}
            ],
        }

    def start(self) -> None:
        self.thread.start()

    def finish(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        self.engine.dispose()


def journal_pool_timeouts(unit: str, since: datetime) -> tuple[int, list[str]]:
    command = [
        "journalctl",
        "--user",
        "--unit",
        unit,
        "--utc",
        "--since",
        since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "--no-pager",
        "--output=cat",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"journalctl failed: {result.stderr.strip()}")
    lines = [line for line in result.stdout.splitlines() if POOL_TIMEOUT.search(line)]
    return len(lines), lines[-5:]


def endpoint_set(base_url: str, project: str) -> list[dict[str, Any]]:
    return [
        {"name": "health", "method": "GET", "url": f"{base_url}/api/health"},
        {
            "name": "search",
            "method": "GET",
            "url": f"{base_url}/api/search?jql=&limit=50",
        },
        {
            "name": "facets",
            "method": "GET",
            "url": f"{base_url}/api/search/facets?jql=",
        },
        {
            "name": "notifications_unread",
            "method": "GET",
            "url": f"{base_url}/api/notifications/unread-count",
        },
        {
            "name": "notifications_list",
            "method": "GET",
            "url": f"{base_url}/api/notifications?limit=50",
        },
        {
            "name": "presence_heartbeat",
            "method": "POST",
            "url": f"{base_url}/api/presence/heartbeat",
            "body": {"page": "capacity-baseline"},
        },
        {
            "name": "dashboard_health",
            "method": "GET",
            "url": f"{base_url}/api/dashboard/health",
        },
        {
            "name": "project_report",
            "method": "GET",
            "url": f"{base_url}/api/projects/{project}/reports/summary?days=30",
        },
    ]


def run_endpoint(
    endpoint: dict[str, Any],
    *,
    cookie: str,
    requests: int,
    concurrency: int,
) -> dict[str, Any]:
    def once(_index: int) -> tuple[float, int]:
        started = time.perf_counter()
        status, _body = request_json(
            endpoint["url"],
            cookie=cookie,
            method=endpoint["method"],
            body=endpoint.get("body"),
        )
        return (time.perf_counter() - started) * 1000, status

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = list(pool.map(once, range(requests)))
    elapsed = time.perf_counter() - started
    times = sorted(sample[0] for sample in samples)
    errors = sum(not 200 <= sample[1] < 300 for sample in samples)
    statuses: dict[str, int] = {}
    for _duration, status in samples:
        key = str(status)
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "endpoint": endpoint["name"],
        "concurrency": concurrency,
        "requests": requests,
        "errors": errors,
        "error_rate": round(errors / requests, 4),
        "throughput_rps": round(requests / elapsed, 2),
        "p50_ms": round(statistics.median(times), 1),
        "p95_ms": round(percentile(times, 0.95), 1),
        "p99_ms": round(percentile(times, 0.99), 1),
        "statuses": statuses,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8801")
    parser.add_argument("--username", default=os.getenv("METJIRA_CAPACITY_USERNAME"))
    parser.add_argument("--password", default=os.getenv("METJIRA_CAPACITY_PASSWORD"))
    parser.add_argument("--project")
    parser.add_argument("--requests-per-endpoint", type=int, default=64)
    parser.add_argument("--concurrency", default="8,16,32")
    parser.add_argument("--database-url")
    parser.add_argument("--connection-budget", type=int, default=12)
    parser.add_argument("--api-unit", default="met-jira-staging.service")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.username or not args.password:
        parser.error(
            "--username/--password or METJIRA_CAPACITY_USERNAME/PASSWORD are required"
        )
    concurrency_levels = [int(value) for value in args.concurrency.split(",")]
    if concurrency_levels != [8, 16, 32]:
        parser.error("--concurrency must be exactly 8,16,32 for the release baseline")
    if args.requests_per_endpoint < max(concurrency_levels):
        parser.error("--requests-per-endpoint must be at least 32")

    base_url = args.url.rstrip("/")
    cookie = login(base_url, args.username, args.password)
    status, raw_projects = request_json(f"{base_url}/api/projects", cookie=cookie)
    if status != 200:
        raise RuntimeError(f"project discovery failed with HTTP {status}")
    projects = json.loads(raw_projects).get("projects") or []
    project = args.project or (projects[0]["key"] if projects else None)
    if not project or project not in {item["key"] for item in projects}:
        raise RuntimeError("no accessible project is available for the report baseline")

    started_at = datetime.now(timezone.utc)
    diagnostics = Diagnostics(direct_database_url(args.database_url), started_at)
    outbox_before = diagnostics.outbox()
    pgbouncer_before = diagnostics.pgbouncer()
    diagnostics.start()
    rows: list[dict[str, Any]] = []
    try:
        for concurrency in concurrency_levels:
            for endpoint in endpoint_set(base_url, project):
                rows.append(
                    run_endpoint(
                        endpoint,
                        cookie=cookie,
                        requests=args.requests_per_endpoint,
                        concurrency=concurrency,
                    )
                )
    finally:
        request_json(
            f"{base_url}/api/presence/leave",
            cookie=cookie,
            method="POST",
            body={},
        )
        diagnostics.finish()

    outbox_after = diagnostics.outbox()
    pgbouncer_after = diagnostics.pgbouncer()
    timeout_count, timeout_lines = journal_pool_timeouts(args.api_unit, started_at)
    total_requests = sum(row["requests"] for row in rows)
    total_errors = sum(row["errors"] for row in rows)
    peak_connections = max(diagnostics.samples, default=0)
    result = {
        "url": base_url,
        "project": project,
        "concurrency_levels": concurrency_levels,
        "requests_per_endpoint": args.requests_per_endpoint,
        "total_requests": total_requests,
        "total_errors": total_errors,
        "error_rate": round(total_errors / total_requests, 6),
        "endpoints": rows,
        "postgres": {
            "peak_connections": peak_connections,
            "connection_budget": args.connection_budget,
            "samples": len(diagnostics.samples),
            "monitor_error": diagnostics.error,
        },
        "pgbouncer": {"before": pgbouncer_before, "after": pgbouncer_after},
        "api_pool_timeouts": {"count": timeout_count, "examples": timeout_lines},
        "outbox": {"before": outbox_before, "after": outbox_after},
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")

    failed = (
        result["error_rate"] >= 0.01
        or timeout_count > 0
        or outbox_after["duplicate_dedupe_keys"] > 0
        or outbox_after["duplicate_delivery_risk"] > 0
        or peak_connections > args.connection_budget
        or diagnostics.error is not None
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
