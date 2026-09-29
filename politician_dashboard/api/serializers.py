"""Convert raw query rows into response dicts.

Rows are positional tuples; these helpers build the snake_case dicts consumed
by the Pydantic response models. ``raw_pdf`` is never selected or serialized.
"""

from __future__ import annotations

from politician_dashboard.api.politicians import politician_id
from politician_dashboard.api.signal_rules import (
    BUY_CLUSTER,
    BUY_CLUSTER_LABEL,
    BUY_CLUSTER_LIMITATIONS,
    BUY_CLUSTER_RULE,
)


def filing_dict(row) -> dict:
    (
        _id, doc_id, year, prefix, first_name, last_name, suffix,
        state_district, filing_date, doc_kind, pdf_url, downloaded_at,
        created_at, transaction_count,
    ) = row[:14]
    result = {
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
    if len(row) >= 19:
        result.update({
            "amends_doc_id": row[14],
            "amendment_method": row[15],
            "amendment_confidence": row[16],
            "amendment_note": row[17],
            "amendment_verified_at": row[18],
        })
    return result


def transaction_dict(row) -> dict:
    (
        id_, filing_id, doc_id, filing_date, sequence, asset_name, ticker,
        asset_type_code, txn_type, txn_date, notification_date, amount_min,
        amount_max, amount_raw, owner_token, filing_status, ownership_source,
        notes, txn_source_id, first_name, last_name, state_district,
        quality_flags, verified_transaction_date, verification_method,
        verification_confidence, verification_source_doc_id,
        verification_note, verified_at, verification_source_doc_exists,
    ) = row
    return {
        "id": id_,
        "filing_id": filing_id,
        "doc_id": doc_id,
        "filing_date": filing_date,
        "politician_id": politician_id(state_district, first_name, last_name),
        "politician_name": f"{first_name} {last_name}".strip(),
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
        "quality_flags": list(quality_flags),
        "verified_transaction_date": verified_transaction_date,
        "verification_method": verification_method,
        "verification_confidence": verification_confidence,
        "verification_source_doc_id": verification_source_doc_id,
        "verification_note": verification_note,
        "verified_at": verified_at,
        "verification_source_doc_exists": verification_source_doc_exists,
    }


def filing_relationship_dict(row) -> dict:
    detail = filing_dict(row)
    return {key: detail[key] for key in (
        "doc_id", "name", "filing_date", "pdf_url", "amends_doc_id",
        "amendment_method", "amendment_confidence", "amendment_note",
        "amendment_verified_at",
    )}


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


# --- Signals -------------------------------------------------------------
#
# Amounts are disclosure ranges. A signal's ``total_min``/``total_max`` are
# sums of the underlying ranges, which is the only honest total available;
# they are never collapsed into a midpoint or a single exact figure.


def _signal_politician(person: dict) -> dict:
    return {
        "id": person["id"],
        "name": person["name"],
        "state_district": person["state_district"],
        "transaction_count": int(person["transaction_count"]),
        "amount_min": float(person["amount_min"]),
        "amount_max": float(person["amount_max"]),
    }


def buy_cluster_summary_dict(row) -> dict:
    (
        id_, ticker, asset_name, transaction_count, politician_count,
        start_date, end_date, span_days, total_min, total_max, politicians,
    ) = row
    return {
        "id": id_,
        "type": BUY_CLUSTER,
        "label": BUY_CLUSTER_LABEL,
        "ticker": ticker,
        "asset_name": asset_name,
        "transaction_count": int(transaction_count),
        "politician_count": int(politician_count),
        "start_date": start_date,
        "end_date": end_date,
        "span_days": int(span_days),
        "total_min": float(total_min),
        "total_max": float(total_max),
        "politicians": [_signal_politician(p) for p in politicians],
        "rule": BUY_CLUSTER_RULE,
        "limitations": BUY_CLUSTER_LIMITATIONS,
    }


def signal_transaction_dict(row) -> dict:
    (
        id_, filing_id, doc_id, sequence, person_key, first_name, last_name,
        _state_district, txn_type, txn_date, notification_date, amount_min,
        amount_max, amount_raw, owner_token, asset_name, ticker, asset_type_code,
    ) = row
    return {
        "id": id_,
        "filing_id": filing_id,
        "doc_id": doc_id,
        "politician_id": person_key,
        "politician_name": f"{first_name} {last_name}".strip(),
        "sequence": int(sequence),
        "txn_type": txn_type,
        "txn_date": txn_date,
        "notification_date": notification_date,
        "amount_min": float(amount_min),
        "amount_max": float(amount_max),
        "amount_raw": amount_raw,
        "owner": owner_token,
        "asset_name": asset_name,
        "ticker": ticker,
        "asset_type_code": asset_type_code,
    }


def buy_cluster_detail_dict(summary_row, transaction_rows) -> dict:
    result = buy_cluster_summary_dict(summary_row)
    result["transactions"] = [
        signal_transaction_dict(row) for row in transaction_rows
    ]
    return result
