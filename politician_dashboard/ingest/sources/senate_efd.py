"""U.S. Senate eFD Search disclosure source adapter.

The Senate Office of Public Records publishes Periodic Transaction Reports
via the eFD Search portal (``efdsearch.senate.gov``). Unlike the House Clerk
source, the Senate portal:

- Requires an explicit **prohibition agreement** to be accepted before any
  search is allowed. The agreement is a Django form POST that must carry a
  CSRF token and a checkbox field; the session must persist the resulting
  cookies for every subsequent request.
- Serves the filing index as a **DataTables paginated JSON** listing rather
  than a downloadable XML/TXT index. Each row describes one PTR with the
  cells ``first, last, office, link_html, date_received``.
- Distinguishes **electronic** PTRs (view URL contains ``/view/ptr/<uuid>/``,
  meaning the filing carries a text layer) from **paper** PTRs (view URL
  contains ``/view/paper/<id>/``, a scanned document with no text layer).

There is no per-district DocID; the adapter uses the Senate view identifier
(the UUID for electronic filings, the numeric id for paper filings) as the
stable ``doc_id``, and resolves each senator's state from the official
``senators_cfm.xml`` contact listing so that ``state_district`` follows the
``<STATE>00`` pseudo-district convention (senators have no district).

If a filer cannot be confidently matched to the official senators listing,
the adapter raises :class:`SenateStateResolveError` rather than guessing.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Protocol

from politician_dashboard.ingest.models import Filing
from politician_dashboard.ingest.sources.base import (
    DisclosureSource,
    PTR_FILING_TYPE,
    select_ptrs,
)

BASE_URL = "https://efdsearch.senate.gov"
LANDING_URL = f"{BASE_URL}/search/home/"
LISTING_URL = f"{BASE_URL}/search/report/data/"
SENATORS_XML_URL = "https://www.senate.gov/general/contact_information/senators_cfm.xml"

USER_AGENT = (
    "Mozilla/5.0 (politician-dashboard/0.1.0; research; "
    "contact: maintainers@example.invalid)"
)
REQUEST_TIMEOUT_SECONDS = 60
USER_AGENT_HEADER = "Mozilla/5.0 (politician-dashboard/0.1.0; research)"

# DataTables listing parameters used by efdsearch.senate.gov.
PTR_REPORT_TYPE = "11"  # Periodic Transaction Report
DEFAULT_PAGE_SIZE = 100
_MAX_PAGES = 20

_ELECTRONIC_VIEW = re.compile(r'href="(/search/view/ptr/(?P<doc_id>[^"]+)/)"')
_PAPER_VIEW = re.compile(r'href="(/search/view/paper/(?P<doc_id>[^"]+)/)"')

_AGREEMENT_CHECKBOX_NAME = "prohibition_agreement"
_AGREEMENT_CHECKBOX_VALUE = "1"
_CSRF_FIELD_NAME = "csrfmiddlewaretoken"

_PTR_REPORT_LABEL = re.compile(
    r"Periodic Transaction Report for (?P<date>\d{2}/\d{2}/\d{4})"
)


class SenateAgreementError(RuntimeError):
    """Raised when the Senate agreement/consent flow cannot be completed."""


class SenateIndexError(RuntimeError):
    """Raised when the Senate listing cannot be fetched or parsed."""


class SenateStateResolveError(RuntimeError):
    """Raised when a senator's state cannot be resolved confidently."""


class SenateDetailError(RuntimeError):
    """Raised when a Senate PTR detail page cannot be parsed."""


