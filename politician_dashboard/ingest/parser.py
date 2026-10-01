"""House PTR PDF text parser.

Extracts filing metadata and transaction records from the text layer of
Periodic Transaction Report PDFs published by the House Clerk's Office.

Scanned PDFs (no text layer) raise :class:`ScannedPdfError`.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date
from decimal import Decimal
from typing import Any

import pdfplumber

logger = logging.getLogger(__name__)


# Several House PTR PDFs embed a font whose glyph-to-Unicode mapping flips
# letter case throughout the text layer ("Filing Id #", "iD owner asset",
# "[sT]", "P t r"). Every structural pattern below therefore matches
# case-insensitively while the captured value is left exactly as the source
# rendered it, so extracted data is never rewritten to match a label.
ASSET_TYPE_RE = re.compile(r"\[([A-Za-z]{1,3})\]", re.IGNORECASE)
# Case-insensitive so a font-flipped ticker such as ``(aCN)`` is still
# captured. The ``(partial)`` sale qualifier is excluded explicitly rather
# than incidentally, which the previous upper-case-only pattern relied on.
TICKER_RE = re.compile(r"\((?!\s*partial\s*\))([A-Za-z0-9.$]+)\)", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
AMOUNT_RE = re.compile(r"(\$[\d,]+\s*-\s*\$[\d,]+)")
TRANSACTION_TYPE_RE = re.compile(r"(?:P|S(?:\s+\(partial\))?|E)(?=\s|$)")

OWNER_TOKENS = {"SP", "SF", "DC", "JT"}
_OWNER_TOKENS_CI = {token.casefold() for token in OWNER_TOKENS}

METADATA_PREFIXES = ("Name:", "Status:", "State/District:", "Digitally Signed:")

# Table header anchors, compared case-insensitively against extracted words.
COLUMN_HEADER_TOKENS = (
    "ID",
    "Owner",
    "Asset",
    "Transaction",
    "Date",
    "Notification",
    "Amount",
    "Cap.",
)

FILING_ID_LABEL = "Filing ID #"

# Transaction-type markers are a closed vocabulary. The garbled font can render
# ``S`` as ``s``, so the marker is normalized at the structural boundary and
# stored canonically. A bare marker must be exactly one of P/S/E: this is what
# keeps a free-standing qualifier such as ``(partial)`` from ever being read as
# a transaction type.
TXN_TYPE_MARKERS = {"P": "P", "S": "S", "E": "E"}
TXN_TYPE_PARTIAL = "S (partial)"
TXN_TYPE_MARKER_RE = re.compile(r"\A([PpSsEe])\Z")
PARTIAL_QUALIFIER_RE = re.compile(r"\A\(partial\)\Z", re.IGNORECASE)


def _label_index(text: str, label: str) -> int:
    """Return the index of *label* in *text*, ignoring case, or ``-1``."""
    return text.casefold().find(label.casefold())


def _starts_with(text: str, label: str) -> bool:
    """Return whether *text* starts with *label*, ignoring case."""
    return text.casefold().startswith(label.casefold())


def _canonical_code(value: str | None) -> str | None:
    """Return a code-like token in canonical upper case.

    Owner tokens and asset type codes are fixed vocabularies that the API
    filters on by exact match, so a font-induced ``sT`` must normalize to
    ``ST`` rather than silently splitting the vocabulary.
    """
    return value.upper() if value else value


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
    """Extract filing metadata from the header lines.

    Label matching ignores case because the source PDF's font mapping can
    render ``Filing ID #`` as ``Filing Id #`` or ``name:`` as ``Name:``. The
    extracted values are returned verbatim.
    """
    meta: dict[str, str] = {}

    for line in lines[:20]:
        label_at = _label_index(line, FILING_ID_LABEL)
        if label_at != -1:
            meta["filing_id"] = line[label_at + len(FILING_ID_LABEL) :].strip()
            continue
        for prefix in METADATA_PREFIXES:
            if _starts_with(line, prefix):
                meta[prefix.rstrip(":").lower().replace("/", "_").replace(" ", "_")] = (
                    line[len(prefix) :]
                )

    return meta


def _source_id_owner(line: str) -> tuple[str | None, str | None]:
    """Extract source ID and owner token from the start of a line.

    Returns ``(source_id, owner)``; either may be ``None``. The owner token is
    matched case-insensitively and returned in canonical upper case.
    """
    parts = line.split()
    if not parts:
        return None, None

    source_id = None
    idx = 0

    if parts[0].isdigit() and len(parts[0]) >= 8:
        source_id = parts[0]
        idx = 1

    if idx < len(parts) and parts[idx].casefold() in _OWNER_TOKENS_CI:
        return source_id, parts[idx].upper()

    return source_id, None


def _parse_asset_type_code(text: str) -> str | None:
    """Return the asset type code (e.g. ``ST``, ``GS``) from ``[XX]`` in *text*.

    Matched case-insensitively and returned in canonical upper case.
    """
    matches = ASSET_TYPE_RE.findall(text)
    return matches[-1].upper() if matches else None


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
    amount_match = AMOUNT_RE.search(after_second_date) or re.search(r"\$[\d,]+(?:\.\d{1,2})?", after_second_date)
    if not amount_match:
        raise ParseError(f"Cannot find amount in: {after_second_date!r}")
    amount_raw = amount_match.group(1) if amount_match.lastindex else amount_match.group(0)

    return txn_type, txn_date, notification_date, amount_raw


SPINE_RE = re.compile(
    r"(?P<type>P|S(?:\s+\(partial\))?|E)\s+"
    r"(?P<d1>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<d2>\d{2}/\d{2}/\d{4})"
)
AMOUNT_VALUE_RE = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
NOTE_PREFIX_RE = re.compile(r"^\s*D\s*:\s*(.*)$", re.IGNORECASE)
STATUS_PREFIX_RE = re.compile(r"^\s*F\s*S\s*:\s*(.*)$", re.IGNORECASE)


def _parse_amount_bounds(amount_raw: str) -> tuple[int, int]:
    """Parse a single/range amount cell into normalized integer bounds."""
    values = [Decimal(v.replace(",", "")) for v in AMOUNT_VALUE_RE.findall(amount_raw)]
    if not values:
        raise ParseError(f"No dollar amount found in: {amount_raw!r}")
    # House amount ranges are whole dollars; a displayed single amount like
    # $15.00 is represented by the existing integer amount columns as 15.
    return int(min(values)), int(max(values))


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

    note_parts: list[str] = []
    cleaned_lines: list[str] = []
    in_notes = False
    for line in block.splitlines() or [block]:
        note_match = NOTE_PREFIX_RE.match(line)
        if note_match:
            in_notes = True
            if note_match.group(1).strip():
                note_parts.append(note_match.group(1).strip())
        elif in_notes:
            note_parts.append(line.strip())
        else:
            cleaned_lines.append(line)
    clean_block = " ".join(cleaned_lines)
    # Only inspect the transaction amount area after its two dates. This text
    # fallback cannot distinguish PDF columns, but it avoids asset and note
    # dollar values; production PDF parsing uses coordinates below.
    after_dates = clean_block[spine.end() :]
    amount_match = AMOUNT_RE.search(after_dates) or re.search(r"\$[\d,]+(?:\.\d{1,2})?", after_dates)
    if not amount_match:
        raise ParseError(f"No transaction amount after dates in: {block!r}")
    low, high = _parse_amount_bounds(after_dates)
    amount_raw = amount_match.group(0)

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
        "notes": " ".join(note_parts).strip() or None,
    }


def _is_transaction_start(line: str) -> bool:
    """Whether *line* begins a new transaction row.

    A new transaction row begins with the transaction spine: a transaction
    type (``P`` or ``S``) followed by two dates.
    """
    return bool(SPINE_RE.search(line))


def _word_lines(page: Any) -> list[list[dict[str, Any]]]:
    """Group pdfplumber words into visual rows while retaining coordinates."""
    words = sorted(page.extract_words(), key=lambda word: (word["top"], word["x0"]))
    rows: list[list[dict[str, Any]]] = []
    for word in words:
        if not rows or abs(word["top"] - rows[-1][0]["top"]) > 2.5:
            rows.append([word])
        else:
            rows[-1].append(word)
    for row in rows:
        row.sort(key=lambda word: word["x0"])
    return rows


def _page_columns(rows: list[list[dict[str, Any]]]) -> dict[str, float] | None:
    """Find the table column anchors from this page's repeated/initial header.

    Anchor words are matched case-insensitively; only their ``x0`` positions
    are used, so no extracted text is altered.
    """
    required = {token.casefold() for token in COLUMN_HEADER_TOKENS}
    for row in rows:
        by_text = {word["text"].rstrip(":").casefold(): word for word in row}
        if not required.issubset(by_text):
            continue
        type_x = by_text["transaction"]["x0"]
        # The first Date in the top header row follows Transaction; there is
        # also a second Date on the next visual row under Notification.
        date_words = [word for word in row if word["text"].casefold() == "date"]
        if not date_words:
            continue
        return {
            "asset": by_text["asset"]["x0"],
            "type": type_x,
            "txn_date": date_words[0]["x0"],
            "notification": by_text["notification"]["x0"],
            "amount": by_text["amount"]["x0"],
            "cap": by_text["cap."]["x0"],
        }
    return None


def _row_text(words: list[dict[str, Any]]) -> str:
    return " ".join(word["text"].replace("\x00", "") for word in words)


def _in_column(word: dict[str, Any], left: float, right: float) -> bool:
    return left <= word["x0"] < right


def _spine_from_row(
    row: list[dict[str, Any]], columns: dict[str, float]
) -> tuple[str, date, date] | None:
    """Recognize a transaction only when marker and dates occupy their columns.

    The marker is matched case-insensitively (the source font can render ``S``
    as ``s``) but normalized to the canonical vocabulary, so the stored value
    and the API's exact-match ``txn_type`` filter stay consistent.
    """
    for index, word in enumerate(row):
        if not _in_column(word, columns["type"] - 10, columns["txn_date"] - 8):
            continue
        marker_match = TXN_TYPE_MARKER_RE.match(word["text"])
        if not marker_match:
            continue
        canonical = TXN_TYPE_MARKERS[marker_match.group(1).upper()]
        txn_type = canonical
        next_index = index + 1
        if canonical == "S" and next_index < len(row) and PARTIAL_QUALIFIER_RE.match(row[next_index]["text"]):
            if _in_column(row[next_index], columns["type"] - 10, columns["txn_date"] - 8):
                txn_type = TXN_TYPE_PARTIAL
                next_index += 1
        dates = [
            (i, candidate)
            for i, candidate in enumerate(row[next_index:], start=next_index)
            if DATE_RE.fullmatch(candidate["text"])
        ]
        txn_date = next((candidate for _, candidate in dates if _in_column(candidate, columns["txn_date"] - 8, columns["notification"] - 8)), None)
        notification = next((candidate for _, candidate in dates if _in_column(candidate, columns["notification"] - 8, columns["amount"] - 8)), None)
        if txn_date and notification:
            return txn_type, _parse_date(txn_date["text"]), _parse_date(notification["text"])
    return None


def _parse_layout_transaction(pending: dict[str, Any]) -> dict[str, object]:
    asset_text = " ".join(pending["asset"]).strip()
    source_id, owner = _source_id_owner(pending["first_line"] + " " + asset_text)
    if source_id:
        asset_text = re.sub(rf"^\s*{re.escape(source_id)}\s+", "", asset_text)
    if owner:
        asset_text = re.sub(rf"^\s*{re.escape(owner)}\s+", "", asset_text)

    asset_type_code = _parse_asset_type_code(asset_text)
    if not asset_type_code:
        raise ParseError(f"No asset type code found in transaction: {asset_text!r}")
    type_match = list(ASSET_TYPE_RE.finditer(asset_text))[-1]
    asset_name = asset_text[:type_match.start()].strip()
    asset_name = re.sub(r"\s*[-–]\s*", " ", asset_name)
    asset_name = " ".join(asset_name.split())
    ticker_match = TICKER_RE.search(asset_name)
    ticker = ticker_match.group(1) if ticker_match else None

    amount_raw = " ".join(pending["amount"]).strip()
    low, high = _parse_amount_bounds(amount_raw)
    normalized_raw = amount_raw if re.fullmatch(r"\$[\d,.]+", amount_raw) else " - ".join(f"${value:,}" for value in (low, high))
    return {
        "source_id": source_id,
        "owner": owner,
        "asset_name": asset_name,
        "ticker": ticker,
        "asset_type_code": asset_type_code,
        "txn_type": pending["txn_type"],
        "txn_date": pending["txn_date"],
        "notification_date": pending["notification_date"],
        "amount_min": low,
        "amount_max": high,
        "amount_raw": normalized_raw,
        "notes": " ".join(pending["notes"]).strip() or None,
        "filing_status": pending["filing_status"],
    }


def _parse_transactions_from_pages(pages: Any) -> list[dict[str, object]]:
    """Parse transaction cells from PDF word coordinates and visual rows."""
    result: list[dict[str, object]] = []
    current: dict[str, Any] | None = None
    last_columns: dict[str, float] | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        try:
            result.append(_parse_layout_transaction(current))
        except ParseError:
            pass
        current = None

    for page in pages:
        rows = _word_lines(page)
        page_columns = _page_columns(rows)
        columns = page_columns or last_columns
        if columns is None:
            continue
        if page_columns is not None:
            last_columns = page_columns
        # Some PDFs omit the repeated header on a continuation page. Reuse
        # the previous page's measured anchors while remaining in the table.
        after_table_header = page_columns is None and last_columns is not None
        skip_header_band = False
        for row in rows:
            text = _row_text(row)
            folded = text.casefold()
            if (
                "id" in folded
                and "owner" in folded
                and "asset" in folded
                and "transaction" in folded
            ):
                skip_header_band = True
                after_table_header = True
                continue
            if skip_header_band:
                # The repeated PTR band is a three-row structure. Skip through
                # its Cap. Gains threshold, then resume so continuation text
                # below the header remains attached to the open transaction.
                if "$200?" in text or "$200" in text:
                    skip_header_band = False
                continue
            if text.startswith("* For the complete list") or text.startswith("I CERTIFY") or text.startswith("Digitally Signed:"):
                finish()
                after_table_header = False
                continue
            if not after_table_header:
                continue

            status_match = STATUS_PREFIX_RE.match(text)
            if status_match:
                if current is not None:
                    current["filing_status"] = status_match.group(1).strip() or None
                continue
            note_match = NOTE_PREFIX_RE.match(text)
            if note_match:
                if current is not None:
                    current["in_notes"] = True
                    if note_match.group(1).strip():
                        current["notes"].append(note_match.group(1).strip())
                continue
            if re.match(r"^\s*(?:S\s*O|C)\s*:", text, re.IGNORECASE):
                continue

            spine = _spine_from_row(row, columns)
            if spine:
                finish()
                txn_type, txn_date, notification_date = spine
                type_x = columns["type"] - 10
                current = {
                    "txn_type": txn_type,
                    "txn_date": txn_date,
                    "notification_date": notification_date,
                    "asset": [],
                    "amount": [],
                    "notes": [],
                    "filing_status": None,
                    "in_notes": False,
                    "first_line": text,
                }
                # The first asset and amount cells share the spine's visual row.
                for word in row:
                    if _in_column(word, columns["asset"] - 10, type_x):
                        current["asset"].append(word["text"])
                    elif _in_column(word, columns["amount"] - 8, columns["cap"] - 5):
                        if AMOUNT_VALUE_RE.search(word["text"]):
                            current["amount"].append(word["text"])
                continue

            if current is None:
                continue
            if current["in_notes"]:
                current["notes"].append(text)
                continue
            for word in row:
                if _in_column(word, columns["asset"] - 10, columns["type"] - 10):
                    current["asset"].append(word["text"])
                elif _in_column(word, columns["amount"] - 8, columns["cap"] - 5):
                    if AMOUNT_VALUE_RE.search(word["text"]):
                        current["amount"].append(word["text"])
        # Do not finish here: a row's status/notes can continue after a
        # repeated page header and belongs to the same transaction.
    finish()
    return result


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

    # Reconstruct each transaction as a single line of text, keeping the
    # per-transaction "Filing Status" (``F S:``) annotation. The annotation
    # is source evidence (``New`` / ``Amended``) for that transaction; it is
    # never used to infer which prior filing is being amended, only preserved
    # separately from the curated ``amends_filing_id`` relationship.
    entries: list[tuple[str, str | None]] = []
    current: list[str] = []
    current_filing_status: str | None = None

    def finish_current() -> None:
        nonlocal current, current_filing_status
        if current:
            entries.append(("\n".join(current), current_filing_status))
        current = []
        current_filing_status = None

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

        # Per-transaction metadata lines: delimit records and annotate them.
        # ``F S:`` records the filing status of the transaction it follows;
        # the other prefixes carry no date-relevant content and are skipped.
        status_match = STATUS_PREFIX_RE.match(stripped)
        if status_match:
            if current:
                # This metadata line belongs to the transaction block that
                # precedes it. Keep the source spelling/case verbatim.
                current_filing_status = status_match.group(1).strip() or None
            continue
        if re.match(r"^(?:S\s*O|C)\s*:", stripped, re.IGNORECASE):
            continue

        # Join amount continuation lines ($...) to the current entry
        if stripped.startswith("$") and current:
            current[-1] = current[-1] + " " + stripped
            continue

        if _is_transaction_start(stripped):
            if current:
                finish_current()
            current = [stripped]
        elif current:
            # Multi-line asset name continuation
            current.append(stripped)
    finish_current()

    # Parse each reconstituted transaction
    results: list[dict[str, object]] = []
    for entry, filing_status in entries:
        try:
            parsed = _parse_one_transaction(entry)
        except ParseError:
            continue
        parsed["filing_status"] = filing_status
        results.append(parsed)

    return results


def parse_ptr_pdf(pdf_bytes: bytes) -> dict[str, object]:
    """Parse a House PTR PDF and return filing metadata plus transactions.

    Raises :class:`ScannedPdfError` if the PDF has no text layer.
    Raises :class:`ParseError` if the document itself is unusable.

    The in-document ``Filing ID`` is advisory only. The index DocID is
    authoritative, so a missing or unrecognizable label yields ``filing_id`` of
    ``None`` rather than discarding an otherwise-valid filing.
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
        transactions = _parse_transactions_from_pages(pdf.pages)

    normalised = normalize_text(full_text)
    lines = normalised.split("\n")

    meta = _extract_metadata(lines)

    filing_id = meta.get("filing_id") or None
    if not filing_id:
        logger.warning(
            "PTR PDF has no recognizable 'Filing ID #' label; "
            "continuing with the authoritative index DocID"
        )

    return {
        "filing_id": filing_id,
        "representative_name": meta.get("name", ""),
        "status": meta.get("status", ""),
        "state_district": meta.get("state_district", ""),
        "transactions": transactions,
    }
