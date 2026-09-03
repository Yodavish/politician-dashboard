"""Tests for the House PTR PDF parser."""

from __future__ import annotations

import pathlib

import pytest

from politician_dashboard.ingest.parser import (
    ParseError,
    ScannedPdfError,
    _extract_metadata,
    _parse_amount_bounds,
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

    def test_scanned_pdf_raises(self) -> None:
        pdf_bytes = (FIXTURES / "2025" / "8220747.pdf").read_bytes()
        with pytest.raises(ScannedPdfError):
            parse_ptr_pdf(pdf_bytes)
