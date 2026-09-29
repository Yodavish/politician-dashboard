"""Rule constants for the first computed signal: the buy cluster.

A "buy cluster" is a burst of same-ticker purchases disclosed by several
different politicians within a short window. It is a *disclosure pattern*, not
evidence of coordination, and is labelled as such in the API so the dashboard
never implies intent.

The signal is computed on read from the existing ``filings`` and
``transactions`` tables. Nothing is materialized and no schema change is
required, so these parameters can be revised without a migration.
"""

from __future__ import annotations

# Stable machine identifier for this signal type.
BUY_CLUSTER = "buy_cluster"

# Human-readable label shown on the dashboard.
BUY_CLUSTER_LABEL = "Buy cluster"

# --- Rule parameters -----------------------------------------------------
# Returned to clients in the ``rule`` block of every response so a user can
# always see which thresholds produced a given signal.

# Open-market purchases only. 'S' (sale) and 'A' (award) are excluded.
BUY_CLUSTER_TXN_TYPE = "P"

# Threshold applied to distinct *politicians*. Transactions are the unit that
# gets grouped; repeated filings by one person do not add to this count.
BUY_CLUSTER_MIN_POLITICIANS = 3

# Purchases within this many days of each other stay in the same burst.
BUY_CLUSTER_GAP_DAYS = 7

# A burst must begin and end within this many days to qualify.
BUY_CLUSTER_MAX_SPAN_DAYS = 14

# Tickers must look like real equity tickers. The parser stores "--" for
# assets with no ticker (municipal bonds and similar), which would otherwise
# produce a meaningless "cluster" of unrelated bond purchases.
BUY_CLUSTER_TICKER_PATTERN = r"^[A-Z][A-Z0-9.\-]{0,5}$"

BUY_CLUSTER_RULE = {
    "type": BUY_CLUSTER,
    "label": BUY_CLUSTER_LABEL,
    "txn_type": BUY_CLUSTER_TXN_TYPE,
    "min_politicians": BUY_CLUSTER_MIN_POLITICIANS,
    "max_gap_days": BUY_CLUSTER_GAP_DAYS,
    "max_span_days": BUY_CLUSTER_MAX_SPAN_DAYS,
    "ticker_pattern": BUY_CLUSTER_TICKER_PATTERN,
    "excludes_future_dates": True,
    "materialized": False,
    "description": (
        "Three or more politicians disclosing open-market purchases (P) of the "
        "same equity ticker, where consecutive purchases are no more than 7 "
        "days apart and the whole burst spans no more than 14 days."
    ),
}

BUY_CLUSTER_LIMITATIONS = [
    (
        "A cluster reflects disclosure timing only. It is not evidence that the "
        "politicians coordinated, and implies nothing about their intent."
    ),
    (
        "Purchases may predate one another by months and still surface together, "
        "because disclosure is filed after the transaction date."
    ),
    (
        "Amounts are disclosure ranges, so the total is a range, not an exact sum."
    ),
    (
        "Small positions are reportable under STOCK Act rules, so cluster size "
        "does not imply unusually large positions."
    ),
    (
        "Politician identity is derived from name and district rather than a "
        "legislative roster, so people are grouped heuristically."
    ),
]
