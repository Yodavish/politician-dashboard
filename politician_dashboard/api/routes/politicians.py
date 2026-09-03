"""Politician read endpoints.

A politician is a derived V1 identity (name + state_district), not an
authoritative permanent identity. See ``api.politicians``.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request

from politician_dashboard.api import queries, serializers
from politician_dashboard.api.db import connection_dependency
from politician_dashboard.api.errors import NotFoundError
from politician_dashboard.api.politicians import resolve_politician
from politician_dashboard.api.schemas import Politician, ok

router = APIRouter(prefix="/politicians", tags=["politicians"])

Conn = Annotated[object, Depends(connection_dependency)]


@router.get("", response_model=dict)
def politicians_list(
    request: Request,
    conn: Conn,
    state: Optional[str] = None,
    sort: str = "last_name",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters: dict = {}
    if state is not None:
        filters["state_prefix"] = state.lower() + "%"
    total, rows = queries.list_politicians(
        conn, filters=filters, sort_key=sort, limit=limit, offset=offset
    )
    items = [serializers.politician_dict(r) for r in rows]
    return ok(
        items=items, total=total, request_url=str(request.url),
        offset=offset, limit=limit,
    )


@router.get("/{politician_id}", response_model=Politician)
def politician_detail(politician_id: str, conn: Conn) -> Politician:
    district, first, last = resolve_politician(conn, politician_id)
    row = queries.get_politician(conn, district, first, last)
    if row is None:
        raise NotFoundError(f"unknown politician: {politician_id}")
    return Politician.model_validate(serializers.politician_dict(row))


@router.get("/{politician_id}/filings", response_model=dict)
def politician_filings(
    request: Request,
    politician_id: str,
    conn: Conn,
    year: Optional[int] = None,
    sort: str = "-created_at",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    district, first, last = resolve_politician(conn, politician_id)
    filters: dict = {"politician": (district, first, last)}
    if year is not None:
        filters["year"] = year
    total, rows = queries.list_filings(
        conn, filters=filters, sort_key=sort, limit=limit, offset=offset
    )
    items = [serializers.filing_dict(r) for r in rows]
    return ok(
        items=items, total=total, request_url=str(request.url),
        offset=offset, limit=limit,
    )


@router.get("/{politician_id}/transactions", response_model=dict)
def politician_transactions(
    request: Request,
    politician_id: str,
    conn: Conn,
    year: Optional[int] = None,
    ticker: Optional[str] = None,
    asset_type_code: Optional[str] = None,
    txn_type: Optional[str] = None,
    owner: Optional[str] = None,
    sort: str = "-txn_date",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    district, first, last = resolve_politician(conn, politician_id)
    filters: dict = {"politician": (district, first, last)}
    if ticker is not None:
        filters["ticker"] = ticker
    if asset_type_code is not None:
        filters["asset_type_code"] = asset_type_code
    if txn_type is not None:
        filters["txn_type"] = txn_type
    if owner is not None:
        filters["owner"] = owner
    if year is not None:
        filters["txn_date_min"] = f"{year}-01-01"
        filters["txn_date_max"] = f"{year}-12-31"
    total, rows = queries.list_transactions(
        conn, filters=filters, sort_key=sort, limit=limit, offset=offset
    )
    items = [serializers.transaction_dict(r) for r in rows]
    return ok(
        items=items, total=total, request_url=str(request.url),
        offset=offset, limit=limit,
    )
