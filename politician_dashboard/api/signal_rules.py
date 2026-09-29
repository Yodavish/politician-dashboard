"""Rule constants for the computed cluster signals.

A "cluster" is a burst of same-ticker transactions disclosed by several
different politicians within a short window. It is a *disclosure pattern*, not
evidence of coordination, and is labelled as such in the API so the dashboard
never implies intent.

Two types share one clustering implementation: ``buy_cluster`` over ``P``
transactions and ``sell_cluster`` over ``S`` transactions. They differ only in
which transaction type they read, the id prefix they emit, and their wording.
The mechanics below are deliberately shared rather than restated per type, so
the two cannot drift apart silently.

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

# --- Sell cluster --------------------------------------------------------
#
# Same mechanics as the buy cluster over a different transaction type.

# Stable machine identifier for this signal type.
SELL_CLUSTER = "sell_cluster"

# Human-readable label shown on the dashboard.
SELL_CLUSTER_LABEL = "Sell cluster"

# Open-market sales only. 'S (partial)' is deliberately excluded: it is a
# distinct disclosure category (a partial disposition rather than a full sale),
# and roughly a quarter of those rows are joint-tenancy holdings where the
# politician files alongside a co-owner. Merging the two would describe a
# pattern the source data does not support. 'P' and 'E' are excluded too.
SELL_CLUSTER_TXN_TYPE = "S"

# The clustering mechanics are intentionally identical to the buy cluster's,
# so these reference those values instead of repeating the literals.
SELL_CLUSTER_MIN_POLITICIANS = BUY_CLUSTER_MIN_POLITICIANS
SELL_CLUSTER_GAP_DAYS = BUY_CLUSTER_GAP_DAYS
SELL_CLUSTER_MAX_SPAN_DAYS = BUY_CLUSTER_MAX_SPAN_DAYS
SELL_CLUSTER_TICKER_PATTERN = BUY_CLUSTER_TICKER_PATTERN

SELL_CLUSTER_RULE = {
    "type": SELL_CLUSTER,
    "label": SELL_CLUSTER_LABEL,
    "txn_type": SELL_CLUSTER_TXN_TYPE,
    "min_politicians": SELL_CLUSTER_MIN_POLITICIANS,
    "max_gap_days": SELL_CLUSTER_GAP_DAYS,
    "max_span_days": SELL_CLUSTER_MAX_SPAN_DAYS,
    "ticker_pattern": SELL_CLUSTER_TICKER_PATTERN,
    "excludes_future_dates": True,
    "materialized": False,
    "description": (
        "Three or more politicians disclosing open-market sales (S) of the "
        "same equity ticker, where consecutive sales are no more than 7 days "
        "apart and the whole burst spans no more than 14 days."
    ),
}

SELL_CLUSTER_LIMITATIONS = [
    (
        "A cluster reflects disclosure timing only. It is not evidence that the "
        "politicians coordinated, and implies nothing about their intent."
    ),
    (
        "Partial sales ('S (partial)') are a separate disclosure category and "
        "are not included here."
    ),
    (
        "Sales may predate one another by months and still surface together, "
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

# --- Registry -------------------------------------------------------------
#
# One entry per signal type. The id prefix is part of the public id
# (``<prefix><TICKER>_<YYYY-MM-DD>``) and is what lets the detail route
# resolve a signal without being told which type it is.

SIGNAL_RULES: dict[str, dict] = {
    BUY_CLUSTER: {
        "rule": BUY_CLUSTER_RULE,
        "limitations": BUY_CLUSTER_LIMITATIONS,
        "id_prefix": "bc_",
    },
    SELL_CLUSTER: {
        "rule": SELL_CLUSTER_RULE,
        "limitations": SELL_CLUSTER_LIMITATIONS,
        "id_prefix": "sc_",
    },
}

# Maps an id prefix back to its signal type, for detail-route dispatch.
SIGNAL_TYPE_BY_PREFIX: dict[str, str] = {
    entry["id_prefix"]: signal_type
    for signal_type, entry in SIGNAL_RULES.items()
}
