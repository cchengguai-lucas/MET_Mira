"""Ops-owned SQLite: online samples, per-user/page usage seconds, and probe history."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import RETENTION_DAYS, STORE_PATH, TZ

SCHEMA = """
create table if not exists samples (
  ts integer not null, env text not null, online integer not null
);
create index if not exists samples_ts on samples (ts);
create table if not exists sample_users (
  ts integer not null, env text not null, user_id integer not null, label text not null
);
create index if not exists sample_users_env_ts on sample_users (env, ts);
create table if not exists usage (
  day text not null, env text not null, user_id integer not null, label text not null,
  page text not null, seconds integer not null,
  primary key (day, env, user_id, page)
);
create table if not exists probes (
  ts integer not null, target text not null, ok integer not null, latency_ms integer
);
create index if not exists probes_target_ts on probes (target, ts);
"""


class Store:
    def __init__(self, path: Path = STORE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma journal_mode=wal")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, tuple(params)).fetchall()

    # -- writes ---------------------------------------------------------------------------

    def record_sample(self, env: str, seats: list[dict[str, Any]], seconds: int,
                      now: float | None = None) -> None:
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now, TZ).date().isoformat()
        with self._lock:
            self._db.execute("begin")
            self._db.execute("insert into samples values (?, ?, ?)", (int(now), env, len(seats)))
            self._db.executemany(
                "insert into sample_users values (?, ?, ?, ?)",
                [(int(now), env, seat["user_id"], seat["label"]) for seat in seats],
            )
            for seat in seats:
                self._db.execute(
                    """
                    insert into usage values (?, ?, ?, ?, ?, ?)
                    on conflict (day, env, user_id, page)
                    do update set seconds = seconds + excluded.seconds, label = excluded.label
                    """,
                    (day, env, seat["user_id"], seat["label"], seat["page"] or "", seconds),
                )
            self._db.execute("commit")

    def record_probe(self, target: str, ok: bool, latency_ms: int | None,
                     now: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "insert into probes values (?, ?, ?, ?)",
                (int(time.time() if now is None else now), target, int(ok), latency_ms),
            )

    def prune(self, now: float | None = None) -> None:
        cutoff = (time.time() if now is None else now) - RETENTION_DAYS * 86400
        day = datetime.fromtimestamp(cutoff, TZ).date().isoformat()
        with self._lock:
            self._db.execute("delete from samples where ts < ?", (int(cutoff),))
            self._db.execute("delete from sample_users where ts < ?", (int(cutoff),))
            self._db.execute("delete from probes where ts < ?", (int(cutoff),))
            self._db.execute("delete from usage where day < ?", (day,))

    # -- reads ----------------------------------------------------------------------------

    def online_history(self, env: str, hours: float, now: float | None = None) -> list[list[Any]]:
        """Per bucket (5 min for <= 48h, 1 h beyond): [ms, peak online, user ids seen]."""
        now = time.time() if now is None else now
        bucket = 300 if hours <= 48 else 3600
        start = int(now - hours * 3600)
        rows = self._rows(
            """
            select (ts / ?) * ? as b, max(online) as peak from samples
            where env = ? and ts >= ? group by b order by b
            """,
            (bucket, bucket, env, start),
        )
        peaks = {int(r["b"]): int(r["peak"]) for r in rows}
        seen: dict[int, list[int]] = {}
        for r in self._rows(
            """
            select distinct (ts / ?) * ? as b, user_id from sample_users
            where env = ? and ts >= ? order by b, user_id
            """,
            (bucket, bucket, env, start),
        ):
            seen.setdefault(int(r["b"]), []).append(int(r["user_id"]))
        first = (start // bucket) * bucket
        return [[b * 1000, peaks.get(b), seen.get(b, [])]
                for b in range(first, int(now) + 1, bucket)]

    def recent_labels(self, env: str, user_ids: Iterable[int]) -> dict[int, str]:
        ids = sorted(set(user_ids))
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self._rows(
            f"select user_id, max(label) as label from sample_users "
            f"where env = ? and user_id in ({marks}) group by user_id",
            (env, *ids),
        )
        return {int(r["user_id"]): r["label"] for r in rows}

    def daily(self, env: str, days: int, today: str | None = None) -> list[dict[str, Any]]:
        end = datetime.strptime(today, "%Y-%m-%d").date() if today else datetime.now(TZ).date()
        start = end - timedelta(days=days - 1)
        rows = {
            r["day"]: r
            for r in self._rows(
                """
                select day, count(distinct user_id) as users, sum(seconds) as seconds
                from usage where env = ? and day >= ? group by day
                """,
                (env, start.isoformat()),
            )
        }
        out = []
        for offset in range(days):
            day = (start + timedelta(days=offset)).isoformat()
            row = rows.get(day)
            out.append({
                "day": day,
                "users": int(row["users"]) if row else 0,
                "seconds": int(row["seconds"]) if row else 0,
            })
        return out

    def leaderboard(self, env: str, days: int, limit: int = 15) -> list[dict[str, Any]]:
        start = (datetime.now(TZ).date() - timedelta(days=days - 1)).isoformat()
        rows = self._rows(
            """
            select user_id, max(label) as label, sum(seconds) as seconds,
                   count(distinct day) as active_days
            from usage where env = ? and day >= ?
            group by user_id order by seconds desc limit ?
            """,
            (env, start, limit),
        )
        return [dict(r) for r in rows]

    def page_dwell(self, env: str, days: int) -> list[dict[str, Any]]:
        start = (datetime.now(TZ).date() - timedelta(days=days - 1)).isoformat()
        rows = self._rows(
            """
            select page, sum(seconds) as seconds, count(distinct user_id) as users
            from usage where env = ? and day >= ? group by page order by seconds desc
            """,
            (env, start),
        )
        return [dict(r) for r in rows]

    def uptime(self, target: str, hours: float, now: float | None = None) -> dict[str, Any]:
        start = int((time.time() if now is None else now) - hours * 3600)
        row = self._rows(
            """
            select count(*) as n, sum(ok) as up, avg(case when ok = 1 then latency_ms end) as avg_ms
            from probes where target = ? and ts >= ?
            """,
            (target, start),
        )[0]
        total = int(row["n"] or 0)
        return {
            "samples": total,
            "ratio": (int(row["up"] or 0) / total) if total else None,
            "avg_ms": round(row["avg_ms"]) if row["avg_ms"] is not None else None,
        }

    def probe_series(self, target: str, hours: float, now: float | None = None) -> list[list[Any]]:
        start = int((time.time() if now is None else now) - hours * 3600)
        rows = self._rows(
            "select ts, ok, latency_ms from probes where target = ? and ts >= ? order by ts",
            (target, start),
        )
        return [[r["ts"] * 1000, r["latency_ms"] if r["ok"] else None] for r in rows]
