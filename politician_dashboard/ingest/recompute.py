"""Recompute derived quality flags for already-stored transactions.

Backfills ``transactions.quality_flags`` from the stored source dates
(``txn_date``, ``notification_date`` and the parent ``filings.filing_date``)
using the same :func:`transaction_date_anomalies` rules the ingestion pipeline
applies to new filings, so there is a single source of truth for the flag
definitions. The ``as_of`` reference date makes the
``transaction_date_after_ingestion_date`` rule deterministic instead of an
implicit "today".

Only ``transactions.quality_flags`` is written; transaction values, filings
and raw documents are never modified. The operation is source-independent
(House and Senate alike) and idempotent: re-running with the same ``as_of``
date is a no-op because unchanged rows are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg

from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.quality import transaction_date_anomalies

_FLAG_SELECT = """
    SELECT t.id, f.doc_id, f.filing_date, t.txn_date, t.notification_date,
           t.quality_flags
    FROM transactions t
    JOIN filings f ON f.id = t.filing_id
    ORDER BY f.doc_id, t.sequence
"""


@dataclass
class RecomputeReport:
    """Outcome of one quality-flag recomputation pass."""

    examined: int = 0
    changed: int = 0
    flag_counts: dict[str, int] = field(default_factory=dict)
    affected_doc_ids: list[str] = field(default_factory=list)
    dry_run: bool = False


def recompute_quality_flags(
    conn: psycopg.Connection,
    *,
    as_of: date,
    dry_run: bool = False,
) -> RecomputeReport:
    """Recompute ``quality_flags`` for every stored transaction.

    Re-uses :func:`transaction_date_anomalies` with the stored source dates;
    only rows whose computed flags differ from the stored flags are updated.
    In ``dry_run`` mode nothing is written and the report describes what
    *would* change.

    Works regardless of the connection's ``autocommit`` mode: the read below
    is committed up front so ``with conn.transaction()`` starts as the outer
    transaction and actually COMMITs the updates (on an ``autocommit=True``
    connection the commit is a harmless no-op).
    """
    report = RecomputeReport(dry_run=dry_run)
    rows = conn.execute(_FLAG_SELECT).fetchall()
    conn.commit()

    # transaction_date_anomalies() only reads txn_date, notification_date and
    # filing.filing_date, so the placeholder values below are never consulted.
    pending: list[tuple[int, list[str]]] = []
    for txn_id, doc_id, filing_date, txn_date, notification_date, stored in rows:
        filing = Filing(
            prefix="",
            last="",
            first="",
            suffix="",
            filing_type="",
            state_district="",
            year=0,
            filing_date=filing_date,
            doc_id=doc_id,
        )
        transaction = Transaction(
            sequence=0,
            asset_name="",
            txn_type="",
            txn_date=txn_date,
            notification_date=notification_date,
            amount_min=0,
            amount_max=0,
            amount_raw="",
        )
        computed = transaction_date_anomalies(
            filing, transaction, ingestion_date=as_of
        )
        report.examined += 1
        if computed == list(stored or []):
            continue
        report.changed += 1
        for flag in computed:
            report.flag_counts[flag] = report.flag_counts.get(flag, 0) + 1
        if doc_id not in report.affected_doc_ids:
            report.affected_doc_ids.append(doc_id)
        if not dry_run:
            pending.append((txn_id, computed))

    if pending:
        with conn.transaction():
            for txn_id, computed in pending:
                conn.execute(
                    "UPDATE transactions SET quality_flags = %s WHERE id = %s",
                    (computed, txn_id),
                )
    return report