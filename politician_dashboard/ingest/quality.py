"""Derived data-quality signals for ingested disclosures.

These helpers never mutate source data; they compute derived flags so that
anomalous values present in the *official source documents* can be surfaced
without altering what was parsed or stored.

The canonical example is House filing 20033889 (Rep. Steve Cohen, TN09): the
published PTR text layer states a transaction date of 12/26/2026 with a
notification date of 01/21/2026 and a signature date of 02/09/2026, which is
internally inconsistent (the transaction postdates both its own notification
and the signed filing). A later "Amended" filing (20034452) reports the same
purchase with transaction date 12/26/2025, but the source document itself is
still authoritative; the stored value remains 12/26/2026 and is *flagged*
here rather than rewritten.
"""

from __future__ import annotations

from datetime import date

from politician_dashboard.ingest.models import Filing, Transaction

AFTER_NOTIFICATION = "transaction_date_after_notification"
AFTER_FILING = "transaction_date_after_filing"


def transaction_date_anomalies(
    filing: Filing, transaction: Transaction
) -> list[str]:
    """Return the date-consistency violations for one transaction.

    A transaction may legitimately be reported some time after it occurred,
    but it can never occur after its own notification date or after the
    filing that discloses it. Returns an empty list for consistent records.
    """
    violations: list[str] = []
    if transaction.txn_date > transaction.notification_date:
        violations.append(AFTER_NOTIFICATION)
    if (
        filing.filing_date is not None
        and transaction.txn_date > filing.filing_date
    ):
        violations.append(AFTER_FILING)
    return violations