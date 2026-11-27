"""Business, AI and database statistics from a MET Jira database (read-only queries)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import psycopg

from .config import TZ, AppEnv
from .pg import connect

LOCAL_DAY = "to_char(({col} at time zone 'Asia/Shanghai')::date, 'YYYY-MM-DD')"
TODAY_START = "(date_trunc('day', now() at time zone 'Asia/Shanghai') at time zone 'Asia/Shanghai')"


def _days(days: int) -> list[str]:
    end = datetime.now(TZ).date()
    return [(end - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _per_day(conn: psycopg.Connection, table: str, col: str, days: int,
             where: str = "true") -> dict[str, int]:
    rows = conn.execute(
        f"""
        select {LOCAL_DAY.format(col=col)} as day, count(*) as n from {table}
        where {col} >= now() - make_interval(days => %s) and {where}
        group by 1
        """,
        (days,),
    ).fetchall()
    return {r["day"]: int(r["n"]) for r in rows}


def counts(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute(
        f"""
        select
          (select count(*) from users) as users_total,
          (select count(*) from users where enabled) as users_enabled,
          (select count(*) from users where last_login_at >= {TODAY_START}) as logins_today,
          (select count(*) from users where last_login_at >= now() - interval '7 days')
            as active_7d,
          (select count(*) from projects) as projects,
          (select count(*) from issues) as issues_total,
          (select count(*) from issues i join statuses s on s.id = i.status_id
             where s.category <> 'done') as issues_open,
          (select count(*) from issues i join statuses s on s.id = i.status_id
             where s.category <> 'done' and i.due_date < current_date) as issues_overdue,
          (select count(*) from issues where created_at >= {TODAY_START}) as created_today,
          (select count(*) from issues where resolved_at >= {TODAY_START}) as resolved_today,
          (select count(*) from comments where created_at >= now() - interval '7 days')
            as comments_7d,
          (select count(*) from agent_audits where created_at >= now() - interval '1 day')
            as ai_calls_24h,
          (select count(*) from agent_actions where status = 'pending') as ai_pending,
          (select count(*) from users where auth_source = 'ldap' and enabled) as ad_users,
          (select count(*) from org_units where not removed) as org_units,
          (select max(finished_at) from org_sync_runs where status = 'success') as org_synced_at,
          (select status from org_sync_runs order by started_at desc limit 1) as org_sync_status,
          pg_database_size(current_database()) as db_bytes,
          (select count(*) from pg_stat_activity where datname = current_database())
            as db_connections
        """
    ).fetchone()
    return _jsonable(dict(row))


def business(env: AppEnv, days: int) -> dict[str, Any]:
    with connect(env) as conn:
        created = _per_day(conn, "issues", "created_at", days)
        resolved = _per_day(conn, "issues", "resolved_at", days)
        activity = _per_day(conn, "activity_logs", "created_at", days)
        comments = _per_day(conn, "comments", "created_at", days)
        by_action = conn.execute(
            """
            select action, count(*) as n from activity_logs
            where created_at >= now() - make_interval(days => %s)
            group by action order by n desc
            """,
            (days,),
        ).fetchall()
        actors = conn.execute(
            """
            select u.display_name as name, u.username, count(*) as n
            from activity_logs a join users u on u.id = a.actor_id
            where a.created_at >= now() - make_interval(days => %s)
            group by u.id order by n desc limit 12
            """,
            (days,),
        ).fetchall()
        projects = conn.execute(
            """
            select p.key, p.name,
                   count(i.id) as total,
                   count(i.id) filter (where s.category = 'todo') as todo,
                   count(i.id) filter (where s.category = 'doing') as doing,
                   count(i.id) filter (where s.category = 'done') as done,
                   count(i.id) filter (where s.category <> 'done' and i.due_date < current_date)
                     as overdue,
                   max(i.updated_at) as last_update
            from projects p
            left join issues i on i.project_id = p.id
            left join statuses s on s.id = i.status_id
            group by p.id order by total desc
            """
        ).fetchall()
        types = conn.execute(
            "select type, count(*) as n from issues group by type order by n desc"
        ).fetchall()
        sync_runs = conn.execute(
            """
            select trigger, status, started_at, finished_at, stats, error
            from org_sync_runs order by started_at desc limit 8
            """
        ).fetchall()
        imports = conn.execute(
            """
            select b.created_at, b.filename, b.count, b.undone, u.display_name as user
            from import_batches b left join users u on u.id = b.user_id
            order by b.created_at desc limit 6
            """
        ).fetchall()
        reporters = conn.execute(
            """
            select i.reporter_id as user_id, count(*) as total,
                   count(*) filter (where s.category <> 'done') as open,
                   count(*) filter (where i.created_at >= now() - make_interval(days => %s))
                     as recent
            from issues i join statuses s on s.id = i.status_id
            where i.reporter_id is not null
            group by i.reporter_id
            """,
            (days,),
        ).fetchall()
    labels = _days(days)
    return {
        "days": labels,
        "created": [created.get(d, 0) for d in labels],
        "resolved": [resolved.get(d, 0) for d in labels],
        "activity": [activity.get(d, 0) for d in labels],
        "comments": [comments.get(d, 0) for d in labels],
        "by_action": [dict(r) for r in by_action],
        "actors": [dict(r) for r in actors],
        "projects": [_jsonable(dict(r)) for r in projects],
        "types": [dict(r) for r in types],
        "sync_runs": [_jsonable(dict(r)) for r in sync_runs],
        "imports": [_jsonable(dict(r)) for r in imports],
        "reporters": [dict(r) for r in reporters],
    }


def ai(env: AppEnv, days: int) -> dict[str, Any]:
    with connect(env) as conn:
        per_kind = conn.execute(
            f"""
            select {LOCAL_DAY.format(col="created_at")} as day, kind, count(*) as n
            from agent_audits where created_at >= now() - make_interval(days => %s)
            group by 1, 2
            """,
            (days,),
        ).fetchall()
        kinds = conn.execute(
            """
            select kind, count(*) as n,
                   avg(case when ok then 1.0 else 0.0 end) as ok_rate,
                   round(avg(latency_ms)) as avg_ms,
                   round(percentile_cont(0.95) within group (order by latency_ms)) as p95_ms
            from agent_audits where created_at >= now() - make_interval(days => %s)
            group by kind order by n desc
            """,
            (days,),
        ).fetchall()
        users = conn.execute(
            """
            select u.display_name as name, count(*) as n
            from agent_audits a join users u on u.id = a.user_id
            where a.created_at >= now() - make_interval(days => %s)
            group by u.id order by n desc limit 10
            """,
            (days,),
        ).fetchall()
        actions = conn.execute(
            """
            select tool, status, count(*) as n from agent_actions
            where created_at >= now() - make_interval(days => %s)
            group by tool, status order by n desc
            """,
            (days,),
        ).fetchall()
        failures = conn.execute(
            """
            select a.created_at, a.kind, a.latency_ms, left(a.result, 300) as result,
                   u.display_name as user
            from agent_audits a left join users u on u.id = a.user_id
            where not a.ok order by a.created_at desc limit 10
            """
        ).fetchall()
    labels = _days(days)
    series: dict[str, dict[str, int]] = {}
    for row in per_kind:
        series.setdefault(row["kind"], {})[row["day"]] = int(row["n"])
    return {
        "days": labels,
        "series": {kind: [vals.get(d, 0) for d in labels] for kind, vals in series.items()},
        "kinds": [_jsonable(dict(r)) for r in kinds],
        "users": [dict(r) for r in users],
        "actions": [dict(r) for r in actions],
        "failures": [_jsonable(dict(r)) for r in failures],
    }


def database(env: AppEnv) -> dict[str, Any]:
    with connect(env) as conn:
        tables = conn.execute(
            """
            select c.relname as name, pg_total_relation_size(c.oid) as bytes,
                   greatest(c.reltuples, 0)::bigint as rows
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r'
            order by bytes desc limit 12
            """
        ).fetchall()
        version = conn.execute("select alembic_version.version_num from alembic_version").fetchone()
    return {
        "tables": [dict(r) for r in tables],
        "migration": version["version_num"] if version else None,
    }


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif hasattr(value, "is_integer"):
            out[key] = float(value)
        else:
            out[key] = value
    return out
