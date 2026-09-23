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

Each PTR filing is validated against the committed House membership snapshot
(``data/house_members.json``): the member named in the index must have served
the reported state (``StateDst`` postal code) on the filing's ``FilingDate``.
On a confident match the filing carries the member's stable ``bioguide_id``;
on a vacancy, an ambiguity, or an unknown member the adapter raises
:class:`HouseMemberResolveError` rather than guessing. District numbers are
informational only (they renumber across censuses) and the original
``StateDst`` is always preserved verbatim.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from politician_dashboard.ingest.models import Filing
from politician_dashboard.ingest.sources import names
from politician_dashboard.ingest.sources.base import (
    DisclosureSource,
    PTR_FILING_TYPE,
)

BASE_URL = "https://disclosures-clerk.house.gov/public_disc"
INDEX_URL_TEMPLATE = f"{BASE_URL}/financial-pdfs/{{year}}FD.zip"
PTR_PDF_URL_TEMPLATE = f"{BASE_URL}/ptr-pdfs/{{year}}/{{doc_id}}.pdf"

REQUEST_TIMEOUT_SECONDS = 60
USER_AGENT = "Mozilla/5.0 (politician-dashboard/0.1.0; research)"

# Committed reference snapshot of House membership (2000-present), generated
# by ``house_members_refresh.py`` from the community-maintained
# congress-legislators dataset (see the asset's ``source``/``provenance_note``
# fields; it is NOT an official U.S. House or Library of Congress source). It
# lets the adapter validate that a PTR filing's member actually served the
# reported state on the filing date and attach a stable bioguide identity.
REFERENCE_MEMBERS_JSON = Path(__file__).parent / "data" / "house_members.json"

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


class HouseMemberResolveError(RuntimeError):
    """Raised when a House PTR filing's member cannot be identified confidently."""


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

    def __init__(self, members: list[HouseMember] | None = None) -> None:
        """``members`` injects a pre-parsed reference roster for hermetic tests.

        When left unset (live mode) the packaged reference snapshot is loaded
        from ``data/house_members.json`` the first time an index is fetched.
        """
        self._members = members

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
                filings = parse_index_zip(response.read())
        except urllib.error.HTTPError as exc:
            raise HouseIndexError(
                f"Index fetch failed for {year} (HTTP {exc.code}): {url}"
            ) from exc
        except urllib.error.URLError as exc:
            raise HouseIndexError(
                f"Index fetch failed for {year}: {exc.reason} ({url})"
            ) from exc

        if self._members is None:
            self._members = load_default_house_members()

        for index, filing in enumerate(filings):
            if filing.filing_type != PTR_FILING_TYPE:
                continue
            state_district = (filing.state_district or "").strip()
            if len(state_district) < 2:
                raise HouseIndexError(
                    f"PTR {filing.doc_id} has no usable StateDst: "
                    f"{filing.state_district!r}"
                )
            member = resolve_house_member(
                state_district[:2],
                filing.last,
                filing.first,
                anchor_date=filing.filing_date,
                members=self._members,
            )
            filings[index] = replace(filing, bioguide_id=member.bioguide_id)
        return filings


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


@dataclass(frozen=True, slots=True)
class HouseMemberTerm:
    """One House service interval of a :class:`HouseMember`.

    ``end`` is ``None`` for an open-ended term (currently serving). Both bounds
    are inclusive: the reference snapshot records the last day of service as
    the ``end`` of a concluded term, so ``start <= anchor <= end`` means the
    member served on ``anchor``. ``district`` is informational only (House
    districts renumber after every census); ``0`` marks an at-large member or
    a delegate / Resident Commissioner.
    """

    start: date
    end: date | None
    district: int


@dataclass(frozen=True, slots=True)
class HouseMember:
    """A representative/delegate from the reference snapshot.

    ``state`` is the postal code of the state/territory represented (the two
    letters of an eFD ``StateDst`` value). ``given_name`` is the official full
    given-name text (first + middle names when the source records them) used
    for the bounded given-name correspondence; when the snapshot omits it the
    parser falls back to ``first_name``. ``last_name_alt`` is an alternate
    surname spelling a filer may use (e.g. ``"Delaney"`` when the roster
    carries the roll-call compound ``"McClain Delaney"``); it is optional and
    never relied on alone. One member can hold many non-contiguous terms, each
    with its own district.
    """

    bioguide_id: str
    last_name: str
    first_name: str
    given_name: str
    last_name_alt: str | None
    state: str
    terms: tuple[HouseMemberTerm, ...]


def _parse_member_term(entry: object, bioguide_id: str) -> HouseMemberTerm:
    if not isinstance(entry, dict):
        raise HouseMemberResolveError(
            f"House member {bioguide_id} has a non-object term"
        )
    start = entry.get("start")
    end = entry.get("end")
    district = entry.get("district")
    if not isinstance(start, str) or not start:
        raise HouseMemberResolveError(
            f"House member {bioguide_id} has a term without a start date"
        )
    try:
        start_date = date.fromisoformat(start)
    except ValueError as exc:
        raise HouseMemberResolveError(
            f"House member {bioguide_id} has a malformed start date {start!r}"
        ) from exc
    end_date: date | None
    if end is None:
        end_date = None
    elif isinstance(end, str) and end:
        try:
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            raise HouseMemberResolveError(
                f"House member {bioguide_id} has a malformed end date {end!r}"
            ) from exc
    else:
        raise HouseMemberResolveError(
            f"House member {bioguide_id} has a malformed end date {end!r}"
        )
    if not isinstance(district, int):
        raise HouseMemberResolveError(
            f"House member {bioguide_id} has a non-integer district {district!r}"
        )
    return HouseMemberTerm(start=start_date, end=end_date, district=district)