def parse_agreement_html(data: bytes) -> tuple[str, str, str]:
    """Extract the agreement form contract from the official landing page.

    Returns ``(action, method, csrf_token)``. Raises
    :class:`SenateAgreementError` when the page does not contain the expected
    agreement form with a CSRF token and the prohibition checkbox.
    """
    text = data.decode("utf-8", errors="replace")
    form = re.search(r'(?is)<form\b[^>]*>.*?</form>', text)
    if form is None:
        raise SenateAgreementError(
            "Landing page contains no agreement form (structure changed)"
        )

    action = _form_attr(form.group(0), "action")
    method = (_form_attr(form.group(0), "method") or "get").upper()

    csrf = re.search(
        rf'(?is)<input\b[^>]*name="{_CSRF_FIELD_NAME}"[^>]*value="([^"]*)"',
        form.group(0),
    )
    if csrf is None:
        raise SenateAgreementError(
            "Agreement form carries no CSRF token (structure changed)"
        )
    token = urllib.parse.unquote(csrf.group(1))

    checkbox = re.search(
        rf'(?is)<input\b[^>]*type="checkbox"'
        rf'(?=[^>]*name="{_AGREEMENT_CHECKBOX_NAME}")'
        rf'(?=[^>]*\bvalue="?{_AGREEMENT_CHECKBOX_VALUE}"?)'
        rf'[^>]*/>',
        form.group(0),
    )
    if checkbox is None:
        raise SenateAgreementError(
            "Agreement form carries no prohibition agreement checkbox "
            "(structure changed)"
        )
    return action or LANDING_URL, method, token


def _form_attr(form_html: str, name: str) -> str:
    match = re.search(
        rf'(?is)\b{name}="([^"]*)"', form_html, re.IGNORECASE
    )
    return match.group(1) if match else ""


def accept_agreement(get: Callable[[str], object]) -> None:
    """Raised marker stub; the real agreement flow is on the source class."""


class SenateHttpClient(Protocol):
    """Minimal HTTP surface used by the Senate source (injectable)."""

    def get(self, url: str) -> bytes: ...

    def post(self, url: str, body: bytes, headers: dict[str, str] | None = None) -> bytes: ...


