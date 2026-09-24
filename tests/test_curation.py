"""Database-backed tests for explicit verification and amendment curation."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from politician_dashboard.ingest.curation import (
    CurationError,
    link_amendment,
    list_amendment_candidates,
    verify_transaction,
)
from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.store import store_filing


def _filing(doc_id: str) -> Filing:
    return Filing(
        prefix="", last="Tester", first="Test", suffix="", filing_type="P",
        state_district="ZZ00", year=2025, filing_date=date(2025, 6, 1),
        doc_id=doc_id,
    )


def _store(conn, doc_id: str, *, status: str | None = None, count: int = 1) -> int:
    txns = [
        Transaction(
            sequence=i, asset_name=f"Asset {i}", txn_type="P",
            txn_date=date(2025, 5, 17), notification_date=date(2025, 5, 18),
            amount_min=1001, amount_max=15000, amount_raw="$1,001 - $15,000",
            filing_status=status,
            quality_flags=("transaction_date_after_filing",),
        )
        for i in range(count)
    ]
    store_filing(
        conn, filing=_filing(doc_id), transactions=txns, raw_pdf=None,
        pdf_url=f"https://example.invalid/{doc_id}.pdf",
    )
    return conn.execute("SELECT id FROM filings WHERE doc_id = %s", (doc_id,)).fetchone()[0]


def test_verification_requires_provenance_and_source_filing_need_not_exist(temp_database_url):
    with psycopg.connect(temp_database_url, autocommit=True) as conn:
        _store(conn, "txn-doc")
        txn_id = conn.execute("SELECT id FROM transactions").fetchone()[0]
        with pytest.raises(CurationError):
            verify_transaction(
                conn, transaction_id=txn_id,
                verified_transaction_date=date(2025, 4, 17), method="amendment_match",
                confidence="high", source_doc_id=None,
            )
        with pytest.raises(CurationError):
            verify_transaction(
                conn, transaction_id=txn_id,
                verified_transaction_date=date(2025, 4, 17), method="amendment_match",
                confidence="high", source_doc_id="  ",
            )
        result = verify_transaction(
            conn, transaction_id=txn_id,
            verified_transaction_date=date(2025, 4, 17), method="amendment_match",
            confidence="high", source_doc_id="not-ingested-yet",
        )
        assert result.source_doc_id == "not-ingested-yet"
        assert conn.execute(
            "SELECT verification_source_doc_id FROM transactions WHERE id = %s", (txn_id,)
        ).fetchone()[0] == "not-ingested-yet"


def test_verification_updates_one_transaction_and_preserves_source_values(temp_database_url):
    with psycopg.connect(temp_database_url, autocommit=True) as conn:
        _store(conn, "two-txns", count=2)
        ids = [row[0] for row in conn.execute("SELECT id FROM transactions ORDER BY id").fetchall()]
        before = conn.execute(
            "SELECT txn_date, notification_date, quality_flags FROM transactions "
            "WHERE id = %s", (ids[0],),
        ).fetchone()
        verify_transaction(
            conn, transaction_id=ids[0],
            verified_transaction_date=date(2025, 4, 17), method="manual_review",
            confidence="medium", source_doc_id="external-doc",
        )
        rows = conn.execute(
            "SELECT verified_transaction_date FROM transactions ORDER BY id"
        ).fetchall()
        after = conn.execute(
            "SELECT txn_date, notification_date, quality_flags FROM transactions "
            "WHERE id = %s", (ids[0],),
        ).fetchone()
    assert rows == [(date(2025, 4, 17),), (None,)]
    assert after == before


def test_amended_status_is_only_a_candidate_and_never_links_automatically(temp_database_url):
    with psycopg.connect(temp_database_url, autocommit=True) as conn:
        _store(conn, "amended-candidate", status="Amended")
        assert [(c.doc_id, c.original_doc_id) for c in list_amendment_candidates(conn)] == [
            ("amended-candidate", None)
        ]
        assert conn.execute(
            "SELECT amends_filing_id FROM filings WHERE doc_id = %s",
            ("amended-candidate",),
        ).fetchone()[0] is None


def test_amendment_link_requires_two_filings_and_provenance(temp_database_url):
    with psycopg.connect(temp_database_url, autocommit=True) as conn:
        _store(conn, "amended", status="Amended")
        _store(conn, "original")
        link = link_amendment(
            conn, amended_doc_id="amended", original_doc_id="original",
            method="amendment_match", confidence="medium",
        )
        assert link.amended_doc_id == "amended"
        assert list_amendment_candidates(conn) == []


def test_database_rejects_invalid_or_incomplete_curation_provenance(temp_database_url):
    with psycopg.connect(temp_database_url, autocommit=True) as conn:
        _store(conn, "constraints-amended")
        _store(conn, "constraints-original")
        txn_id = conn.execute("SELECT id FROM transactions").fetchone()[0]
        amended_id = conn.execute(
            "SELECT id FROM filings WHERE doc_id = %s", ("constraints-amended",)
        ).fetchone()[0]
        original_id = conn.execute(
            "SELECT id FROM filings WHERE doc_id = %s", ("constraints-original",)
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE filings SET amends_filing_id = %s WHERE id = %s", (original_id, amended_id))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE filings SET amendment_method = 'other', "
                "amendment_confidence = 'high' WHERE id = %s", (amended_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE transactions SET verified_transaction_date = %s, "
                "verification_method = 'other', verification_confidence = 'high', "
                "verification_source_doc_id = 'source' WHERE id = %s",
                (date(2025, 4, 17), txn_id),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE transactions SET verified_transaction_date = %s, "
                "verification_method = 'manual_review', verification_confidence = 'high', "
                "verification_source_doc_id = '   ' WHERE id = %s",
                (date(2025, 4, 17), txn_id),
            )
