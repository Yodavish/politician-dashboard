"""House PTR PDF text parser.

Extracts filing metadata and transaction records from the text layer of
Periodic Transaction Report PDFs published by the House Clerk's Office.

Scanned PDFs (no text layer) raise :class:`ScannedPdfError`.
"""

from __future__ import annotations

import io
import re
from datetime import date

import pdfplumber


ASSET_TYPE_RE = re.compile(r"\[([A-Z]{1,3})\]")
TICKER_RE = re.compile(r"\(([A-Z0-9.$]+)\)")
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
AMOUNT_RE = re.compile(r"(\$[\d,]+\s*-\s*\$[\d,]+)")
TRANSACTION_TYPE_RE = re.compile(r"P|S(?:\s+\(partial\))?(?=\s|$)")

OWNER_TOKENS = {"SP", "SF", "DC"}

METADATA_PREFIXES = ("Name:", "Status:", "State/District:", "Digitally Signed:")


class ScannedPdfError(RuntimeError):
    """Raised when a PDF has no extractable text layer."""


class ParseError(RuntimeError):
    """Raised when a PTR PDF cannot be parsed."""


def normalize_text(text: str) -> str:
    """Strip null bytes and collapse whitespace."""
    text = text.replace("\x00", "")
    lines = text.split("\n")
    return "\n".join(" ".join(line.split()) for line in lines)


def _extract_metadata(lines: list[str]) -> dict[str, str]:
    """Extract filing metadata from the header lines."""
    meta: dict[str, str] = {}

    for line in lines[:20]:
        if line.startswith("Filing ID #"):
            meta["filing_id"] = line[len("Filing ID #") :]
            continue
        for prefix in METADATA_PREFIXES:
            if line.startswith(prefix):
                meta[prefix.rstrip(":").lower().replace("/", "_").replace(" ", "_")] = (
                    line[len(prefix) :]
                )

    return meta


def _source_id_owner(line: str) -> tuple[str | None, str | None]:
    """Extract source ID and owner token from the start of a line.

    Returns ``(source_id, owner)``; either may be ``None``.
    """
    parts = line.split()
    if not parts:
        return None, None

    source_id = None
    idx = 0

    if parts[0].isdigit() and len(parts[0]) >= 8:
        source_id = parts[0]
        idx = 1

    if idx < len(parts) and parts[idx] in OWNER_TOKENS:
        return source_id, parts[idx]

    return source_id, None


def _parse_asset_type_code(text: str) -> str | None:
    """Return the asset type code (e.g. ``ST``, ``GS``) from ``[XX]`` in *text*."""
    matches = ASSET_TYPE_RE.findall(text)
    return matches[-1] if matches else None


def _parse_date(raw: str) -> date:
    """Parse a ``MM/DD/YYYY`` string into a :class:`date`."""
    month, day, year = raw.split("/")
    return date(int(year), int(month), int(day))


def _parse_transaction_type_date_amount(
    remaining: str,
) -> tuple[str, date, date, str]:
    """Parse transaction type, dates, and amount from text after the asset type code.

    Returns ``(txn_type, txn_date, notification_date, amount_raw)``.
    """
    m = TRANSACTION_TYPE_RE.search(remaining)
    if not m:
        raise ParseError(f"Cannot find transaction type in: {remaining!r}")
    txn_type = m.group(0).strip()

    after_type = remaining[m.end() :]

    date_match = DATE_RE.search(after_type)
    if not date_match:
        raise ParseError(f"Cannot find transaction date in: {after_type!r}")
    txn_date = _parse_date(date_match.group(1))

    after_first_date = after_type[date_match.end() :]
    date_match2 = DATE_RE.search(after_first_date)
    if not date_match2:
        raise ParseError(f"Cannot find notification date in: {after_first_date!r}")
    notification_date = _parse_date(date_match2.group(1))

    after_second_date = after_first_date[date_match2.end() :]
    amount_match = AMOUNT_RE.search(after_second_date)
    if not amount_match:
        raise ParseError(f"Cannot find amount in: {after_second_date!r}")
    amount_raw = amount_match.group(1)

    return txn_type, txn_date, notification_date, amount_raw


SPINE_RE = re.compile(
    r"(?P<type>P|S(?:\s+\(partial\))?)\s+"
    r"(?P<d1>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<d2>\d{2}/\d{2}/\d{4})"
)
AMOUNT_VALUE_RE = re.compile(r"\$([\d,]+)")


def _parse_amount_bounds(amount_raw: str) -> tuple[int, int]:
    """Parse ``$1,001 - $15,000`` into ``(1001, 15000)``.

    Falls back to the min and max of every dollar figure in *amount_raw* to
    handle cases where the range is split across asset-name lines.
    """
    values = [int(v.replace(",", "")) for v in AMOUNT_VALUE_RE.findall(amount_raw)]
    if not values:
        raise ParseError(f"No dollar amount found in: {amount_raw!r}")
    return min(values), max(values)


