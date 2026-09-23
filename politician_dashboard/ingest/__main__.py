"""CLI for the politician-dashboard ingestion pipeline.

Usage:
    python -m politician_dashboard.ingest [--year 2025]
    python -m politician_dashboard.ingest --source senate [--year 2026]
    python -m politician_dashboard.ingest --backfill [--since 2011]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import psycopg

from politician_dashboard.config import get_database_url
from politician_dashboard.ingest.runner import (
    IngestionResult,
    run_ingestion,
    run_senate_ingestion,
)

EARLIEST_YEAR = 2011


def resolve_years(
    year: int | None,
    backfill: bool,
    since: int,
    current_year: int | None = None,
) -> list[int]:
    """Return the list of target years given the CLI flags."""
    current_year = current_year or datetime.now(timezone.utc).year
    if backfill:
        if since > current_year:
            raise ValueError(f"--since {since} is after the current year {current_year}")
        return list(range(since, current_year + 1))
    return [year if year is not None else current_year]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m politician_dashboard.ingest",
        description="Ingest U.S. House or Senate PTR disclosures into PostgreSQL.",
    )
    parser.add_argument(
        "--source",
        choices=("house", "senate"),
        default="house",
        help=(
            "Disclosure source to ingest. 'house' (the House Clerk) is the "
            "default; 'senate' ingests the Senate eFD portal and must be "
            "selected explicitly. No auto-detection is performed."
        ),
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Ingest a single year (default: the current year).",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Ingest every year from --since through the current year.",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=EARLIEST_YEAR,
        help=f"Starting year for --backfill (default {EARLIEST_YEAR}).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the DATABASE_URL from the environment.",
    )
    return parser


def _format_result(result: IngestionResult) -> str:
    return (
        f"{result.year}: {result.status} "
        f"(run {result.run_id}; indexed={result.filings_indexed}, "
        f"new={result.filings_new}, skipped={result.filings_skipped}, "
        f"scanned_skipped={result.scanned_skipped}, "
        f"download_failed={result.download_failed}, "
        f"parse_failed={result.parse_failed}, "
        f"transactions={result.transactions_stored})"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or get_database_url()

    try:
        years = resolve_years(args.year, args.backfill, args.since)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Targeting year(s): {years}")

    with psycopg.connect(database_url, autocommit=True) as conn:
        for year in years:
            print(f"Ingesting {args.source} {year}...")
            if args.source == "senate":
                result = run_senate_ingestion(year=year, conn=conn)
            else:
                result = run_ingestion(year=year, conn=conn)
            print(_format_result(result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
