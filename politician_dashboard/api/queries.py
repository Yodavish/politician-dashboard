"""Data access (parameterized SQL) for the read-only API.

Each function returns raw rows plus a total count for offset pagination.
Column names are aliased to snake_case for direct use by the schema mapping.
"""

from __future__ import annotations

from politician_dashboard.api import sql as _sql
from politician_dashboard.api.signal_rules import (
    SIGNAL_RULES,
)


def _count(conn, base_from: str, clauses: list, params: list) -> int:
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    row = conn.execute(
        f"SELECT count(*) FROM {base_from} {where}", params
    ).fetchone()
    return int(row[0])


def list_filings(conn, *, filters: dict, sort_key: str, limit: int, offset: int):
    clauses: list[str] = []
    params: list = []

    if filters.get("politician") is not None:
        district, first, last = filters["politician"]
        clauses.append("f.state_district = %s AND lower(f.first_name) = %s "
                       "AND lower(f.last_name) = %s")
        params += [district, first.lower(), last.lower()]
    if filters.get("state_prefix") is not None:
        clauses.append("f.state_district LIKE %s")
        params.append(filters["state_prefix"])
    if filters.get("year") is not None:
        clauses.append("f.year = %s")
        params.append(filters["year"])
    _sql.add_date_range(
        clauses, params, filters.get("filing_date_min"), filters.get("filing_date_max"),
        "f.filing_date", "filing_date",
    )

    column, descending = _sql.parse_sort(sort_key, _sql.FILING_SORTS, "created_at")
    order = "DESC" if descending else "ASC"
    base_from = "filings f"
    total = _count(conn, base_from, clauses, params)
    rows = conn.execute(
        f"""
        SELECT f.id, f.doc_id, f.year, f.prefix, f.first_name, f.last_name,
               f.suffix, f.state_district, f.filing_date, f.doc_kind, f.pdf_url,
               f.downloaded_at, f.created_at,
               (SELECT count(*) FROM transactions t WHERE t.filing_id = f.id)
                   AS transaction_count
        FROM {base_from}
        {f'WHERE {" AND ".join(clauses)}' if clauses else ''}
        ORDER BY {column} {order}, f.doc_id
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    ).fetchall()
    return total, rows


def list_transactions(
    conn, *, filters: dict, sort_key: str, limit: int, offset: int,
):
    clauses: list[str] = []
    params: list = []

    if filters.get("politician") is not None:
        district, first, last = filters["politician"]
        clauses.append("f.state_district = %s AND lower(f.first_name) = %s "
                       "AND lower(f.last_name) = %s")
        params += [district, first.lower(), last.lower()]
    if filters.get("politician_name") is not None:
        clauses.append("(f.first_name || ' ' || f.last_name) ILIKE %s")
        params.append(f"%{filters['politician_name']}%")
    if filters.get("doc_id") is not None:
        clauses.append("t.filing_id = (SELECT id FROM filings WHERE doc_id = %s)")
        params.append(filters["doc_id"])
    if filters.get("ticker") is not None:
        clauses.append("lower(t.ticker) = lower(%s)")
        params.append(filters["ticker"])
    if filters.get("asset_type_code") is not None:
        clauses.append("t.asset_type_code = %s")
        params.append(filters["asset_type_code"])
    if filters.get("txn_type") is not None:
        clauses.append("t.txn_type = %s")
        params.append(filters["txn_type"])
    if filters.get("owner") is not None:
        clauses.append("t.owner_token = %s")
        params.append(filters["owner"])
    _sql.add_date_range(
        clauses, params, filters.get("txn_date_min"), filters.get("txn_date_max"),
        "t.txn_date", "txn_date",
    )
    _sql.add_range(
        clauses, params, filters.get("amount_min"), filters.get("amount_max"),
        "t.amount_min", "amount",
    )

    column, descending = _sql.parse_sort(
        sort_key, _sql.TRANSACTION_SORTS, "txn_date"
    )
    order = "DESC" if descending else "ASC"
    amount_secondary = (
        f", t.amount_max {order} NULLS LAST"
        if sort_key.lstrip("-") == "amount_min"
        else ""
    )
    base_from = "transactions t JOIN filings f ON f.id = t.filing_id"
    total = _count(conn, base_from, clauses, params)
    rows = conn.execute(
        f"""
        SELECT t.id, t.filing_id, f.doc_id, f.filing_date, t.sequence,
               t.asset_name, t.ticker, t.asset_type_code, t.txn_type,
               t.txn_date, t.notification_date, t.amount_min, t.amount_max,
               t.amount_raw, t.owner_token, t.filing_status, t.ownership_source,
               t.notes, t.txn_source_id,
               f.first_name, f.last_name, f.state_district, t.quality_flags,
               t.verified_transaction_date, t.verification_method,
               t.verification_confidence, t.verification_source_doc_id,
               t.verification_note, t.verified_at,
               EXISTS (SELECT 1 FROM filings vf
                       WHERE vf.doc_id = t.verification_source_doc_id)
                   AS verification_source_doc_exists
        FROM {base_from}
        {f'WHERE {" AND ".join(clauses)}' if clauses else ''}
        ORDER BY {column} {order} NULLS LAST{amount_secondary}, t.id
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    ).fetchall()
    return total, rows


