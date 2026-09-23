"""Stage 3 integration tests for Senate eFD ingestion through the pipeline.

These exercise the source -> runner -> storage boundary chosen for Stage 3:
the Senate source produces an index of electronic and paper PTRs; the runner
classifies, acquires detail pages, normalizes transactions, and persists them
with ``senate_efd`` provenance. Paper (scanned) filings must never be stored
or fabricated.

Two tiers, mirroring the rest of the suite:
- Hermetic: no socket, no database. The runner's collaborators (store,
  existence check, run accounting) are in-memory fakes.
- Database-gated (``temp_database_url``): throws away a Postgres database,
  applies migrations, and runs the real store/API against it.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.runner import run_senate_ingestion
from politician_dashboard.ingest.sources.senate_efd import (
    SenateEfdSource,
    view_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "senate"

ELECTRONIC_IDS = (
    "fda235b3-bad7-4637-8fa1-053f354d929c",
    "b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f",
)


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class _SenateReplayTransport:
    """Replays landing/agreement/senators plus listing and detail pages.

    The listing is served once; every later page is empty so the adapter's
    recordsTotal-driven paging terminates after the real fixture rows.
    """

    def __init__(self, listing_page: str, detail_pages: dict[str, bytes]) -> None:
        self._listing = _load(listing_page)
        self._detail_pages = detail_pages
        self._served_listing = False
        self.post_urls: list[str] = []
        self.view_gets: list[str] = []

    def get(self, url: str) -> bytes:
        for view_id, data in self._detail_pages.items():
            if f"/{view_id}/" in url:
                self.view_gets.append(url)
                return data
        if "contact_information" in url:
            return _load("senators_cfm.xml")
        if "home" in url:
            return _load("landing.html")
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, body: bytes, headers=None) -> bytes:
        self.post_urls.append(url)
        if "home" in url:
            return b"<html>accepted</html>"
        if "data" in url:
            if not self._served_listing:
                self._served_listing = True
                return self._listing
            return _load("listing_empty.json")
        raise AssertionError(f"unexpected POST {url}")


def _electronic_transport() -> _SenateReplayTransport:
    detail = _load("ptr_view_electronic.html")
    return _SenateReplayTransport(
        "listing_page1.json", {view_id: detail for view_id in ELECTRONIC_IDS}
    )


def _paper_transport() -> _SenateReplayTransport:
    return _SenateReplayTransport("listing_page2.json", {})


class _SenateHarness:
    """In-memory runner collaborators recording every pipeline call."""

    def __init__(self, source: SenateEfdSource) -> None:
        self.source = source
        self.stored: list[dict] = []
        self.calls: dict[str, list] = {"create_run": [], "filing_exists": []}
        self.finished: list[tuple[int, str, object]] = []
        self._exists_impl = lambda conn, doc_id: False
        self._store_impl = None
        self._next_run_id = itertools.count(11)

    def connector(self):
        return object()

    def store(self, conn, *, filing, transactions, raw_pdf, pdf_url, doc_kind, source):
        if self._store_impl is not None:
            return self._store_impl(
                conn,
                filing=filing,
                transactions=transactions,
                raw_pdf=raw_pdf,
                pdf_url=pdf_url,
                doc_kind=doc_kind,
                source=source,
            )
        self.stored.append(
            {
                "conn": conn,
                "filing": filing,
                "transactions": transactions,
                "raw_pdf": raw_pdf,
                "pdf_url": pdf_url,
                "doc_kind": doc_kind,
                "source": source,
            }
        )
        return True

    def filing_exists(self, conn, doc_id: str) -> bool:
        self.calls["filing_exists"].append(doc_id)
        return self._exists_impl(conn, doc_id)

    def create_run(self, conn, year, started_at, source) -> int:
        self.calls["create_run"].append((year, source))
        return next(self._next_run_id)

    def finish_run(self, conn, run_id, status, result) -> None:
        self.finished.append((run_id, status, result))

    def execute(self, year: int = 2026) -> object:
        return run_senate_ingestion(
            year=year,
            conn=self.connector(),
            source=self.source,
            store=self.store,
            filing_exists=self.filing_exists,
            create_run=self.create_run,
            finish_run=self.finish_run,
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )


class TestSenateElectronicFlow:
    def test_stores_efiled_filings_with_senate_provenance(self) -> None:
        source = SenateEfdSource(transport=_electronic_transport())
        h = _SenateHarness(source)
        result = h.execute()

        assert result.status == "success"
        assert result.filings_indexed == 2
        assert result.filings_new == 2
        assert result.transactions_stored == 2
        assert result.download_failed == 0
        assert result.parse_failed == 0

        assert h.calls["create_run"] == [(2026, "senate_efd")]
        assert len(h.stored) == 2
        assert all(s["source"] == "senate_efd" for s in h.stored)
        assert all(s["doc_kind"] == "efiled" for s in h.stored)
        assert all(b"Williams Companies" in s["raw_pdf"] for s in h.stored)

        by_doc = {s["filing"].doc_id: s for s in h.stored}
        assert set(by_doc) == set(ELECTRONIC_IDS)

    def test_transactions_normalized_from_detail_table(self) -> None:
        source = SenateEfdSource(transport=_electronic_transport())
        h = _SenateHarness(source)
        h.execute()

        stored = h.stored[0]
        assert stored["filing"].state_district == "OK00"
        (tx,) = stored["transactions"]
        assert isinstance(tx.asset_name, str)
        assert tx.asset_name.startswith("Williams Companies")
        assert tx.ticker == "WMB"
        assert tx.txn_type == "S"
        assert tx.txn_date == date(2026, 6, 22)
        assert tx.amount_min == 250001
        assert tx.amount_max == 500000
        assert tx.amount_raw == "$250,001 - $500,000"
        assert tx.owner_token == "Joint"
        assert tx.asset_type_code == "Stock Option"
        assert tx.notes == "--"

    def test_notification_date_normalized_from_listing_date_received(
        self,
    ) -> None:
        source = SenateEfdSource(transport=_electronic_transport())
        h = _SenateHarness(source)
        h.execute()

        # filing_date comes from each listing row's "Date Received"; the
        # detail table has no notification date, so it is carried through.
        by_doc = {s["filing"].doc_id: s for s in h.stored}
        fda = by_doc[ELECTRONIC_IDS[0]]
        b999 = by_doc[ELECTRONIC_IDS[1]]
        assert fda["filing"].filing_date == date(2026, 7, 21)
        assert fda["transactions"][0].notification_date == date(2026, 7, 21)
        assert b999["filing"].filing_date == date(2026, 9, 17)
        assert b999["transactions"][0].notification_date == date(2026, 9, 17)

    def test_document_url_points_at_senate_view(self) -> None:
        source = SenateEfdSource(transport=_electronic_transport())
        h = _SenateHarness(source)
        h.execute()
        urls = {s["pdf_url"] for s in h.stored}
        assert urls == {view_url(i) for i in ELECTRONIC_IDS}

    def test_detail_pages_fetched_via_view_get(self) -> None:
        transport = _electronic_transport()
        source = SenateEfdSource(transport=transport)
        h = _SenateHarness(source)
        h.execute()
        # agreement accepted once during index, then one GET per filing
        assert len([u for u in transport.post_urls if "home" in u]) == 1
        assert set(transport.view_gets) == {view_url(i) for i in ELECTRONIC_IDS}


class TestSenatePaperFlow:
    def test_paper_filings_skipped_without_storage(self) -> None:
        transport = _paper_transport()
        source = SenateEfdSource(transport=transport)
        h = _SenateHarness(source)
        result = h.execute()

        assert result.status == "success"
        assert result.filings_indexed == 2
        assert result.scanned_skipped == 2
        assert result.filings_new == 0
        assert result.transactions_stored == 0
        assert h.stored == []
        assert h.calls["filing_exists"] == []
        # no detail page was fetched for a paper filing
        assert transport.view_gets == []
        assert h.calls["create_run"] == [(2026, "senate_efd")]

    def test_paper_filings_resolve_mo00_ga00(self) -> None:
        # Hawley and Ossoff are the paper index rows; fetch_index resolves
        # both senators from the official listing (MO / GA) with the 00
        # pseudo-district suffix.
        source = SenateEfdSource(transport=_paper_transport())
        by_last = {f.last: f for f in source.fetch_index(year=2026)}
        assert by_last["Hawley"].state_district == "MO00"
        assert by_last["Ossoff"].state_district == "GA00"


class TestSenateRunnerAccounting:
    def test_existing_doc_arg_is_skipped_before_fetch(self) -> None:
        source = SenateEfdSource(transport=_electronic_transport())
        h = _SenateHarness(source)
        h._exists_impl = lambda conn, doc_id: doc_id == ELECTRONIC_IDS[0]

        result = h.execute()

        assert result.filings_skipped == 1
        assert result.filings_new == 1
        assert result.transactions_stored == 1
        assert {s["filing"].doc_id for s in h.stored} == {ELECTRONIC_IDS[1]}

    def test_run_is_idempotent_when_docs_already_stored(self) -> None:
        source = SenateEfdSource(transport=_electronic_transport())
        h = _SenateHarness(source)
        first = h.execute()
        assert first.filings_new == 2

        # second run against the same source re-fetches the index
        source2 = SenateEfdSource(transport=_electronic_transport())
        h2 = _SenateHarness(source2)
        h2._exists_impl = lambda conn, doc_id: True
        second = h2.execute()
        assert second.status == "success"
        assert second.filings_new == 0
        assert second.filings_skipped == 2
        assert h2.stored == []


class TestSenateCliSelection:
    def test_parser_defaults_to_house(self) -> None:
        from politician_dashboard.ingest.__main__ import build_parser

        args = build_parser().parse_args(["--year", "2026"])
        assert args.source == "house"
        assert args.year == 2026

    def test_parser_accepts_senate(self) -> None:
        from politician_dashboard.ingest.__main__ import build_parser

        args = build_parser().parse_args(["--source", "senate"])
        assert args.source == "senate"

    def test_parser_rejects_unknown_source(self) -> None:
        from politician_dashboard.ingest.__main__ import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["--source", "mars"])

    def test_main_dispatches_senate(self, monkeypatch) -> None:
        import politician_dashboard.ingest.__main__ as cli
        from politician_dashboard.ingest.runner import IngestionResult

        called: list[tuple] = []

        def fake_senate(**kwargs):
            called.append(("senate", kwargs))
            return IngestionResult(year=kwargs["year"], status="success")

        def fake_house(**kwargs):
            called.append(("house", kwargs))
            return IngestionResult(year=kwargs["year"], status="success")

        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(cli, "psycopg", type("psycopg", (), {"connect": lambda *a, **k: _FakeConn()}))
        monkeypatch.setattr(cli, "run_senate_ingestion", fake_senate)
        monkeypatch.setattr(cli, "run_ingestion", fake_house)

        rc = cli.main(["--source", "senate", "--year", "2026", "--database-url", "postgresql://x"])
        assert rc == 0
        assert [name for name, _ in called] == ["senate"]
        assert called[0][1]["year"] == 2026

    def test_main_dispatches_house_by_default(self, monkeypatch) -> None:
        import politician_dashboard.ingest.__main__ as cli
        from politician_dashboard.ingest.runner import IngestionResult

        called: list[str] = []

        def fake_senate(**kwargs):
            called.append("senate")
            return IngestionResult(year=kwargs["year"], status="success")

        def fake_house(**kwargs):
            called.append("house")
            return IngestionResult(year=kwargs["year"], status="success")

        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(cli, "psycopg", type("psycopg", (), {"connect": lambda *a, **k: _FakeConn()}))
        monkeypatch.setattr(cli, "run_senate_ingestion", fake_senate)
        monkeypatch.setattr(cli, "run_ingestion", fake_house)

        cli.main(["--year", "2026", "--database-url", "postgresql://x"])
        assert called == ["house"]

    def test_run_senate_ingestion_defaults_to_senate_source(self, monkeypatch) -> None:
        import politician_dashboard.ingest.runner as runner_mod
        from politician_dashboard.ingest.runner import run_senate_ingestion as real_ingest

        fake_source = SenateEfdSource(transport=_electronic_transport())
        monkeypatch.setattr(runner_mod, "SenateEfdSource", lambda: fake_source)

        h = _SenateHarness(source=fake_source)
        result = real_ingest(
            year=2026,
            conn=h.connector(),
            store=h.store,
            filing_exists=h.filing_exists,
            create_run=h.create_run,
            finish_run=h.finish_run,
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        assert result.status == "success"
        assert result.filings_new == 2
        assert h.calls["create_run"] == [(2026, "senate_efd")]
        assert all(s["source"] == "senate_efd" for s in h.stored)


class TestSenateDatabase:
    """Full Senate runs against a disposable Postgres database."""

    def test_full_run_persists_senate_provenance(
        self, temp_database_url: str
    ) -> None:
        import psycopg

        source = SenateEfdSource(transport=_electronic_transport())
        with psycopg.connect(temp_database_url, autocommit=True) as conn:
            result = run_senate_ingestion(year=2026, conn=conn, source=source)

        assert result.status == "success"
        assert result.filings_new == 2
        assert result.transactions_stored == 2

        with psycopg.connect(temp_database_url) as conn:
            filing_rows = conn.execute(
                "SELECT doc_id, source, state_district, doc_kind "
                "FROM filings ORDER BY doc_id"
            ).fetchall()
            run_rows = conn.execute(
                "SELECT source, status FROM ingest_runs"
            ).fetchall()
            txn_rows = conn.execute(
                "SELECT notification_date, txn_type, ticker, notes "
                "FROM transactions ORDER BY id"
            ).fetchall()

        assert len(filing_rows) == 2
        assert all(row[1] == "senate_efd" for row in filing_rows)
        assert all(row[2] == "OK00" for row in filing_rows)
        assert all(row[3] == "efiled" for row in filing_rows)

        assert run_rows == [("senate_efd", "success")]

        # notification_date normalized from the listing "Date Received"
        assert sorted(row[0] for row in txn_rows) == [
            date(2026, 7, 21),
            date(2026, 9, 17),
        ]
        assert all(row[1] == "S" for row in txn_rows)
        assert all(row[2] == "WMB" for row in txn_rows)
        assert all(row[3] == "--" for row in txn_rows)  # comment preserved

    def test_repeat_run_only_skips_existing(self, temp_database_url: str) -> None:
        import psycopg

        source = SenateEfdSource(transport=_electronic_transport())
        with psycopg.connect(temp_database_url, autocommit=True) as conn:
            first = run_senate_ingestion(year=2026, conn=conn, source=source)
        assert first.filings_new == 2

        # a fresh source re-fetches the index; the stored doc_ids are skipped
        second_source = SenateEfdSource(transport=_electronic_transport())
        with psycopg.connect(temp_database_url, autocommit=True) as conn:
            second = run_senate_ingestion(year=2026, conn=conn, source=second_source)
        assert second.status == "success"
        assert second.filings_indexed == 2
        assert second.filings_new == 0
        assert second.filings_skipped == 2
        assert second.transactions_stored == 0

        with psycopg.connect(temp_database_url) as conn:
            count = conn.execute("SELECT count(*) FROM filings").fetchone()[0]
            txn_count = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
        assert count == 2
        assert txn_count == 2


class TestSenateApiCompat:
    """The read API serves Senate data with the <STATE>00 convention."""

    def test_senate_politician_round_trips(self, temp_database_url: str) -> None:
        import psycopg
        from fastapi.testclient import TestClient

        from politician_dashboard.api import create_app
        from politician_dashboard.api.politicians import politician_id
        from politician_dashboard.ingest.store import store_filing

        doc_id = "fda235b3-bad7-4637-8fa1-053f354d929c"
        filing = Filing(
            prefix="",
            last="Armstrong",
            first="Alan",
            suffix="",
            filing_type="P",
            state_district="OK00",
            year=2026,
            filing_date=date(2026, 7, 21),
            doc_id=doc_id,
        )
        transaction = Transaction(
            sequence=0,
            asset_name="Williams Companies, Inc. (The) Common Stock",
            txn_type="S",
            txn_date=date(2026, 6, 22),
            notification_date=date(2026, 7, 21),
            amount_min=250001,
            amount_max=500000,
            amount_raw="$250,001 - $500,000",
            owner_token="Joint",
            ticker="WMB",
            asset_type_code="Stock Option",
            notes="--",
        )
        pid = politician_id("OK00", "Alan", "Armstrong")
        assert pid == "ok00_alan_armstrong"

        with psycopg.connect(temp_database_url, autocommit=True) as conn:
            store_filing(
                conn,
                filing=filing,
                transactions=[transaction],
                raw_pdf=b"<html>detail</html>",
                pdf_url=view_url(doc_id),
                doc_kind="efiled",
                source="senate_efd",
            )

        app = create_app(database_url=temp_database_url)
        with TestClient(app) as client:
            resp = client.get(f"/politicians/{pid}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["state"] == "OK"
            assert body["district"] == "00"
            assert body["state_district"] == "OK00"
            assert body["filing_count"] == 1
            assert body["transaction_count"] == 1

            txn_resp = client.get(f"/politicians/{pid}/transactions")
            assert txn_resp.status_code == 200
            items = txn_resp.json()["items"]
            assert len(items) == 1
            assert items[0]["politician_id"] == pid
            assert items[0]["ticker"] == "WMB"

            filing_resp = client.get(f"/filings/{doc_id}")
            assert filing_resp.status_code == 200
            assert filing_resp.json()["state_district"] == "OK00"
            assert "raw_pdf" not in filing_resp.json()