"""Integration tests for the migration runner.

These apply migrations against a throwaway database (see ``conftest.py``) and
require a reachable ``DATABASE_URL``; the tests skip if it is not available.
"""

from __future__ import annotations

import psycopg
import pytest

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


def test_run_migrations_tracks_source_provenance(temp_database_url: str):
    with psycopg.connect(temp_database_url) as conn:
        rows = conn.execute(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_name = 'filings'"
        ).fetchall()
        columns = {row[0] for row in rows}
        assert "source" in columns
        default = dict(rows).get("source")
        assert default is not None and "house_clerk" in default

    with psycopg.connect(temp_database_url) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ingest_runs'"
        ).fetchall()
        assert "source" in {row[0] for row in rows}


def test_amount_max_is_nullable_and_amount_min_is_not(temp_database_url: str):
    """The eFD open-ended tier stores a lower bound with no upper bound."""
    with psycopg.connect(temp_database_url) as conn:
        rows = conn.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'transactions' "
            "AND column_name IN ('amount_min', 'amount_max')"
        ).fetchall()
    nullability = dict(rows)
    assert nullability["amount_min"] == "NO", "amount_min must stay required"
    assert nullability["amount_max"] == "YES", "amount_max must allow NULL"


def test_transactions_accept_null_amount_max(temp_database_url: str):
    """An open-ended amount round-trips as (50000000, NULL), not a fake range."""
    with psycopg.connect(temp_database_url) as conn:
        conn.execute(
            "INSERT INTO filings (doc_id, year, first_name, last_name, "
            "suffix, state_district, filing_date, doc_kind, pdf_url) "
            "VALUES ('open-ended-doc', 2026, 'Test', 'Filer', '', 'AA00', "
            "CURRENT_DATE, 'efiled', '')"
        )
        filing_id = conn.execute(
            "SELECT id FROM filings WHERE doc_id = 'open-ended-doc'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO transactions (filing_id, sequence, asset_name, "
            "txn_type, txn_date, notification_date, amount_min, amount_max, "
            "amount_raw) VALUES (%s, 0, 'Test Corp', 'S (partial)', "
            "CURRENT_DATE, CURRENT_DATE, 50000000, NULL, 'Over $50,000,000')",
            (filing_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT amount_min, amount_max, amount_raw FROM transactions "
            "WHERE filing_id = %s",
            (filing_id,),
        ).fetchone()
    assert row == (50_000_000, None, "Over $50,000,000")


def test_transactions_reject_null_amount_min(temp_database_url: str):
    """amount_min stays NOT NULL: every disclosure has a lower bound."""
    with psycopg.connect(temp_database_url) as conn:
        conn.execute(
            "INSERT INTO filings (doc_id, year, first_name, last_name, "
            "suffix, state_district, filing_date, doc_kind, pdf_url) "
            "VALUES ('null-min-doc', 2026, 'Test', 'Filer', '', 'AA00', "
            "CURRENT_DATE, 'efiled', '')"
        )
        filing_id = conn.execute(
            "SELECT id FROM filings WHERE doc_id = 'null-min-doc'"
        ).fetchone()[0]
        with pytest.raises(psycopg.Error):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO transactions (filing_id, sequence, asset_name, "
                    "txn_type, txn_date, notification_date, amount_min, "
                    "amount_max, amount_raw) VALUES (%s, 0, 'Test Corp', 'S', "
                    "CURRENT_DATE, CURRENT_DATE, NULL, 10, 'x')",
                    (filing_id,),
                )
