"""Tests for recomputing quality flags on existing stored transactions.

DB-gated like the rest of the store/API integration tests: they run against a
throwaway database (``temp_database_url``) and skip when no reachable
``DATABASE_URL`` is set. CLI validation tests run without a database.
"""

from __future__ import annotations

from datetime import date

import psycopg

from politician_dashboard.ingest.__main__ import main
from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.quality import (
    AFTER_FILING,
    AFTER_INGESTION_DATE,
    AFTER_NOTIFICATION,
    NOTIFICATION_AFTER_FILING,
)
from politician_dashboard.ingest.recompute import (
    RecomputeReport,
    recompute_quality_flags,
)
from politician_dashboard.ingest.store import store_filing

PDF_BYTES = b"%PDF-1.4 fake pdf bytes for testing"


def _seed(
    url: str,
    doc_id: str,
    filing_date: date,
    txns: list[tuple[date, date]],
    source: str = "house_clerk",
) -> None:
    filing = Filing(
        prefix="Hon.",
        last="Member",
        first="Test",
        suffix="",
        filing_type="P",
        state_district="ZZ00",
        year=filing_date.year,
        filing_date=filing_date,
        doc_id=doc_id,
    )
    transactions = [
        Transaction(
            sequence=idx,
            asset_name=f"Asset {idx}",
            txn_type="S",
            txn_date=txn_date,
            notification_date=notification_date,
            amount_min=1001,
            amount_max=15000,
            amount_raw="$1,001 - $15,000",
        )
        for idx, (txn_date, notification_date) in enumerate(txns)
    ]
    with psycopg.connect(url) as conn:
        store_filing(
            conn,
            filing=filing,
            transactions=transactions,
            raw_pdf=PDF_BYTES,
            pdf_url=f"https://example.invalid/{doc_id}.pdf",
            source=source,
        )


def _recompute(url: str, *, as_of: date, dry_run: bool = False) -> RecomputeReport:
    with psycopg.connect(url) as conn:
        return recompute_quality_flags(conn, as_of=as_of, dry_run=dry_run)


def _txn_rows(url: str, doc_id: str) -> list[tuple]:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT txn_date, notification_date, quality_flags "
            "FROM transactions t "
            "JOIN filings f ON f.id = t.filing_id "
            "WHERE f.doc_id = %s ORDER BY t.sequence",
            (doc_id,),
        ).fetchall()


