"""Apply pending SQL migrations in filename order.

Usage:
    uv run --env-file .env python -m politician_dashboard.migrations.migrate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import psycopg

from politician_dashboard.config import get_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parent
_SCHEMA_MIGRATIONS_TABLE = "schema_migrations"


def _discover_migrations(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.sql"))


def _applied_migrations(conn: psycopg.Connection) -> set[str]:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_SCHEMA_MIGRATIONS_TABLE} ("
        "filename text PRIMARY KEY, "
        "applied_at timestamptz NOT NULL DEFAULT now())"
    )
    rows = conn.execute(
        f"SELECT filename FROM {_SCHEMA_MIGRATIONS_TABLE}"
    ).fetchall()
    return {row[0] for row in rows}


def run_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every unapplied migration, each inside its own transaction."""
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        already_applied = _applied_migrations(conn)
        for path in _discover_migrations(migrations_dir):
            if path.name in already_applied:
                continue
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    f"INSERT INTO {_SCHEMA_MIGRATIONS_TABLE} (filename) VALUES (%s)",
                    (path.name,),
                )
            applied.append(path.name)
    return applied


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--migrations-dir",
        default=str(MIGRATIONS_DIR),
        help="Directory containing numbered .sql migration files.",
    )
    args = parser.parse_args(argv)

    database_url = args.database_url or get_database_url()
    applied = run_migrations(database_url, Path(args.migrations_dir))

    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("No pending migrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())