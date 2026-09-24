"""Curated verification and amendment records.

Ingestion only ever stores source-reported data and derived quality flags;
it never writes corrections. Corrections are applied explicitly by a human
reviewer through this module (and the CLI in ``ingest.__main__``).

Guarantees enforced here:

* **Source data is never mutated.** ``txn_date``, ``notification_date``,
  ``filing_status`` and ``quality_flags`` are never written. The verified
  interpretation lives in separate columns.
* **Provenance is required.** Every verification record must carry a method,
  a confidence and a source document id. ``verification_source_doc_id`` is a
  plain text reference (not a FK) so verification never depends on the
  evidence filing having been ingested yet.
* **No inference.** A ``filing_status == 'Amended'`` is never used to derive
  ``amends_filing_id``; linking two filings is an explicit, per-filing act
  with its own method/confidence/note provenance.
* **No fan-out.** The operations target exactly one amendment link or one
  transaction at a time; nothing ever spreads a verification across a filing's
  transactions or matches amendments by loose heuristics.
* **Idempotent within a connection's transaction semantics.** Each operation
  opens its own ``conn.transaction()`` so it commits on ``autocommit=True``
  connections and participates in an enclosing transaction otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import psycopg

METHOD_VALUES = ("explicit_source", "amendment_match", "manual_review")
CONFIDENCE_VALUES = ("high", "medium", "low")


class CurationError(RuntimeError):
    """Raised when a curation request is invalid or unlinkable."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CurationError(message)


def _validate_method(method: str | None) -> str:
    _require(method in METHOD_VALUES, f"invalid method: {method!r}")
    return method  # type: ignore[return-value]


def _validate_confidence(confidence: str | None) -> str:
    _require(confidence in CONFIDENCE_VALUES, f"invalid confidence: {confidence!r}")
    return confidence  # type: ignore[return-value]


def _stamp(verified_at: datetime | None) -> datetime:
    return verified_at or datetime.now(timezone.utc)


def _filing_id(conn: psycopg.Connection, doc_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM filings WHERE doc_id = %s", (doc_id,)
    ).fetchone()
    return row[0] if row else None


@dataclass(frozen=True, slots=True)
class AmendmentLink:
    """The curated link recorded on an amendment filing."""

    amended_doc_id: str
    original_doc_id: str
    method: str
    confidence: str
    note: str | None
    verified_at: datetime


def link_amendment(
    conn: psycopg.Connection,
    *,
    amended_doc_id: str,
    original_doc_id: str,
    method: str | None,
    confidence: str | None,
    note: str | None = None,
    verified_at: datetime | None = None,
) -> AmendmentLink:
    """Point ``amended_doc_id`` at its original ``original_doc_id``.

    Both filings must already exist (the relationship is between two filings
    stored in this database). Explicit provenance is required; no inference
    is performed.
    """
    method = _validate_method(method)
    confidence = _validate_confidence(confidence)
    _require(amended_doc_id != original_doc_id, "a filing cannot amend itself")

    amended_id = _filing_id(conn, amended_doc_id)
    _require(amended_id is not None, f"unknown amended filing: {amended_doc_id}")
    original_id = _filing_id(conn, original_doc_id)
    _require(original_id is not None, f"unknown original filing: {original_doc_id}")

    stamp = _stamp(verified_at)
    with conn.transaction():
        conn.execute(
            """
            UPDATE filings SET amends_filing_id = %s, amendment_method = %s,
                amendment_confidence = %s, amendment_note = %s,
                amendment_verified_at = %s
            WHERE id = %s
            """,
            (original_id, method, confidence, note, stamp, amended_id),
        )
    return AmendmentLink(
        amended_doc_id=amended_doc_id,
        original_doc_id=original_doc_id,
        method=method,
        confidence=confidence,
        note=note,
        verified_at=stamp,
    )


def unlink_amendment(
    conn: psycopg.Connection, *, amended_doc_id: str
) -> bool:
    """Remove the curated amendment link from ``amended_doc_id``.

    Returns ``True`` if a link was removed, ``False`` if none existed.
    """
    amended_id = _filing_id(conn, amended_doc_id)
    _require(amended_id is not None, f"unknown filing: {amended_doc_id}")
    with conn.transaction():
        cursor = conn.execute(
            "UPDATE filings SET amends_filing_id = NULL, amendment_method = NULL, "
            "amendment_confidence = NULL, amendment_note = NULL, "
            "amendment_verified_at = NULL WHERE id = %s",
            (amended_id,),
        )
        return bool(cursor.rowcount)


def _transaction_row(conn: psycopg.Connection, transaction_id: int):
    return conn.execute(
        "SELECT txn_date, notification_date, quality_flags FROM transactions "
        "WHERE id = %s",
        (transaction_id,),
    ).fetchone()


@dataclass(frozen=True, slots=True)
class Verification:
    """The curated verified date attached to one transaction."""

    transaction_id: int
    verified_transaction_date: date
    method: str
    confidence: str
    source_doc_id: str
    note: str | None
    verified_at: datetime


