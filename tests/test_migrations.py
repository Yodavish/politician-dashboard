"""Integration tests for the migration runner.

These apply migrations against a throwaway database (see ``conftest.py``) and
require a reachable ``DATABASE_URL``; the tests skip if it is not available.
"""

from __future__ import annotations

import psycopg

from politician_dashboard.migrations.migrate import run_migrations


def _public_tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def test_run_migrations_creates_schema(temp_database_url: str):
    tables = _public_tables(temp_database_url)
    assert {"filings", "transactions", "ingest_runs", "schema_migrations"} <= tables


def test_run_migrations_is_idempotent(temp_database_url: str):
    assert run_migrations(temp_database_url) == []
