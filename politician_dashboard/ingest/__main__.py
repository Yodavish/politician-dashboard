"""CLI for the politician-dashboard ingestion pipeline.

Usage:
    python -m politician_dashboard.ingest [--year 2025]
    python -m politician_dashboard.ingest --source senate [--year 2026]
    python -m politician_dashboard.ingest --backfill [--since 2011]
    python -m politician_dashboard.ingest --recompute-flags --as-of 2026-09-23 [--dry-run]

Curation (explicit, provenance-required; never inferred automatically):
    python -m politician_dashboard.ingest --curate-verify \\
        --transaction-id 42 --verified-date 2025-04-17 \\
        --method amendment_match --confidence high \\
        --source-doc 20030336 [--note "..."]

    python -m politician_dashboard.ingest --curate-amend \\
        --amended-doc 20034452 --original-doc 20033889 \\
        --method amendment_match --confidence medium [--note "..."]

    python -m politician_dashboard.ingest --curate-candidates
    python -m politician_dashboard.ingest --curate-unresolved
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone

import psycopg

from politician_dashboard.config import get_database_url
from politician_dashboard.ingest.curation import (
    CurationError,
    clear_verification,
    find_transaction_id,
    link_amendment,
    list_amendment_candidates,
    list_unresolved_transactions,
    unlink_amendment,
    verify_transaction,
)
from politician_dashboard.ingest.recompute import recompute_quality_flags
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
        "--recompute-flags",
        action="store_true",
        help=(
            "Recompute quality_flags for existing transactions from their "
            "stored source dates instead of ingesting. Source-independent "
            "(House and Senate); requires --as-of."
        ),
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Fixed ingestion reference date used by --recompute-flags so the "
            "transaction_date_after_ingestion_date flag is deterministic."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --recompute-flags, report what would change without writing.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the DATABASE_URL from the environment.",
    )

    curation = parser.add_argument_group(
        "curation",
        "Explicit, provenance-required curation. Never inferred automatically.",
    )
    curation.add_argument(
        "--curate-verify",
        action="store_true",
        help="Attach a verified transaction date to exactly one transaction.",
    )
    curation.add_argument(
        "--curate-clear-verification",
        action="store_true",
        help="Remove the curated verification from one transaction.",
    )
    curation.add_argument(
        "--curate-amend",
        action="store_true",
        help="Link an amended filing to its original filing.",
    )
    curation.add_argument(
        "--curate-unamend",
        action="store_true",
        help="Remove the curated amendment link from a filing.",
    )
    curation.add_argument(
        "--curate-candidates",
        action="store_true",
        help="List filings whose source status is 'Amended' but unlinked.",
    )
    curation.add_argument(
        "--curate-unresolved",
        action="store_true",
        help="List source-flagged transactions that still lack verification.",
    )
    curation.add_argument(
        "--transaction-id",
        type=int,
        default=None,
        help="DB-internal transactions.id for --curate-verify/-clear.",
    )
    curation.add_argument(
        "--doc-id",
        default=None,
        help="Filing doc_id for resolving a transaction (with --sequence).",
    )
    curation.add_argument(
        "--sequence",
        type=int,
        default=None,
        help="Transaction sequence within --doc-id (with --transaction-id "
        "resolution; use when the DB id is unknown).",
    )
    curation.add_argument(
        "--verified-date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="The verified transaction date for --curate-verify.",
    )
    curation.add_argument(
        "--method",
        choices=("explicit_source", "amendment_match", "manual_review"),
        default=None,
        help="Verification/amendment method.",
    )
    curation.add_argument(
        "--confidence",
        choices=("high", "medium", "low"),
        default=None,
        help="Verification/amendment confidence.",
    )
    curation.add_argument(
        "--source-doc",
        default=None,
        help="Evidence document id (filings.doc_id) for --curate-verify. "
        "Text reference; the filing need not be ingested yet.",
    )
    curation.add_argument(
        "--amended-doc",
        default=None,
        help="The amendment filing's doc_id for --curate-amend/-unamend.",
    )
    curation.add_argument(
        "--original-doc",
        default=None,
        help="The original filing's doc_id for --curate-amend.",
    )
    curation.add_argument(
        "--note",
        default=None,
        help="Human-readable rationale written to the record.",
    )
    return parser


def _format_report(report) -> str:
    lines = [
        f"  examined: {report.examined}",
        f"  changed:  {report.changed}",
    ]
    if report.flag_counts:
        lines.append("  by flag:")
        for flag, count in sorted(report.flag_counts.items()):
            lines.append(f"    {flag}: {count}")
    lines.append(f"  affected filings ({len(report.affected_doc_ids)}):")
    if report.affected_doc_ids:
        lines.append("    " + ", ".join(report.affected_doc_ids))
    else:
        lines.append("    (none)")
    return "\n".join(lines)


def _format_result(result: IngestionResult) -> str:
    summary = (
        f"{result.year}: {result.status} "
        f"(run {result.run_id}; indexed={result.filings_indexed}, "
        f"new={result.filings_new}, skipped={result.filings_skipped}, "
        f"scanned_skipped={result.scanned_skipped}, "
        f"download_failed={result.download_failed}, "
        f"parse_failed={result.parse_failed}, "
        f"transactions={result.transactions_stored})"
    )
    if result.status == "failed" and result.error:
        summary += f"\n  error: {result.error}"
    return summary


def _resolve_curation_transaction_id(
    conn, args,
) -> int | None:
    if args.transaction_id is not None:
        return args.transaction_id
    if args.doc_id is not None and args.sequence is not None:
        return find_transaction_id(
            conn, doc_id=args.doc_id, sequence=args.sequence
        )
    return None


def _run_curation(args, conn) -> int:
    """Execute the selected curation action and print its outcome."""
    if args.curate_candidates:
        candidates = list_amendment_candidates(conn)
        print(f"Amendment candidates ({len(candidates)}):")
        if not candidates:
            print("  (none)")
        for c in candidates:
            original = c.original_doc_id or "(unlinked)"
            print(f"  {c.doc_id} -> {original}")
        return 0

    if args.curate_unresolved:
        unresolved = list_unresolved_transactions(conn)
        print(f"Unresolved transactions ({len(unresolved)}):")
        if not unresolved:
            print("  (none)")
        for u in unresolved:
            flags = ", ".join(u.quality_flags)
            print(
                f"  {u.doc_id} seq={u.sequence} "
                f"txn={u.txn_date} notif={u.notification_date} [{flags}]"
            )
        return 0

    try:
        if args.curate_verify:
            transaction_id = _resolve_curation_transaction_id(conn, args)
            if transaction_id is None:
                print(
                    "error: --curate-verify requires a target (--transaction-id "
                    "or --doc-id with --sequence)",
                    file=sys.stderr,
                )
                return 2
            result = verify_transaction(
                conn,
                transaction_id=transaction_id,
                verified_transaction_date=args.verified_date,
                method=args.method,
                confidence=args.confidence,
                source_doc_id=args.source_doc,
                note=args.note,
            )
            print(
                f"Verified txn {result.transaction_id}: "
                f"{result.verified_transaction_date} "
                f"(method={result.method}, confidence={result.confidence}, "
                f"source={result.source_doc_id})"
            )
            return 0

        if args.curate_clear_verification:
            transaction_id = _resolve_curation_transaction_id(conn, args)
            if transaction_id is None:
                print(
                    "error: --curate-clear-verification requires a target "
                    "(--transaction-id or --doc-id with --sequence)",
                    file=sys.stderr,
                )
                return 2
            removed = clear_verification(conn, transaction_id=transaction_id)
            if removed:
                print(f"Cleared verification on txn {transaction_id}")
            else:
                print(f"Txn {transaction_id} had no verification to clear")
            return 0

        if args.curate_amend:
            link = link_amendment(
                conn,
                amended_doc_id=args.amended_doc,
                original_doc_id=args.original_doc,
                method=args.method,
                confidence=args.confidence,
                note=args.note,
            )
            print(
                f"Linked amendment {link.amended_doc_id} -> "
                f"{link.original_doc_id} "
                f"(method={link.method}, confidence={link.confidence})"
            )
            return 0

        if args.curate_unamend:
            removed = unlink_amendment(conn, amended_doc_id=args.amended_doc)
            if removed:
                print(f"Unlinked amendment {args.amended_doc}")
            else:
                print(f"{args.amended_doc} had no amendment link")
            return 0
    except CurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        "error: no curation action selected (see --help)",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or get_database_url()

    if args.recompute_flags:
        if args.as_of is None:
            print(
                "error: --recompute-flags requires --as-of YYYY-MM-DD",
                file=sys.stderr,
            )
            return 2
        if args.year is not None or args.backfill:
            print(
                "error: --recompute-flags cannot be combined with "
                "--year or --backfill (it covers all stored transactions)",
                file=sys.stderr,
            )
            return 2
        with psycopg.connect(database_url, autocommit=True) as conn:
            report = recompute_quality_flags(
                conn, as_of=args.as_of, dry_run=args.dry_run
            )
        mode = "dry-run" if report.dry_run else "recompute"
        print(f"Quality flags {mode} (as-of {args.as_of})")
        print(_format_report(report))
        return 0

    curation_actions = (
        args.curate_verify,
        args.curate_clear_verification,
        args.curate_amend,
        args.curate_unamend,
        args.curate_candidates,
        args.curate_unresolved,
    )
    if any(curation_actions):
        if sum(curation_actions) != 1:
            print("error: select exactly one curation action", file=sys.stderr)
            return 2
        if args.year is not None or args.backfill:
            print(
                "error: curation actions cannot be combined with "
                "--year or --backfill",
                file=sys.stderr,
            )
            return 2
        with psycopg.connect(database_url, autocommit=True) as conn:
            return _run_curation(args, conn)

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
