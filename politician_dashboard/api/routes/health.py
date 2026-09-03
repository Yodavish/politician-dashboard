"""Health endpoint: reports API liveness and database reachability."""

from __future__ import annotations

from fastapi import APIRouter, Request

from politician_dashboard.api.schemas import Health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health(request: Request) -> Health:
    database = "ok"
    try:
        with request.app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - any failure means DB is down
        database = "unavailable"
    status = "ok" if database == "ok" else "degraded"
    return Health(status=status, database=database)
