"""Persist parsed filings and their transactions to PostgreSQL.

The filing's ``doc_id`` is the idempotency key: re-ingesting a filing that
already exists is a no-op (its transactions are not rewritten). Each filing
and all of its transactions are written atomically in a single transaction.
"""

from __future__ import annotations

from typing import Sequence

import psycopg

from politician_dashboard.ingest.models import Filing, Transaction


class StoreError(RuntimeError):
    """Raised when persisting a filing fails."""


def store_filing(
    conn: psycopg.Connection,
    *,
    filing: Filing,
    transactions: Sequence[Transaction],
    raw_pdf: bytes | None,
    pdf_url: str,
    doc_kind: str = "efiled",
) -> bool:
    """Persist one filing and all of its transactions atomically.

    Uses ``filings.doc_id`` as the idempotency key. If a filing with the same
    ``doc_id`` already exists nothing is written and ``False`` is returned
    (companion transactions are not stored or duplicated). Otherwise the
    filing and every transaction are inserted in a single transaction and
    ``True`` is returned.

    If any write fails the whole transaction is rolled back, leaving no
    partial data, and :class:`StoreError` is raised.
    """
    try:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO filings (
                    doc_id, year, prefix, first_name, last_name, suffix,
                    state_district, filing_date, doc_kind, pdf_url, raw_pdf
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (doc_id) DO NOTHING
                RETURNING id
                """,
                (
                    filing.doc_id,
                    filing.year,
                    filing.prefix or None,
                    filing.first,
                    filing.last,
                    filing.suffix or None,
                    filing.state_district,
                    filing.filing_date,
                    doc_kind,
                    pdf_url,
                    psycopg.Binary(raw_pdf) if raw_pdf is not None else None,
                ),
            ).fetchone()

            if row is None:
                return False

            filing_id = row[0]
            for tx in transactions:
                conn.execute(
                    """
                    INSERT INTO transactions (
                        filing_id, sequence, txn_source_id, owner_token,
                        asset_name, ticker, asset_type_code, txn_type,
                        txn_date, notification_date, amount_min, amount_max,
                        amount_raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        filing_id,
                        tx.sequence,
                        tx.txn_source_id,
                        tx.owner_token,
                        tx.asset_name,
                        tx.ticker,
                        tx.asset_type_code,
                        tx.txn_type,
                        tx.txn_date,
                        tx.notification_date,
                        tx.amount_min,
                        tx.amount_max,
                        tx.amount_raw,
                    ),
                )
            return True
    except psycopg.Error as exc:
        raise StoreError(f"failed to store filing {filing.doc_id}: {exc}") from exc
