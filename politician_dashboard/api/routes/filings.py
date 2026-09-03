"""Filing read endpoints."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request

from politician_dashboard.api import queries, serializers
from politician_dashboard.api.db import connection_dependency
from politician_dashboard.api.errors import NotFoundError
from politician_dashboard.api.politicians import resolve_politician
from politician_dashboard.api.schemas import (
    FilingDetail,
    ok,
)
from politician_dashboard.api import sql as _sql

router = APIRouter(prefix="/filings", tags=["filings"])

Conn = Annotated[object, Depends(connection_dependency)]


@router.get("", response_model=dict)
def filings_list(
    request: Request,
    conn: Conn,
    year: Optional[int] = None,
    state: Optional[str] = None,
    politician_id: Optional[str] = None,
    filing_date_min: Optional[str] = None,
    filing_date_max: Optional[str] = None,
    sort: str = "-created_at",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters: dict = {}
    if politician_id is not None:
        filters["politician"] = resolve_politician(conn, politician_id)
    if state is not None:
        filters["state_prefix"] = state.upper() + "%"
    if year is not None:
        filters["year"] = year
    filing_date_min = _sql.parse_date(filing_date_min, "filing_date_min")
    filing_date_max = _sql.parse_date(filing_date_max, "filing_date_max")
    filters["filing_date_min"] = filing_date_min
    filters["filing_date_max"] = filing_date_max

    total, rows = queries.list_filings(
        conn, filters=filters, sort_key=sort, limit=limit, offset=offset
    )
    items = [serializers.filing_dict(r) for r in rows]
    return ok(
        items=items, total=total, request_url=str(request.url),
        offset=offset, limit=limit,
    )


@router.get("/{doc_id}", response_model=FilingDetail)
def filing_detail(doc_id: str, conn: Conn) -> FilingDetail:
    row = queries.get_filing(conn, doc_id)
    if row is None:
        raise NotFoundError(f"unknown filing: {doc_id}")
    detail = serializers.filing_dict(row)
    txs = queries.list_filing_transactions(conn, row[0])
    detail["transactions"] = [serializers.transaction_dict(t) for t in txs]
    return FilingDetail.model_validate(detail)