def get_filing(conn, doc_id: str):
    return conn.execute(
        """
        SELECT f.id, f.doc_id, f.year, f.prefix, f.first_name, f.last_name,
               f.suffix, f.state_district, f.filing_date, f.doc_kind, f.pdf_url,
               f.downloaded_at, f.created_at,
               (SELECT count(*) FROM transactions t WHERE t.filing_id = f.id)
                   AS transaction_count,
               (SELECT o.doc_id FROM filings o WHERE o.id = f.amends_filing_id)
                   AS amends_doc_id,
               f.amendment_method, f.amendment_confidence, f.amendment_note,
               f.amendment_verified_at
        FROM filings f WHERE f.doc_id = %s
        """,
        (doc_id,),
    ).fetchone()


def list_filing_transactions(conn, filing_id: int):
    return conn.execute(
        """
        SELECT t.id, t.filing_id, f.doc_id, f.filing_date, t.sequence,
               t.asset_name, t.ticker, t.asset_type_code, t.txn_type,
               t.txn_date, t.notification_date, t.amount_min, t.amount_max,
               t.amount_raw, t.owner_token, t.filing_status, t.ownership_source,
               t.notes, t.txn_source_id,
               f.first_name, f.last_name, f.state_district, t.quality_flags,
               t.verified_transaction_date, t.verification_method,
               t.verification_confidence, t.verification_source_doc_id,
               t.verification_note, t.verified_at,
               EXISTS (SELECT 1 FROM filings vf
                       WHERE vf.doc_id = t.verification_source_doc_id)
                   AS verification_source_doc_exists
        FROM transactions t JOIN filings f ON f.id = t.filing_id
        WHERE t.filing_id = %s
        ORDER BY t.sequence
        """,
        (filing_id,),
    ).fetchall()


def list_filing_amendments(conn, filing_id: int):
    """Return the filings that were curated as amendments of ``filing_id``."""
    return conn.execute(
        """
        SELECT f.id, f.doc_id, f.year, f.prefix, f.first_name, f.last_name,
               f.suffix, f.state_district, f.filing_date, f.doc_kind, f.pdf_url,
               f.downloaded_at, f.created_at,
               (SELECT count(*) FROM transactions t WHERE t.filing_id = f.id)
                   AS transaction_count,
               (SELECT o.doc_id FROM filings o WHERE o.id = f.amends_filing_id)
                   AS amends_doc_id,
               f.amendment_method, f.amendment_confidence, f.amendment_note,
               f.amendment_verified_at
        FROM filings f
        WHERE f.amends_filing_id = %s
        ORDER BY f.filing_date, f.doc_id
        """,
        (filing_id,),
    ).fetchall()


