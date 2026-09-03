"""Pydantic response schemas for the V1 read-only API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class Health(BaseModel):
    status: str
    database: str


class Politician(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    party: str | None = None
    state: str
    district: str
    state_district: str
    filing_count: int
    transaction_count: int


class FilingSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str
    year: int
    name: str
    state_district: str
    filing_date: date | None
    doc_kind: str
    pdf_url: str
    downloaded_at: datetime
    created_at: datetime
    transaction_count: int


class Transaction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    filing_id: int
    doc_id: str
    politician_id: str
    sequence: int
    asset_name: str
    ticker: str | None
    asset_type_code: str | None
    txn_type: str
    txn_date: date
    notification_date: date
    amount_min: float
    amount_max: float
    amount_raw: str
    owner: str | None
    filing_status: str | None
    ownership_source: str | None
    notes: str | None
    txn_source_id: str | None


class FilingDetail(FilingSummary):
    transactions: list[Transaction]


class Pagination(BaseModel):
    limit: int
    offset: int
    total: int
    next_url: str | None = None
    prev_url: str | None = None


class Paginated(BaseModel, Generic[T]):
    items: list[T]
    pagination: Pagination


def ok(*, items: list[Any], total: int, request_url: str, offset: int, limit: int) -> dict:
    """Build the ``{items, pagination}`` envelope shared by list endpoints."""
    next_offset = offset + limit
    prev_offset = max(0, offset - limit)
    next_url = _with_offset(request_url, next_offset) if next_offset < total else None
    prev_url = _with_offset(request_url, prev_offset) if offset > 0 else None
    return {
        "items": items,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "next_url": next_url,
            "prev_url": prev_url,
        },
    }


def _with_offset(url: str, offset: int) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["offset"] = str(offset)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
