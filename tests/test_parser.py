"""Tests for the House PTR PDF parser."""

from __future__ import annotations

import pathlib
from datetime import date

import pytest

from politician_dashboard.ingest.parser import (
    ParseError,
    ScannedPdfError,
    _extract_metadata,
    _page_columns,
    _parse_amount_bounds,
    _spine_from_row,
    _parse_asset_type_code,
    _parse_transaction_type_date_amount,
    _source_id_owner,
    normalize_text,
    parse_ptr_pdf,
    parse_transactions,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "pdfs"


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_strips_null_bytes(self) -> None:
        assert normalize_text("P\x00\x00 T\x00 R") == "P T R"

    def test_collapses_whitespace(self) -> None:
        assert normalize_text("  a   b  c  ") == "a b c"

    def test_preserves_newlines(self) -> None:
        result = normalize_text("line1\nline2\nline3")
        assert result == "line1\nline2\nline3"


# ---------------------------------------------------------------------------
# _extract_metadata
# ---------------------------------------------------------------------------


class TestExtractMetadata:
    def test_filing_id(self) -> None:
        lines = ["Filing ID #20032062", "P T R"]
        meta = _extract_metadata(lines)
        assert meta["filing_id"] == "20032062"

    def test_name(self) -> None:
        lines = ["Name: Hon. Robert B. Aderholt"]
        meta = _extract_metadata(lines)
        assert meta["name"] == " Hon. Robert B. Aderholt"

    def test_state_district(self) -> None:
        lines = ["State/District: AL04"]
        meta = _extract_metadata(lines)
        assert meta["state_district"] == " AL04"

    def test_digitally_signed(self) -> None:
        lines = [
            "Digitally Signed: Hon. Robert B. Aderholt , 09/10/2025"
        ]
        meta = _extract_metadata(lines)
        assert "digitally_signed" in meta


# ---------------------------------------------------------------------------
# _source_id_owner
# ---------------------------------------------------------------------------


class TestSourceIdOwner:
    def test_source_id_and_owner(self) -> None:
        sid, owner = _source_id_owner("2000086356 SP 3M Company (MMM)")
        assert sid == "2000086356"
        assert owner == "SP"

    def test_owner_only(self) -> None:
        sid, owner = _source_id_owner("SP GSK plc American Depositary Shares")
        assert sid is None
        assert owner == "SP"

    def test_no_owner(self) -> None:
        sid, owner = _source_id_owner("Activision Blizzard, Inc (ATVI) [ST]")
        assert sid is None
        assert owner is None

    def test_sf_owner(self) -> None:
        sid, owner = _source_id_owner("SF Some Fund (FUND) [ST]")
        assert sid is None
        assert owner == "SF"

    def test_dc_owner(self) -> None:
        sid, owner = _source_id_owner("DC Some Corp (SC) [ST]")
        assert sid is None
        assert owner == "DC"

    def test_jt_owner(self) -> None:
        sid, owner = _source_id_owner("JT Berkshire Hathaway Inc. (BRK.B) [ST]")
        assert sid is None
        assert owner == "JT"

    def test_empty_line(self) -> None:
        sid, owner = _source_id_owner("")
        assert sid is None
        assert owner is None


# ---------------------------------------------------------------------------
# _parse_asset_type_code
# ---------------------------------------------------------------------------


class TestParseAssetTypeCode:
    def test_stock(self) -> None:
        assert _parse_asset_type_code("GSK plc (GSK) [ST]") == "ST"

    def test_government_security(self) -> None:
        assert _parse_asset_type_code("US Treasury Bill (123) [GS]") == "GS"

    def test_other(self) -> None:
        assert _parse_asset_type_code("MONSANTO [OT]") == "OT"

    def test_no_code(self) -> None:
        assert _parse_asset_type_code("Some asset name") is None

    def test_last_code_wins(self) -> None:
        text = "[ST] some text [GS]"
        assert _parse_asset_type_code(text) == "GS"


# ---------------------------------------------------------------------------
# _parse_transaction_type_date_amount
# ---------------------------------------------------------------------------


class TestParseTransactionTypeDateAmount:
    def test_purchase(self) -> None:
        r = "P 07/28/2025 08/11/2025 $1,001 - $15,000"
        txn_type, txn_date, notif_date, amount = (
            _parse_transaction_type_date_amount(r)
        )
        assert txn_type == "P"
        assert txn_date.isoformat() == "2025-07-28"
        assert notif_date.isoformat() == "2025-08-11"
        assert amount == "$1,001 - $15,000"

    def test_sale(self) -> None:
        r = "S 05/14/2020 05/20/2020 $15,001 - $50,000"
        txn_type, _, _, _ = _parse_transaction_type_date_amount(r)
        assert txn_type == "S"

    def test_partial_sale(self) -> None:
        r = "S (partial) 01/08/2025 02/04/2025 $15,001 - $50,000"
        txn_type, _, _, _ = _parse_transaction_type_date_amount(r)
        assert txn_type == "S (partial)"

    def test_missing_type_raises(self) -> None:
        with pytest.raises(ParseError, match="Cannot find transaction type"):
            _parse_transaction_type_date_amount("no type here")


# ---------------------------------------------------------------------------
# _parse_amount_bounds
# ---------------------------------------------------------------------------


class TestParseAmountBounds:
    def test_standard_range(self) -> None:
        assert _parse_amount_bounds("$1,001 - $15,000") == (1001, 15000)

    def test_large_range(self) -> None:
        assert _parse_amount_bounds("$100,001 - $250,000") == (100001, 250000)

    def test_no_upper(self) -> None:
        assert _parse_amount_bounds("$1,001") == (1001, 1001)


# ---------------------------------------------------------------------------
# parse_transactions (unit tests on normalised text)
# ---------------------------------------------------------------------------


class TestParseTransactions:
    def test_filing_status_is_attached_to_its_source_block(self) -> None:
        pdf_bytes = (FIXTURES / "2023" / "20023082.pdf").read_bytes()
        result = parse_ptr_pdf(pdf_bytes)
        assert result["transactions"][0]["filing_status"] == "Amended"
        assert result["transactions"][1]["filing_status"] == "New"

    def test_missing_filing_status_is_none(self) -> None:
        text = (
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n$200?\n"
            "Example Inc (EX) [ST] P 01/01/2025 01/02/2025 $1,001 - $15,000\n"
            "F S: New\n"
            "Another Inc (AN) [ST] P 02/01/2025 02/02/2025 $1,001 - $15,000\n"
        )
        txns = parse_transactions(text)
        assert [t["filing_status"] for t in txns] == ["New", None]

    def test_malformed_block_does_not_shift_filing_status(self) -> None:
        text = (
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n$200?\n"
            "Example Inc (EX) [ST] P 01/01/2025 01/02/2025 $1,001 - $15,000\n"
            "F S: New\n"
            "Unparseable block P 02/01/2025 02/02/2025 $1,001 - $15,000\n"
            "F S: Deleted\n"
            "Last Inc (LAST) [ST] P 03/01/2025 03/02/2025 $1,001 - $15,000\n"
            "F S: Amended\n"
        )
        txns = parse_transactions(text)
        assert [t["ticker"] for t in txns] == ["EX", "LAST"]
        assert [t["filing_status"] for t in txns] == ["New", "Amended"]

    def test_single_simple_transaction(self) -> None:
        text = (
            "Filing ID #00000001\n"
            "Name: Hon. Test\n"
            "Status: Member\n"
            "State/District: XX00\n"
            "T\n"
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n"
            "$200?\n"
            "SP GSK plc (GSK) [ST] P 07/28/2025 08/11/2025 $1,001 - $15,000\n"
            "F S: New\n"
            "S O: Some Account\n"
        )
        txns = parse_transactions(text)
        assert len(txns) == 1
        t = txns[0]
        assert t["owner"] == "SP"
        assert t["asset_name"] == "GSK plc (GSK)"
        assert t["ticker"] == "GSK"
        assert t["asset_type_code"] == "ST"
        assert t["txn_type"] == "P"
        assert t["amount_min"] == 1001
        assert t["amount_max"] == 15000

    def test_source_id_and_owner(self) -> None:
        text = (
            "T\n"
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n"
            "$200?\n"
            "2000086356 SP 3M Company (MMM) [ST] S 05/14/2020 05/20/2020 $15,001 - $50,000\n"
            "F S: Amended\n"
        )
        txns = parse_transactions(text)
        assert len(txns) == 1
        assert txns[0]["source_id"] == "2000086356"
        assert txns[0]["owner"] == "SP"
        assert txns[0]["asset_name"] == "3M Company (MMM)"

    def test_no_owner(self) -> None:
        text = (
            "T\n"
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n"
            "$200?\n"
            "Activision Blizzard, Inc (ATVI) [ST] P 04/20/2023 05/05/2023 $1,001 - $15,000\n"
            "F S: New\n"
        )
        txns = parse_transactions(text)
        assert len(txns) == 1
        assert txns[0]["owner"] is None
        assert txns[0]["asset_name"] == "Activision Blizzard, Inc (ATVI)"

    def test_jt_owner_is_not_left_in_asset_name(self) -> None:
        text = (
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n$200?\n"
            "JT Berkshire Hathaway Inc. New Common Stock (BRK.B) [OP] "
            "P 01/13/2025 01/13/2025 $1,001 - $15,000\n"
        )
        (txn,) = parse_transactions(text)
        assert txn["owner"] == "JT"
        assert txn["asset_name"] == "Berkshire Hathaway Inc. New Common Stock (BRK.B)"

    def test_split_amount(self) -> None:
        text = (
            "T\n"
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n"
            "$200?\n"
            "SP Rollins, Inc. Common Stock (ROL) P 12/12/2024 01/08/2025 $15,001 -\n"
            "[ST] $50,000\n"
            "F S: New\n"
        )
        txns = parse_transactions(text)
        assert len(txns) == 1
        assert txns[0]["amount_min"] == 15001
        assert txns[0]["amount_max"] == 50000

    def test_partial_sale(self) -> None:
        text = (
            "T\n"
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n"
            "$200?\n"
            "US Treasury Bill 912797JR9 [GS] S (partial) 01/08/2025 02/04/2025 $15,001 - $50,000\n"
            "F S: New\n"
        )
        txns = parse_transactions(text)
        assert len(txns) == 1
        assert txns[0]["txn_type"] == "S (partial)"
        assert txns[0]["ticker"] is None
        assert "912797JR9" in txns[0]["asset_name"]

    def test_exchange_is_a_transaction_boundary_and_d_notes_are_preserved(self) -> None:
        text = (
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n$200?\n"
            "SP Tempus AI (TEM) [ST] P 01/16/2026 01/16/2026 $50,001 - $100,000\n"
            "F S: New\nD: Exercised options at a strike price of $20.\n"
            "SP Versant Media (VSNT) [ST] E 01/02/2026 01/02/2026 $15.00\n"
            "F S: New\nD : Received shares in a spinoff.\n"
        )
        txns = parse_transactions(text)
        assert len(txns) == 2
        assert [t["txn_type"] for t in txns] == ["P", "E"]
        assert txns[0]["notes"] == "Exercised options at a strike price of $20."
        assert txns[1]["notes"] == "Received shares in a spinoff."

    def test_multi_line_asset(self) -> None:
        text = (
            "T\n"
            "ID Owner Asset Transaction Date Notification Amount Cap.\n"
            "Type Date Gains >\n"
            "$200?\n"
            "SP The Charles Schwab Corporation P 05/18/2023 05/17/2023 $15,001 -\n"
            "Depositary Shares each representing $50,000\n"
            "1/40th interest in a share of 5.95%\n"
            "Non-Cumulative Perpetual Preferred\n"
            "Stock, Series D (SCHW$D) [ST]\n"
            "F S: New\n"
        )
        txns = parse_transactions(text)
        assert len(txns) == 1
        assert txns[0]["ticker"] == "SCHW$D"
        assert txns[0]["asset_type_code"] == "ST"
        assert "Charles Schwab Corporation" in txns[0]["asset_name"]

    def test_empty_text(self) -> None:
        assert parse_transactions("") == []

    def test_no_transaction_header(self) -> None:
        assert parse_transactions("Some random text\nwithout header") == []


# ---------------------------------------------------------------------------
# parse_ptr_pdf (integration tests with real fixtures)
# ---------------------------------------------------------------------------


class TestParsePtrPdf:
    def test_single_txn_20032062(self) -> None:
        pdf_bytes = (FIXTURES / "2025" / "20032062.pdf").read_bytes()
        result = parse_ptr_pdf(pdf_bytes)
        assert result["filing_id"] == "20032062"
        assert result["representative_name"] == " Hon. Robert B. Aderholt"
        assert result["state_district"] == " AL04"
        assert len(result["transactions"]) == 1
        t = result["transactions"][0]
        assert t["owner"] is None
        assert t["asset_name"] == "GSK plc American Depositary Shares (GSK)"
        assert t["ticker"] == "GSK"
        assert t["txn_type"] == "S"
        assert t["amount_min"] == 1001
        assert t["amount_max"] == 15000

    def test_multi_txn_split_amount_20026537(self) -> None:
        pdf_bytes = (FIXTURES / "2025" / "20026537.pdf").read_bytes()
        result = parse_ptr_pdf(pdf_bytes)
        assert result["filing_id"] == "20026537"
        assert len(result["transactions"]) == 4

        # First: Rollins with split amount
        t0 = result["transactions"][0]
        assert t0["asset_name"] == "Rollins, Inc. Common Stock (ROL)"
        assert t0["ticker"] == "ROL"
        assert t0["amount_min"] == 15001
        assert t0["amount_max"] == 50000

        # Second: Treasury note
        t1 = result["transactions"][1]
        assert t1["ticker"] == "91282CJP7"
        assert t1["amount_min"] == 100001
        assert t1["amount_max"] == 250000

    def test_partial_sales_20026727(self) -> None:
        pdf_bytes = (FIXTURES / "2025" / "20026727.pdf").read_bytes()
        result = parse_ptr_pdf(pdf_bytes)
        assert result["filing_id"] == "20026727"
        assert len(result["transactions"]) == 5

        # Verify partial sales: treasury bills carry a bare CUSIP (no ticker
        # in parentheses), so the ticker is left empty.
        partials = [t for t in result["transactions"] if t["txn_type"] == "S (partial)"]
        assert len(partials) == 2
        for p in partials:
            assert p["ticker"] is None
            assert "912797JR9" in p["asset_name"]

    def test_blank_owner_multiline_asset_20022986(self) -> None:
        pdf_bytes = (FIXTURES / "2023" / "20022986.pdf").read_bytes()
        result = parse_ptr_pdf(pdf_bytes)
        assert result["filing_id"] == "20022986"
        assert len(result["transactions"]) == 3

        # First transaction: blank owner, Activision
        t0 = result["transactions"][0]
        assert t0["owner"] is None
        assert t0["asset_name"] == "Activision Blizzard, Inc (ATVI)"
        assert t0["ticker"] == "ATVI"

        # Third: multi-line asset (Charles Schwab preferred)
        t2 = result["transactions"][2]
        assert t2["ticker"] == "SCHW$D"
        assert t2["amount_min"] == 15001
        assert t2["amount_max"] == 50000

    def test_large_amended_filing_20023082(self) -> None:
        pdf_bytes = (FIXTURES / "2023" / "20023082.pdf").read_bytes()
        result = parse_ptr_pdf(pdf_bytes)
        assert result["filing_id"] == "20023082"
        assert len(result["transactions"]) > 50

        # Verify some source IDs are captured
        with_source = [t for t in result["transactions"] if t["source_id"]]
        assert len(with_source) > 0

        # Verify a specific transaction
        mmm_txns = [
            t for t in result["transactions"] if t["ticker"] == "MMM"
        ]
        assert len(mmm_txns) >= 1

        # These source rows previously inherited the repeated "$200?" header
        # or leaked amount text into the asset cell.
        abbot = next(t for t in result["transactions"] if t["ticker"] == "ABT" and t["txn_date"] == date(2019, 9, 27))
        assert (abbot["amount_min"], abbot["amount_max"]) == (15001, 50000)
        assert "$200" not in abbot["asset_name"]
        bayer = next(t for t in result["transactions"] if t["ticker"] == "BAYZF" and t["txn_date"] == date(2018, 6, 7))
        assert (bayer["amount_min"], bayer["amount_max"]) == (1001, 15000)
        assert "$1,001" not in bayer["asset_name"]

    def test_pelosi_20033725_separates_tempus_and_versant(self) -> None:
        result = parse_ptr_pdf((FIXTURES / "2026" / "20033725.pdf").read_bytes())
        tempus = [t for t in result["transactions"] if t["ticker"] == "TEM"]
        versant = [t for t in result["transactions"] if t["ticker"] == "VSNT"]
        assert len(tempus) == len(versant) == 1
        assert (tempus[0]["amount_min"], tempus[0]["amount_max"]) == (50001, 100000)
        assert tempus[0]["notes"].startswith("Exercised 50 call options")
        assert (versant[0]["amount_min"], versant[0]["amount_max"]) == (15, 15)
        assert versant[0]["amount_raw"] == "$15.00"
        assert versant[0]["notes"].startswith("776 shares and cash in lieu")

    def test_jt_owner_in_house_filing_20024346(self) -> None:
        result = parse_ptr_pdf((FIXTURES / "2025" / "20024346.pdf").read_bytes())
        txn = next(
            t for t in result["transactions"]
            if t["ticker"] == "BRK.B"
            and t["txn_type"] == "P"
            and t["txn_date"] == date(2025, 1, 13)
            and t["asset_type_code"] == "OP"
            and t["amount_min"] == 1001
        )
        assert txn["owner"] == "JT"
        assert txn["asset_name"] == "Berkshire Hathaway Inc. New Common Stock (BRK.B)"

    def test_pelosi_20035143_option_strikes_do_not_change_amounts(self) -> None:
        result = parse_ptr_pdf((FIXTURES / "2026" / "20035143.pdf").read_bytes())
        txns = result["transactions"]
        bloom_option = next(t for t in txns if t["ticker"] == "BE" and t["asset_type_code"] == "OP" and t["txn_date"] == date(2026, 7, 24))
        intel_option = next(t for t in txns if t["ticker"] == "INTC" and t["asset_type_code"] == "OP")
        intel_stock = next(t for t in txns if t["ticker"] == "INTC" and t["asset_type_code"] == "ST")
        assert (bloom_option["amount_min"], bloom_option["amount_max"]) == (1000001, 5000000)
        assert (intel_option["amount_min"], intel_option["amount_max"]) == (250001, 500000)
        assert (intel_stock["amount_min"], intel_stock["amount_max"]) == (500001, 1000000)
        for txn in (bloom_option, intel_option, intel_stock):
            assert "$" not in txn["asset_name"]
            assert "$200" not in txn["asset_name"]

    def test_laurel_20034694_nokia_partial_sale_survives_header(self) -> None:
        result = parse_ptr_pdf((FIXTURES / "2026" / "20034694.pdf").read_bytes())
        nokia = next(
            t for t in result["transactions"]
            if t["ticker"] == "NOK" and t["txn_type"] == "S (partial)"
            and t["txn_date"] == date(2026, 6, 2)
        )
        assert (nokia["amount_min"], nokia["amount_max"]) == (1001, 15000)
        assert nokia["notes"] == "Call option contracts"
        assert "$200" not in nokia["asset_name"]

    def test_scanned_pdf_raises(self) -> None:
        pdf_bytes = (FIXTURES / "2025" / "8220747.pdf").read_bytes()
        with pytest.raises(ScannedPdfError):
            parse_ptr_pdf(pdf_bytes)

    def test_source_anomalous_date_20033889(self) -> None:
        # House filing 20033889 (Rep. Steve Cohen, TN09) publishes a SONY
        # purchase with transaction date 12/26/2026, notification date
        # 01/21/2026, and signature date 02/09/2026. The parser must return
        # exactly what the official source states -- no "correction" to 2025.
        pdf_bytes = (FIXTURES / "2026" / "20033889.pdf").read_bytes()
        result = parse_ptr_pdf(pdf_bytes)
        assert result["filing_id"] == "20033889"
        assert result["state_district"] == " TN09"
        (t,) = result["transactions"]
        assert t["asset_name"] == (
            "Sony Group Corporation American Depositary Shares (SONY)"
        )
        assert t["ticker"] == "SONY"
        assert t["txn_type"] == "P"
        assert t["txn_date"] == date(2026, 12, 26)
        assert t["notification_date"] == date(2026, 1, 21)
        assert t["amount_min"] == 1001
        assert t["amount_max"] == 15000

    def test_anomalous_date_is_not_reinterpreted_as_2025(self) -> None:
        # Regression: the source's 12/26/2026 must never silently become
        # 12/26/2025, even though the amended filing 20034452 reports the
        # same purchase as 12/26/2025.
        pdf_bytes = (FIXTURES / "2026" / "20033889.pdf").read_bytes()
        (t,) = parse_ptr_pdf(pdf_bytes)["transactions"]
        assert t["txn_date"] == date(2026, 12, 26)
        assert t["txn_date"] != date(2025, 12, 26)


# ---------------------------------------------------------------------------
# Font-induced letter-case handling
#
# Several House PTR PDFs embed a font whose glyph-to-Unicode mapping flips
# letter case throughout the text layer ("Filing Id #", "iD owner asset
# transaction", "[sT]", "P t r"). Structural matching must tolerate that
# without rewriting the extracted data.
# ---------------------------------------------------------------------------


def _minimal_pdf(lines: list[str]) -> bytes:
    """Build a one-page PDF with a text layer holding *lines*."""
    content = "BT /F1 11 Tf 40 700 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content += f"({escaped}) Tj T*\n"
    content += "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = "%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    start_xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{start_xref}\n%%EOF\n"
    )
    return out.encode("latin-1")


