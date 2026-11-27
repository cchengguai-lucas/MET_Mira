"""Read-only PostgreSQL access to the MET Jira databases."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from .config import AppEnv


@contextmanager
def connect(env: AppEnv) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        env.dsn,
        connect_timeout=3,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=8000",
    )
    try:
        yield conn
    finally:
        conn.close()
