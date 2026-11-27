from __future__ import annotations

from datetime import timedelta


def test_scheduler_leader_lock_is_exclusive(admin):
    from backend.db import engine
    from backend.services import scheduler

    with engine().connect() as first, engine().connect() as second:
        assert scheduler._try_lock(first, scheduler.LEADER_LOCK_KEY) is True
        assert scheduler._try_lock(second, scheduler.LEADER_LOCK_KEY) is False
        scheduler._unlock(first, scheduler.LEADER_LOCK_KEY)
        assert scheduler._try_lock(second, scheduler.LEADER_LOCK_KEY) is True
        scheduler._unlock(second, scheduler.LEADER_LOCK_KEY)


def test_each_scheduler_job_has_an_independent_lock(admin):
    from backend.db import engine
    from backend.services import scheduler

    called = []
    key = scheduler._lock_key("job:test")
    with engine().connect() as held:
        assert scheduler._try_lock(held, key)
        assert scheduler.run_job_locked("test", lambda: called.append(True)) is False
        scheduler._unlock(held, key)
    assert scheduler.run_job_locked("test", lambda: called.append(True)) is True
    assert called == [True]


def test_jobs_coalesce_and_have_misfire_grace(admin):
    from backend.services.scheduler import build_scheduler

    scheduler = build_scheduler()
    jobs = scheduler.get_jobs()
    assert jobs
    assert all(job.coalesce and job.max_instances == 1 for job in jobs)
    assert all(job.misfire_grace_time == 300 for job in jobs)
    jira = scheduler.get_job("software_jira_refresh")
    assert jira is not None
    assert jira.trigger.interval == timedelta(minutes=5)
