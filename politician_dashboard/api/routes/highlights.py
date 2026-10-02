"""Homepage highlights derived from existing disclosure data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends

from politician_dashboard.api import queries, serializers
from politician_dashboard.api.db import connection_dependency

router = APIRouter(prefix="/highlights", tags=["highlights"])

Conn = Annotated[object, Depends(connection_dependency)]


@router.get("", response_model=dict)
def highlights_list(conn: Conn):
    """Return newest clusters and top disclosed purchase/sale by amount floor."""
    now = datetime.now(timezone.utc)
    as_of_date = now.date()
    cutoff_date = queries.six_month_cutoff(as_of_date)
    cluster_rows = queries.list_recent_cluster_highlights(
        conn, cutoff_date=cutoff_date, as_of_date=as_of_date
    )
    transaction_rows = queries.list_largest_disclosed_transactions(
        conn, cutoff_date=cutoff_date, as_of_date=as_of_date
    )
    return {
        "generated_at": now,
        "activity_window": {
            "start_date": cutoff_date,
            "end_date": as_of_date,
        },
        "recent_cluster_activity": [
            serializers.cluster_highlight_dict(row) for row in cluster_rows
        ],
        "largest_disclosed_transactions": [
            serializers.largest_transaction_highlight_dict(row)
            for row in transaction_rows
        ],
    }
