"""HTTP-aware error types for the API.

Handlers raise these; the application registers a single exception handler
that converts them into a JSON error envelope without leaking internals.
"""

from __future__ import annotations


class APIError(Exception):
    """A user-facing API error with an HTTP status and machine code."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFoundError(APIError):
    status_code = 404
    code = "not_found"


class BadRequestError(APIError):
    status_code = 400
    code = "bad_request"


class UnavailableError(APIError):
    status_code = 503
    code = "unavailable"
