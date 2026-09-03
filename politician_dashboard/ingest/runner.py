"""Ingestion runner.

Orchestrates the end-to-end pipeline for a single calendar year:

    DisclosureSource.fetch_ptrs(year)
    -> classify DocID (efiled vs scanned)
    -> skip already-stored doc_ids
    -> download PDF
    -> parse_ptr_pdf()
    -> store_filing()

Failure semantics (per AGENTS.md):
- A failed yearly index download fails the run (status ``failed``).
- Individual PDF download / parse / store failures are counted and the run
  continues; the run is marked ``partial`` if any occurred.
- Each filing and all of its transactions are written atomically by
  :func:`store_filing`.

All collaborators (source, downloader, parser, store, existence check and
run-accounting) are injectable so the orchestration can be tested without a
live database or network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import psycopg

from politician_dashboard.ingest.models import Transaction
from politician_dashboard.ingest.parser import ScannedPdfError, parse_ptr_pdf
from politician_dashboard.ingest.sources.base import DisclosureSource
from politician_dashboard.ingest.sources.house_clerk import (
    HouseClerkSource,
    classify_doc_id,
    download_pdf,
)
from politician_dashboard.ingest.store import StoreError, store_filing

logger = logging.getLogger(__name__)

RunStatus = str


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


def _create_run(conn: psycopg.Connection, year: int, started_at: datetime) -> int:
    row = conn.execute(
        "INSERT INTO ingest_runs (started_at, status, year_targets, filings_indexed) "
        "VALUES (%s, 'failed', %s, 0) RETURNING id",
        (started_at, [str(year)]),
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
            )
        )
    return transactions


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
    """Ingest a single calendar year into the database.

    Returns an :class:`IngestionResult` summarising what happened.
    """
    source = source or HouseClerkSource()
    downloader = downloader or download_pdf
    parser = parser or parse_ptr_pdf
    store = store or store_filing
    filing_exists = filing_exists or _filing_exists
    create_run = create_run or _create_run
    finish_run = finish_run or _finish_run
    started_at = now or datetime.now(timezone.utc)

    result = IngestionResult(year=year)
    result.run_id = create_run(conn, year, started_at)
    result.filings_indexed = 0

    try:
        filings = source.fetch_ptrs(year)
    except Exception as exc:  # noqa: BLE001 - index failure fails the run
        result.status = "failed"
        result.error = f"failed to fetch {year} index: {exc}"
        finish_run(conn, result.run_id, "failed", result)
        return result

    result.filings_indexed = len(filings)

    for filing in filings:
        kind = classify_doc_id(filing.doc_id)
        if kind == "scanned":
            result.scanned_skipped += 1
            continue

        if filing_exists(conn, filing.doc_id):
            result.filings_skipped += 1
            continue

        url = source.pdf_url(filing.year, filing.doc_id)
        try:
            pdf_bytes = downloader(url)
        except Exception as exc:  # noqa: BLE001
            result.download_failed += 1
            logger.warning("download failed for %s: %s", filing.doc_id, exc)
            continue

        try:
            parsed = parser(pdf_bytes)
        except ScannedPdfError:
            result.scanned_skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001 - includes ParseError
            result.parse_failed += 1
            logger.warning("parse failed for %s: %s", filing.doc_id, exc)
            continue

        transactions = _to_transactions(parsed)

        try:
            inserted = store(
                conn,
                filing=filing,
                transactions=transactions,
                raw_pdf=pdf_bytes,
                pdf_url=url,
                doc_kind=kind,
            )
        except StoreError as exc:
            result.store_failed += 1
            result.store_failure_details.append(f"{filing.doc_id}: {exc}")
            logger.warning("store failed for %s: %s", filing.doc_id, exc)
            continue

        if inserted:
            result.filings_new += 1
            result.transactions_stored += len(transactions)
        else:
            result.filings_skipped += 1

    if result.download_failed or result.parse_failed or result.store_failed:
        result.status = "partial"
    else:
        result.status = "success"

    finish_run(conn, result.run_id, result.status, result)
    return result
