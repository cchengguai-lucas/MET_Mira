from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select


def test_enqueue_rolls_back_with_business_transaction(admin):
    from backend.db import session_factory
    from backend.models import OutboxEvent
    from backend.services.outbox import enqueue

    with session_factory()() as db:
        enqueue(db, "webhook", {"value": 1})
        db.rollback()
    with session_factory()() as db:
        assert db.scalar(select(func.count(OutboxEvent.id))) == 0


def test_two_workers_do_not_claim_same_event(admin):
    from backend.db import session_factory
    from backend.services.outbox import claim_batch, enqueue

    with session_factory()() as db:
        event = enqueue(db, "webhook", {"value": 1})
        event_id = event.id
        db.commit()
    with session_factory()() as first:
        claimed = claim_batch(first, owner="worker-1")
    with session_factory()() as second:
        other = claim_batch(second, owner="worker-2")
    assert [row.id for row in claimed] == [event_id]
    assert other == []


def test_expired_lease_can_be_reclaimed(admin):
    from backend.db import session_factory, utcnow
    from backend.services.outbox import claim_batch, enqueue

    with session_factory()() as db:
        enqueue(db, "webhook", {"value": 1})
        db.commit()
    now = utcnow()
    with session_factory()() as db:
        first = claim_batch(db, owner="worker-1", lease_seconds=5, now=now)
    with session_factory()() as db:
        second = claim_batch(
            db, owner="worker-2", lease_seconds=5, now=now + timedelta(seconds=6)
        )
    assert first[0].id == second[0].id
    assert second[0].attempts == 2
    assert second[0].claim_token != first[0].claim_token


def test_retry_then_dead_letter(admin):
    from backend.db import session_factory, utcnow
    from backend.models import OutboxEvent
    from backend.services.outbox import claim_batch, enqueue, retry_or_dead

    with session_factory()() as db:
        event = enqueue(db, "webhook", {"value": 1}, max_attempts=2)
        event_id = event.id
        db.commit()
    now = utcnow()
    with session_factory()() as db:
        first = claim_batch(db, now=now)[0]
    with session_factory()() as db:
        assert retry_or_dead(
            db, event_id, first.claim_token, "temporary", now=now, base_delay_seconds=0
        ) == "pending"
    with session_factory()() as db:
        second = claim_batch(db, now=now)[0]
    with session_factory()() as db:
        assert retry_or_dead(db, event_id, second.claim_token, "permanent", now=now) == "dead"
        row = db.get(OutboxEvent, event_id)
        assert row.attempts == 2
        assert row.last_error == "permanent"


def test_expired_final_lease_moves_to_dead_letter(admin):
    from backend.db import session_factory, utcnow
    from backend.models import OutboxEvent
    from backend.services.outbox import claim_batch, enqueue

    with session_factory()() as db:
        event = enqueue(db, "webhook", {"value": 1}, max_attempts=1)
        event_id = event.id
        db.commit()
    now = utcnow()
    with session_factory()() as db:
        assert claim_batch(db, lease_seconds=1, now=now)
    with session_factory()() as db:
        assert claim_batch(db, now=now + timedelta(seconds=2)) == []
        row = db.get(OutboxEvent, event_id)
        assert row.status == "dead"


def test_dedupe_key_is_unique(admin):
    from backend.db import session_factory
    from backend.services.outbox import enqueue

    with session_factory()() as db:
        first = enqueue(db, "webhook", {"value": 1}, dedupe_key="same")
        second = enqueue(db, "webhook", {"value": 2}, dedupe_key="same")
        db.commit()
        assert first.id == second.id
