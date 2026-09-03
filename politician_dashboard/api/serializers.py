"""Convert raw query rows into response dicts.

Rows are positional tuples; these helpers build the snake_case dicts consumed
by the Pydantic response models. ``raw_pdf`` is never selected or serialized.
"""

from __future__ import annotations

from politician_dashboard.api.politicians import politician_id


def filing_dict(row) -> dict:
    (
        _id, doc_id, year, prefix, first_name, last_name, suffix,
        state_district, filing_date, doc_kind, pdf_url, downloaded_at,
        created_at, transaction_count,
    ) = row
    return {
        "doc_id": doc_id,
        "year": year,
        "name": " ".join(part for part in (prefix, first_name, last_name, suffix)
                         if part).strip() or f"{first_name} {last_name}".strip(),
        "state_district": state_district,
        "filing_date": filing_date,
        "doc_kind": doc_kind,
        "pdf_url": pdf_url,
        "downloaded_at": downloaded_at,
        "created_at": created_at,
        "transaction_count": int(transaction_count),
    }


def transaction_dict(row) -> dict:
    (
        id_, filing_id, doc_id, sequence, asset_name, ticker, asset_type_code,
        txn_type, txn_date, notification_date, amount_min, amount_max,
        amount_raw, owner_token, filing_status, ownership_source, notes,
        txn_source_id, first_name, last_name, state_district,
    ) = row
    return {
        "id": id_,
        "filing_id": filing_id,
        "doc_id": doc_id,
        "politician_id": politician_id(state_district, first_name, last_name),
        "sequence": sequence,
        "asset_name": asset_name,
        "ticker": ticker,
        "asset_type_code": asset_type_code,
        "txn_type": txn_type,
        "txn_date": txn_date,
        "notification_date": notification_date,
        "amount_min": float(amount_min),
        "amount_max": float(amount_max),
        "amount_raw": amount_raw,
        "owner": owner_token,
        "filing_status": filing_status,
        "ownership_source": ownership_source,
        "notes": notes,
        "txn_source_id": txn_source_id,
    }


def politician_dict(row) -> dict:
    _sd, first_name, last_name, state_district, filing_count, transaction_count = row
    return {
        "id": politician_id(state_district, first_name, last_name),
        "name": f"{first_name} {last_name}".strip(),
        "party": None,
        "state": state_district[:2],
        "district": state_district[2:],
        "state_district": state_district,
        "filing_count": int(filing_count),
        "transaction_count": int(transaction_count),
    }
