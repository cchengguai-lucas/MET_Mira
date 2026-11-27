"""Durable transactional outbox and foreground delivery worker."""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import is_postgres, session_factory, utcnow
from ..models import OutboxEvent

LOGGER = logging.getLogger(__name__)
Handler = Callable[[OutboxEvent], None]


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    max_attempts: int = 8,
) -> OutboxEvent:
    """Insert an event in the caller's transaction; rollback removes it too."""
    event = OutboxEvent(
        kind=kind,
        payload=payload,
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    if dedupe_key is None:
        db.add(event)
        db.flush()
        return event
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
        return event
    except IntegrityError:
        existing = db.scalar(select(OutboxEvent).where(OutboxEvent.dedupe_key == dedupe_key))
        if existing is None:  # pragma: no cover - defensive against an unusual concurrent delete
            raise
        return existing


def enqueue_mail(
    db: Session,
    mails: list[Any],
    *,
    dedupe_key: str | None = None,
    max_attempts: int = 8,
) -> OutboxEvent | None:
    if not mails:
        return None
    payload = {
        "mails": [
            {"to": list(mail.to), "subject": mail.subject, "text": mail.text, "html": mail.html}
            for mail in mails
        ]
    }
    return enqueue(
        db, "mail", payload, dedupe_key=dedupe_key, max_attempts=max_attempts
    )


def enqueue_wecom(
    db: Session,
    emails: list[str | None],
    markdown: str,
    *,
    dedupe_key: str | None = None,
) -> OutboxEvent:
    return enqueue(
        db,
        "wecom",
        {"emails": emails, "markdown": markdown},
        dedupe_key=dedupe_key,
    )


def enqueue_webhook(
    db: Session,
    kind: str,
    title: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
) -> OutboxEvent:
    return enqueue(
        db,
        "webhook",
        {"kind": kind, "title": title, "payload": payload},
        dedupe_key=dedupe_key,
    )


def _claimable(now):
    return or_(
        (OutboxEvent.status == "pending") & (OutboxEvent.next_attempt_at <= now),
        (OutboxEvent.status == "claimed") & (OutboxEvent.lease_expires_at <= now),
    )


def claim_batch(
    db: Session,
    *,
    limit: int = 20,
    lease_seconds: int = 60,
    owner: str | None = None,
    now=None,
) -> list[OutboxEvent]:
    """Atomically claim available rows; PostgreSQL workers skip each other's locks."""
    now = now or utcnow()
    owner = owner or f"{socket.gethostname()}:{os.getpid()}"
    token = str(uuid.uuid4())
    db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.status == "claimed",
            OutboxEvent.lease_expires_at <= now,
            OutboxEvent.attempts >= OutboxEvent.max_attempts,
        )
        .values(
            status="dead",
            completed_at=now,
            lease_expires_at=None,
            claim_token=None,
            claimed_by=None,
            last_error="delivery lease expired after maximum attempts",
        )
    )
    stmt = (
        select(OutboxEvent.id)
        .where(_claimable(now), OutboxEvent.attempts < OutboxEvent.max_attempts)
        .order_by(OutboxEvent.next_attempt_at, OutboxEvent.id)
        .limit(limit)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True)
    candidate_ids = list(db.scalars(stmt))
    claimed_ids: list[int] = []
    for event_id in candidate_ids:
        changed = db.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                _claimable(now),
                OutboxEvent.attempts < OutboxEvent.max_attempts,
            )
            .values(
                status="claimed",
                attempts=OutboxEvent.attempts + 1,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                claim_token=token,
                claimed_by=owner,
            )
        ).rowcount
        if changed:
            claimed_ids.append(event_id)
    db.commit()
    if not claimed_ids:
        return []
    return list(
        db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.id.in_(claimed_ids), OutboxEvent.claim_token == token)
            .order_by(OutboxEvent.id)
        )
    )


def complete(db: Session, event_id: int, claim_token: str, *, now=None) -> bool:
    now = now or utcnow()
    changed = db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == "claimed",
            OutboxEvent.claim_token == claim_token,
        )
        .values(
            status="succeeded",
            completed_at=now,
            lease_expires_at=None,
            claim_token=None,
            claimed_by=None,
            last_error=None,
        )
    ).rowcount
    db.commit()
    return bool(changed)