def list_politicians(
    conn, *, filters: dict, sort_key: str, limit: int, offset: int,
):
    clauses: list[str] = []
    params: list = []
    if filters.get("state_prefix") is not None:
        clauses.append("lower(f.state_district) LIKE %s")
        params.append(filters["state_prefix"])
    if filters.get("name") is not None:
        clauses.append("(f.first_name || ' ' || f.last_name) ILIKE %s")
        params.append(f"%{filters['name']}%")

    column, descending = _sql.parse_sort(
        sort_key,
        {
            "name": "lower(concat_ws(' ', f.first_name, f.last_name))",
            "last_name": "lower(f.last_name)",
            "state_district": "lower(f.state_district)",
            "filing_count": "count(DISTINCT f.id)",
            "transaction_count": "count(t.id)",
        },
        "last_name",
    )
    order = "DESC" if descending else "ASC"
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total_row = conn.execute(
        f"""
        SELECT count(*) FROM (
            SELECT 1 FROM filings f {where} GROUP BY f.state_district,
                lower(f.first_name), lower(f.last_name)
        ) g
        """,
        params,
    ).fetchone()
    total = int(total_row[0])

    rows = conn.execute(
        f"""
        SELECT lower(f.state_district) AS sd, f.first_name, f.last_name,
               f.state_district,
               count(DISTINCT f.id) AS filing_count,
               count(t.id) AS transaction_count
        FROM filings f
        LEFT JOIN transactions t ON t.filing_id = f.id
        {where}
        GROUP BY lower(f.state_district), f.first_name, f.last_name, f.state_district
        ORDER BY {column} {order} NULLS LAST,
                 lower(f.last_name), lower(f.first_name), lower(f.state_district),
                 f.last_name, f.first_name, f.state_district
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    ).fetchall()
    return total, rows


def get_politician(conn, district: str, first: str, last: str):
    return conn.execute(
        """
        SELECT lower(f.state_district) AS sd, f.first_name, f.last_name,
               f.state_district,
               count(DISTINCT f.id) AS filing_count,
               count(t.id) AS transaction_count
        FROM filings f
        LEFT JOIN transactions t ON t.filing_id = f.id
        WHERE f.state_district = %s AND lower(f.first_name) = %s
            AND lower(f.last_name) = %s
        GROUP BY lower(f.state_district), f.first_name, f.last_name, f.state_district
        """,
        (district, first.lower(), last.lower()),
    ).fetchone()


# --- Cluster signals -----------------------------------------------------
#
# The signal is computed on read with window functions, so there is no stored
# table and no migration. See ``api/signal_rules.py`` for the parameters and
# the caveat text returned to clients.
#
# One implementation serves every cluster type. Which transactions it reads
# (``txn_type``), how tight a burst must be, how wide it may be, and which id
# prefix it emits are all bound as parameters taken from ``SIGNAL_RULES``; only
# ``type`` changes between one signal and the next.
#
# Cluster identity is ``(ticker, burst_id)``, and a burst's first transaction
# date is unique within a ticker, so ``(ticker, start_date)`` alone identifies
# a signal. That pair is what the public ``<prefix><TICKER>_<YYYY-MM-DD>`` id
# encodes, which lets the detail route look a signal up without storing or
# decoding a hash.

# Mirrors ``politicians.politician_id`` so a signal's people match the
# politician ids used everywhere else in the API.
_PERSON_KEY_SQL = r"""
    regexp_replace(lower(btrim(f.state_district)), '[\s_]+', '_', 'g') || '_' ||
    regexp_replace(lower(btrim(f.first_name)), '[\s_]+', '_', 'g') || '_' ||
    regexp_replace(lower(btrim(f.last_name)), '[\s_]+', '_', 'g')
"""

# Named placeholders (``%(name)s``) are used because the same fragment is
# embedded in several statements with different trailing parameters.
_CLUSTER_CTE = f"""
WITH events AS (
    SELECT
        t.id,
        t.filing_id,
        f.doc_id,
        t.sequence,
        t.ticker,
        t.asset_name,
        t.asset_type_code,
        t.txn_type,
        t.txn_date,
        t.notification_date,
        t.amount_min,
        t.amount_max,
        t.amount_raw,
        t.owner_token,
        f.first_name,
        f.last_name,
        f.state_district,
        {_PERSON_KEY_SQL} AS person_key
    FROM transactions t
    JOIN filings f ON f.id = t.filing_id
    WHERE t.txn_type = %(txn_type)s
      AND t.ticker ~ %(ticker_pattern)s
      AND t.txn_date <= CURRENT_DATE
),
ordered AS (
    SELECT events.*,
           lag(txn_date) OVER (
               PARTITION BY ticker ORDER BY txn_date, id
           ) AS prev_date
    FROM events
),
bursts AS (
    SELECT ordered.*,
           sum(CASE WHEN prev_date IS NULL
                     OR txn_date - prev_date > %(max_gap_days)s
                    THEN 1 ELSE 0 END) OVER (
               PARTITION BY ticker ORDER BY txn_date, id
           ) AS burst_id
    FROM ordered
),
clusters AS (
    SELECT
        ticker,
        burst_id,
        min(txn_date) AS start_date,
        max(txn_date) AS end_date,
        (max(txn_date) - min(txn_date))::int AS span_days,
        count(*)::int AS transaction_count,
        count(DISTINCT person_key)::int AS politician_count,
        sum(amount_min) AS total_min,
        sum(amount_max) AS total_max,
        mode() WITHIN GROUP (ORDER BY asset_name)
            FILTER (WHERE asset_name IS NOT NULL) AS asset_name
    FROM bursts
    GROUP BY ticker, burst_id
    HAVING count(DISTINCT person_key) >= %(min_politicians)s
       AND (max(txn_date) - min(txn_date)) <= %(max_span_days)s
)
"""

# Shared summary projection so the list and detail responses are built from
# exactly the same expression and cannot drift apart. The id prefix is bound
# per signal type (``bc_`` for buy clusters, ``sc_`` for sell clusters).
_CLUSTER_SUMMARY_SELECT = """
    SELECT
        %(id_prefix)s || lower(c.ticker) || '_' || to_char(c.start_date, 'YYYY-MM-DD')
            AS id,
        c.ticker,
        c.asset_name,
        c.transaction_count,
        c.politician_count,
        c.start_date,
        c.end_date,
        c.span_days,
        c.total_min,
        c.total_max,
        coalesce(people.politicians, '[]'::jsonb) AS politicians
    FROM clusters c
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object(
                   'id', g.person_key,
                   'name', concat_ws(' ', g.first_name, g.last_name),
                   'state_district', g.state_district,
                   'transaction_count', g.transaction_count,
                   'amount_min', g.amount_min,
                   'amount_max', g.amount_max
               ) ORDER BY g.amount_max DESC NULLS LAST, g.person_key)
               AS politicians
        FROM (
            SELECT b.person_key,
                   min(b.first_name) AS first_name,
                   min(b.last_name) AS last_name,
                   min(b.state_district) AS state_district,
                   count(*)::int AS transaction_count,
                   sum(b.amount_min) AS amount_min,
                   sum(b.amount_max) AS amount_max
            FROM bursts b
            WHERE b.ticker = c.ticker AND b.burst_id = c.burst_id
            GROUP BY b.person_key
        ) g
    ) people ON TRUE
