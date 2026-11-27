"""Background thread: online samples every 15s, health probes every 60s, daily pruning."""

from __future__ import annotations

import logging
import threading
import time

from . import presence, system
from .config import ENVS, PROBE_SECONDS, SAMPLE_SECONDS
from .pg import connect
from .store import Store

LOGGER = logging.getLogger("met_jira_ops.sampler")


def _probe_all(store: Store) -> None:
    for env in ENVS.values():
        ok, latency = system.http_probe(env.health_url)
        store.record_probe(f"web:{env.key}", ok, latency)
    ok, latency = system.llm_probe()
    store.record_probe("llm", ok, latency)
    started = time.perf_counter()
    try:
        with connect(ENVS["production"]) as conn:
            conn.execute("select 1")
        store.record_probe("postgres", True, round((time.perf_counter() - started) * 1000))
    except Exception:  # noqa: BLE001
        store.record_probe("postgres", False, None)


def run(store: Store, stop: threading.Event) -> None:
    last_sample = time.time()
    next_probe = 0.0
    next_prune = 0.0
    while not stop.is_set():
        now = time.time()
        # Credit real elapsed time, capped so a stalled loop never inflates usage.
        step = int(min(max(now - last_sample, 1), SAMPLE_SECONDS * 2))
        last_sample = now
        for key in ENVS:
            try:
                store.record_sample(key, presence.online(key), step, now=now)
            except Exception:  # noqa: BLE001
                LOGGER.exception("presence sample failed for %s", key)
        if now >= next_probe:
            next_probe = now + PROBE_SECONDS
            try:
                _probe_all(store)
            except Exception:  # noqa: BLE001
                LOGGER.exception("probe failed")
        if now >= next_prune:
            next_prune = now + 86400
            store.prune()
        stop.wait(SAMPLE_SECONDS)


def start(store: Store) -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=run, args=(store, stop), name="ops-sampler", daemon=True).start()
    return stop
