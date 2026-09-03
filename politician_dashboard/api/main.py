"""Application factory for the read-only V1 API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from politician_dashboard.api import db
from politician_dashboard.api.errors import APIError
from politician_dashboard.api.routes import filings, health, politicians, transactions
from politician_dashboard.config import get_database_url


def create_app(database_url: str | None = None) -> FastAPI:
    """Build the FastAPI app, opening its PostgreSQL pool on startup."""
    url = database_url or get_database_url()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = db.create_pool(url)
        # Open the pool eagerly so startup failures surface immediately.
        pool.wait()
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="Politician Dashboard API", version="0.1.0", lifespan=lifespan)
    app.state.database_url = url

    app.include_router(health.router)
    app.include_router(politicians.router)
    app.include_router(filings.router)
    app.include_router(transactions.router)

    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": "validation error", "code": "validation_error"},
        )

    @app.exception_handler(Exception)
    async def unexpected_handler(_request: Request, _exc: Exception):
        # Never leak internals; log the real error and return a generic message.
        import logging

        logging.getLogger(__name__).exception("unhandled API error")
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error", "code": "internal_error"},
        )

    return app
