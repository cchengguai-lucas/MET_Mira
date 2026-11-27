"""Low-cardinality Prometheus metrics used by the API and background workers."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import event
from sqlalchemy.engine import Engine

HTTP_REQUESTS = Counter(
    "metjira_http_requests_total",
    "HTTP requests",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "metjira_http_request_duration_seconds",
    "HTTP request duration",
    ("method", "route"),
)
DB_POOL = Gauge(
    "metjira_db_pool_connections",
    "SQLAlchemy pool connections",
    ("state",),
)
AI_SSE_ACTIVE = Gauge("metjira_ai_sse_active", "Active AI SSE streams")
AI_SSE_DURATION = Histogram("metjira_ai_sse_duration_seconds", "AI SSE stream duration")
OUTBOX = Gauge(
    "metjira_outbox_events",
    "Outbox events by bounded state",
    ("state",),
)
OUTBOX_RETRIES = Gauge("metjira_outbox_retry_attempts", "Current outbox retry attempts")
FACETS_CACHE = Counter(
    "metjira_facets_cache_total",
    "Facet cache lookups",
    ("result",),
)


def observe_http(method: str, route: str, status: int, elapsed: float) -> None:
    """Record only framework route templates, never raw paths or query values."""
    safe_route = route if route and route.startswith("/") else "unmatched"
    HTTP_REQUESTS.labels(method.upper(), safe_route, str(status)).inc()
    HTTP_DURATION.labels(method.upper(), safe_route).observe(elapsed)


@contextmanager
def ai_sse_stream() -> Iterator[None]:
    started = time.monotonic()
    AI_SSE_ACTIVE.inc()
    try:
        yield
    finally:
        AI_SSE_ACTIVE.dec()
        AI_SSE_DURATION.observe(time.monotonic() - started)


def facets_cache(result: str) -> None:
    FACETS_CACHE.labels("hit" if result == "hit" else "miss").inc()


def update_outbox(counts: dict[str, int], retries: int) -> None:
    for state in ("pending", "claimed", "succeeded", "dead"):
        OUTBOX.labels(state).set(counts.get(state, 0))
    OUTBOX_RETRIES.set(retries)


def instrument_pool(engine: Engine) -> None:
    """Track pool state without database/user labels."""
    pool = engine.pool

    def refresh(*_args) -> None:
        checked_out = getattr(pool, "checkedout", lambda: 0)()
        size = getattr(pool, "size", lambda: 0)()
        overflow = getattr(pool, "overflow", lambda: 0)()
        DB_POOL.labels("checked_out").set(max(0, checked_out))
        DB_POOL.labels("idle").set(max(0, size + max(0, overflow) - checked_out))

    event.listen(engine, "checkout", refresh)
    event.listen(engine, "checkin", refresh)
    refresh()


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
