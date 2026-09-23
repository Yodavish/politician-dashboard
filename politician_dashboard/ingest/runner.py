"""Ingestion runner.

Orchestrates the end-to-end pipeline for a single calendar year and source:

    DisclosureSource.fetch_ptrs(year)
    -> classify doc_id (efiled vs scanned)
    -> skip already-stored doc_ids
    -> acquire filing (source-specific download + parse)
    -> store_filing()

The runner is source-agnostic: a :class:`FilingAcquirer` owns the source-
specific download/parse/classification, so the same orchestration drives the
House Clerk (PDF text layer) and Senate eFD (HTML detail page) sources and
persists source provenance with every filing and run.

Failure semantics (per AGENTS.md):
- A failed yearly index download fails the run (status ``failed``).
- Individual document download / parse / store failures are counted and the
  run continues; the run is marked ``partial`` if any occurred.
- Each filing and all of its transactions are written atomically by
  :func:`store_filing`.

All collaborators (source, acquirer, store, existence check and
run-accounting) are injectable so the orchestration can be tested without a
live database or network.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import psycopg

from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.parser import ScannedPdfError, parse_ptr_pdf
from politician_dashboard.ingest.sources.base import DisclosureSource
from politician_dashboard.ingest.sources.house_clerk import (
    HouseClerkSource,
    classify_doc_id,
    download_pdf,
)
from politician_dashboard.ingest.sources.senate_efd import (
    SenateEfdSource,
    classify_senate_doc_id,
    parse_ptr_view_html,
    view_url,
)
from politician_dashboard.ingest.store import StoreError, store_filing

logger = logging.getLogger(__name__)

RunStatus = str

class AcquisitionError(RuntimeError):
    """Base class for failures acquiring a single filing's document."""


class AcquisitionDownloadError(AcquisitionError):
    """Raised when a filing's source document cannot be downloaded."""


class AcquisitionParseError(AcquisitionError):
    """Raised when a filing's source document cannot be parsed."""


class ScannedDocumentError(AcquisitionError):
    """Raised when a filing's source document is a textless scan."""


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    """Normalized transactions plus the source document for one filing."""

    transactions: list[Transaction]
    document_url: str
    raw_document: bytes | None
    doc_kind: str


@dataclass
class IngestionResult:
    """Outcome of ingesting a single year."""

    year: int
    run_id: int | None = None
    status: RunStatus = "failed"
    error: str | None = None
    filings_indexed: int = 0
    filings_new: int = 0
    filings_skipped: int = 0
    scanned_skipped: int = 0
    download_failed: int = 0
    parse_failed: int = 0
    store_failed: int = 0
    transactions_stored: int = 0
    store_failure_details: list[str] = field(default_factory=list)


def _create_run(
    conn: psycopg.Connection,
    year: int,
    started_at: datetime,
    source: str = "house_clerk",
) -> int:
    row = conn.execute(
        "INSERT INTO ingest_runs "
        "(started_at, status, year_targets, filings_indexed, source) "
        "VALUES (%s, 'failed', %s, 0, %s) RETURNING id",
        (started_at, [str(year)], source),
    ).fetchone()
    assert row is not None
    return row[0]


def _finish_run(
    conn: psycopg.Connection,
    run_id: int,
    status: RunStatus,
    result: IngestionResult,
) -> None:
    error_text = result.error
    if result.store_failure_details:
        detail = "; ".join(result.store_failure_details)
        error_text = (error_text + "; " if error_text else "") + f"store_failures={detail}"
    conn.execute(
        """
        UPDATE ingest_runs SET
            finished_at = now(),
            status = %s,
            filings_indexed = %s,
            filings_new = %s,
            filings_skipped = %s,
            scanned_skipped = %s,
            download_failed = %s,
            parse_failed = %s,
            transactions_stored = %s,
            error = %s
        WHERE id = %s
        """,
        (
            status,
            result.filings_indexed,
            result.filings_new,
            result.filings_skipped,
            result.scanned_skipped,
            result.download_failed,
            result.parse_failed,
            result.transactions_stored,
            error_text,
            run_id,
        ),
    )


def _filing_exists(conn: psycopg.Connection, doc_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM filings WHERE doc_id = %s", (doc_id,)
    ).fetchone()
    return row is not None