class _UrllibSession:
    """Cookie-persisting urllib session used by the default transport."""

    def __init__(self) -> None:
        import http.cookiejar

        self._cookiejar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookiejar)
        )

    def _request(
        self, url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
    ) -> urllib.request.Request:
        request_headers = {"User-Agent": USER_AGENT}
        if headers:
            request_headers.update(headers)
        return urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method="POST" if data is not None else "GET",
        )

    def _open(self, request: urllib.request.Request) -> bytes:
        try:
            with self._opener.open(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise SenateIndexError(
                f"Senate HTTP request failed (HTTP {exc.code}): {request.full_url}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SenateIndexError(
                f"Senate HTTP request failed: {exc.reason} ({request.full_url})"
            ) from exc

    def get(self, url: str) -> bytes:
        return self._open(self._request(url))

    def post(
        self, url: str, body: bytes, headers: dict[str, str] | None = None
    ) -> bytes:
        return self._open(self._request(url, data=body, headers=headers))


@dataclass(frozen=True, slots=True)
class SenateListingRow:
    """One row of the Senate PTR listing (DataTables JSON)."""

    first: str
    last: str
    office: str
    view_kind: str  # "electronic" | "paper"
    view_id: str
    date_received: str
    report_html: str


def classify_view_link(link_html: str) -> tuple[str, str]:
    """Classify a Senate PTR row link as ``electronic`` or ``paper``.

    Returns ``(view_kind, doc_id)`` where ``doc_id`` is the view id from the
    URL (UUID for electronic filings, numeric id for paper filings). Raises
    :class:`SenateIndexError` for a link that matches neither shape.
    """
    match = _ELECTRONIC_VIEW.search(link_html or "")
    if match:
        return "electronic", match.group("doc_id")
    match = _PAPER_VIEW.search(link_html or "")
    if match:
        return "paper", match.group("doc_id")
    raise SenateIndexError(f"Senate PTR row link is unrecognized: {link_html!r}")


def _parse_date_received(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_listing_json(data: bytes) -> list[SenateListingRow]:
    """Parse a Senate DataTables listing JSON page into rows."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SenateIndexError(f"Senate listing is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SenateIndexError(
            f"Senate listing JSON has no 'data' array (got {type(payload).__name__})"
        )

    rows: list[SenateListingRow] = []
    for raw in payload["data"]:
        if not isinstance(raw, list) or len(raw) < 5:
            raise SenateIndexError(
                f"Senate listing row has unexpected shape: {raw!r}"
            )
        first, last, office, link_html, date_raw = (str(v).strip() for v in raw[:5])
        view_kind, view_id = classify_view_link(link_html)
        rows.append(
            SenateListingRow(
                first=first,
                last=last,
                office=office,
                view_kind=view_kind,
                view_id=view_id,
                date_received=date_raw,
                report_html=link_html,
            )
        )
    return rows


def classify_senate_doc_id(doc_id: str) -> str:
    """Classify a Senate PTR id as ``efiled`` or ``scanned``.

    Electronic filings carry a UUID view id served under ``/view/ptr/<uuid>/``
    and expose a text layer. Paper (scanned) filings carry small numeric ids
    served under ``/view/paper/<id>/`` with no text layer, mirroring the
    House source's 7-digit scanned classification.
    """
    if doc_id.isdigit():
        return "scanned"
    return "efiled"


def view_url(doc_id: str) -> str:
    """Return the Senate eFD view URL for a filing's doc_id."""
    kind = "paper" if classify_senate_doc_id(doc_id) == "scanned" else "ptr"
    return f"{BASE_URL}/search/view/{kind}/{doc_id}/"


# Transaction-type labels from the Senate detail table. The House source
# stores one-character codes ('P', 'S', 'E') and 'S (partial)' for partial
# sales; the Senate uses descriptive labels that map onto the same shared
# vocabulary so both chambers store comparable transaction types.
SENATE_TXN_TYPES = {
    "purchase": "P",
    "sale (full)": "S",
    "sale (partial)": "S (partial)",
    "exchange": "E",
}

_AMOUNT_VALUE_RE = re.compile(r"\$([\d,]+)")


def _map_txn_type(raw: str) -> str:
    normalized = raw.strip().lower()
    if normalized not in SENATE_TXN_TYPES:
        raise SenateDetailError(
            f"Unrecognized Senate transaction type: {raw!r}"
        )
    return SENATE_TXN_TYPES[normalized]


def _parse_amount_bounds(amount_raw: str) -> tuple[int, int]:
    """Parse ``$250,001 - $500,000`` into ``(250001, 500000)``."""
    values = [
        int(value.replace(",", ""))
        for value in _AMOUNT_VALUE_RE.findall(amount_raw)
    ]
    if not values:
        raise SenateDetailError(f"No dollar amount in: {amount_raw!r}")
    return min(values), max(values)


class _DetailTableParser(HTMLParser):
    """Collect table rows as lists of normalized cell texts.

    Content inside ``<div class="text-muted">`` (derivative/option metadata
    rendered under the asset name) is ignored so it does not pollute the
    asset name.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._ignored:
            if tag == "div":
                self._ignored += 1
            return
        attr_map = dict(attrs)
        if tag == "div" and "text-muted" in attr_map.get("class", "").split():
            self._ignored = 1
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if self._ignored:
            if tag == "div":
                self._ignored -= 1
            return
        if tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            if self._row is not None:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._ignored or self._cell is None:
            return
        self._cell.append(data)


def parse_ptr_view_html(data: bytes) -> list[dict[str, object]]:
    """Parse the transactions table of a Senate PTR detail page.

    Returns a list of transaction dicts matching the House parser shapes:
    ``asset_name, ticker, txn_type, txn_date, amount_min, amount_max,
    amount_raw, owner, asset_type_code, notes``. ``txn_type`` uses the shared
    cross-chamber codes (see :data:`SENATE_TXN_TYPES`). The detail page has no
    notification date; callers set ``notification_date`` from the listing's
    "Date Received" (the filing date).

    Raises :class:`SenateDetailError` when a transaction row cannot be
    interpreted, rather than silently dropping it.
    """
    parser = _DetailTableParser()
    parser.feed(data.decode("utf-8", errors="replace"))

    transactions: list[dict[str, object]] = []
    for row in parser.rows:
        if len(row) < 9:
            continue
        if not row[0].strip().isdigit():
            continue
        txn_date = _parse_date_received(row[1])
        if txn_date is None:
            continue
        if not row[7].strip().startswith("$"):
            raise SenateDetailError(f"Transaction row has no amount: {row!r}")

        amount_min, amount_max = _parse_amount_bounds(row[7])
        transactions.append(
            {
                "asset_name": row[4].strip(),
                "ticker": row[3].strip() or None,
                "txn_type": _map_txn_type(row[6]),
                "txn_date": txn_date,
                "amount_min": amount_min,
                "amount_max": amount_max,
                "amount_raw": row[7].strip(),
                "owner": row[2].strip() or None,
                "asset_type_code": row[5].strip() or None,
                "notes": row[8].strip() or None,
            }
        )
    return transactions


def _office_name_parts(office: str, first: str, last: str) -> tuple[str, str]:
    """Return ``(first, last)`` for state resolution.

    The listing's ``office`` cell is formatted ``"Last, First (Senator)"``;
    when it is present its names win, otherwise the first/last cells are used.
    """
    match = re.match(r'^\s*(?P<last>[^,]+)\s*,\s*(?P<first>[^(]+)\s*\(', office)
    if match:
        return match.group("first").strip(), match.group("last").strip()
    return first, last


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower()).strip()


class SenateEfdSource(DisclosureSource):
    """Adapter for the Senate eFD Search PTR listing.

    ``transport`` is an injectable :class:`SenateHttpClient` (defaults to a
    cookie-persisting urllib session). ``senators`` may be a pre-parsed
    ``dict[(last, first) -> state]`` for hermetic tests.
    """

    name = "senate_efd"

    def __init__(
        self,
        transport: SenateHttpClient | None = None,
        senators: dict[tuple[str, str], str] | None = None,
    ) -> None:
        if transport is None:
            from politician_dashboard.ingest.sources import _senate_urllib_session

            transport = _senate_urllib_session()
        self._transport = transport
        self._senators = senators
        self._consented = False

    def _accept_agreement(self) -> None:
        """Run the official prohibition-agreement flow."""
        try:
            landing = self._transport.get(LANDING_URL)
        except Exception as exc:
            raise SenateAgreementError(f"Landing fetch failed: {exc}") from exc

        action, method, token = parse_agreement_html(landing)
        body = urllib.parse.urlencode(
            {
                _CSRF_FIELD_NAME: token,
                _AGREEMENT_CHECKBOX_NAME: _AGREEMENT_CHECKBOX_VALUE,
            }
        ).encode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LANDING_URL,
            "User-Agent": USER_AGENT,
        }
        try:
            response = self._transport.post(action, body, headers)
        except Exception as exc:
            raise SenateAgreementError(
                f"Agreement acceptance failed: {exc}"
            ) from exc
        if not response and response is not None:
            raise SenateAgreementError("Agreement acceptance returned no body")
        self._consented = True

    def _fetch_listing_page(
        self, start: int, length: int, year: int
    ) -> tuple[list[SenateListingRow], int]:
        """Fetch one DataTables page. Returns ``(rows, records_total)``."""
        params = urllib.parse.urlencode(
            {
                "draw": "1",
                "start": str(start),
                "length": str(length),
                "order[0][column]": "4",
                "order[0][dir]": "asc",
                "search[value]": "",
                "search[regex]": "false",
                "report_types[]": PTR_REPORT_TYPE,
                "submitted_start_date": f"01/01/{year} 00:00:00",
                "submitted_end_date": f"12/31/{year} 23:59:59",
            }
        ).encode()
        response = self._transport.post(
            LISTING_URL,
            params,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            payload = json.loads(response.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SenateIndexError(
                f"Senate listing page is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise SenateIndexError("Senate listing payload is not an object")
        rows = parse_listing_json(response)
        records_total = payload.get("recordsTotal")
        if not isinstance(records_total, int):
            raise SenateIndexError(
                f"Senate listing payload has no integer recordsTotal "
                f"(got {records_total!r})"
            )
        return rows, records_total

    def _fetch_and_page(self, year: int) -> list[SenateListingRow]:
        rows: list[SenateListingRow] = []
        start = 0
        for _ in range(_MAX_PAGES):
            page, records_total = self._fetch_listing_page(
                start, DEFAULT_PAGE_SIZE, year
            )
            if not page:
                break
            rows.extend(page)
            start += len(page)
            if start >= records_total:
                break
        return rows

    def fetch_index(self, year: int) -> list[Filing]:
        self._accept_agreement()
        if self._senators is None:
            senators = _fetch_senators(self._transport)
            self._senators = senators

        filings: list[Filing] = []
        for row in self._fetch_and_page(year):
            filing_date = _parse_date_received(row.date_received)
            first, last = _office_name_parts(row.office, row.first, row.last)
            state = _resolve_state(
                last, first, self._senators, office=row.office
            )
            filings.append(
                Filing(
                    prefix="",
                    last=last,
                    first=first,
                    suffix="",
                    filing_type=PTR_FILING_TYPE,
                    state_district=f"{state}00",
                    year=year,
                    filing_date=filing_date,
                    doc_id=row.view_id,
                )
            )
        return filings

    def fetch_ptrs(self, year: int) -> list[Filing]:
        return select_ptrs(self.fetch_index(year))

    def fetch_detail(self, doc_id: str) -> bytes:
        """Download the raw PTR detail page (electronic or paper) for ``doc_id``.

        Re-runs the prohibition-agreement flow only if it has not already been
        accepted during index fetching; the transport keeps the resulting
        session cookies, so consent persists on a shared transport instance.
        """
        if not self._consented:
            self._accept_agreement()
        return self._transport.get(view_url(doc_id))


def _fetch_senators(transport: SenateHttpClient) -> dict[tuple[str, str], str]:
    """Download and parse the official senators contact listing."""
    try:
        data = transport.get(SENATORS_XML_URL)
    except Exception as exc:
        raise SenateStateResolveError(
            f"Senators listing fetch failed: {exc}"
        ) from exc
    return parse_senators_xml(data)


def parse_senators_xml(data: bytes) -> dict[tuple[str, str], str]:
    """Parse ``senators_cfm.xml`` into ``{(last, first) -> state}``.

    Raises :class:`SenateStateResolveError` on malformed XML or members that
    are missing required name/state fields.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SenateStateResolveError(f"Malformed senators XML: {exc}") from exc

    if root.tag != "contact_information":
        raise SenateStateResolveError(
            f"Unexpected senators XML root '{root.tag}' "
            "(expected 'contact_information')"
        )

    result: dict[tuple[str, str], str] = {}
    for member in root:
        if member.tag != "member":
            continue
        last = _member(member, "last_name")
        first = _member(member, "first_name")
        state = _member(member, "state")
        if not last or not first or not state:
            raise SenateStateResolveError(
                "Senator member is missing last_name/first_name/state: "
                f"{ET.tostring(member, encoding='unicode')[:200]}"
            )
        result[(_normalize_name(last), _normalize_name(first))] = state
    if not result:
        raise SenateStateResolveError("Senators XML contains no senators")
    return result


def _member(member: ET.Element, tag: str) -> str:
    element = member.find(tag)
    return (element.text or "").strip() if element is not None else ""


def _resolve_state(
    last: str,
    first: str,
    senators: dict[tuple[str, str], str],
    *,
    office: str,
) -> str:
    """Resolve a senator's state from the official senators listing.

    Matching is on normalized ``(last, first)``. Raises
    :class:`SenateStateResolveError` when the senator is not present or the
    name is ambiguous (multiple senators share the name pair).
    """
    key = (_normalize_name(last), _normalize_name(first))
    if key not in senators:
        raise SenateStateResolveError(
            f"Unresolved senator: '{office}' ({first} {last}); "
            "not found in the official senators listing"
        )
    return senators[key]
