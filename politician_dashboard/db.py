"""Database connection helpers (psycopg 3)."""

from __future__ import annotations

import psycopg

from politician_dashboard.config import get_database_url


def connect() -> psycopg.Connection:
    """Open a connection to the application database."""
    return psycopg.connect(get_database_url())