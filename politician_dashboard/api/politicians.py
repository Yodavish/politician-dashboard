"""Derived politician identity.

A "politician" is not a table in the V1 schema; it is an emergent identity
derived from a filing's person fields. This is a V1 convenience identifier,
NOT an authoritative permanent politician identity. It groups a member's
filings by ``(state_district, first_name, last_name)``.
"""

from __future__ import annotations

import re

from politician_dashboard.api.errors import NotFoundError

STATE_DISTRICT_RE = re.compile(r"^([A-Za-z]{2}\d{1,2})_(.+)$")


def _norm(value: str | None) -> str:
    return re.sub(r"[\s_]+", "_", (value or "").strip().lower())


def politician_id(state_district: str, first: str, last: str) -> str:
    """Return the canonical URL slug for a person.

    Example: ``ROBERT Aderholt, AL04`` -> ``al04_robert_aderholt``.
    """
    return f"{_norm(state_district)}_{_norm(first)}_{_norm(last)}"


def parse_state_district(politician_id: str) -> str | None:
    """Return the leading state_district component of an id, if present."""
    match = STATE_DISTRICT_RE.match(politician_id)
    return match.group(1).upper() if match else None


def _person_key(first: str, last: str) -> str:
    return f"{_norm(first)}_{_norm(last)}"


def resolve_politician(conn, politician_id: str) -> tuple[str, str, str]:
    """Resolve an id to ``(state_district, first_name, last_name)``.

    The remaining portion of the id (after the state_district prefix) is
    matched against the distinct person rows for that district, which
    correctly handles multi-word names without fragile first/last splitting.
    """
    district = parse_state_district(politician_id)
    if district is None:
        raise NotFoundError(f"unknown politician: {politician_id}")
    target = politician_id[len(f"{district}_"):]
    target = target.rstrip("_")

    rows = conn.execute(
        "SELECT DISTINCT first_name, last_name FROM filings WHERE state_district = %s",
        (district,),
    ).fetchall()

    for first, last in rows:
        if _person_key(first, last) == target:
            return district, first, last

    raise NotFoundError(f"unknown politician: {politician_id}")
