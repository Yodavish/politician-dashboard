"""Tests for the House Clerk source adapter and index parsing."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from politician_dashboard.ingest.models import Filing
from politician_dashboard.ingest.sources.base import select_ptrs
from politician_dashboard.ingest.sources.house_clerk import (
    HouseClerkSource,
    HouseIndexError,
    classify_doc_id,
    parse_filing_date,
    parse_index_txt,
    parse_index_xml,
    parse_index_zip,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _sample_filings() -> list[Filing]:
    return parse_index_xml(_load_fixture("2025FD_sample.xml"))


class TestParseIndexXml:
    def test_returns_one_filing_per_member(self):
        filings = _sample_filings()
        assert len(filings) == 7
        assert all(isinstance(f, Filing) for f in filings)

    def test_parses_fields_for_ptr(self):
        by_doc = {f.doc_id: f for f in _sample_filings()}
        filing = by_doc["20032062"]
        assert filing.first == "Robert B."
        assert filing.last == "Aderholt"
        assert filing.prefix == ""
        assert filing.suffix == ""
        assert filing.state_district == "AL04"
        assert filing.filing_type == "P"
        assert filing.year == 2025
        assert filing.filing_date == date(2025, 9, 10)

    def test_preserves_prefix_and_suffix(self):
        by_doc = {f.doc_id: f for f in _sample_filings()}
        filing = by_doc["10072809"]
        assert filing.prefix == "Mr."
        assert filing.suffix == "Jr."

    def test_parses_filing_date_in_calendar_year_after_index(self):
        by_doc = {f.doc_id: f for f in _sample_filings()}
        filing = by_doc["10073223"]
        assert filing.year == 2025
        assert filing.filing_date == date(2026, 3, 17)

    def test_rejects_unexpected_root(self):
        data = b"<Root><Member /></Root>"
        with pytest.raises(HouseIndexError):
            parse_index_xml(data)

    def test_rejects_malformed_xml(self):
        with pytest.raises(HouseIndexError):
            parse_index_xml(b"<FinancialDisclosure><Member>")

    def test_rejects_missing_required_field(self):
        data = (
            "<FinancialDisclosure>"
            "<Member><Last>NoDocId</Last><FilingType>P</FilingType></Member>"
            "</FinancialDisclosure>"
        ).encode()
        with pytest.raises(HouseIndexError):
            parse_index_xml(data)

    def test_rejects_invalid_year(self):
        data = (
            "<FinancialDisclosure>"
            "<Member><Last>A</Last><FilingType>P</FilingType>"
            "<DocID>20032062</DocID><Year>not-a-year</Year></Member>"
            "</FinancialDisclosure>"
        ).encode()
        with pytest.raises(HouseIndexError):
            parse_index_xml(data)


class TestPtrSelection:
    def test_select_ptrs_filters_to_periodic_transaction_reports(self):
        ptrs = select_ptrs(_sample_filings())
        assert len(ptrs) == 4
        assert all(f.filing_type == "P" for f in ptrs)
        assert sorted(f.doc_id for f in ptrs) == [
            "20026537",
            "20026727",
            "20032062",
            "8220747",
        ]


class TestDocIdClassification:
    def test_efiled_eight_digit(self):
        assert classify_doc_id("20032062") == "efiled"

    def test_scanned_seven_digit(self):
        assert classify_doc_id("8220747") == "scanned"


class TestParseFilingDate:
    def test_parses_m_d_yyyy(self):
        assert parse_filing_date("9/10/2025") == date(2025, 9, 10)
        assert parse_filing_date("12/31/2025") == date(2025, 12, 31)

    def test_blank_returns_none(self):
        assert parse_filing_date("") is None

    def test_malformed_returns_none(self):
        assert parse_filing_date("not-a-date") is None


class TestParseIndexTxt:
    def test_parses_tab_delimited_rows(self):
        data = (
            "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\n"
            "\tAderholt\tRobert B.\t\tP\tAL04\t2025\t9/10/2025\t20032062\n"
            "\tAllen\tRichard W.\t\tP\tGA12\t2025\t1/16/2025\t20026537\n"
        ).encode()
        filings = parse_index_txt(data)
        assert len(filings) == 2
        assert filings[0].doc_id == "20032062"
        assert filings[1].filing_date == date(2025, 1, 16)

    def test_handles_utf8_bom(self):
        data = (
            "\ufeffPrefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\n"
            "\tAderholt\tRobert B.\t\tP\tAL04\t2025\t9/10/2025\t20032062\n"
        ).encode()
        filings = parse_index_txt(data)
        assert len(filings) == 1

    def test_rejects_missing_header(self):
        with pytest.raises(HouseIndexError):
            parse_index_txt(b"Aderholt\t20032062\n")


class TestParseIndexZip:
    def test_prefers_xml_listing(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("2025FD.txt", "Prefix\tLast\n")
            archive.writestr("2025FD.xml", _load_fixture("2025FD_sample.xml"))
        filings = parse_index_zip(buf.getvalue())
        assert len(filings) == 7

    def test_falls_back_to_txt_when_no_xml(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr(
                "2025FD.txt",
                "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\n"
                "\tAderholt\tRobert B.\t\tP\tAL04\t2025\t9/10/2025\t20032062\n",
            )
        filings = parse_index_zip(buf.getvalue())
        assert len(filings) == 1

    def test_rejects_non_zip_bytes(self):
        with pytest.raises(HouseIndexError):
            parse_index_zip(b"not a zip file")

    def test_rejects_zip_without_listing(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("readme.md", "hello")
        with pytest.raises(HouseIndexError):
            parse_index_zip(buf.getvalue())


class TestHouseClerkUrls:
    def setup_method(self):
        self.source = HouseClerkSource()

    def test_index_url(self):
        assert self.source.index_url(2025) == (
            "https://disclosures-clerk.house.gov/public_disc"
            "/financial-pdfs/2025FD.zip"
        )

    def test_pdf_url(self):
        assert self.source.pdf_url(2025, "20032062") == (
            "https://disclosures-clerk.house.gov/public_disc"
            "/ptr-pdfs/2025/20032062.pdf"
        )

    def test_name(self):
        assert self.source.name == "house_clerk"

    def test_fetch_ptrs_is_select_ptrs_of_fetch_index(self, monkeypatch):
        filings = _sample_filings()
        source = HouseClerkSource()
        monkeypatch.setattr(
            source, "fetch_index", lambda year: filings  # noqa: ARG005
        )
        ptrs = source.fetch_ptrs(2025)
        assert len(ptrs) == 4