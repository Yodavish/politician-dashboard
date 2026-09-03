"""Shared pytest fixtures for database integration tests."""

from __future__ import annotations

import os
import uuid
from urllib.parse import quote, urlsplit

import psycopg
import pytest

from politician_dashboard.config import get_database_url
from politician_dashboard.migrations.migrate import run_migrations


def _admin_connection() -> psycopg.Connection:
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    try:
        return psycopg.connect(get_database_url(), autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"database unreachable: {exc}")


def _url_with_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    user = quote(parts.username or "")
    password = quote(parts.password or "")
    host = parts.hostname or "localhost"
    port = parts.port or 5432
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


@pytest.fixture()
def temp_database_url() -> str:
    """Create a throwaway database and yield its URL, dropping it afterwards."""
    conn = _admin_connection()
    dbname = f"pd_migration_test_{uuid.uuid4().hex[:8]}"
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{dbname}"')
    except psycopg.Error as exc:
        conn.close()
        pytest.skip(f"cannot create temporary database: {exc}")

    url = _url_with_db(get_database_url(), dbname)
    run_migrations(url)
    yield url

    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')
    finally:
        conn.close()


def _filing(**kwargs):
    from datetime import date

    from politician_dashboard.ingest.models import Filing

    defaults = dict(
        prefix="Hon.",
        last="Aderholt",
        first="Robert",
        suffix="",
        filing_type="P",
        state_district="AL04",
        year=2025,
        filing_date=None,
        doc_id="20032062",
    )
    defaults.update(kwargs)
    return Filing(**defaults)


def _txn(sequence, **kwargs):
    from datetime import date

    from politician_dashboard.ingest.models import Transaction

    defaults = dict(
        sequence=sequence,
        asset_name="GSK plc (GSK)",
        txn_type="S",
        txn_date=date(2025, 7, 28),
        notification_date=date(2025, 8, 11),
        amount_min=1001,
        amount_max=15000,
        amount_raw="$1,001 - $15,000",
        owner_token="SP",
        ticker="GSK",
        asset_type_code="ST",
    )
    defaults.update(kwargs)
    return Transaction(**defaults)


def seed_api_data(url: str) -> None:
    """Insert representative filings across two politicians."""
    import psycopg
    from datetime import date

    from politician_dashboard.ingest.store import store_filing

    def put(filing, transactions, doc_kind="efiled"):
        with psycopg.connect(url, autocommit=True) as conn:
            store_filing(
                conn,
                filing=filing,
                transactions=transactions,
                raw_pdf=b"%PDF-1.4 fake",
                pdf_url=f"https://example.invalid/{filing.year}/{filing.doc_id}.pdf",
                doc_kind=doc_kind,
            )

    # Robert Aderholt (AL04) - two filings
    put(
        _filing(),
        [
            _txn(0),
            _txn(
                1,
                asset_name="Apple Inc. (AAPL)", ticker="AAPL",
                asset_type_code="ST", txn_type="P",
                txn_date=date(2025, 7, 20), notification_date=date(2025, 8, 1),
                amount_min=15001, amount_max=50000,
                amount_raw="$15,001 - $50,000",
            ),
        ],
    )
    put(
        _filing(doc_id="20026537", filing_date=date(2023, 5, 1), year=2023),
        [
            _txn(
                0,
                asset_name="Vanguard Total Stock (VTI)", ticker="VTI",
                txn_type="S (partial)", txn_date=date(2023, 4, 2),
                notification_date=date(2023, 4, 10), amount_min=15001,
                amount_max=50000, amount_raw="$15,001 - $50,000",
                owner_token="Self",
            ),
            _txn(
                1,
                asset_name="Microsoft Corp (MSFT)", ticker="MSFT",
                txn_type="E", txn_date=date(2023, 3, 12),
                notification_date=date(2023, 3, 20), amount_min=1001,
                amount_max=15000, amount_raw="$1,001 - $15,000",
                asset_type_code="ST", owner_token="SP",
            ),
        ],
    )
    # Nancy Pelosi (CA11) - one filing
    put(
        _filing(
            doc_id="20026727", first="Nancy", last="Pelosi",
            state_district="CA11", filing_date=date(2024, 3, 10), year=2024,
        ),
        [
            _txn(
                0,
                asset_name="Cisco (CSCO)", ticker="CSCO", txn_type="S",
                txn_date=date(2024, 2, 5), notification_date=date(2024, 2, 12),
                amount_min=1001, amount_max=15000,
                amount_raw="$1,001 - $15,000", asset_type_code="ST", owner_token="SP",
            ),
            _txn(
                1,
                asset_name="NVIDIA Corp (NVDA)", ticker="NVDA",
                txn_type="S", txn_date=date(2024, 1, 20),
                notification_date=date(2024, 1, 28), amount_min=50001,
                amount_max=100000, amount_raw="$50,001 - $100,000",
                asset_type_code="ST", owner_token="Spouse",
            ),
        ],
    )


@pytest.fixture()
def api_client(temp_database_url: str):
    """A FastAPI TestClient backed by a disposable, seeded database."""
    from fastapi.testclient import TestClient

    from politician_dashboard.api import create_app

    seed_api_data(temp_database_url)
    app = create_app(database_url=temp_database_url)
    with TestClient(app) as client:
        yield client
