"""Database connection lifecycle for the API.

A single :class:`psycopg_pool.ConnectionPool` is created for the FastAPI app
in its lifespan and terminated at shutdown. Synchronous psycopg connections
are used throughout, matching the rest of the codebase; FastAPI runs sync
dependencies and route handlers in a worker threadpool.
"""

from __future__ import annotations

import psycopg
import psycopg_pool
from fastapi import Request

from politician_dashboard.api.errors import UnavailableError


def create_pool(database_url: str) -> psycopg_pool.ConnectionPool:
    return psycopg_pool.ConnectionPool(
        database_url,
        min_size=1,
        max_size=10,
        timeout=5,
        open=True,
        kwargs={"autocommit": True},
    )


def connection_dependency(request: Request):
    """FastAPI dependency yielding a pooled connection.

    The pool is attached to the app instance at startup, so tests that build
    their own app instance (against a disposable database) get the correct
    pool automatically.

    Only genuine database-connection failures are converted to a 503. A broad
    ``except Exception`` here would also swallow errors raised by the endpoint
    as they are re-flung into this generator during finalization, wrongly
    turning e.g. a 404/400 into a 503.
    """
    pool = request.app.state.pool
    try:
        with pool.connection(timeout=5) as conn:
            yield conn
    except (psycopg_pool.PoolTimeout, psycopg.OperationalError) as exc:
        raise UnavailableError("database unavailable") from exc
