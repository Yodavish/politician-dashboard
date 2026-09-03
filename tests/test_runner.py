"""Tests for the ingestion runner orchestration.

These exercise the runner's control flow with mocked source, downloader,
parser, store, and run-accounting collaborators, and therefore do not require
a live PostgreSQL server or network access.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, timezone

import pytest

from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.parser import ParseError, ScannedPdfError
from politician_dashboard.ingest.runner import (
    IngestionResult,
    _to_transactions,
    run_ingestion,
)
from politician_dashboard.ingest.__main__ import resolve_years
from politician_dashboard.ingest.sources.house_clerk import classify_doc_id
from politician_dashboard.ingest.store import StoreError


def _filing(doc_id: str, year: int = 2025) -> Filing:
    return Filing(
        prefix="Hon.",
        last="Aderholt",
        first="Robert",
        suffix="",
        filing_type="P",
        state_district="AL04",
        year=year,
        filing_date=date(2025, 9, 10),
        doc_id=doc_id,
    )


class _FakeSource:
    """In-memory source returning a fixed set of PTR filings."""

    def __init__(self, filings: list[Filing]) -> None:
        self._filings = filings
        self.calls: list[str] = []

    def fetch_ptrs(self, year: int) -> list[Filing]:
        self.calls.append(f"fetch_ptrs:{year}")
        return [f for f in self._filings if f.year == year]

    def pdf_url(self, year: int, doc_id: str) -> str:
        return f"https://example.invalid/{year}/{doc_id}.pdf"


class _Harness:
    """Collects the injectable collaborators and records their calls."""

    def __init__(self, source: _FakeSource):
        self.source = source
        self.downloaded: list[str] = []
        self.parsed: list[bytes] = []
        self.stored: list[dict] = []
        self.exists_checks: list[str] = []
        self.finished: list[tuple[int, str, IngestionResult]] = []
        self._next_run_id = itertools.count(7)
        self.downloader_impl = lambda url: b"%PDF-1.4 fake"
        self.parser_impl = lambda pdf: {
            "filing_id": "20032062",
            "transactions": [
                {
                    "source_id": None,
                    "owner": "SP",
                    "asset_name": "GSK plc (GSK)",
                    "ticker": "GSK",
                    "asset_type_code": "ST",
                    "txn_type": "S",
                    "txn_date": date(2025, 7, 28),
                    "notification_date": date(2025, 8, 11),
                    "amount_min": 1001,
                    "amount_max": 15000,
                    "amount_raw": "$1,001 - $15,000",
                }
            ],
        }
        self._store_impl = None
        self._filing_exists_impl = lambda conn, doc_id: False

    def connector(self):
        return object()

    def downloader(self, url: str) -> bytes:
        self.downloaded.append(url)
        result = self.downloader_impl(url)
        if isinstance(result, Exception):
            raise result
        return result

    def parser(self, pdf: bytes) -> dict:
        self.parsed.append(pdf)
        result = self.parser_impl(pdf)
        if isinstance(result, Exception):
            raise result
        return result

    def store(self, conn, *, filing, transactions, raw_pdf, pdf_url, doc_kind):
        if self._store_impl is not None:
            return self._store_impl(
                conn,
                filing=filing,
                transactions=transactions,
                raw_pdf=raw_pdf,
                pdf_url=pdf_url,
                doc_kind=doc_kind,
            )
        self.stored.append(
            {
                "conn": conn,
                "filing": filing,
                "transactions": transactions,
                "raw_pdf": raw_pdf,
                "pdf_url": pdf_url,
                "doc_kind": doc_kind,
            }
        )
        return True

    def filing_exists(self, conn, doc_id: str) -> bool:
        self.exists_checks.append(doc_id)
        return self._filing_exists_impl(conn, doc_id)

    def create_run(self, conn, year, started_at) -> int:
        return next(self._next_run_id)

    def finish_run(self, conn, run_id, status, result) -> None:
        self.finished.append((run_id, status, result))

    def execute(self, year: int = 2025) -> IngestionResult:
        self.result = run_ingestion(
            year=year,
            conn=self.connector(),
            source=self.source,
            downloader=self.downloader,
            parser=self.parser,
            store=self.store,
            filing_exists=self.filing_exists,
            create_run=self.create_run,
            finish_run=self.finish_run,
            now=datetime(2025, 9, 1, tzinfo=timezone.utc),
        )
        return self.result


def _harness(filings: list[Filing] | None = None) -> _Harness:
    return _Harness(_FakeSource(filings or [_filing("20032062")]))


class TestRunIngestion:
    def test_successful_run_stores_filing_and_transactions(self) -> None:
        h = _harness()
        result = h.execute()
        assert result.status == "success"
        assert result.run_id == 7
        assert result.filings_indexed == 1
        assert result.filings_new == 1
        assert result.filings_skipped == 0
        assert result.transactions_stored == 1
        assert len(h.downloaded) == 1
        assert len(h.stored) == 1

        stored = h.stored[0]
        assert stored["filing"].doc_id == "20032062"
        assert stored["raw_pdf"] == b"%PDF-1.4 fake"
        assert stored["pdf_url"] == "https://example.invalid/2025/20032062.pdf"
        assert stored["doc_kind"] == "efiled"
        # Transaction conversion produced a Transaction model
        assert len(stored["transactions"]) == 1
        tx = stored["transactions"][0]
        assert isinstance(tx, Transaction)
        assert tx.sequence == 0
        assert tx.asset_name == "GSK plc (GSK)"
        assert tx.txn_type == "S"
        assert tx.amount_min == 1001
        assert tx.amount_max == 15000

        # Run accounting persisted
        assert h.finished == [(7, "success", result)]

    def test_scanned_filings_skipped_without_download(self) -> None:
        # A 7-digit DocID is classified as scanned (paper).
        h = _harness(filings=[_filing("8220747")])
        result = h.execute()
        assert classify_doc_id("8220747") == "scanned"
        assert result.scanned_skipped == 1
        assert result.status == "success"
        assert h.downloaded == []
        assert h.stored == []

    def test_scanned_pdf_raised_by_parser_counts_scanned(self) -> None:
        h = _harness()

        def raiser(pdf: bytes) -> dict:
            raise ScannedPdfError("no text")

        h.parser_impl = raiser
        result = h.execute()
        assert result.scanned_skipped == 1
        assert result.status == "success"
        assert h.stored == []

    def test_existing_doc_id_skipped_before_download(self) -> None:
        h = _harness()
        h._filing_exists_impl = lambda conn, doc_id: True
        result = h.execute()
        assert result.filings_skipped == 1
        assert result.filings_new == 0
        assert result.status == "success"
        assert h.downloaded == []
        assert h.stored == []

    def test_download_failure_continues_and_marks_partial(self) -> None:
        h = _harness(filings=[_filing("20032062"), _filing("20026537")])
        fail_once = []

        def slow(url: str) -> bytes:
            fail_once.append(url)
            if len(fail_once) == 1:
                raise OSError("network down")
            return b"%PDF-1.4 fake"

        h.downloader_impl = slow
        result = h.execute()
        assert result.download_failed == 1
        assert result.filings_new == 1
        assert result.status == "partial"

    def test_parse_failure_continues_and_marks_partial(self) -> None:
        h = _harness(filings=[_filing("20032062"), _filing("20026537")])
        h.parser_impl = ParseError("bad pdf")
        result = h.execute()
        assert result.parse_failed == 2
        assert result.filings_new == 0
        assert result.status == "partial"

    def test_store_failure_continues_and_marks_partial(self) -> None:
        h = _harness()

        def boom(*args, **kwargs) -> bool:
            raise StoreError("db busy")

        h._store_impl = boom
        result = h.execute()
        assert result.store_failed == 1
        assert result.status == "partial"
        assert h.finished[-1][1] == "partial"
        assert "20032062" in " ".join(result.store_failure_details)

    def test_index_failure_fails_the_run(self) -> None:
        class BoomSource(_FakeSource):
            def fetch_ptrs(self, year: int):
                raise RuntimeError("index 404")

        harness = _Harness(BoomSource([]))
        result = harness.execute()
        assert result.status == "failed"
        assert "index 404" in (result.error or "")
        assert harness.finished[0][1] == "failed"

    def test_store_returns_false_counts_as_skipped(self) -> None:
        h = _harness()
        h._store_impl = lambda *a, **k: False
        result = h.execute()
        assert result.filings_skipped == 1
        assert result.filings_new == 0
        assert result.status == "success"

    def test_multiple_years_run_separately(self) -> None:
        # Two filings across two different years; each year is its own run.
        filings = [_filing("20032062", year=2025), _filing("20022986", year=2023)]
        h = _harness(filings=filings)
        result = h.execute(year=2025)
        assert h.source.calls == ["fetch_ptrs:2025"]
        assert result.filings_indexed == 1


class TestToTransactions:
    def test_converts_parser_dicts(self) -> None:
        parsed = {
            "transactions": [
                {
                    "source_id": "2000086356",
                    "owner": "SP",
                    "asset_name": "3M Company (MMM)",
                    "ticker": "MMM",
                    "asset_type_code": "ST",
                    "txn_type": "S (partial)",
                    "txn_date": date(2020, 5, 14),
                    "notification_date": date(2020, 5, 20),
                    "amount_min": 15001,
                    "amount_max": 50000,
                    "amount_raw": "$15,001 - $50,000",
                }
            ]
        }
        txns = _to_transactions(parsed)
        assert len(txns) == 1
        tx = txns[0]
        assert tx.sequence == 0
        assert tx.txn_source_id == "2000086356"
        assert tx.owner_token == "SP"
        assert tx.txn_type == "S (partial)"
        assert tx.amount_raw == "$15,001 - $50,000"


class TestResolveYears:
    def test_default_is_current_year(self) -> None:
        assert resolve_years(None, False, 2011, current_year=2025) == [2025]

    def test_explicit_year(self) -> None:
        assert resolve_years(2023, False, 2011, current_year=2025) == [2023]

    def test_backfill_range(self) -> None:
        assert resolve_years(None, True, 2023, current_year=2025) == [2023, 2024, 2025]

    def test_backfill_since_after_current_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_years(None, True, 2026, current_year=2025)