"""


def _cluster_params(signal_type: str) -> dict:
    """Rule parameters bound into every cluster statement."""
    rule = SIGNAL_RULES[signal_type]["rule"]
    return {
        "id_prefix": SIGNAL_RULES[signal_type]["id_prefix"],
        "txn_type": rule["txn_type"],
        "ticker_pattern": rule["ticker_pattern"],
        "max_gap_days": rule["max_gap_days"],
        "min_politicians": rule["min_politicians"],
        "max_span_days": rule["max_span_days"],
    }


def _cluster_filters(filters: dict, params: dict) -> list[str]:
    """Build post-cluster filter clauses.

    Filters are applied to the *computed* clusters rather than to the
    transactions feeding them. Narrowing the input first would move burst
    boundaries and make the same signal resolve to a different id depending
    on the query string.
    """
    clauses: list[str] = []
    if filters.get("ticker") is not None:
        clauses.append("lower(c.ticker) = lower(%(filter_ticker)s)")
        params["filter_ticker"] = filters["ticker"]
    # Narrows already-computed clusters by width. The clustering rule itself
    # still caps a burst at its own max_span_days, so a ceiling above that
    # value is accepted but cannot exclude anything.
    if filters.get("span_days_max") is not None:
        clauses.append("c.span_days <= %(span_days_max)s")
        params["span_days_max"] = filters["span_days_max"]
    if filters.get("politician") is not None:
        district, first, last = filters["politician"]
        clauses.append(
            "EXISTS ("
            "  SELECT 1 FROM bursts b"
            "  WHERE b.ticker = c.ticker AND b.burst_id = c.burst_id"
            "    AND b.state_district = %(politician_district)s"
            "    AND lower(b.first_name) = lower(%(politician_first)s)"
            "    AND lower(b.last_name) = lower(%(politician_last)s)"
            ")"
        )
        params["politician_district"] = district
        params["politician_first"] = first
        params["politician_last"] = last
    # Overlap semantics: keep clusters whose window touches the range, so a
    # cluster that starts before the range is not silently dropped.
    if filters.get("start_date") is not None:
        clauses.append("c.end_date >= %(filter_start_date)s")
        params["filter_start_date"] = filters["start_date"]
    if filters.get("end_date") is not None:
        clauses.append("c.start_date <= %(filter_end_date)s")
        params["filter_end_date"] = filters["end_date"]
    return clauses


def list_clusters(
    conn, *, signal_type: str, filters: dict, sort_key: str, limit: int, offset: int,
):
    params = _cluster_params(signal_type)
    clauses = _cluster_filters(filters, params)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = int(conn.execute(
        f"{_CLUSTER_CTE} SELECT count(*) FROM clusters c {where}", params
    ).fetchone()[0])

    column, descending = _sql.parse_sort(
        sort_key, _sql.SIGNAL_SORTS, "politician_count"
    )
    order = "DESC" if descending else "ASC"

    # (ticker, start_date) is unique per signal, so this tiebreak makes the
    # ordering total and keeps offset pagination stable across requests.
    rows = conn.execute(
        f"""
        {_CLUSTER_CTE}
        {_CLUSTER_SUMMARY_SELECT}
        {where}
        ORDER BY {column} {order} NULLS LAST, c.ticker, c.start_date
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": limit, "offset": offset},
    ).fetchall()
    return total, rows