class TestRecomputeQualityFlags:
    def test_sony_filing_gets_all_txn_flags(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20033889", date(2026, 2, 9), [(date(2026, 12, 26), date(2026, 1, 21))])
        report = _recompute(url, as_of=date(2026, 2, 9))
        assert report.examined == 1
        assert report.changed == 1
        assert report.affected_doc_ids == ["20033889"]
        assert report.flag_counts == {
            AFTER_NOTIFICATION: 1,
            AFTER_FILING: 1,
            AFTER_INGESTION_DATE: 1,
        }

        txn_date, notification_date, flags = _txn_rows(url, "20033889")[0]
        # Source dates are preserved verbatim; only quality_flags is written.
        assert txn_date == date(2026, 12, 26)
        assert notification_date == date(2026, 1, 21)
        assert flags == [AFTER_NOTIFICATION, AFTER_FILING, AFTER_INGESTION_DATE]

    def test_notification_after_filing(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20018054", date(2025, 1, 10), [(date(2024, 12, 31), date(2025, 1, 31))])
        report = _recompute(url, as_of=date(2025, 2, 1))
        assert report.changed == 1
        assert report.flag_counts == {NOTIFICATION_AFTER_FILING: 1}
        assert _txn_rows(url, "20018054")[0][2] == [NOTIFICATION_AFTER_FILING]

    def test_after_notification(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20027879", date(2025, 3, 9), [(date(2025, 2, 24), date(2025, 1, 31))])
        report = _recompute(url, as_of=date(2025, 4, 1))
        assert report.changed == 1
        assert report.flag_counts == {AFTER_NOTIFICATION: 1}
        assert _txn_rows(url, "20027879")[0][2] == [AFTER_NOTIFICATION]

    def test_after_filing(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20030312", date(2025, 5, 11), [(date(2025, 5, 17), date(2025, 5, 17))])
        report = _recompute(url, as_of=date(2025, 6, 1))
        assert report.changed == 1
        flags = _txn_rows(url, "20030312")[0][2]
        # txn == notification equals draws no after-notification flag, but both
        # postdate the filing.
        assert AFTER_FILING in flags
        assert NOTIFICATION_AFTER_FILING in flags
        assert AFTER_INGESTION_DATE not in flags

    def test_future_transaction_relative_to_as_of(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20034520", date(2027, 1, 15), [(date(2026, 12, 26), date(2026, 12, 30))])
        report = _recompute(url, as_of=date(2026, 2, 9))
        assert report.changed == 1
        assert report.flag_counts == {AFTER_INGESTION_DATE: 1}
        assert _txn_rows(url, "20034520")[0][2] == [AFTER_INGESTION_DATE]

    def test_normal_transaction(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20032062", date(2025, 9, 10), [(date(2025, 7, 28), date(2025, 8, 11))])
        report = _recompute(url, as_of=date(2025, 9, 1))
        assert report.examined == 1
        assert report.changed == 0
        assert report.flag_counts == {}
        assert report.affected_doc_ids == []
        assert _txn_rows(url, "20032062")[0][2] == []

    def test_dry_run_reports_without_writing(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20033889", date(2026, 2, 9), [(date(2026, 12, 26), date(2026, 1, 21))])
        report = _recompute(url, as_of=date(2026, 2, 9), dry_run=True)
        assert report.dry_run is True
        assert report.examined == 1
        assert report.changed == 1
        assert report.affected_doc_ids == ["20033889"]
        assert report.flag_counts[AFTER_NOTIFICATION] == 1
        # Nothing was written.
        assert _txn_rows(url, "20033889")[0][2] == []

    def test_repeated_recomputation_is_idempotent(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20033889", date(2026, 2, 9), [(date(2026, 12, 26), date(2026, 1, 21))])
        first = _recompute(url, as_of=date(2026, 2, 9))
        assert first.changed == 1
        flags_after_first = _txn_rows(url, "20033889")[0][2]

        second = _recompute(url, as_of=date(2026, 2, 9))
        assert second.examined == 1
        assert second.changed == 0
        assert second.affected_doc_ids == []
        assert _txn_rows(url, "20033889")[0][2] == flags_after_first

    def test_source_independent(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20033889", date(2026, 2, 9), [(date(2026, 12, 26), date(2026, 1, 21))])
        _seed(
            url,
            "fda235b3-bad7-4637-8fa1-053f354d929c",
            date(2026, 2, 9),
            [(date(2026, 12, 26), date(2026, 1, 21))],
            source="senate_efd",
        )
        report = _recompute(url, as_of=date(2026, 2, 9))
        assert report.examined == 2
        assert report.changed == 2
        assert sorted(report.affected_doc_ids) == [
            "20033889",
            "fda235b3-bad7-4637-8fa1-053f354d929c",
        ]

    def test_partial_recompute_updates_only_changed_rows(self, temp_database_url: str):
        url = temp_database_url
        _seed(url, "20033889", date(2026, 2, 9), [(date(2026, 12, 26), date(2026, 1, 21))])
        _seed(url, "20032062", date(2025, 9, 10), [(date(2025, 7, 28), date(2025, 8, 11))])
        report = _recompute(url, as_of=date(2026, 2, 9))
        assert report.examined == 2
        assert report.changed == 1
        assert report.affected_doc_ids == ["20033889"]
        assert _txn_rows(url, "20033889")[0][2] == [
            AFTER_NOTIFICATION,
            AFTER_FILING,
            AFTER_INGESTION_DATE,
        ]
        assert _txn_rows(url, "20032062")[0][2] == []


class TestRecomputeCli:
    def test_recompute_requires_as_of(self):
        assert main(["--recompute-flags", "--database-url", "postgresql://nowhere"]) == 2

    def test_recompute_rejects_backfill(self):
        assert (
            main(
                [
                    "--recompute-flags",
                    "--as-of",
                    "2026-09-23",
                    "--backfill",
                    "--database-url",
                    "postgresql://nowhere",
                ]
            )
            == 2
        )

    def test_recompute_rejects_year(self):
        assert (
            main(
                [
                    "--recompute-flags",
                    "--as-of",
                    "2026-09-23",
                    "--year",
                    "2025",
                    "--database-url",
                    "postgresql://nowhere",
                ]
            )
            == 2
        )