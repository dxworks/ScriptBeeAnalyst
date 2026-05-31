"""PostgreSQL access layer — single-tenant local mode, no auth, no RLS.

Replaces the former Supabase service-role client. A process-wide
:class:`psycopg_pool.ConnectionPool` is created lazily on first use from
:data:`src.config.DATABASE_URL`. Every repository / endpoint borrows a
connection from the pool for the duration of a single logical operation.

The whole codebase issues DB calls *synchronously* (the build pipeline in
``processor.py`` is plain sync; the FastAPI handlers wrap blocking calls in
``asyncio.to_thread``). A synchronous pool therefore keeps every existing
call-site signature intact — no ``async`` colouring leaks into the
repositories.

Rows are returned as ``dict`` (``psycopg.rows.dict_row``) so call-sites read
columns by name exactly as they did off the Supabase ``response.data`` rows.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from src.config import DATABASE_URL
from src.logger import get_logger

logger = get_logger("db")

_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    """Return the process-wide connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                logger.info("Opening PostgreSQL connection pool")
                _pool = ConnectionPool(
                    conninfo=DATABASE_URL,
                    min_size=1,
                    max_size=10,
                    kwargs={"row_factory": dict_row},
                    open=True,
                )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a pooled connection as a context manager.

    The connection is committed on a clean exit and rolled back on error,
    then returned to the pool.
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def query(sql: str, params: Sequence[Any] | None = None) -> List[dict]:
    """Run a SELECT-style statement and return all rows as dicts."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return cur.fetchall()


def query_one(sql: str, params: Sequence[Any] | None = None) -> Optional[dict]:
    """Run a statement and return the first row (or ``None``)."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return None
            return cur.fetchone()


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    """Run a write statement (INSERT/UPDATE/DELETE) and return rowcount."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def close_pool() -> None:
    """Close the pool on shutdown (best-effort)."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        finally:
            _pool = None


__all__ = [
    "close_pool",
    "connection",
    "execute",
    "get_pool",
    "query",
    "query_one",
]
