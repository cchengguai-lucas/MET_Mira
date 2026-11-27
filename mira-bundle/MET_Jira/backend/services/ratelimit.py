"""Cross-worker rate limits and expiring AI concurrency leases.

PostgreSQL is the shared source of truth. SQLite keeps the original in-memory
implementation so unit tests and local development do not need database-specific
locking primitives.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update

from ..config import get_settings
from ..db import is_postgres, session_factory, utcnow
from ..models import AiSlotLease, RateLimitEvent


class SlidingWindow:
    """Counts events per key inside a rolling window."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, window: float, now: float) -> deque[float]:
        events = self._events[key]
        while events and events[0] <= now - window:
            events.popleft()
        return events

    def retry_after(self, key: str, limit: int, window: float) -> int:
        """Seconds until ``key`` may act again; 0 when it is under ``limit``."""
        now = time.monotonic()
        with self._lock:
            events = self._trim(key, window, now)
            if len(events) < limit:
                return 0
            return max(1, math.ceil(events[0] + window - now))

    def hit(self, key: str, window: float) -> None:
        now = time.monotonic()
        with self._lock:
            self._trim(key, window, now).append(now)

    def take(self, key: str, limit: int, window: float) -> int:
        """Record an event unless over ``limit``; returns the wait in seconds when refused."""
        now = time.monotonic()
        with self._lock:
            events = self._trim(key, window, now)
            if len(events) >= limit:
                return max(1, math.ceil(events[0] + window - now))
            events.append(now)
            return 0

    def clear(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


login_failures = SlidingWindow()
ai_requests = SlidingWindow()
_ai_running: dict[int, int] = defaultdict(int)
_ai_lock = threading.Lock()

# One address may try several accounts (shared terminals), but not spray many.
IP_FAILURE_FACTOR = 4


def reset() -> None:
    """Reset only the SQLite/test fallback; shared PostgreSQL state must survive restarts."""
    login_failures.clear()
    ai_requests.clear()
    with _ai_lock:
        _ai_running.clear()


def cleanup_expired(db, *, now=None) -> dict[str, int]:
    """Delete expired shared limiter history and abandoned AI leases."""
    now = now or utcnow()
    retention = max(get_settings().login_window_seconds, 60) * IP_FAILURE_FACTOR
    events = db.execute(
        delete(RateLimitEvent).where(
            RateLimitEvent.occurred_at <= now - timedelta(seconds=retention)
        )
    ).rowcount
    leases = db.execute(
        delete(AiSlotLease).where(AiSlotLease.expires_at <= now)
    ).rowcount
    db.commit()
    return {"rate_limit_events": events, "ai_slot_leases": leases}


def _too_many(message: str, wait: int) -> HTTPException:
    return HTTPException(429, message.format(n=wait), headers={"Retry-After": str(wait)})


def _subject(kind: str, value: str) -> str:
    """Return an opaque stable subject key; raw usernames/IPs never reach the tables."""
    return hmac.new(
        get_settings().secret.encode(),
        f"{kind}\0{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _lock_key(kind: str, subject_hash: str) -> int:
    raw = hashlib.sha256(f"{kind}\0{subject_hash}".encode()).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


def _shared_retry_after(
    kind: str,
    value: str,
    limit: int,
    window: float,
    *,
    record: bool,
) -> int:
    subject_hash = _subject(kind, value)
    now = utcnow()
    cutoff = now - timedelta(seconds=window)
    with session_factory()() as db:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _lock_key(kind, subject_hash)},
        )
        db.execute(
            delete(RateLimitEvent).where(
                RateLimitEvent.kind == kind,
                RateLimitEvent.subject_hash == subject_hash,
                RateLimitEvent.occurred_at <= cutoff,
            )
        )
        count, oldest = db.execute(
            select(func.count(RateLimitEvent.id), func.min(RateLimitEvent.occurred_at)).where(
                RateLimitEvent.kind == kind,
                RateLimitEvent.subject_hash == subject_hash,
            )
        ).one()
        if count >= limit:
            assert oldest is not None
            wait = max(1, math.ceil((oldest + timedelta(seconds=window) - now).total_seconds()))
            db.commit()
            return wait
        if record:
            db.add(
                RateLimitEvent(
                    kind=kind,
                    subject_hash=subject_hash,
                    occurred_at=now,
                )
            )
        db.commit()
        return 0


def _retry_after(
    memory: SlidingWindow,
    kind: str,
    value: str,
    limit: int,
    window: float,
    *,
    record: bool = False,
) -> int:
    if is_postgres():
        return _shared_retry_after(kind, value, limit, window, record=record)
    return (
        memory.take(value, limit, window)
        if record
        else memory.retry_after(value, limit, window)
    )


def _hit(memory: SlidingWindow, kind: str, value: str, window: float) -> None:
    if is_postgres():
        # A failure is always recorded; the large limit avoids refusing the insert.
        _shared_retry_after(kind, value, 2_147_483_647, window, record=True)
    else:
        memory.hit(value, window)


