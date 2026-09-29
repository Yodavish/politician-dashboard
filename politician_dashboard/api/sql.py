"""Shared SQL for the read-only API.

Keeps parameterized WHERE building and row rows-to-dict mapping in one place
so the route handlers stay thin. All filters are parameterized; no user input
is ever interpolated into SQL.
"""

from __future__ import annotations

from datetime import date

from politician_dashboard.api.errors import BadRequestError

# Whitelisted sort keys mapped to a safe SQL column expression.
FILING_SORTS: dict[str, str] = {
    "created_at": "f.created_at",
    "filing_date": "f.filing_date",
}
TRANSACTION_SORTS: dict[str, str] = {
    "txn_date": "t.txn_date",
    "notification_date": "t.notification_date",
    "politician_name": "lower(concat_ws(' ', f.first_name, f.last_name))",
    "asset_name": "lower(t.asset_name)",
    "ticker": "lower(t.ticker)",
    "txn_type": "t.txn_type",
    "owner": "lower(t.owner_token)",
    "amount_min": "t.amount_min",
    "amount_max": "t.amount_max",
    "created_at": "f.created_at",
    "doc_id": "lower(f.doc_id)",
}
# Sort keys for computed signals. These address the aggregated cluster
# columns produced by the signal CTE, not raw table columns.
SIGNAL_SORTS: dict[str, str] = {
    "politician_count": "c.politician_count",
    "transaction_count": "c.transaction_count",
    "total_max": "c.total_max",
    "total_min": "c.total_min",
    "span_days": "c.span_days",
    "start_date": "c.start_date",
    "ticker": "lower(c.ticker)",
}


def parse_sort(sort: str, sorts: dict[str, str], default: str) -> tuple[str, bool]:
    """Return ``(column_expr, descending)`` from a whitelisted sort key.

    A leading ``-`` means descending. Unknown keys raise BadRequestError.
    """
    value = sort or default
    descending = value.startswith("-")
    key = value[1:] if descending else value
    if key not in sorts:
        raise BadRequestError(f"invalid sort key: {key}")
    return sorts[key], descending


def parse_date(value: str | None, field: str) -> str | None:
    """Validate an ISO date filter (returns it unchanged)."""
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise BadRequestError(f"invalid {field}: expected YYYY-MM-DD") from exc
    return value


def add_date_range(
    clauses: list[str], params: list, min_value: str | None,
    max_value: str | None, column: str, field: str,
) -> None:
    """Add ``column >= min AND column <= max`` clauses if provided."""
    if min_value is not None:
        clauses.append(f"{column} >= %s")
        params.append(min_value)
    if max_value is not None:
        clauses.append(f"{column} <= %s")
        params.append(max_value)
    if min_value and max_value and min_value > max_value:
        raise BadRequestError(f"{field}: min cannot be after max")


def add_range(
    clauses: list[str], params: list, low: float | None,
    high: float | None, column: str, field: str,
) -> None:
    if low is not None:
        clauses.append(f"{column} >= %s")
        params.append(low)
    if high is not None:
        clauses.append(f"{column} <= %s")
        params.append(high)
    if low is not None and high is not None and low > high:
        raise BadRequestError(f"{field}: min cannot exceed max")
