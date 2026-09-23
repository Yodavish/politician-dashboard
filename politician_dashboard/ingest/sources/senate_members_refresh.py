"""Regenerate the committed Senate membership snapshot.

The Senate eFD adapter (``senate_efd.py``) resolves a filer's state from the
current ``senators_cfm.xml`` listing plus the historical reference snapshot
committed as ``data/senate_members.json``. This module regenerates that
snapshot from the community-maintained United States Congress Legislators
dataset (``https://unitedstates.github.io/congress-legislators/``), which is
**not** an official publication of the U.S. Senate or the Library of Congress;
those.gov sources are Cloudflare-blocked for scripted access, which is why the
reproducible source lives here. Service-boundary members are cross-verified
against official ``bioguide.congress.gov`` JSON (fetchable only through
browser-class access) and the verified facts are recorded in the snapshot's
``boundary_members`` block as provenance -- never as resolver logic.

Usage::

    python -m politician_dashboard.ingest.sources.senate_members_refresh
    python -m politician_dashboard.ingest.sources.senate_members_refresh --check
    python -m politician_dashboard.ingest.sources.senate_members_refresh --cur PATH --hist PATH --check

``--check`` regenerates the snapshot in memory from the same inputs and fails
(nonzero exit, no file writes) if any member or term differs from the
committed asset.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

DATASET_CUR = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
DATASET_HIST = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"

GENERATED_AT_FORMAT = "%Y-%m-%d"

# Reference snapshot covers Senate membership from this date forward.
HISTORY_START = "2000-01-01"

SNAPSHOT_PATH = Path(__file__).parent / "data" / "senate_members.json"

# Service-boundary members verified out-of-band against official
# bioguide.congress.gov JSON on the generation date (scripted/gov access is
# Cloudflare-blocked; these were confirmed through browser-class access). This
# block is provenance/history only; the resolver never reads it.
BIOGUIDE_VERIFIED_BOUNDARIES: dict[str, dict[str, str]] = {
    "M001190": {
        "last_name": "Mullin",
        "state": "OK",
        "verified": "Resigned 2026-03-23 (bioguide departureReason 'Resigned'); "
        "class 2 successor Armstrong effective 2026-03-24.",
    },
    "A000383": {
        "last_name": "Armstrong",
        "state": "OK",
        "verified": "Senate service began 2026-03-24; currently serving "
        "(bioguide lists no endDate).",
    },
    "G000359": {
        "last_name": "Graham",
        "state": "SC",
        "verified": "Died 2026-07-11 (bioguide deathDate); bioguide's final job "
        "position has no structured endDate.",
    },
    "G000608": {
        "last_name": "Graham",
        "state": "SC",
        "verified": "Appointed, service began 2026-07-14. Note: source "
        "'name.last' artifact 'Graham Nordone' corrected from official_full "
        "'Darline Graham'.",
    },
}


def _todays_date() -> str:
    return date.today().isoformat()


def _source_member_sort_key(member: dict) -> tuple[str, str, str]:
    return (
        member["last_name"].lower(),
        member["first_name"].lower(),
        member["bioguide_id"],
    )


def _surname_from_official_full(record: dict) -> str:
    """Best-effort official surname from a congress-legislators record.

    ``name.last`` is authoritative except when it is not a suffix of the
    ``official_full`` name (a data artifact, e.g. Darline Graham's ``last`` is
    ``"Graham Nordone"`` while ``official_full`` is ``"Darline Graham"``).
    Compound surnames ("Van Hollen") and suffixes ("King, Jr.") survive the
    suffix check unmodified.
    """
    name = record.get("name") or {}
    last = (name.get("last") or "").strip()
    official_full = (name.get("official_full") or "").strip()
    if official_full and last and not official_full.endswith(last):
        last = official_full.split()[-1]
    return last


def _term_overlaps(term: dict, start: str, end: str) -> bool:
    """Whether ``term`` overlaps the inclusive ISO interval ``[start, end]``."""
    term_start = (term.get("start") or "").strip()
    term_end = (term.get("end") or "").strip()
    if not term_start:
        return False
    return not (term_start > end or (term_end and term_end < start))


def _iso_date(value: str) -> str:
    """Validate/normalize an ISO-8601 date string from the source dataset."""
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def build_members_snapshot(
    records: list[dict],
    *,
    history_start: str = HISTORY_START,
    generated_at: str | None = None,
) -> dict:
    """Build the reference snapshot dict from congress-legislators records.

    ``records`` is the concatenation of ``legislators-historical.json`` and
    ``legislators-current.json`` (a list of member records with ``id.bioguide``,
    ``name`` and ``terms``). Only Senate (``type == "sen"``) terms that overlap
    ``[history_start, today]`` are kept. Raises ``ValueError`` for malformed
    records that cannot produce a reliable snapshot.
    """
    generated_at = generated_at or _todays_date()
    today = _todays_date()  # upper bound of the coverage window

    members: list[dict] = []
    for record in records:
        bioguide = (record.get("id") or {}).get("bioguide", "").strip()
        if not bioguide:
            raise ValueError("member record missing id.bioguide")
        name = record.get("name") or {}
        first = (name.get("first") or "").strip()
        last = _surname_from_official_full(record)
        if not first or not last:
            raise ValueError(f"member {bioguide} missing name.first/last")

        sen_terms: list[dict] = []
        for term in record.get("terms") or []:
            if (term.get("type") or "").strip() != "sen":
                continue
            if not _term_overlaps(term, history_start, today):
                continue
            start = term.get("start") or ""
            if not start:
                raise ValueError(
                    f"sen term for {bioguide} missing start date"
                )
            end = term.get("end") or ""
            sen_terms.append(
                {
                    "start": _iso_date(start),
                    "end": _iso_date(end) if end else None,
                }
            )

        if not sen_terms:
            continue

        # Establishes state/party for the Senate service; source terms within
        # a contiguous run share one state and party. Include all overlapping
        # terms so interval lookups can rely on inclusive date ranges.
        sample = next((t for t in record["terms"] if t["type"] == "sen"), {})
        members.append(
            {
                "bioguide_id": bioguide,
                "last_name": last,
                "first_name": first,
                "state": (sample.get("state") or "").strip(),
                "party": (sample.get("party") or "").strip() or None,
                "terms": sorted(sen_terms, key=lambda t: t["start"]),
            }
        )

    if not members:
        raise ValueError("no Senate members produced from the supplied data")

    members.sort(key=_source_member_sort_key)

    return {
        "source": (
            "unitedstates/congress-legislators "
            "(legislators-current.json + legislators-historical.json)"
        ),
        "source_urls": [DATASET_CUR, DATASET_HIST],
        "provenance_note": (
            "This snapshot is derived from the community-maintained "
            "congress-legislators dataset, which is NOT an official "
            "publication of the U.S. Senate or the Library of Congress. "
            "Service-boundary members listed under 'boundary_members' were "
            "cross-checked against official bioguide.congress.gov JSON on the "
            "generation date through browser-class access (scripted gov "
            "requests are blocked); those verified facts are provenance only "
            "and are never consulted by the resolver, which matches identities "
            "and Senate service intervals and otherwise fails closed."
        ),
        "coverage_start": history_start,
        "generated_at": generated_at,
        "boundary_members": BIOGUIDE_VERIFIED_BOUNDARIES,
        "members": members,
    }


def _pretty_json(snapshot: dict) -> bytes:
    return (
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=False)
        + "\n"
    ).encode("utf-8")


def _write_snapshot(snapshot: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_pretty_json(snapshot))


def load_dataset(path: Path) -> list[dict]:
    data = json.loads(path.read_bytes())
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON array")
    return data


def _fetch_json(url: str) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "politician-dashboard/0.1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, list):
        raise ValueError(f"{url} is not a JSON array")
    return payload


def refresh(cur_path: Path | None, hist_path: Path | None) -> dict:
    """Load both datasets (files when given, otherwise network) and build."""
    if cur_path is not None and hist_path is not None:
        cur_records = load_dataset(cur_path)
        hist_records = load_dataset(hist_path)
    elif cur_path is not None or hist_path is not None:
        raise ValueError("--cur and --hist must be provided together")
    else:
        cur_records = _fetch_json(DATASET_CUR)
        hist_records = _fetch_json(DATASET_HIST)
    return build_members_snapshot([*hist_records, *cur_records])


def _diff_line(member: dict, field: str, committed: object, fresh: object) -> str:
    return f"  {member['bioguide_id']} {member['last_name']}, {member['first_name']}: {field} {committed!r} != {fresh!r}"


def diff_snapshots(committed: dict, fresh: dict) -> list[str]:
    """Return human-readable member differences (``members`` only; empty=equal).

    ``generated_at`` and the provenance notes are not identity fields; only
    member identity/terms are compared so ``--check`` catches real drift
    without failing on the machine-stamped generation date.
    """
    diffs: list[str] = []
    members = {m["bioguide_id"]: m for m in committed.get("members", [])}
    fresh_members = {m["bioguide_id"]: m for m in fresh.get("members", [])}
    for bioguide in sorted(set(members) | set(fresh_members)):
        if bioguide not in members:
            f = fresh_members[bioguide]
            diffs.append(
                f"  + new member {f['last_name']}, {f['first_name']} ({bioguide})"
            )
            continue
        if bioguide not in fresh_members:
            m = members[bioguide]
            diffs.append(
                f"  - removed member {m['last_name']}, {m['first_name']} ({bioguide})"
            )
            continue
        m, f = members[bioguide], fresh_members[bioguide]
        for field in ("last_name", "first_name", "state", "party"):
            if m.get(field) != f.get(field):
                diffs.append(_diff_line(m, field, m.get(field), f.get(field)))
        if m.get("terms") != f.get("terms"):
            diffs.append(_diff_line(m, "terms", m.get("terms"), f.get("terms")))
    return diffs


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="senate_members_refresh",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--cur",
        type=Path,
        default=None,
        help="local legislators-current.json (with --hist); default: fetch both URLs",
    )
    parser.add_argument("--hist", type=Path, default=None, help="local legislators-historical.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and fail (exit 1) on any difference from the committed asset",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the snapshot to PATH instead of the packaged asset",
    )
    args = parser.parse_args(argv)

    if (args.cur is None) != (args.hist is None):
        parser.error("--cur and --hist must be provided together")

    try:
        fresh = refresh(args.cur, args.hist)
    except Exception as exc:  # noqa: BLE001 - CLI surface
        print(f"error: {exc}")
        return 1

    if args.check:
        if not SNAPSHOT_PATH.exists():
            print(f"snapshot missing at {SNAPSHOT_PATH}; run without --check first")
            return 1
        committed = json.loads(SNAPSHOT_PATH.read_bytes())
        diffs = diff_snapshots(committed, fresh)
        if diffs:
            print(f"snapshot {SNAPSHOT_PATH} is out of date:")
            print("\n".join(diffs))
            return 1
        print(
            f"snapshot {SNAPSHOT_PATH} is up to date "
            f"(members: {len(fresh['members'])})"
        )
        return 0

    target = args.output or SNAPSHOT_PATH
    try:
        _write_snapshot(fresh, target)
    except Exception as exc:  # noqa: BLE001 - CLI surface
        print(f"error: {exc}")
        return 1
    print(f"wrote {target} (members: {len(fresh['members'])})")
    return 0


if __name__ == "__main__":
    sys.exit(_main())