class TestCaseInsensitiveStructure:
    def test_filing_id_label_matches_any_casing(self) -> None:
        for line, expected in (
            ("Filing ID #20016985", "20016985"),
            ("Filing Id #20017471", "20017471"),
            ("FILING ID #20015000", "20015000"),
        ):
            assert _extract_metadata([line])["filing_id"] == expected

    def test_filing_id_label_survives_a_leading_prefix(self) -> None:
        # Amended filings render the label after an "Eagle Seal" stamp.
        assert (
            _extract_metadata(["Eagle Seal Filing Id #20017999"])["filing_id"]
            == "20017999"
        )

    def test_metadata_prefixes_match_any_casing(self) -> None:
        for line in ("name: Hon. A B", "Name: Hon. A B", "NAME: Hon. A B"):
            assert _extract_metadata([line])["name"] == " Hon. A B"

    def test_missing_filing_id_fails_soft(self) -> None:
        # The index DocID is authoritative, so an unrecognizable label must
        # not discard an otherwise readable filing.
        result = parse_ptr_pdf(_minimal_pdf(["P t r", "some other content"]))
        assert result["filing_id"] is None

    def test_recognizable_filing_id_is_returned_verbatim(self) -> None:
        result = parse_ptr_pdf(
            _minimal_pdf(["Filing Id #20017471", "name: Hon. Test Member"])
        )
        assert result["filing_id"] == "20017471"
        assert result["representative_name"] == " Hon. Test Member"

    def test_column_anchors_match_any_casing(self) -> None:
        # The lowercase header is what previously made entire pages yield no
        # transactions, because the column anchors went undetected.
        positions = {
            "ID": 20, "Owner": 50, "Asset": 100, "Transaction": 246,
            "Date": 312, "Notification": 367, "Amount": 432, "Cap.": 505,
        }
        for fold in (lambda s: s, str.casefold):
            rows = [
                [
                    {"text": fold(label), "x0": x, "top": 100.0}
                    for label, x in sorted(positions.items(), key=lambda p: p[1])
                ]
            ]
            columns = _page_columns(rows)
            assert columns is not None
            assert columns["asset"] == 100.0
            assert columns["type"] == 246.0

    def test_asset_type_code_is_canonicalized(self) -> None:
        # Filtered by exact match in the API, so the vocabulary must not split.
        assert _parse_asset_type_code("Amazon.com, Inc. (aCN) [sT]") == "ST"
        assert _parse_asset_type_code("Northwest Natural [ST]") == "ST"

    def test_owner_token_is_canonicalized(self) -> None:
        assert _source_id_owner("20017471 sP") == ("20017471", "SP")
        assert _source_id_owner("20017471 SP") == ("20017471", "SP")

    def test_extracted_data_is_not_rewritten_to_upper_case(self) -> None:
        # Matching is case-insensitive, but source text is stored as rendered.
        result = parse_ptr_pdf(
            _minimal_pdf(
                [
                    "Filing Id #20017471",
                    "filer information",
                    "name: Hon. Earl Blumenauer",
                    "State/District: oR03",
                ]
            )
        )
        assert result["representative_name"] == " Hon. Earl Blumenauer"
        assert result["state_district"] == " oR03"