def get_cluster(conn, *, signal_type: str, ticker: str, start_date: str):
    """Return ``(summary_row, transaction_rows)`` for one signal, or ``None``."""
    params = {
        **_cluster_params(signal_type),
        "signal_ticker": ticker,
        "signal_start_date": start_date,
    }
    where = (
        "WHERE lower(c.ticker) = lower(%(signal_ticker)s) "
        "AND c.start_date = %(signal_start_date)s"
    )
    summary = conn.execute(
        f"{_CLUSTER_CTE} {_CLUSTER_SUMMARY_SELECT} {where}", params
    ).fetchone()
    if summary is None:
        return None

    transactions = conn.execute(
        f"""
        {_CLUSTER_CTE}
        SELECT b.id, b.filing_id, b.doc_id, b.sequence, b.person_key,
               b.first_name, b.last_name, b.state_district, b.txn_type,
               b.txn_date, b.notification_date, b.amount_min, b.amount_max,
               b.amount_raw, b.owner_token, b.asset_name, b.ticker,
               b.asset_type_code
        FROM clusters c
        JOIN bursts b ON b.ticker = c.ticker AND b.burst_id = c.burst_id
        {where}
        ORDER BY b.txn_date, b.id
        """,
        params,
    ).fetchall()
    return summary, transactions