def retry_or_dead(
    db: Session,
    event_id: int,
    claim_token: str,
    error: BaseException | str,
    *,
    now=None,
    base_delay_seconds: int = 5,
    max_delay_seconds: int = 3600,
) -> str | None:
    now = now or utcnow()
    event = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == "claimed",
            OutboxEvent.claim_token == claim_token,
        )
    )
    if event is None:
        return None
    event.last_error = str(error)[:4000]
    event.lease_expires_at = None
    event.claim_token = None
    event.claimed_by = None
    if event.attempts >= event.max_attempts:
        event.status = "dead"
        event.completed_at = now
    else:
        event.status = "pending"
        delay = min(max_delay_seconds, base_delay_seconds * (2 ** max(0, event.attempts - 1)))
        event.next_attempt_at = now + timedelta(seconds=delay)
    db.commit()
    return event.status


def status_metrics(db: Session) -> dict[str, Any]:
    counts = {"pending": 0, "claimed": 0, "succeeded": 0, "dead": 0}
    for status, count in db.execute(
        select(OutboxEvent.status, func.count(OutboxEvent.id)).group_by(OutboxEvent.status)
    ):
        counts[status] = count
    now = utcnow()
    oldest = db.scalar(
        select(func.min(OutboxEvent.created_at)).where(
            OutboxEvent.status.in_(("pending", "claimed"))
        )
    )
    attempts = db.scalar(
        select(func.coalesce(func.sum(OutboxEvent.attempts), 0)).where(
            OutboxEvent.status.in_(("pending", "claimed", "dead"))
        )
    )
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=now.tzinfo)
    return {
        "status": counts,
        "retry_attempts": int(attempts or 0),
        "oldest_ready_age_seconds": (
            max(0, int((now - oldest).total_seconds())) if oldest is not None else 0
        ),
    }


def deliver(event: OutboxEvent) -> None:
    """Perform one external side effect. Completion is recorded separately."""
    from . import alerts

    delivery_id = f"outbox-{event.id}"
    if event.kind == "mail":
        mails = [alerts.Mail(**item) for item in event.payload["mails"]]
        result = alerts.send_mail(
            mails,
            delivery_id=delivery_id,
            dedupe_key=event.dedupe_key,
        )
        if result["failed"] or result["sent"] != len(mails):
            raise RuntimeError(f"mail delivery incomplete: {result}")
        return
    if event.kind == "wecom":
        result = alerts.push_wecom(
            event.payload["emails"],
            event.payload["markdown"],
            delivery_id=delivery_id,
            dedupe_key=event.dedupe_key,
        )
        if not result["configured"] or result.get("error"):
            raise RuntimeError(f"WeCom delivery failed: {result}")
        return
    if event.kind == "webhook":
        ok = alerts.send(
            event.payload["kind"],
            event.payload["title"],
            event.payload["payload"],
            delivery_id=delivery_id,
            dedupe_key=event.dedupe_key,
        )
        if not ok:
            raise RuntimeError("webhook delivery failed")
        return
    raise ValueError(f"unknown outbox event kind: {event.kind}")


def process_batch(
    *,
    limit: int = 20,
    lease_seconds: int = 60,
    handler: Handler = deliver,
) -> dict[str, int]:
    with session_factory()() as db:
        events = claim_batch(db, limit=limit, lease_seconds=lease_seconds)
    result = {"claimed": len(events), "succeeded": 0, "retried": 0, "dead": 0}
    for event in events:
        assert event.claim_token is not None
        try:
            handler(event)
        except Exception as error:  # noqa: BLE001 - each delivery is isolated
            LOGGER.exception("Outbox delivery %s failed", event.id)
            with session_factory()() as db:
                status = retry_or_dead(db, event.id, event.claim_token, error)
            if status == "dead":
                result["dead"] += 1
            elif status == "pending":
                result["retried"] += 1
        else:
            with session_factory()() as db:
                if complete(db, event.id, event.claim_token):
                    result["succeeded"] += 1
    return result


def run_forever() -> None:
    from ..config import get_settings

    settings = get_settings()
    LOGGER.info("Outbox worker started")
    while True:
        result = process_batch(
            limit=settings.outbox_batch_size,
            lease_seconds=settings.outbox_lease_seconds,
        )
        if not result["claimed"]:
            time.sleep(settings.outbox_poll_seconds)


if __name__ == "__main__":  # pragma: no cover - foreground process entry point
    logging.basicConfig(level=logging.INFO)
    run_forever()