def _to_transactions(parsed: dict) -> list[Transaction]:
    """Convert parser transaction dicts into :class:`Transaction` models."""
    transactions: list[Transaction] = []
    for index, item in enumerate(parsed.get("transactions", [])):
        transactions.append(
            Transaction(
                sequence=index,
                asset_name=item.get("asset_name") or "",
                txn_type=item.get("txn_type") or "",
                txn_date=item["txn_date"],
                notification_date=item["notification_date"],
                amount_min=item.get("amount_min") or 0,
                amount_max=item.get("amount_max") or 0,
                amount_raw=item.get("amount_raw") or "",
                txn_source_id=item.get("source_id"),
                owner_token=item.get("owner"),
                ticker=item.get("ticker"),
                asset_type_code=item.get("asset_type_code"),
                notes=item.get("notes"),
            )
        )
    return transactions


class FilingAcquirer(ABC):
    """Source-specific acquisition of a single filing into normalized form.

    ``classify`` maps a filing's doc_id to ``efiled`` or ``scanned``;
    ``acquire`` downloads and parses the filing's source document and returns
    an :class:`AcquisitionResult`. Acquisition failures are raised as
    :class:`AcquisitionDownloadError`, :class:`AcquisitionParseError`, or
    :class:`ScannedDocumentError` so the shared engine can account for them.
    """

    source_name: str = "unnamed"

    @abstractmethod
    def classify(self, doc_id: str) -> str:
        """Return ``efiled`` or ``scanned`` for ``doc_id``."""

    @abstractmethod
    def acquire(self, filing: Filing) -> AcquisitionResult:
        """Download and parse one filing into normalized form."""


class HouseFilingAcquirer(FilingAcquirer):
    """Acquires House Clerk disclosures (PTR PDFs with a text layer)."""

    source_name = "house_clerk"

    def __init__(
        self,
        source: DisclosureSource,
        downloader: Callable[[str], bytes] | None = None,
        parser: Callable[[bytes], dict] | None = None,
    ) -> None:
        self._source = source
        self._downloader = downloader or download_pdf
        self._parser = parser or parse_ptr_pdf

    def classify(self, doc_id: str) -> str:
        return classify_doc_id(doc_id)

    def acquire(self, filing: Filing) -> AcquisitionResult:
        url = self._source.pdf_url(filing.year, filing.doc_id)
        try:
            raw = self._downloader(url)
        except Exception as exc:  # noqa: BLE001 - transport details vary
            raise AcquisitionDownloadError(
                f"download failed for {filing.doc_id}: {exc}"
            ) from exc

        try:
            parsed = self._parser(raw)
        except ScannedPdfError as exc:
            raise ScannedDocumentError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - includes ParseError
            raise AcquisitionParseError(
                f"parse failed for {filing.doc_id}: {exc}"
            ) from exc

        return AcquisitionResult(
            transactions=_to_transactions(parsed),
            document_url=url,
            raw_document=raw,
            doc_kind=self.classify(filing.doc_id),
        )


class SenateFilingAcquirer(FilingAcquirer):
    """Acquires Senate eFD disclosures (electronic HTML detail pages).

    Paper (scanned) filings carry no text layer; acquiring one raises
    :class:`ScannedDocumentError` rather than fabricating a parse. The detail
    page has no notification date, so ``notification_date`` is normalized from
    the listing's "Date Received" (the filing date) per the Senate contract.
    """

    source_name = "senate_efd"

    def __init__(
        self,
        source: DisclosureSource | None = None,
        parser: Callable[[bytes], list[dict]] | None = None,
    ) -> None:
        self._source = source or SenateEfdSource()
        self._parser = parser or parse_ptr_view_html

    def classify(self, doc_id: str) -> str:
        return classify_senate_doc_id(doc_id)

    def acquire(self, filing: Filing) -> AcquisitionResult:
        if self.classify(filing.doc_id) == "scanned":
            raise ScannedDocumentError(
                "paper filing has no text layer "
                f"(view /search/view/paper/{filing.doc_id}/)"
            )
        url = view_url(filing.doc_id)
        try:
            raw = self._source.fetch_detail(filing.doc_id)
        except Exception as exc:  # noqa: BLE001 - transport details vary
            raise AcquisitionDownloadError(
                f"download failed for {filing.doc_id}: {exc}"
            ) from exc

        try:
            rows = self._parser(raw)
            for item in rows:
                item["notification_date"] = filing.filing_date
            transactions = _to_transactions({"transactions": rows})
        except AcquisitionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AcquisitionParseError(
                f"parse failed for {filing.doc_id}: {exc}"
            ) from exc

        return AcquisitionResult(
            transactions=transactions,
            document_url=url,
            raw_document=raw,
            doc_kind="efiled",
        )


