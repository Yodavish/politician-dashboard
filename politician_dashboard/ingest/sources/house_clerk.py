"""U.S. House Office of the Clerk disclosures source adapter.

The Clerk publishes a yearly disclosure index and the individual PTR PDFs:

- Index: <BASE>/financial-pdfs/{year}FD.zip
    A ZIP containing {year}FD.xml (and an identical {year}FD.txt listing).
    Each <Member> row describes one filing with fields
    Prefix, Last, First, Suffix, FilingType, StateDst, Year, FilingDate,
    DocID. FilingType "P" marks a Periodic Transaction Report (a trade
    disclosure); all other filing types are ignored.
- PTR PDFs: <BASE>/ptr-pdfs/{year}/{DocID}.pdf

E-filed PTRs have 8-digit DocIDs starting with "2" and carry a text layer.
Paper filings have 7-digit DocIDs; the PDF is a scan (image only, no text).
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

from politician_dashboard.ingest.models import Filing
from politician_dashboard.ingest.sources.base import DisclosureSource

BASE_URL = "https://disclosures-clerk.house.gov/public_disc"
INDEX_URL_TEMPLATE = f"{BASE_URL}/financial-pdfs/{{year}}FD.zip"
PTR_PDF_URL_TEMPLATE = f"{BASE_URL}/ptr-pdfs/{{year}}/{{doc_id}}.pdf"

REQUEST_TIMEOUT_SECONDS = 60
USER_AGENT = "Mozilla/5.0 (politician-dashboard/0.1.0; research)"

_MEMBER_TAGS = (
    "Prefix",
    "Last",
    "First",
    "Suffix",
    "FilingType",
    "StateDst",
    "Year",
    "FilingDate",
    "DocID",
)
_REQUIRED_FIELDS = ("Last", "FilingType", "DocID")


class HouseIndexError(RuntimeError):
    """Raised when a House disclosure index cannot be fetched or parsed."""


class HouseDownloadError(RuntimeError):
    """Raised when a House PTR PDF cannot be downloaded."""


def classify_doc_id(doc_id: str) -> str:
    """Classify a House PTR DocID as ``efiled`` or ``scanned``.

    7-digit document IDs denote paper (scanned) filings that have no text
    layer and would require OCR; everything else is treated as e-filed.
    """
    if doc_id.isdigit() and len(doc_id) == 7:
        return "scanned"
    return "efiled"


def parse_filing_date(value: str) -> date | None:
    """Parse ``M/D/YYYY``; return None for blank or malformed values."""
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        return None


def _field(member: ET.Element, tag: str) -> str:
    value = member.findtext(tag)
    return (value or "").strip()


def _to_filing(member: ET.Element) -> Filing:
    fields = {tag: _field(member, tag) for tag in _MEMBER_TAGS}
    for required in _REQUIRED_FIELDS:
        if not fields[required]:
            raise HouseIndexError(
                f"index row missing required field '{required}': "
                f"{ET.tostring(member, encoding='unicode')[:200]}"
            )

    year_raw = fields["Year"]
    if not year_raw.isdigit():
        raise HouseIndexError(
            f"index row has invalid Year '{year_raw}' for DocID {fields['DocID']}"
        )

    return Filing(
        prefix=fields["Prefix"],
        last=fields["Last"],
        first=fields["First"],
        suffix=fields["Suffix"],
        filing_type=fields["FilingType"],
        state_district=fields["StateDst"],
        year=int(year_raw),
        filing_date=parse_filing_date(fields["FilingDate"]),
        doc_id=fields["DocID"],
    )


def parse_index_xml(data: bytes) -> list[Filing]:
    """Parse the XML index into :class:`Filing` records."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise HouseIndexError(f"Malformed index XML: {exc}") from exc

    if root.tag != "FinancialDisclosure":
        raise HouseIndexError(
            f"Unexpected index root element '{root.tag}' (expected 'FinancialDisclosure')"
        )

    filings: list[Filing] = []
    for child in root:
        if child.tag != "Member":
            continue
        filings.append(_to_filing(child))
    return filings


def parse_index_txt(data: bytes) -> list[Filing]:
    """Parse the tab-delimited index text into :class:`Filing` records."""
    text = data.decode("utf-8", errors="replace")
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames is None:
        raise HouseIndexError("Index text has no header row")

    if not all(tag in reader.fieldnames for tag in _MEMBER_TAGS):
        raise HouseIndexError(
            f"Index text header is missing expected columns: {reader.fieldnames}"
        )

    filings: list[Filing] = []
    for row in reader:
        filings.append(
            Filing(
                prefix=(row["Prefix"] or "").strip(),
                last=(row["Last"] or "").strip(),
                first=(row["First"] or "").strip(),
                suffix=(row["Suffix"] or "").strip(),
                filing_type=(row["FilingType"] or "").strip(),
                state_district=(row["StateDst"] or "").strip(),
                year=int((row["Year"] or "").strip()),
                filing_date=parse_filing_date((row["FilingDate"] or "").strip()),
                doc_id=(row["DocID"] or "").strip(),
            )
        )
    return filings


def parse_index_zip(data: bytes) -> list[Filing]:
    """Unpack an ``{year}FD.zip`` index and parse its listing.

    The XML listing is preferred; the tab-delimited text is used as a
    fallback if no XML is present in the archive.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HouseIndexError("Index bytes are not a valid ZIP archive") from exc

    xml_members = [
        name for name in archive.namelist() if name.lower().endswith(".xml")
    ]
    txt_members = [
        name for name in archive.namelist() if name.lower().endswith(".txt")
    ]
    chosen = xml_members or txt_members
    if not chosen:
        raise HouseIndexError("Index ZIP contains no XML or TXT filing listing")

    raw = archive.read(chosen[0])
    if chosen[0].lower().endswith(".xml"):
        return parse_index_xml(raw)
    return parse_index_txt(raw)


class HouseClerkSource(DisclosureSource):
    name = "house_clerk"

    def index_url(self, year: int) -> str:
        return INDEX_URL_TEMPLATE.format(year=year)

    def pdf_url(self, year: int, doc_id: str) -> str:
        return PTR_PDF_URL_TEMPLATE.format(year=year, doc_id=doc_id)

    def fetch_index(self, year: int) -> list[Filing]:
        url = self.index_url(year)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                return parse_index_zip(response.read())
        except urllib.error.HTTPError as exc:
            raise HouseIndexError(
                f"Index fetch failed for {year} (HTTP {exc.code}): {url}"
            ) from exc
        except urllib.error.URLError as exc:
            raise HouseIndexError(
                f"Index fetch failed for {year}: {exc.reason} ({url})"
            ) from exc


def download_pdf(url: str) -> bytes:
    """Download a House PTR PDF and return its raw bytes.

    Raises :class:`HouseDownloadError` on any transport error.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise HouseDownloadError(
            f"PDF download failed (HTTP {exc.code}): {url}"
        ) from exc
    except urllib.error.URLError as exc:
        raise HouseDownloadError(
            f"PDF download failed: {exc.reason} ({url})"
        ) from exc