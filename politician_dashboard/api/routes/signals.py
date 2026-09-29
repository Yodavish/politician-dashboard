"""Computed-signal read endpoints.

Signals are derived on read from filings and transactions. Nothing is stored,
so there is no signal table and no background job behind these routes.
"""

from __future__ import annotations

import re
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request

from politician_dashboard.api import queries, serializers
from politician_dashboard.api import sql as _sql
from politician_dashboard.api.db import connection_dependency
from politician_dashboard.api.errors import BadRequestError, NotFoundError
from politician_dashboard.api.politicians import resolve_politician
from politician_dashboard.api.schemas import ok

router = APIRouter(prefix="/signals", tags=["signals"])

Conn = Annotated[object, Depends(connection_dependency)]

# A signal id is ``bc_<TICKER>_<YYYY-MM-DD>``. The rule's ticker pattern
# (``^[A-Z][A-Z0-9.\-]{0,5}$``) cannot contain an underscore, so the final
# underscore unambiguously separates the ticker from the cluster's start
# date, and a cluster's first transaction date is unique within a ticker.
# That makes the id both stable and directly resolvable, with no hash to
# store or reverse.
_SIGNAL_ID_RE = re.compile(
    r"^bc_([A-Za-z0-9.\-]{1,6})_(\d{4}-\d{2}-\d{2})$"
)


def parse_signal_id(signal_id: str) -> tuple[str, str]:
    """Return ``(ticker, start_date)`` for a signal id."""
    match = _SIGNAL_ID_RE.match(signal_id)
    if match is None:
        raise NotFoundError(f"unknown signal: {signal_id}")
    return match.group(1), match.group(2)


@router.get("", response_model=dict)
def signals_list(
    request: Request,
    conn: Conn,
    politician_id: Optional[str] = None,
    ticker: Optional[str] = None,
    span_days_max: Optional[int] = Query(None, ge=0),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort: str = "-politician_count",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List computed signals.

    ``start_date``/``end_date`` filter on cluster overlap: a cluster is kept
    when its window touches the range, so a cluster that begins before the
    range is not dropped.

    ``span_days_max`` filters on the width of an already-computed cluster.
    It does not re-run clustering, and a value above the rule's own
    ``max_span_days`` simply cannot exclude anything.
    """
    filters: dict = {}
    if politician_id is not None:
        filters["politician"] = resolve_politician(conn, politician_id)
    if ticker is not None and ticker.strip():
        filters["ticker"] = ticker.strip()
    filters["span_days_max"] = span_days_max
    filters["start_date"] = _sql.parse_date(start_date, "start_date")
    filters["end_date"] = _sql.parse_date(end_date, "end_date")
    if (
        filters["start_date"] is not None
        and filters["end_date"] is not None
        and filters["start_date"] > filters["end_date"]
    ):
        raise BadRequestError("date range: start_date cannot be after end_date")

    total, rows = queries.list_buy_clusters(
        conn, filters=filters, sort_key=sort, limit=limit, offset=offset
    )
    items = [serializers.buy_cluster_summary_dict(row) for row in rows]
    return ok(
        items=items, total=total, request_url=str(request.url),
        offset=offset, limit=limit,
    )


@router.get("/{signal_id}", response_model=dict)
def signal_detail(signal_id: str, conn: Conn):
    """Return one signal with the transactions that triggered it."""
    ticker, start_date = parse_signal_id(signal_id)
    found = queries.get_buy_cluster(conn, ticker, start_date)
    if found is None:
        raise NotFoundError(f"unknown signal: {signal_id}")
    summary_row, transaction_rows = found
    return serializers.buy_cluster_detail_dict(summary_row, transaction_rows)
