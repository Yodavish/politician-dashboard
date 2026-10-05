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
    "asset_type_code": """lower(CASE t.asset_type_code
        WHEN '4K' THEN '401K and Other Non-Federal Retirement Accounts'
        WHEN '5C' THEN '529 College Savings Plan'
        WHEN '5F' THEN '529 Portfolio'
        WHEN '5P' THEN '529 Prepaid Tuition Plan'
        WHEN 'AB' THEN 'Asset-Backed Securities'
        WHEN 'BA' THEN 'Bank Accounts, Money Market Accounts and CDs'
        WHEN 'BK' THEN 'Brokerage Accounts'
        WHEN 'CO' THEN 'Collectibles'
        WHEN 'CS' THEN 'Corporate Securities (Bonds and Notes)'
        WHEN 'CT' THEN 'Cryptocurrency'
        WHEN 'DB' THEN 'Defined Benefit Pension'
        WHEN 'DO' THEN 'Debts Owed to the Filer'
        WHEN 'DS' THEN 'Delaware Statutory Trust'
        WHEN 'EF' THEN 'Exchange Traded Funds (ETF)'
        WHEN 'EQ' THEN 'Excepted/Qualified Blind Trust'
        WHEN 'ET' THEN 'Exchange Traded Notes'
        WHEN 'FA' THEN 'Farms'
        WHEN 'FE' THEN 'Foreign Exchange Position (Currency)'
        WHEN 'FN' THEN 'Fixed Annuity'
        WHEN 'FU' THEN 'Futures'
        WHEN 'GS' THEN 'Government Securities and Agency Debt'
        WHEN 'HE' THEN 'Hedge Funds & Private Equity Funds (EIF)'
        WHEN 'HN' THEN 'Hedge Funds & Private Equity Funds (non-EIF)'
        WHEN 'IC' THEN 'Investment Club'
        WHEN 'IH' THEN 'IRA (Held in Cash)'
        WHEN 'IP' THEN 'Intellectual Property & Royalties'
        WHEN 'IR' THEN 'IRA'
        WHEN 'MA' THEN 'Managed Accounts (e.g., SMA and UMA)'
        WHEN 'MF' THEN 'Mutual Funds'
        WHEN 'MO' THEN 'Mineral/Oil/Solar Energy Rights'
        WHEN 'OI' THEN 'Ownership Interest (Holding Investments)'
        WHEN 'OL' THEN 'Ownership Interest (Engaged in a Trade or Business)'
        WHEN 'OP' THEN 'Options'
        WHEN 'OT' THEN 'Other'
        WHEN 'PE' THEN 'Pensions'
        WHEN 'PM' THEN 'Precious Metals'
        WHEN 'PS' THEN 'Stock (Not Publicly Traded)'
        WHEN 'RE' THEN 'Real Estate Invest. Trust (REIT)'
        WHEN 'RF' THEN 'REIT (EIF)'
        WHEN 'RN' THEN 'REIT (non-EIF)'
        WHEN 'RP' THEN 'Real Property'
        WHEN 'RS' THEN 'Restricted Stock Units (RSUs)'
        WHEN 'SA' THEN 'Stock Appreciation Right'
        WHEN 'ST' THEN 'Stocks (including ADRs)'
        WHEN 'TR' THEN 'Trust'
        WHEN 'VA' THEN 'Variable Annuity'
        WHEN 'VI' THEN 'Variable Insurance'
        WHEN 'WU' THEN 'Whole/Universal Insurance'
        ELSE t.asset_type_code
    END)""",
    "disclosure_lag_days": "(f.filing_date - t.txn_date)",
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