def _parse_one_transaction(block: str) -> dict[str, object]:
    """Parse a single reconstructed transaction block."""
    source_id, owner = _source_id_owner(block)

    asset_type_code = _parse_asset_type_code(block)
    if not asset_type_code:
        raise ParseError(f"No asset type code found in block: {block!r}")

    bracket_pos = block.rfind(f"[{asset_type_code}]")
    asset_name = block[:bracket_pos].strip()
    if source_id and asset_name.startswith(source_id + " "):
        asset_name = asset_name[len(source_id) + 1 :].lstrip()
    if owner and asset_name.startswith(owner + " "):
        asset_name = asset_name[len(owner) + 1 :].lstrip()
    # Transaction type and dates may appear before or after the asset type
    # code, so scan the whole block.
    spine = SPINE_RE.search(block)
    if not spine:
        raise ParseError(f"Cannot find transaction type/dates in block: {block!r}")
    txn_type = spine.group("type").strip()
    txn_date = _parse_date(spine.group("d1"))
    notification_date = _parse_date(spine.group("d2"))

    low, high = _parse_amount_bounds(block)

    # Drop any transaction spine / amount figures that leaked into the asset
    # name when the asset description wraps around them (rendering artifact).
    asset_name = SPINE_RE.sub("", asset_name).strip()
    for value in (low, high):
        asset_name = asset_name.replace(f"${value:,}", "")
    asset_name = re.sub(r"\s*-\s*", " ", asset_name).strip()
    asset_name = " ".join(asset_name.split())

    ticker_match = TICKER_RE.search(asset_name + block[bracket_pos:])
    ticker = ticker_match.group(1) if ticker_match else None

    amount_raw = " - ".join(f"${v:,}" for v in (low, high)) if low != high else f"${low:,}"
    return {
        "source_id": source_id,
        "owner": owner,
        "asset_name": asset_name,
        "ticker": ticker,
        "asset_type_code": asset_type_code,
        "txn_type": txn_type,
        "txn_date": txn_date,
        "notification_date": notification_date,
        "amount_min": low,
        "amount_max": high,
        "amount_raw": amount_raw,
    }


def _is_transaction_start(line: str) -> bool:
    """Whether *line* begins a new transaction row.

    A new transaction row begins with the transaction spine: a transaction
    type (``P`` or ``S``) followed by two dates.
    """
    return bool(SPINE_RE.search(line))


def parse_transactions(text: str) -> list[dict[str, object]]:
    """Parse all transaction blocks from normalised PTR text."""
    lines = text.split("\n")

    # Locate the start of the transaction table
    start_idx = None
    for i, line in enumerate(lines):
        if "Transaction" in line and "Amount" in line:
            start_idx = i
            break
    if start_idx is None:
        return []

    # Reconstruct each transaction as a single line of text
    entries: list[str] = []
    current: list[str] = []
    for line in lines[start_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue

        # End of the transaction section
        if stripped.startswith("* For the complete list"):
            break
        if stripped.startswith("I CERTIFY"):
            break
        if stripped.startswith("Digitally Signed:"):
            break
        if stripped.startswith("L :"):
            continue
        if stripped.startswith("Yes No"):
            continue
        if "ID Owner Asset" in stripped and "Transaction" in stripped:
            continue

        # Per-transaction metadata lines: skip, they delimit records
        if stripped.startswith("F S:") or stripped.startswith("S O:"):
            continue
        if stripped.startswith("D :") or stripped.startswith("C :"):
            continue

        # Join amount continuation lines ($...) to the current entry
        if stripped.startswith("$") and current:
            current[-1] = current[-1] + " " + stripped
            continue

        if _is_transaction_start(stripped):
            if current:
                entries.append(" ".join(current))
            current = [stripped]
        elif current:
            # Multi-line asset name continuation
            current.append(stripped)
    if current:
        entries.append(" ".join(current))

    # Parse each reconstituted transaction
    results: list[dict[str, object]] = []
    for entry in entries:
        try:
            results.append(_parse_one_transaction(entry))
        except ParseError:
            continue

    return results


def parse_ptr_pdf(pdf_bytes: bytes) -> dict[str, object]:
    """Parse a House PTR PDF and return filing metadata plus transactions.

    Raises :class:`ScannedPdfError` if the PDF has no text layer.
    Raises :class:`ParseError` if parsing fails.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if len(pdf.pages) == 0:
            raise ParseError("PDF has no pages")

        # Check for scanned PDF
        if all((page.extract_text() or "") == "" for page in pdf.pages):
            raise ScannedPdfError("PDF has no extractable text layer (scanned)")

        full_text = "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )

    normalised = normalize_text(full_text)
    lines = normalised.split("\n")

    meta = _extract_metadata(lines)

    transactions = parse_transactions(normalised)

    filing_id = meta.get("filing_id")
    if not filing_id:
        raise ParseError("Could not extract Filing ID from PDF")

    return {
        "filing_id": filing_id,
        "representative_name": meta.get("name", ""),
        "status": meta.get("status", ""),
        "state_district": meta.get("state_district", ""),
        "transactions": transactions,
    }
