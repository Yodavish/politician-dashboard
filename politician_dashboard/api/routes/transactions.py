"""Transaction read endpoints."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request

from politician_dashboard.api import queries, serializers
from politician_dashboard.api import sql as _sql
from politician_dashboard.api.db import connection_dependency
from politician_dashboard.api.politicians import resolve_politician
from politician_dashboard.api.schemas import ok

router = APIRouter(prefix="/transactions", tags=["transactions"])

Conn = Annotated[object, Depends(connection_dependency)]


@router.get("", response_model=dict)
def transactions_list(
    request: Request,
    conn: Conn,
    politician_id: Optional[str] = None,
    ticker: Optional[str] = None,
    asset_type_code: Optional[str] = None,
    txn_type: Optional[str] = None,
    owner: Optional[str] = None,
    year: Optional[int] = None,
    txn_date_min: Optional[str] = None,
    txn_date_max: Optional[str] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    sort: str = "-txn_date",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters: dict = {}
    if politician_id is not None:
        filters["politician"] = resolve_politician(conn, politician_id)
    if ticker is not None:
        filters["ticker"] = ticker
    if asset_type_code is not None:
        filters["asset_type_code"] = asset_type_code
    if txn_type is not None:
        filters["txn_type"] = txn_type
    if owner is not None:
        filters["owner"] = owner
    txn_date_min = _sql.parse_date(txn_date_min, "txn_date_min")
    txn_date_max = _sql.parse_date(txn_date_max, "txn_date_max")
    filters["txn_date_min"] = txn_date_min
    filters["txn_date_max"] = txn_date_max
    filters["amount_min"] = amount_min
    filters["amount_max"] = amount_max
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