def _run_year(
    *,
    year: int,
    conn: psycopg.Connection,
    source: DisclosureSource,
    acquirer: FilingAcquirer,
    store: Callable[..., bool],
    filing_exists: Callable[[psycopg.Connection, str], bool],
    create_run: Callable[..., int],
    finish_run: Callable[..., None],
    started_at: datetime,
) -> IngestionResult:
    """Shared per-source engine for the source -> store pipeline."""
    result = IngestionResult(year=year)
    result.run_id = create_run(conn, year, started_at, acquirer.source_name)

    try:
        filings = source.fetch_ptrs(year)
    except Exception as exc:  # noqa: BLE001 - index failure fails the run
        result.status = "failed"
        result.error = f"failed to fetch {year} index: {exc}"
        finish_run(conn, result.run_id, "failed", result)
        return result

    result.filings_indexed = len(filings)

    for filing in filings:
        if acquirer.classify(filing.doc_id) == "scanned":
            result.scanned_skipped += 1
            continue

        if filing_exists(conn, filing.doc_id):
            result.filings_skipped += 1
            continue

        try:
            acquired = acquirer.acquire(filing)
        except ScannedDocumentError:
            result.scanned_skipped += 1
            continue
        except AcquisitionDownloadError as exc:
            result.download_failed += 1
            logger.warning("download failed for %s: %s", filing.doc_id, exc)
            continue
        except AcquisitionParseError as exc:
            result.parse_failed += 1
            logger.warning("parse failed for %s: %s", filing.doc_id, exc)
            continue

        try:
            inserted = store(
                conn,
                filing=filing,
                transactions=acquired.transactions,
                raw_pdf=acquired.raw_document,
                pdf_url=acquired.document_url,
                doc_kind=acquired.doc_kind,
                source=acquirer.source_name,
            )
        except StoreError as exc:
            result.store_failed += 1
            result.store_failure_details.append(f"{filing.doc_id}: {exc}")
            logger.warning("store failed for %s: %s", filing.doc_id, exc)
            continue

        if inserted:
            result.filings_new += 1
            result.transactions_stored += len(acquired.transactions)
        else:
            result.filings_skipped += 1

    if result.download_failed or result.parse_failed or result.store_failed:
        result.status = "partial"
    else:
        result.status = "success"

    finish_run(conn, result.run_id, result.status, result)
    return result


def run_ingestion(
    *,
    year: int,
    conn: psycopg.Connection,
    source: DisclosureSource | None = None,
    downloader: Callable[[str], bytes] | None = None,
    parser: Callable[[bytes], dict] | None = None,
    store: Callable[..., bool] | None = None,
    filing_exists: Callable[[psycopg.Connection, str], bool] | None = None,
    create_run: Callable[..., int] | None = None,
    finish_run: Callable[..., None] | None = None,
    now: datetime | None = None,
) -> IngestionResult:
    """Ingest a single calendar year of House disclosures into the database.

    Returns an :class:`IngestionResult` summarising what happened.
    """
    source = source or HouseClerkSource()
    return _run_year(
        year=year,
        conn=conn,
        source=source,
        acquirer=HouseFilingAcquirer(
            source=source, downloader=downloader, parser=parser
        ),
        store=store or store_filing,
        filing_exists=filing_exists or _filing_exists,
        create_run=create_run or _create_run,
        finish_run=finish_run or _finish_run,
        started_at=now or datetime.now(timezone.utc),
    )


def run_senate_ingestion(
    *,
    year: int,
    conn: psycopg.Connection,
    source: SenateEfdSource | None = None,
    parser: Callable[[bytes], list[dict]] | None = None,
    store: Callable[..., bool] | None = None,
    filing_exists: Callable[[psycopg.Connection, str], bool] | None = None,
    create_run: Callable[..., int] | None = None,
    finish_run: Callable[..., None] | None = None,
    now: datetime | None = None,
) -> IngestionResult:
    """Ingest a single calendar year of Senate eFD disclosures.

    ``source`` must be a :class:`SenateEfdSource` (a live transport for real
    runs, an in-memory replay transport for tests). Paper (scanned) filings
    are counted as scanned and never stored: no reliable text extraction
    exists for them, so nothing is fabricated.
    """
    source = source or SenateEfdSource()
    return _run_year(
        year=year,
        conn=conn,
        source=source,
        acquirer=SenateFilingAcquirer(source=source, parser=parser),
        store=store or store_filing,
        filing_exists=filing_exists or _filing_exists,
        create_run=create_run or _create_run,
        finish_run=finish_run or _finish_run,
        started_at=now or datetime.now(timezone.utc),
    )
