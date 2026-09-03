"""Data access (parameterized SQL) for the read-only API.

Each function returns raw rows plus a total count for offset pagination.
Column names are aliased to snake_case for direct use by the schema mapping.
"""

from __future__ import annotations

from politician_dashboard.api import sql as _sql


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

    column, descending = _sql.parse_sort(sort_key, _sql.TRANSACTION_SORTS, "txn_date")
    order = "DESC" if descending else "ASC"
    base_from = "transactions t JOIN filings f ON f.id = t.filing_id"
    total = _count(conn, base_from, clauses, params)
    rows = conn.execute(
        f"""
        SELECT t.id, t.filing_id, f.doc_id, t.sequence, t.asset_name, t.ticker,
               t.asset_type_code, t.txn_type, t.txn_date, t.notification_date,
               t.amount_min, t.amount_max, t.amount_raw, t.owner_token,
               t.filing_status, t.ownership_source, t.notes, t.txn_source_id,
               f.first_name, f.last_name, f.state_district
        FROM {base_from}
        {f'WHERE {" AND ".join(clauses)}' if clauses else ''}
        ORDER BY {column} {order}, t.id
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
                   AS transaction_count
        FROM filings f WHERE f.doc_id = %s
        """,
        (doc_id,),
    ).fetchone()


def list_filing_transactions(conn, filing_id: int):
    return conn.execute(
        """
        SELECT t.id, t.filing_id, f.doc_id, t.sequence, t.asset_name, t.ticker,
               t.asset_type_code, t.txn_type, t.txn_date, t.notification_date,
               t.amount_min, t.amount_max, t.amount_raw, t.owner_token,
               t.filing_status, t.ownership_source, t.notes, t.txn_source_id,
               f.first_name, f.last_name, f.state_district
        FROM transactions t JOIN filings f ON f.id = t.filing_id
        WHERE t.filing_id = %s
        ORDER BY t.sequence
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

    column, descending = _sql.parse_sort(
        sort_key, {"last_name": "last_name", "state_district": "state_district"},
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
        ORDER BY lower({column}) {order}
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
