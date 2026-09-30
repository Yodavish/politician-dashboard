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


class HouseMemberAmbiguousError(HouseMemberResolveError):
    """Raised when a filing's state and name match more than one member.

    A distinct type because ambiguity is a different kind of failure from an
    unknown identity: several reference members remain equally plausible, so
    there is no safe way to choose between them. Callers that recover from an
    unresolvable filing must not recover from this one, and the diagnostic
    must not be relabelled as a plain "no match".
    """


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
            member = resolve_ptr_member(
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
    parser falls back to ``first_name``. ``last_name_alts`` are the alternate
    surname spellings a filer may use, all of them exact-match keys rather
    than similarity hints: a compound roster surname filed under its final
    token (``"Delaney"`` for ``"McClain Delaney"``), and the given-name tail a
    filer may push into the ``LastName`` field (``"Paulina Luna"`` for the
    roster's ``"Luna"`` / given name ``"Anna Paulina"``, which is how eFD's
    first-space split records Anna Paulina Luna). It is optional, it is
    generated by :mod:`house_members_refresh`, and an alternate is never
    sufficient on its own: the given-name correspondence and the service
    interval are still required. One member can hold many non-contiguous terms,
    each with its own district.
    """

    bioguide_id: str
    last_name: str
    first_name: str
    given_name: str
    last_name_alts: tuple[str, ...]
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
        if "last_name_alt" in entry:
            # Fail loudly rather than quietly resolving fewer filings: a
            # snapshot on the retired singular field parses, but every member
            # loses its alternate spellings and the resulting unresolved rows
            # would look like new data problems.
            raise HouseMemberResolveError(
                f"House member {bioguide_id} uses the retired 'last_name_alt' "
                "field; regenerate the snapshot with "
                "'python -m politician_dashboard.ingest.sources."
                "house_members_refresh'"
            )
        raw_alts = entry.get("last_name_alts")
        if raw_alts is None:
            last_name_alts: tuple[str, ...] = ()
        elif isinstance(raw_alts, list) and all(
            isinstance(value, str) and value.strip() for value in raw_alts
        ):
            last_name_alts = tuple(value.strip() for value in raw_alts)
        else:
            raise HouseMemberResolveError(
                f"House member {bioguide_id} has a malformed last_name_alts: "
                f"{raw_alts!r}"
            )
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
                last_name_alts=last_name_alts,
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


def _normalize_house_surname(value: str) -> str:
    """Normalize a surname for matching, removing known trailing credentials."""
    tokens = names.normalize_name(value).split()
    while len(tokens) > 1 and tokens[-1].strip(".,;") in _POSTNOMINAL_CREDENTIALS:
        tokens.pop()
    return " ".join(tokens).rstrip(".,;")


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
    ``StateDst``), agree on the normalized last name (ignoring recognized
    trailing professional credentials) -- against the roster surname or one of
    its exact-match ``last_name_alts`` -- satisfy the bounded given-name
    correspondence, and -- when ``anchor_date`` is given -- have a House
    service interval covering that date. District is deliberately not a
    match criterion: House districts renumber after every census (members
    change district numbers while their service is continuous), so a filing's
    ``StateDst`` digits are informational only.

    The surname comparison is equality, never similarity. An alternate only
    adds one more spelling a filer may have used; it does not relax the rule,
    so no edit distance, substring, or initial-matching behaviour exists here.
    A filer who records the given-name tail in the ``LastName`` field (eFD
    splits on the first space, recording Anna Paulina Luna as
    ``First="Anna"``, ``Last="Paulina Luna"``) matches through a generated
    alternate while the given-name correspondence and the service interval
    are still required.

    This mirrors the Senate resolver's safety invariant (never guessing):
    matching is anchored by an exact surname equality inside one state, the
    given-name correspondence is bounded (:mod:`names`), and service dates
    must legitimately cover the filing date. If zero candidates satisfy the
    rules (a vacancy day, an unknown person, or an unsupported state code), or
    more than one does (a real ambiguity, e.g. a father/son pair sharing a
    name, state and surname), resolution raises rather than guessing. The
    ambiguous case raises :class:`HouseMemberAmbiguousError`; every other
    failure raises :class:`HouseMemberResolveError`, and the two are reported
    differently because a matched-but-not-serving identity is not a name
    mismatch. Without ``anchor_date`` a filing with an unknown
    parse date can still be identified when the state+surname+given-name group
    is unique.
    """
    if not members:
        raise HouseMemberResolveError(
            f"House membership snapshot unavailable: '{first} {last}' "
            f"({state}) cannot be resolved"
        )
    state_key = state.strip().upper()
    last_key = _normalize_house_surname(last)
    identity_hits: list[HouseMember] = []
    hits: list[HouseMember] = []
    for member in members:
        if member.state != state_key:
            continue
        if _normalize_house_surname(member.last_name) != last_key and not any(
            _normalize_house_surname(alt) == last_key for alt in member.last_name_alts
        ):
            continue
        if not names.given_names_agree(
            first, member.first_name, official_given=member.given_name
        ):
            continue
        identity_hits.append(member)
        if anchor_date is not None and not _serves_on(member, anchor_date):
            continue
        hits.append(member)

    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise HouseMemberAmbiguousError(
            f"Ambiguous House member: '{first} {last}' ({state_key}) matches "
            f"multiple reference members on "
            f"{anchor_date.isoformat() if anchor_date else 'any date'} "
            f"({', '.join(_format_member(member) for member in hits)}); "
            "refusing to guess"
        )
    if identity_hits:
        if len(identity_hits) > 1:
            # The date disambiguated for some filings (a term that covers
            # 2011-06-01 separates the two Donald Paynes) but not for this one.
            # The identity itself stays undecidable and the unanchored retry
            # below would reach the same verdict, so report it as ambiguity
            # here rather than blaming a service record that is not at fault.
            raise HouseMemberAmbiguousError(
                f"Ambiguous House member: '{first} {last}' ({state_key}) matches "
                f"multiple reference members; refusing to guess (filing dated "
                f"{anchor_date.isoformat()})"
            )
        # Exactly one member carries this identity and was simply not in office
        # on the filing date: a vacancy day, a service gap, or a filing dated
        # before the member took office. Reporting this as an unknown name
        # would send the reader after a spelling mismatch that does not exist.
        raise HouseMemberResolveError(
            f"Unresolved House member: '{first} {last}' ({state_key}); matched "
            f"reference member "
            f"{_format_member(identity_hits[0])} "
            f"but no reference House service covers {anchor_date.isoformat()}"
        )
    raise HouseMemberResolveError(
        f"Unresolved House member: '{first} {last}' ({state_key}); no reference "
        f"House member matches this state and name on "
        f"{anchor_date.isoformat() if anchor_date else 'any date'}"
    )


def _service_ended_before(member: HouseMember, anchor_date: date) -> bool:
    """Whether every service interval of ``member`` ended before ``anchor_date``.

    A late PTR is filed after the filer left office; admitting it under the
    post-service fallback requires the member's representation to have
    conclusively ended before the filing date. An open-ended or later term,
    or a term ending on/after the filing date, fails this check so a filing
    is never attributed forward into a vacancy, a service gap, or a seat a
    member did not yet hold.
    """
    return all(
        term.end is not None and term.end < anchor_date for term in member.terms
    )


def resolve_ptr_member(
    state: str,
    last: str,
    first: str,
    *,
    anchor_date: date | None,
    members: list[HouseMember],
) -> HouseMember:
    """Resolve a House PTR filer, admitting post-service late filings.

    The date-anchored resolution (:func:`resolve_house_member`) is tried
    first: a filing dated during the filer's House service resolves exactly
    as before. When that fails, the identity is re-resolved without an anchor
    and accepted only when exactly one reference member matches (ambiguous
    and unknown identities still raise, as
    :class:`HouseMemberAmbiguousError` and :class:`HouseMemberResolveError`
    respectively) AND
    that member's service ended before the filing date -- i.e. a legitimate
    late PTR filed after leaving office. No fixed grace period is applied:
    the requirement is purely that the service record is complete and before
    the filing date, so a filing dated before service began or during a
    service gap still fails closed. The resolved member's stable ``bioguide_id``
    is preserved.

    Every failure is reported against the ``anchor_date`` the index supplied.
    The unanchored retry is an internal lookup, not a claim that the filing
    has no date, so its "any date" wording is never allowed to reach the
    caller: a failure that loses the filing date sends an operator looking for
    a service-interval problem when the real cause may be the name itself.
    """
    try:
        return resolve_house_member(
            state, last, first, anchor_date=anchor_date, members=members
        )
    except HouseMemberAmbiguousError:
        # The unanchored retry below searches a superset of the candidates
        # matched here (it only drops the service-date filter), so it cannot
        # reduce an ambiguous group to one. Fail now, with the filing date the
        # index actually reported.
        raise
    except HouseMemberResolveError:
        if anchor_date is None:
            raise

    # Retry without the anchor to admit a filing made after the filer left
    # office. This lookup is deliberately unanchored -- the point is to find a
    # member who was NOT serving on the filing date -- so it must never be
    # reported as if the filing itself had no date. The candidate set is the
    # same one the anchored stage just searched (only the service-date filter is
    # dropped), so an ambiguity would already have been raised above; the guard
    # keeps that true locally rather than relying on the argument holding.
    try:
        member = resolve_house_member(
            state, last, first, anchor_date=None, members=members
        )
    except HouseMemberAmbiguousError:
        raise
    except HouseMemberResolveError as exc:
        raise HouseMemberResolveError(
            f"Unresolved House member: '{first} {last}' ({state}); no reference "
            f"House member matches this state and name on any date, so none can "
            f"have served on the filing dated {anchor_date.isoformat()}"
        ) from exc
    if not _service_ended_before(member, anchor_date):
        raise HouseMemberResolveError(
            f"Unresolved House member: '{first} {last}' ({state}); "
            f"no reference House service covers {anchor_date.isoformat()}"
        )
    return member
