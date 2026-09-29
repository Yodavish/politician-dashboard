"""Core data models shared by ingestion sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Filing:
    """One row from a chamber disclosure index (a single filed document)."""

    prefix: str
    last: str
    first: str
    suffix: str
    filing_type: str
    state_district: str
    year: int
    filing_date: date | None
    doc_id: str
    bioguide_id: str | None = None


@dataclass(frozen=True, slots=True)
class Transaction:
    """One trade disclosure record belonging to a :class:`Filing`."""

    sequence: int
    asset_name: str
    txn_type: str
    txn_date: date
    notification_date: date
    amount_min: int
    # None means the source stated a lower bound but no upper bound (the eFD
    # "Over $X" tier). It is never coerced to 0 or to amount_min.
    amount_max: int | None
    amount_raw: str
    txn_source_id: str | None = None
    owner_token: str | None = None
    ticker: str | None = None
    asset_type_code: str | None = None
    filing_status: str | None = None
    ownership_source: str | None = None
    notes: str | None = None
    quality_flags: tuple[str, ...] = ()