def verify_transaction(
    conn: psycopg.Connection,
    *,
    transaction_id: int,
    verified_transaction_date: date | None,
    method: str | None,
    confidence: str | None,
    source_doc_id: str | None,
    note: str | None = None,
    verified_at: datetime | None = None,
) -> Verification:
    """Attach a verified date to exactly one transaction.

    ``verified_transaction_date`` is required and the provenance fields are
    all required (mirroring the database CHECK). ``source_doc_id`` may name a
    filing that has not been ingested yet: it is a text reference, never a
    foreign key. The source dates and quality flags are preserved verbatim.
    """
    _require(verified_transaction_date is not None, "verified_transaction_date is required")
    method = _validate_method(method)
    confidence = _validate_confidence(confidence)
    _require(
        source_doc_id is not None and bool(source_doc_id.strip()),
        "verification_source_doc_id is required and cannot be blank",
    )

    existing = _transaction_row(conn, transaction_id)
    _require(existing is not None, f"unknown transaction: {transaction_id}")

    stamp = _stamp(verified_at)
    with conn.transaction():
        conn.execute(
            """
            UPDATE transactions SET verified_transaction_date = %s,
                verification_method = %s, verification_confidence = %s,
                verification_source_doc_id = %s, verification_note = %s,
                verified_at = %s
            WHERE id = %s
            """,
            (
                verified_transaction_date,
                method,
                confidence,
                source_doc_id,
                note,
                stamp,
                transaction_id,
            ),
        )
    return Verification(
        transaction_id=transaction_id,
        verified_transaction_date=verified_transaction_date,
        method=method,
        confidence=confidence,
        source_doc_id=source_doc_id,
        note=note,
        verified_at=stamp,
    )


def clear_verification(
    conn: psycopg.Connection, *, transaction_id: int
) -> bool:
    """Remove a curated verification from one transaction.

    Returns ``True`` if a verification was removed, ``False`` if none existed.
    Source dates and quality flags are untouched in both cases.
    """
    existing = _transaction_row(conn, transaction_id)
    _require(existing is not None, f"unknown transaction: {transaction_id}")
    with conn.transaction():
        cursor = conn.execute(
            "UPDATE transactions SET verified_transaction_date = NULL, "
            "verification_method = NULL, verification_confidence = NULL, "
            "verification_source_doc_id = NULL, verification_note = NULL, "
            "verified_at = NULL WHERE id = %s",
            (transaction_id,),
        )
        return bool(cursor.rowcount)


def find_transaction_id(
    conn: psycopg.Connection, *, doc_id: str, sequence: int
) -> int | None:
    """Resolve a transaction id by filing ``doc_id`` and ``sequence``.

    A convenience for reviewers who do not know the DB-internal id.
    """
    row = conn.execute(
        "SELECT t.id FROM transactions t "
        "JOIN filings f ON f.id = t.filing_id "
        "WHERE f.doc_id = %s AND t.sequence = %s",
        (doc_id, sequence),
    ).fetchone()
    return row[0] if row else None


@dataclass(frozen=True, slots=True)
class AmendmentCandidate:
    """A filing that source evidence flags as amended but is not linked."""

    doc_id: str
    original_doc_id: str | None


def list_amendment_candidates(conn: psycopg.Connection) -> list[AmendmentCandidate]:
    """Report filings whose transactions carry ``filing_status == 'Amended'``
    but that have no curated amendment link yet.

    Pure read-ahead for the review queue. Never links anything.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT f.doc_id, o.doc_id
        FROM filings f
        LEFT JOIN filings o ON o.id = f.amends_filing_id
        JOIN transactions t ON t.filing_id = f.id
        WHERE t.filing_status = 'Amended' AND f.amends_filing_id IS NULL
        ORDER BY f.doc_id
        """
    ).fetchall()
    return [
        AmendmentCandidate(doc_id=row[0], original_doc_id=row[1]) for row in rows
    ]


@dataclass(frozen=True, slots=True)
class UnresolvedTransaction:
    """One source-flagged transaction with no verification attached."""

    doc_id: str
    sequence: int
    txn_date: date
    notification_date: date
    quality_flags: tuple[str, ...]


def list_unresolved_transactions(
    conn: psycopg.Connection,
) -> list[UnresolvedTransaction]:
    """Report transactions that carry source-dated anomalies but no curated
    verification, i.e. the queue of still-open anomalies.

    Pure read-ahead. Never resolves anything.
    """
    rows = conn.execute(
        """
        SELECT f.doc_id, t.sequence, t.txn_date, t.notification_date,
               t.quality_flags
        FROM transactions t
        JOIN filings f ON f.id = t.filing_id
        WHERE t.quality_flags <> '{}' AND t.verified_transaction_date IS NULL
        ORDER BY f.doc_id, t.sequence
        """
    ).fetchall()
    return [
        UnresolvedTransaction(
            doc_id=row[0],
            sequence=row[1],
            txn_date=row[2],
            notification_date=row[3],
            quality_flags=tuple(row[4] or []),
        )
        for row in rows
    ]
