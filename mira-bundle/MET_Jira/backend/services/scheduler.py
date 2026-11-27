"""Foreground scheduler with PostgreSQL leader and per-job advisory locks."""

from __future__ import annotations

import hashlib
import logging
import threading

from sqlalchemy import text

from ..config import get_settings
from ..db import engine, session_factory

LOGGER = logging.getLogger(__name__)
LEADER_LOCK_KEY = 0x4D45544A534348
_LOCAL_LOCKS: dict[int, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _lock_key(name: str) -> int:
    value = int.from_bytes(hashlib.sha256(f"met-jira:{name}".encode()).digest()[:8], "big")
    return value if value < 2**63 else value - 2**64


def _try_lock(connection, key: int) -> bool:
    if connection.dialect.name == "postgresql":
        return bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.setdefault(key, threading.Lock())
    return lock.acquire(blocking=False)


def _unlock(connection, key: int) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        return
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.get(key)
    if lock and lock.locked():
        lock.release()


def run_job_locked(job_id: str, job) -> bool:
    """Run one job only when this process owns its independent session lock."""
    key = _lock_key(f"job:{job_id}")
    with engine().connect() as connection:
        if not _try_lock(connection, key):
            LOGGER.info("Skipping %s; another scheduler owns its job lock", job_id)
            return False
        try:
            job()
            return True
        finally:
            _unlock(connection, key)


def _org_sync_job() -> None:
    from .org_sync import run_org_sync

    with session_factory()() as db:
        run = run_org_sync(db, trigger="schedule")
        LOGGER.info("Org sync finished: %s %s %s", run.status, run.stats, run.error or "")
        if run.status != "success":
            from .outbox import enqueue_webhook

            enqueue_webhook(
                db,
                "org_sync",
                "Mira 组织同步失败",
                {"run_id": run.id, "error": run.error},
                dedupe_key=f"org-sync-failed:{run.id}",
            )
            db.commit()


def _weekly_report_job() -> None:
    from .agent import generate_scheduled_reports

    with session_factory()() as db:
        count = generate_scheduled_reports(db, "weekly")
        LOGGER.info("Generated %s weekly AI reports", count)


def _sla_job() -> None:
    from .sla import scan

    with session_factory()() as db:
        LOGGER.info("SLA scan: %s", scan(db))


def _software_jira_job() -> None:
    from .software_jira import refresh_open_snapshots

    with session_factory()() as db:
        LOGGER.info("Software Jira refresh: %s", refresh_open_snapshots(db))


def _due_digest_job() -> None:
    from .reminders import due_digest

    with session_factory()() as db:
        LOGGER.info("Due digest: %s", due_digest(db))


def _pm_risk_job() -> None:
    from .pm_risk_alerts import scan

    with session_factory()() as db:
        LOGGER.info("PM schedule risk scan: %s", scan(db))


def _cleanup_job() -> None:
    from .agent import cleanup_retention
    from .ratelimit import cleanup_expired

    with session_factory()() as db:
        LOGGER.info(
            "Cleanup: concurrency=%s agent=%s",
            cleanup_expired(db),
            cleanup_retention(db),
        )


def build_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler

    settings = get_settings()
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    common = {
        "replace_existing": True,
        "max_instances": 1,
        "coalesce": True,
        "misfire_grace_time": 300,
    }
    scheduler.add_job(
        run_job_locked,
        "cron",
        args=("org_sync", _org_sync_job),
        hour=settings.org_sync_hour,
        minute=7,
        id="org_sync",
        **common,
    )
    scheduler.add_job(
        run_job_locked,
        "interval",
        args=("sla_scan", _sla_job),
        minutes=5,
        id="sla_scan",
        **common,
    )
    scheduler.add_job(
        run_job_locked,
        "interval",
        args=("software_jira_refresh", _software_jira_job),
        minutes=5,
        id="software_jira_refresh",
        **common,
    )
    scheduler.add_job(
        run_job_locked,
        "cron",
        args=("due_digest", _due_digest_job),
        hour=settings.due_digest_hour,
        minute=30,
        id="due_digest",
        **common,
    )
    scheduler.add_job(
        run_job_locked,
        "interval",
        args=("cleanup", _cleanup_job),
        hours=1,
        id="cleanup",
        **common,
    )
    scheduler.add_job(
        run_job_locked,
        "interval",
        args=("pm_risk_scan", _pm_risk_job),
        minutes=15,
        id="pm_risk_scan",
        **common,
    )
    if settings.llm_enabled:
        scheduler.add_job(
            run_job_locked,
            "cron",
            args=("weekly_report", _weekly_report_job),
            day_of_week=settings.report_weekday,
            hour=17,
            minute=30,
            id="weekly_report",
            **common,
        )
    return scheduler


def run_forever() -> bool:
    if not get_settings().scheduler_enabled:
        LOGGER.info("Scheduler disabled by METJIRA_SCHEDULER")
        return False
    with engine().connect() as connection:
        if not _try_lock(connection, LEADER_LOCK_KEY):
            LOGGER.warning("Scheduler leader already active; exiting")
            return False
        try:
            LOGGER.info("Scheduler leader acquired")
            build_scheduler().start()
            return True
        finally:
            _unlock(connection, LEADER_LOCK_KEY)


if __name__ == "__main__":  # pragma: no cover - foreground process entry point
    logging.basicConfig(level=logging.INFO)
    run_forever()