# ---------------------------------------------------------------------------
# Transaction-type marker casing
#
# The garbled font renders the ``S`` sale marker as lowercase ``s``. The marker
# is a closed vocabulary: it must be recognized case-insensitively, normalized
# to a canonical value for the API's exact-match filter, and never extended to
# admit qualifiers such as ``(partial)`` as a type in their own right.
# ---------------------------------------------------------------------------


_SPINE_COLUMNS = {
    "type": 100.0,
    "txn_date": 160.0,
    "notification": 220.0,
    "amount": 280.0,
}


def _spine_row(marker: str, *extra: str) -> list[dict[str, object]]:
    """Build one word row: marker then dates, inside the type/date columns."""
    texts = [marker, *extra, "03/18/2020", "03/20/2020", "$15,001 - $50,000"]
    xs = [110.0, *[130.0] * len(extra), 165.0, 225.0, 290.0]
    return [
        {"text": text, "x0": x, "x1": x + 30.0, "top": 100.0}
        for text, x in zip(texts, xs)
    ]


class TestTransactionTypeMarkerCasing:
    @pytest.mark.parametrize("marker", ["P", "p"])
    def test_purchase_marker(self, marker: str) -> None:
        spine = _spine_from_row(_spine_row(marker), _SPINE_COLUMNS)
        assert spine is not None
        assert spine[0] == "P"

    @pytest.mark.parametrize("marker", ["S", "s"])
    def test_sale_marker_is_canonicalized(self, marker: str) -> None:
        spine = _spine_from_row(_spine_row(marker), _SPINE_COLUMNS)
        assert spine is not None
        assert spine[0] == "S"

    @pytest.mark.parametrize("qualifier", ["(partial)", "(Partial)", "(PARTIAL)"])
    @pytest.mark.parametrize("marker", ["S", "s"])
    def test_partial_sale_is_canonicalized(self, marker: str, qualifier: str) -> None:
        spine = _spine_from_row(_spine_row(marker, qualifier), _SPINE_COLUMNS)
        assert spine is not None
        assert spine[0] == "S (partial)"
        assert spine[1] == date(2020, 3, 18)
        assert spine[2] == date(2020, 3, 20)

    @pytest.mark.parametrize("marker", ["E", "e"])
    def test_exchange_marker(self, marker: str) -> None:
        spine = _spine_from_row(_spine_row(marker), _SPINE_COLUMNS)
        assert spine is not None
        assert spine[0] == "E"

    @pytest.mark.parametrize("qualifier", ["(partial)", "(Partial)", "partial"])
    def test_partial_qualifier_is_never_a_standalone_type(self, qualifier: str) -> None:
        # A free-standing qualifier must never be promoted to a transaction
        # type; only an existing S plus the qualifier forms "S (partial)".
        assert _spine_from_row(_spine_row(qualifier), _SPINE_COLUMNS) is None

    @pytest.mark.parametrize(
        "marker", ["SP", "DC", "JT", "$1,001", "PS", "PP", "SS", "SEE", "sale"]
    )
    def test_invalid_markers_are_rejected(self, marker: str) -> None:
        assert _spine_from_row(_spine_row(marker), _SPINE_COLUMNS) is None

    @pytest.mark.parametrize("marker", ["P", "E"])
    def test_partial_qualifier_does_not_upgrade_other_types(self, marker: str) -> None:
        # Only "S" takes a partial qualifier. "P (partial)" is not a real House
        # form; the qualifier must not be absorbed into a different type.
        spine = _spine_from_row(_spine_row(marker, "(partial)"), _SPINE_COLUMNS)
        assert spine is not None
        assert spine[0] == marker

    def test_partial_qualifier_outside_the_type_column_is_ignored(self) -> None:
        row = _spine_row("s")
        stray = {"text": "(partial)", "x0": 400.0, "x1": 460.0, "top": 100.0}
        row = sorted(row + [stray], key=lambda word: word["x0"])
        spine = _spine_from_row(row, _SPINE_COLUMNS)
        assert spine is not None
        assert spine[0] == "S"

    def test_lowercase_marker_yields_the_same_transaction_as_uppercase(self) -> None:
        # The garbled font must not change the parsed result, only whether it
        # is recognized at all.
        upper = _spine_from_row(_spine_row("S", "(partial)"), _SPINE_COLUMNS)
        lower = _spine_from_row(_spine_row("s", "(partial)"), _SPINE_COLUMNS)
        assert upper == lower
        assert lower is not None and lower[0] == "S (partial)"

    def test_marker_case_does_not_alter_the_dates(self) -> None:
        for marker in ("S", "s", "P", "p", "E", "e"):
            spine = _spine_from_row(_spine_row(marker), _SPINE_COLUMNS)
            assert spine is not None
            assert spine[1:] == (date(2020, 3, 18), date(2020, 3, 20))
