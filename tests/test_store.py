"""Integration tests for the Postgres storage layer.

These run against a throwaway database created by ``conftest.py`` (which also
applies the migrations) and skip when no reachable ``DATABASE_URL`` is set.
"""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.store import StoreError, store_filing

PDF_BYTES = b"%PDF-1.4 fake pdf bytes for testing"


def _filing(doc_id: str = "20032062", filing_date=date(2025, 9, 10)) -> Filing:
    return Filing(
        prefix="Hon.",
        last="Aderholt",
        first="Robert",
        suffix="",
        filing_type="P",
        state_district="AL04",
        year=2025,
        filing_date=filing_date,
        doc_id=doc_id,
    )


def _transactions() -> list[Transaction]:
    return [
        Transaction(
            sequence=0,
            asset_name="GSK plc American Depositary Shares (GSK)",
            txn_type="S",
            txn_date=date(2025, 7, 28),
            notification_date=date(2025, 8, 11),
            amount_min=1001,
            amount_max=15000,
            amount_raw="$1,001 - $15,000",
            owner_token="SP",
            ticker="GSK",
            asset_type_code="ST",
        ),
        Transaction(
            sequence=1,
            asset_name="US Treasury Bill Due 3/20/25 (912797KJ5)",
            txn_type="S (partial)",
            txn_date=date(2025, 1, 8),
            notification_date=date(2025, 2, 4),
            amount_min=15001,
            amount_max=50000,
            amount_raw="$15,001 - $50,000",
            owner_token=None,
            ticker=None,
            asset_type_code="GS",
            txn_source_id="2000086356",
        ),
    ]


def _filing_row(url: str, doc_id: str) -> tuple | None:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT id, doc_id, year, first_name, last_name, filing_date, "
            "raw_pdf FROM filings WHERE doc_id = %s",
            (doc_id,),
        ).fetchone()


def _txn_rows(url: str, filing_id: int) -> list[tuple]:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT sequence, txn_source_id, owner_token, asset_name, ticker, "
            "txn_type, txn_date, notification_date, amount_min, amount_max, "
            "amount_raw FROM transactions WHERE filing_id = %s ORDER BY sequence",
            (filing_id,),
        ).fetchall()


class TestStoreFiling:
    def test_inserts_filing_and_transactions(self, temp_database_url: str):
        filing = _filing()
        result = store_filing(
            psycopg.connect(temp_database_url),
            filing=filing,
            transactions=_transactions(),
            raw_pdf=PDF_BYTES,
            pdf_url="https://example.invalid/20032062.pdf",
        )
        assert result is True

        filing_row = _filing_row(temp_database_url, "20032062")
        assert filing_row is not None
        filing_id, doc_id, year, first, last, filing_date, raw_pdf = filing_row
        assert doc_id == "20032062"
        assert year == 2025
        assert first == "Robert"
        assert last == "Aderholt"
        assert filing_date == date(2025, 9, 10)
        assert raw_pdf == PDF_BYTES

        txns = _txn_rows(temp_database_url, filing_id)
        assert len(txns) == 2

        seq0, sid0, owner0, asset0, ticker0, ttype0, tdate0, ndate0, amin0, amax0, araw0 = txns[0]
        assert seq0 == 0
        assert sid0 is None
        assert owner0 == "SP"
        assert asset0 == "GSK plc American Depositary Shares (GSK)"
        assert ticker0 == "GSK"
        assert ttype0 == "S"
        assert tdate0 == date(2025, 7, 28)
        assert ndate0 == date(2025, 8, 11)
        assert amin0 == 1001
        assert amax0 == 15000
        assert araw0 == "$1,001 - $15,000"

        # Preserve "(partial)" suffix and source_id on the second transaction
        seq1, sid1, _owner1, _asset1, _ticker1, ttype1, *_ = txns[1]
        assert seq1 == 1
        assert sid1 == "2000086356"
        assert ttype1 == "S (partial)"

    def test_reingest_same_doc_id_is_skipped(self, temp_database_url: str):
        url = temp_database_url
        filing = _filing()
        conn = psycopg.connect(url)
        assert store_filing(
            conn,
            filing=filing,
            transactions=_transactions(),
            raw_pdf=PDF_BYTES,
            pdf_url="https://example.invalid/20032062.pdf",
        ) is True
        conn.close()

        # Re-ingest the identical DocID
        conn = psycopg.connect(url)
        assert store_filing(
            conn,
            filing=filing,
            transactions=_transactions(),
            raw_pdf=PDF_BYTES,
            pdf_url="https://example.invalid/20032062.pdf",
        ) is False
        conn.close()

        # No duplicate filing and no duplicated transactions
        with psycopg.connect(url) as c:
            filing_count = c.execute(
                "SELECT count(*) FROM filings WHERE doc_id = %s", ("20032062",)
            ).fetchone()[0]
            txn_count = c.execute(
                "SELECT count(*) FROM transactions WHERE filing_id = "
                "(SELECT id FROM filings WHERE doc_id = %s)",
                ("20032062",),
            ).fetchone()[0]
        assert filing_count == 1
        assert txn_count == 2

    def test_transaction_sequence_uniqueness_violation_rolls_back(
        self, temp_database_url: str
    ):
        """Two transactions with the same sequence must fail and roll back."""
        url = temp_database_url
        conn = psycopg.connect(url)

        dup = _transactions()
        dup[1] = Transaction(  # same sequence as the first
            sequence=0,
            asset_name="Duplicate sequence",
            txn_type="P",
            txn_date=date(2025, 1, 1),
            notification_date=date(2025, 1, 5),
            amount_min=1001,
            amount_max=15000,
            amount_raw="$1,001 - $15,000",
        )

        with pytest.raises(StoreError):
            store_filing(
                conn,
                filing=_filing(),
                transactions=dup,
                raw_pdf=PDF_BYTES,
                pdf_url="https://example.invalid/20032062.pdf",
            )
        conn.close()

        # The filing must not be left behind
        assert _filing_row(url, "20032062") is None

        # A corrected re-ingest succeeds on a fresh connection
        conn = psycopg.connect(url)
        assert store_filing(
            conn,
            filing=_filing(),
            transactions=_transactions(),
            raw_pdf=PDF_BYTES,
            pdf_url="https://example.invalid/20032062.pdf",
        ) is True
        conn.close()

    def test_filing_date_nullable(self, temp_database_url: str):
        filing = _filing(filing_date=None)
        conn = psycopg.connect(temp_database_url)
        assert store_filing(
            conn,
            filing=filing,
            transactions=[],
            raw_pdf=PDF_BYTES,
            pdf_url="https://example.invalid/20032062.pdf",
        ) is True
        conn.close()

        row = _filing_row(temp_database_url, "20032062")
        assert row is not None
        assert row[5] is None  # filing_date
