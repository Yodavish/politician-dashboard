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
    cluster_rows = queries.list_recent_cluster_highlights(conn)
    transaction_rows = queries.list_largest_disclosed_transactions(conn)
    return {
        "generated_at": datetime.now(timezone.utc),
        "recent_cluster_activity": [
            serializers.cluster_highlight_dict(row) for row in cluster_rows
        ],
        "largest_disclosed_transactions": [
            serializers.largest_transaction_highlight_dict(row)
            for row in transaction_rows
        ],
    }