def _clear(memory: SlidingWindow, kind: str, value: str) -> None:
    if not is_postgres():
        memory.clear(value)
        return
    subject_hash = _subject(kind, value)
    with session_factory()() as db:
        db.execute(
            delete(RateLimitEvent).where(
                RateLimitEvent.kind == kind,
                RateLimitEvent.subject_hash == subject_hash,
            )
        )
        db.commit()


def check_login(ip: str, username: str) -> None:
    settings = get_settings()
    limit, window = settings.login_max_failures, settings.login_window_seconds
    wait = max(
        _retry_after(
            login_failures,
            "login-account",
            f"{ip}|{username.lower()}",
            limit,
            window,
        ),
        _retry_after(login_failures, "login-ip", ip, limit * IP_FAILURE_FACTOR, window),
    )
    if wait:
        raise _too_many("登录失败次数过多，请 {n} 秒后再试", wait)


def login_failed(ip: str, username: str) -> None:
    window = get_settings().login_window_seconds
    # Each call owns and commits its own short transaction. Authentication failures
    # are retained even when the request session later rolls back.
    _hit(login_failures, "login-account", f"{ip}|{username.lower()}", window)
    _hit(login_failures, "login-ip", ip, window)


def login_succeeded(ip: str, username: str) -> None:
    _clear(login_failures, "login-account", f"{ip}|{username.lower()}")


def take_ai_request(user_id: int) -> None:
    wait = _retry_after(
        ai_requests,
        "ai-request",
        str(user_id),
        get_settings().ai_requests_per_minute,
        60,
        record=True,
    )
    if wait:
        raise _too_many("AI 请求过于频繁，请 {n} 秒后再试", wait)


def acquire_ai_slot(user_id: int) -> str:
    if is_postgres():
        settings = get_settings()
        subject_hash = _subject("ai-slot", str(user_id))
        now = utcnow()
        token = str(uuid.uuid4())
        with session_factory()() as db:
            db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _lock_key("ai-slot", subject_hash)},
            )
            db.execute(
                delete(AiSlotLease).where(
                    AiSlotLease.subject_hash == subject_hash,
                    AiSlotLease.expires_at <= now,
                )
            )
            running = db.scalar(
                select(func.count(AiSlotLease.token)).where(
                    AiSlotLease.subject_hash == subject_hash,
                    AiSlotLease.expires_at > now,
                )
            ) or 0
            if running >= settings.ai_max_concurrent:
                db.commit()
                raise HTTPException(429, "已有 AI 任务在进行中，请等待完成后再试")
            db.add(
                AiSlotLease(
                    token=token,
                    subject_hash=subject_hash,
                    owner=f"{os.getpid()}:{threading.get_ident()}",
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=settings.ai_slot_lease_ttl_seconds),
                )
            )
            db.commit()
        return token
    with _ai_lock:
        if _ai_running[user_id] >= get_settings().ai_max_concurrent:
            raise HTTPException(429, "已有 AI 任务在进行中，请等待完成后再试")
        _ai_running[user_id] += 1
    return str(user_id)


def heartbeat_ai_slot(token: str) -> bool:
    if not is_postgres():
        return True
    now = utcnow()
    with session_factory()() as db:
        changed = db.execute(
            update(AiSlotLease)
            .where(AiSlotLease.token == token, AiSlotLease.expires_at > now)
            .values(
                heartbeat_at=now,
                expires_at=now
                + timedelta(seconds=get_settings().ai_slot_lease_ttl_seconds),
            )
        ).rowcount
        db.commit()
    return bool(changed)


def release_ai_slot(user_id: int, token: str | None = None) -> None:
    if is_postgres():
        lease_token = token or str(user_id)
        with session_factory()() as db:
            db.execute(delete(AiSlotLease).where(AiSlotLease.token == lease_token))
            db.commit()
        return
    with _ai_lock:
        _ai_running[user_id] = max(0, _ai_running[user_id] - 1)


@contextmanager
def _heartbeat(token: str) -> Iterator[None]:
    stop = threading.Event()
    thread: threading.Thread | None = None
    if is_postgres():
        interval = max(1, get_settings().ai_slot_heartbeat_seconds)

        def beat() -> None:
            while not stop.wait(interval):
                if not heartbeat_ai_slot(token):
                    return

        thread = threading.Thread(target=beat, name="ai-lease-heartbeat", daemon=True)
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=0.2)


@contextmanager
def held_ai_slot(user_id: int) -> Iterator[None]:
    """Hold and heartbeat a concurrency lease without consuming request quota."""
    token = acquire_ai_slot(user_id)
    try:
        with _heartbeat(token):
            yield
    finally:
        release_ai_slot(user_id, token)


@contextmanager
def ai_slot(user_id: int) -> Iterator[None]:
    """Rate-limit then hold one of the user's concurrent AI slots for the block."""
    take_ai_request(user_id)
    with held_ai_slot(user_id):
        yield
