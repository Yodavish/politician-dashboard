"""Regenerate the committed House membership snapshot.

The House Clerk adapter (``house_clerk.py``) validates every PTR filing
against the historical reference snapshot committed as
``data/house_members.json``. This module regenerates that snapshot from the
community-maintained United States Congress Legislators dataset
(``https://unitedstates.github.io/congress-legislators/``), which is **not**
an official publication of the U.S. House or the Library of Congress; those
.gov sources are Cloudflare-blocked for scripted access, which is why the
reproducible source lives here. Service-boundary members are cross-verified
against official ``bioguide.congress.gov`` JSON (fetchable only through
browser-class access) and the verified facts are recorded in the snapshot's
``boundary_members`` block as provenance -- never as resolver logic. The
official current-members feed ``clerk.house.gov/xml/lists/MemberData.xml`` is
curl-safe and cross-checks the current Congress's seats outside this script.

House districts are recorded per term but are informational: they renumber
after every census, so member resolution matches on state + name + service
interval only. District ``0`` marks an at-large representative or a delegate /
Resident Commissioner.

Usage::

    python -m politician_dashboard.ingest.sources.house_members_refresh
    python -m politician_dashboard.ingest.sources.house_members_refresh --check
    python -m politician_dashboard.ingest.sources.house_members_refresh --cur PATH --hist PATH --check

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

# Reference snapshot covers House membership from this date forward.
HISTORY_START = "2000-01-01"

SNAPSHOT_PATH = Path(__file__).parent / "data" / "house_members.json"

# Service-boundary members verified out-of-band against official
# bioguide.congress.gov JSON on the generation date (scripted/gov access is
# Cloudflare-blocked; these were confirmed through browser-class access).
# Death, resignation, and expulsion *end* dates agree exactly between bioguide
# and the snapshot (e.g. Santos expelled 2023-12-01, Gaetz resigned
# 2024-11-13, McEachin died 2022-11-28, Walorski died 2022-08-03, McCarthy
# resigned 2023-12-31). Special-election *start* dates differ by design:
# bioguide records the election/effective date (Patronis 2025-04-01, Fine
# 2025-04-01, Walkinshaw 2025-09-09, Menefee 2026-01-31) while the snapshot
# records the sworn-in date (2025-04-02, 2025-04-02, 2025-09-10, 2026-02-02);
# the resolver treats the seat as vacant before the sworn-in date. This block
# is provenance/history only; the resolver never reads it.
BIOGUIDE_VERIFIED_BOUNDARIES: dict[str, dict[str, str]] = {
    "M001165": {
        "last_name": "McCarthy",
        "state": "CA",
        "verified": "Resigned 2023-12-31 (bioguide departureReason 'Resigned'); "
        "served CA 22/23/20 across redistrictings.",
    },
    "S001222": {
        "last_name": "Santos",
        "state": "NY",
        "verified": "Expelled 2023-12-01 (bioguide departureReason 'Expelled', "
        "per H. Res. 878); seat vacant until Suozzi sworn 2024-02.",
    },
    "G000578": {
        "last_name": "Gaetz",
        "state": "FL",
        "verified": "Resigned 2024-11-13 (bioguide departureReason 'Resigned'); "
        "reelected to 119th but declined his seat; successor Patronis.",
    },
    "P000622": {
        "last_name": "Patronis",
        "state": "FL",
        "verified": "Special election 2025-04-01 (bioguide startDate); snapshot "
        "start 2025-04-02 is the sworn-in date.",
    },
    "F000484": {
        "last_name": "Fine",
        "state": "FL",
        "verified": "Special election 2025-04-01 (bioguide startDate); snapshot "
        "start 2025-04-02 is the sworn-in date.",
    },
    "W000831": {
        "last_name": "Walkinshaw",
        "state": "VA",
        "verified": "Special election 2025-09-09 (bioguide startDate); snapshot "
        "start 2025-09-10 is the sworn-in date.",
    },
    "M001245": {
        "last_name": "Menefee",
        "state": "TX",
        "verified": "Special election 2026-01-31 (bioguide startDate); snapshot "
        "start 2026-02-02 is the sworn-in date.",
    },
    "M001200": {
        "last_name": "McEachin",
        "state": "VA",
        "verified": "Died 2022-11-28 (bioguide deathDate; 117th endDate "
        "2022-11-28, departureReason 'Died').",
    },
    "W000813": {
        "last_name": "Walorski",
        "state": "IN",
        "verified": "Died 2022-08-03 (bioguide deathDate; 117th endDate "
        "2022-08-03, departureReason 'Died').",
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


_SUFFIX_TOKEN_PATTERN = frozenset(
    {"jr", "sr", "junior", "senior", "ii", "iii", "iv", "v"}
)


def _surname_from_official_full(record: dict) -> str:
    """Best-effort official surname from a congress-legislators record.

    ``name.last`` is authoritative whenever it appears as a contiguous phrase
    of ``official_full`` without a real name following it (the trailing
    ``"Jr."`` of "Donald M. Payne, Jr." is a generational suffix, not a
    sibling name). Compound surnames ("Wasserman Schultz") survive because the
    match is on the whole phrase, not a single token. It is only corrected
    when the source carried an artifact that puts a non-name in ``name.last``
    (e.g. ``name.last`` ``==`` ``"Graham Nordone"`` vs official ``"Graham"``).
    """
    name = record.get("name") or {}
    last = (name.get("last") or "").strip()
    official_full = (name.get("official_full") or "").strip()
    if not last or not official_full:
        return last
    tokens = [token for token in official_full.split() if token]
    phrase = last.split()
    positions: list[int] = []
    for index in range(len(tokens) - len(phrase) + 1):
        if all(
            tokens[index + offset].strip(".,'\u2019").lower()
            == phrase[offset].lower()
            for offset in range(len(phrase))
        ):
            positions.append(index)
    if positions:
        trailing = tokens[positions[0] + len(phrase):]
        if all(
            token.strip(".,'\u2019").lower() in _SUFFIX_TOKEN_PATTERN
            for token in trailing
        ):
            return last
    meaningful = [
        token
        for token in tokens
        if token.strip(".,'\u2019").lower() not in _SUFFIX_TOKEN_PATTERN
    ]
    if meaningful:
        return meaningful[-1]
    return last


def _official_given_name(record: dict) -> str:
    """Official full given-name text of a record.

    Preferred derivation is the ``official_full`` name minus its surname and
    generational-suffix tokens ("April McClain Delaney" with the compound
    surname "McClain Delaney" leaves "April"; "Linda T. Sánchez" leaves "Linda
    T."). The union record's optional ``middle`` field is not part of the
    registered name ("April Lynn" vs the official "April McClain"), so
    ``first`` + ``middle`` is only a fallback when ``official_full`` is empty
    or yields nothing. House filings are filed under the member's fuller
    registered given name (e.g. "Greg Steube" for the "W. Gregory Steube"
    recorded by the Clerk, "Rohit Khanna" for "Ro Khanna"), so the resolver
    matches against the full given name, never just the first token.
    """
    name = record.get("name") or {}
    official_full = (name.get("official_full") or "").strip()
    if official_full:
        surname = (name.get("last") or "").strip()
        surname_tokens = {
            token.strip(".,'\u2019").lower()
            for token in surname.split()
            if token.strip(".,'\u2019")
        }
        given_tokens = []
        for token in official_full.split():
            cleaned = token.strip(".,'\u2019").lower()
            if not cleaned or cleaned in _SUFFIX_TOKEN_PATTERN:
                continue
            if cleaned in surname_tokens:
                continue
            given_tokens.append(token)
        if given_tokens:
            return " ".join(given_tokens)
    first = (name.get("first") or "").strip()
    middle = (name.get("middle") or "").strip()
    if not first:
        return first
    if not middle:
        return first
    return f"{first} {middle}".strip()


def _surname_filer_alternate(record: dict) -> str:
    """Alternate surname spelling a filer may use.

    eFD ``LastName`` can drop the roster's compound surname to its final
    surname token (``"Delaney"`` where the Clerk roll-call style is the
    compound ``"McClain Delaney"``). The last meaningful token of
    ``official_full`` is that alternate; it differs from the roster surname
    (compared after normalization) precisely when such a filing variant
    exists. When the roster surname is already the final token the alternate
    is meaningless and the empty string is returned.
    """
    name = record.get("name") or {}
    last = _surname_from_official_full(record)
    official_full = (name.get("official_full") or "").strip()
    if not last or not official_full:
        return ""
    tokens = [token for token in official_full.split() if token]
    meaningful = [
        token
        for token in tokens
        if token.strip(".,'\u2019").lower() not in _SUFFIX_TOKEN_PATTERN
    ]
    if not meaningful:
        return ""
    last_token = meaningful[-1]
    if last_token.strip(".,'\u2019").lower() == last.lower():
        return ""
    return last_token


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
    ``name`` and ``terms``). Only House (``type == "rep"``) terms that overlap
    ``[history_start, today]`` are kept, with their ``district`` (``0`` for
    at-large delegates/resident commissioners). Raises ``ValueError`` for
    malformed records that cannot produce a reliable snapshot.
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

        rep_terms: list[dict] = []
        for term in record.get("terms") or []:
            if (term.get("type") or "").strip() != "rep":
                continue
            if not _term_overlaps(term, history_start, today):
                continue
            start = term.get("start") or ""
            if not start:
                raise ValueError(
                    f"rep term for {bioguide} missing start date"
                )
            end = term.get("end") or ""
            district = term.get("district")
            if not isinstance(district, int):
                raise ValueError(
                    f"rep term for {bioguide} has non-integer district "
                    f"{district!r}"
                )
            rep_terms.append(
                {
                    "start": _iso_date(start),
                    "end": _iso_date(end) if end else None,
                    "district": district,
                }
            )

        if not rep_terms:
            continue

        # Member-level state/party from the first included House term; within
        # the snapshot a member represents one state/territory.
        sample = next(
            (t for t in record.get("terms") or [] if t["type"] == "rep"),
            {},
        )
        members.append(
            {
                "bioguide_id": bioguide,
                "last_name": last,
                "first_name": first,
                "given_name": _official_given_name(record),
                "state": (sample.get("state") or "").strip(),
                "party": (sample.get("party") or "").strip() or None,
                "terms": sorted(rep_terms, key=lambda t: t["start"]),
            }
            | (
                {"last_name_alt": alt}
                if (alt := _surname_filer_alternate(record))
                else {}
            )
        )

    if not members:
        raise ValueError("no House members produced from the supplied data")

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
            "publication of the U.S. House of Representatives or the Library "
            "of Congress. Per member it records the official full given name "
            "(first + middle names, as House filings name members by their "
            "fuller given name) and a surname derived from the source, keeping "
            "compound surnames intact (e.g. Wasserman Schultz) and never a "
            "trailing generational suffix. Service-boundary members listed "
            "under 'boundary_members' were cross-checked against official "
            "bioguide.congress.gov JSON on the generation date through "
            "browser-class access (scripted gov requests are blocked); the "
            "official current-Congress MemberData.xml feed is exercised by a "
            "skippable live test. Those verified facts are provenance only "
            "and are never consulted by the resolver, which matches "
            "identities and House service intervals by state + name + date, "
            "treats district numbers as informational, and otherwise fails "
            "closed."
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
        prog="house_members_refresh",
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