def parse_house_members_json(data: bytes) -> list[HouseMember]:
    """Parse the reference House membership snapshot.

    Raises :class:`HouseMemberResolveError` on a malformed snapshot or any
    member missing its identity or an interpretable term list, so a corrupt
    asset fails closed instead of silently disabling member resolution.
    """
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HouseMemberResolveError(
            f"Malformed house members snapshot: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HouseMemberResolveError(
            "house members snapshot is not a JSON object"
        )
    raw_members = payload.get("members")
    if not isinstance(raw_members, list):
        raise HouseMemberResolveError(
            "house members snapshot has no members list"
        )

    members: list[HouseMember] = []
    for entry in raw_members:
        if not isinstance(entry, dict):
            raise HouseMemberResolveError("house members snapshot has a non-object member")
        bioguide_id = entry.get("bioguide_id")
        last_name = entry.get("last_name")
        first_name = entry.get("first_name")
        state = entry.get("state")
        if not all(
            isinstance(value, str) and value
            for value in (bioguide_id, last_name, first_name, state)
        ):
            raise HouseMemberResolveError(
                "house members snapshot member is missing "
                "bioguide_id/last_name/first_name/state: "
                f"{bioguide_id!r} {last_name!r} {first_name!r}"
            )
        given_name = entry.get("given_name")
        if not isinstance(given_name, str) or not given_name:
            given_name = first_name
        last_name_alt = entry.get("last_name_alt")
        if not isinstance(last_name_alt, str) or not last_name_alt:
            last_name_alt = None
        raw_terms = entry.get("terms")
        if not isinstance(raw_terms, list) or not raw_terms:
            raise HouseMemberResolveError(
                f"House member {bioguide_id} has no terms"
            )
        terms = tuple(
            _parse_member_term(term, bioguide_id) for term in raw_terms
        )
        members.append(
            HouseMember(
                bioguide_id=bioguide_id,
                last_name=last_name,
                first_name=first_name,
                given_name=given_name,
                last_name_alt=last_name_alt,
                state=state,
                terms=terms,
            )
        )
    if not members:
        raise HouseMemberResolveError(
            "house members snapshot contains no members"
        )
    return members


def load_default_house_members() -> list[HouseMember]:
    """Load the packaged reference snapshot, failing closed if unavailable."""
    try:
        data = REFERENCE_MEMBERS_JSON.read_bytes()
    except OSError as exc:
        raise HouseMemberResolveError(
            f"House reference membership snapshot unavailable: {exc}"
        ) from exc
    return parse_house_members_json(data)


def _serves_on(member: HouseMember, anchor_date: date) -> bool:
    """Whether any of the member's (inclusive) service intervals cover the date."""
    return any(
        term.start <= anchor_date and (term.end is None or anchor_date <= term.end)
        for term in member.terms
    )


def _format_member(member: HouseMember) -> str:
    return f"{member.first_name} {member.last_name} ({member.state})"


def resolve_house_member(
    state: str,
    last: str,
    first: str,
    *,
    anchor_date: date | None = None,
    members: list[HouseMember] | None = None,
) -> HouseMember:
    """Resolve a House PTR filer to (at most) one reference member.

    Candidates must represent ``state`` (the two-letter prefix of the eFD
    ``StateDst``), agree on the normalized last name -- against the roster
    surname or its optional filer-spelling alternate -- satisfy the bounded
    given-name correspondence, and -- when ``anchor_date`` is given -- have a
    House service interval covering that date. District is deliberately not a
    match criterion: House districts renumber after every census (members
    change district numbers while their service is continuous), so a filing's
    ``StateDst`` digits are informational only.

    This mirrors the Senate resolver's safety invariant (never guessing):
    matching is anchored by an exact surname equality inside one state, the
    given-name correspondence is bounded (:mod:`names`), and service dates
    must legitimately cover the filing date. If zero candidates satisfy the
    rules (a vacancy day, an unknown person, or an unsupported state code), or
    more than one does (a real ambiguity, e.g. a father/son pair sharing a
    name, state and surname), resolution raises :class:`HouseMemberResolveError`
    rather than guessing. Without ``anchor_date`` a filing with an unknown
    parse date can still be identified when the state+surname+given-name group
    is unique.
    """
    if not members:
        raise HouseMemberResolveError(
            f"House membership snapshot unavailable: '{first} {last}' "
            f"({state}) cannot be resolved"
        )
    state_key = state.strip().upper()
    last_key = names.normalize_name(last)
    hits: list[HouseMember] = []
    for member in members:
        if member.state != state_key:
            continue
        if names.normalize_name(member.last_name) != last_key:
            if not (
                member.last_name_alt
                and names.normalize_name(member.last_name_alt) == last_key
            ):
                continue
        if not names.given_names_agree(
            first, member.first_name, official_given=member.given_name
        ):
            continue
        if anchor_date is not None and not _serves_on(member, anchor_date):
            continue
        hits.append(member)

    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise HouseMemberResolveError(
            f"Ambiguous House member: '{first} {last}' ({state_key}) matches "
            f"multiple reference members on "
            f"{anchor_date.isoformat() if anchor_date else 'any date'} "
            f"({', '.join(_format_member(member) for member in hits)}); "
            "refusing to guess"
        )
    raise HouseMemberResolveError(
        f"Unresolved House member: '{first} {last}' ({state_key}); "
        "no reference House service covers "
        f"{anchor_date.isoformat() if anchor_date else 'any date'}"
    )