"""In-process fan-out for live issue/project updates (SSE).

Single Uvicorn worker: sync writers enqueue after DB commit; async SSE readers
drain asyncio queues woken via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

_MAX_QUEUE = 64
_KEEPALIVE_SECONDS = 25.0


class LiveHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        # (loop, queue, project_keys_upper)
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict], frozenset[str]]] = []

    def subscribe(self, project_keys: set[str]) -> asyncio.Queue[dict]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_MAX_QUEUE)
        filt = frozenset(key.upper() for key in project_keys if key)
        with self._lock:
            self._subs.append((loop, queue, filt))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        with self._lock:
            self._subs = [row for row in self._subs if row[1] is not queue]

    def publish(
        self,
        *,
        project_key: str,
        kind: str,
        issue_key: str | None = None,
        actor_id: int | None = None,
        fields: list[str] | None = None,
    ) -> dict:
        event = {
            "id": next(self._seq),
            "type": "change",
            "kind": kind,
            "project_key": project_key.upper(),
            "issue_key": issue_key.upper() if issue_key else None,
            "actor_id": actor_id,
            "fields": list(fields or []),
            "at": time.time(),
        }
        key = event["project_key"]
        with self._lock:
            subscribers = list(self._subs)
        for loop, queue, filt in subscribers:
            if key not in filt:
                continue
            try:
                loop.call_soon_threadsafe(self._enqueue, queue, event)
            except RuntimeError:
                pass
        return event

    @staticmethod
    def _enqueue(queue: asyncio.Queue[dict], event: dict) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


hub = LiveHub()


def queue(
    db: Session,
    *,
    project_key: str,
    kind: str,
    issue_key: str | None = None,
    actor_id: int | None = None,
    fields: list[str] | None = None,
) -> None:
    """Buffer a live event until the session commits successfully."""
    events: list[dict[str, Any]] = db.info.setdefault("live_events", [])
    events.append({
        "project_key": project_key,
        "kind": kind,
        "issue_key": issue_key,
        "actor_id": actor_id,
        "fields": list(fields or []),
    })


def flush_queued(session: Session) -> None:
    for payload in session.info.pop("live_events", []):
        hub.publish(**payload)


def clear_queued(session: Session) -> None:
    session.info.pop("live_events", None)


async def iter_sse(
    queue: asyncio.Queue[dict],
    disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    yield f"data: {json.dumps({'type': 'hello'}, ensure_ascii=False)}\n\n"
    while True:
        if await disconnected():
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
            continue
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
