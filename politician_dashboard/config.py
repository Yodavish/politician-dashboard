"""Runtime configuration, sourced from the environment."""

from __future__ import annotations

import os


def get_database_url() -> str:
    """Return the PostgreSQL connection string from the ``DATABASE_URL`` env var.

    Raises ``RuntimeError`` if unset so that a missing credential fails
    loudly instead of silently connecting somewhere unintended.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Export it or run with `--env-file .env`."
        )
    return url