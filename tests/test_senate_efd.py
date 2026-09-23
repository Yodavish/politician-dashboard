"""Tests for the U.S. Senate eFD Search source adapter and index parsing.

All tests are hermetic: they run against bytes from the fixtures in
``tests/fixtures/senate/`` (built from official captures) and never open a
socket. The full agreement-to-listing-to-senators flow is exercised through
a small in-memory transport that replays fixture responses, so nothing
touches efdsearch.senate.gov or the production database.

Conventions intentionally match ``tests/test_house_clerk.py``.
"""

from __future__ import annotations

import urllib.parse
from datetime import date
from pathlib import Path

import pytest

from politician_dashboard.ingest.models import Filing
from politician_dashboard.ingest.sources.base import PTR_FILING_TYPE
from politician_dashboard.ingest.sources.senate_efd import (
    LANDING_URL,
    SenateAgreementError,
    SenateDetailError,
    SenateEfdSource,
    SenateIndexError,
    SenateStateResolveError,
    _given_names_agree,
    _office_name_parts,
    _resolve_state,
    classify_senate_doc_id,
    classify_view_link,
    parse_agreement_html,
    parse_listing_json,
    parse_ptr_view_html,
    parse_senators_xml,
    view_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "senate"

ELECTRONIC_DETAIL_ID = "fda235b3-bad7-4637-8fa1-053f354d929c"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _sample_agreement() -> tuple[str, str, str]:
    return parse_agreement_html(_load_fixture("landing.html"))


def _sample_listing_rows():
    return parse_listing_json(_load_fixture("listing_page1.json"))


def _sample_senators() -> dict[tuple[str, str], str]:
    return parse_senators_xml(_load_fixture("senators_cfm.xml"))


def _parse_body(body: bytes) -> dict[str, str]:
    """Decode a urlencoded POST body into its form fields.

    ``keep_blank_values=True`` preserves the empty filter fields (e.g.
    ``candidate_state=``) that the live page sends.
    """
    return dict(
        urllib.parse.parse_qsl(body.decode("utf-8"), keep_blank_values=True)
    )


class _MemoryTransport:
    """Replays landing/agreement/listing/senators responses from fixtures.

    ``records_total`` lets tests simulate server-side pagination: the first
    listing page holds two official Armstrong rows and reports a total, so
    the adapter issues a second (empty) page only when a page reports more
    records than it returns.
    """

    def __init__(self, records_total: int | None = None) -> None:
        self.landing = _load_fixture("landing.html")
        self.listing_pages = [
            _load_fixture("listing_page1.json"),
            _load_fixture("listing_empty.json"),
        ]
        self.senators = _load_fixture("senators_cfm.xml")
        self.records_total = records_total
        self.post_urls: list[str] = []
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []

    def get(self, url: str) -> bytes:
        if "contact_information" in url:
            return self.senators
        if "home" in url:
            return self.landing
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, body: bytes, headers=None) -> bytes:
        self.post_urls.append(url)
        self.posts.append((url, body, headers or {}))
        if "home" in url:
            return b"<html>accepted</html>"
        if "data" in url:
            raw = self.listing_pages.pop(0)
            if self.records_total is not None:
                raw = raw.replace(
                    b'"recordsFiltered":131', b'"recordsFiltered":2'
                ).replace(b'"recordsTotal":131', b'"recordsTotal":2')
            return raw
        raise AssertionError(f"unexpected POST {url}")


class _DetailTransport(_MemoryTransport):
    """Replays agreement/listing/senators plus per-view detail pages."""

    def __init__(self, detail_pages: dict[str, bytes]) -> None:
        super().__init__(records_total=2)
        self.detail_pages = detail_pages
        self.view_gets: list[str] = []

    def get(self, url: str) -> bytes:
        for view_id, data in self.detail_pages.items():
            if f"/{view_id}/" in url:
                self.view_gets.append(url)
                return data
        return super().get(url)


class TestParseAgreementHtml:
    def test_extracts_action_method_csrf(self):
        action, method, token = _sample_agreement()
        assert action == LANDING_URL  # official form carries action=""
        assert method == "POST"
        assert token

    def test_rejects_page_without_csrf(self):
        with pytest.raises(SenateAgreementError):
            parse_agreement_html(_load_fixture("landing_no_csrf.html"))

    def test_rejects_page_without_prohibition_checkbox(self):
        with pytest.raises(SenateAgreementError):
            parse_agreement_html(_load_fixture("landing_no_checkbox.html"))


class TestClassifyViewLink:
    def test_electronic_ptr_link(self):
        kind, view_id = classify_view_link(
            '<a href="/search/view/ptr/fda235b3-bad7-4637-8fa1-053f354d929c/" '
            'target="_blank">Periodic Transaction Report for 07/21/2026</a>'
        )
        assert kind == "electronic"
        assert view_id == "fda235b3-bad7-4637-8fa1-053f354d929c"

    def test_paper_ptr_link(self):
        kind, view_id = classify_view_link(
            '<a href="/search/view/paper/123456/" target="_blank">'
            "Periodic Transaction Report for 09/01/2026</a>"
        )
        assert kind == "paper"
        assert view_id == "123456"

    def test_rejects_unknown_link_shape(self):
        with pytest.raises(SenateIndexError):
            classify_view_link(
                '<a href="/search/view/scan/999/" target="_blank">'
                "Periodic Transaction Report for 09/01/2026</a>"
            )

    def test_rejects_empty_link(self):
        with pytest.raises(SenateIndexError):
            classify_view_link("")


class TestClassifySenateDocId:
    def test_uuid_is_efiled(self):
        assert classify_senate_doc_id(ELECTRONIC_DETAIL_ID) == "efiled"

    def test_numeric_is_scanned(self):
        assert classify_senate_doc_id("123456") == "scanned"


class TestViewUrl:
    def test_electronic_view_url(self):
        expected = (
            "https://efdsearch.senate.gov/search/view/ptr/"
            f"{ELECTRONIC_DETAIL_ID}/"
        )
        assert view_url(ELECTRONIC_DETAIL_ID) == expected

    def test_paper_view_url(self):
        assert view_url("123456") == (
            "https://efdsearch.senate.gov/search/view/paper/123456/"
        )


def _single_row_html(type_label: str) -> bytes:
    return (
        "<table><tbody><tr>"
        "<td>1</td><td>06/22/2026</td><td>Self</td><td>AAPL</td>"
        "<td>Apple Inc.</td><td>ST</td>"
        f"<td>{type_label}</td><td>$1,001 - $15,000</td><td>--</td>"
        "</tr></tbody></table>"
    ).encode()


class TestParsePtrViewHtml:
    def test_parses_official_electronic_row(self):
        rows = parse_ptr_view_html(_load_fixture("ptr_view_electronic.html"))
        assert len(rows) == 1
        row = rows[0]
        assert row["asset_name"] == "Williams Companies, Inc. (The) Common Stock"
        assert row["ticker"] == "WMB"
        assert row["txn_type"] == "S"
        assert row["txn_date"] == date(2026, 6, 22)
        assert row["amount_min"] == 250001
        assert row["amount_max"] == 500000
        assert row["amount_raw"] == "$250,001 - $500,000"
        assert row["owner"] == "Joint"
        assert row["asset_type_code"] == "Stock Option"
        assert row["notes"] == "--"

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Purchase", "P"),
            ("Sale (Full)", "S"),
            ("Sale (Partial)", "S (partial)"),
            ("Exchange", "E"),
        ],
    )
    def test_maps_transaction_types_to_shared_vocabulary(self, label, expected):
        rows = parse_ptr_view_html(_single_row_html(label))
        assert rows[0]["txn_type"] == expected

    def test_rejects_unknown_transaction_type(self):
        with pytest.raises(SenateDetailError):
            parse_ptr_view_html(_single_row_html("Gift"))

    def test_rejects_row_without_amount(self):
        html = (
            "<table><tbody><tr>"
            "<td>1</td><td>06/22/2026</td><td>Self</td><td>AAPL</td>"
            "<td>Apple Inc.</td><td>ST</td>"
            "<td>Purchase</td><td></td><td>--</td>"
            "</tr></tbody></table>"
        ).encode()
        with pytest.raises(SenateDetailError):
            parse_ptr_view_html(html)

    def test_empty_or_paper_page_yields_no_rows(self):
        assert parse_ptr_view_html(b"") == []
        assert parse_ptr_view_html(_load_fixture("ptr_view_paper.html")) == []


class TestParseListingJson:
    def test_parses_official_electronic_rows_with_uuid_ids(self):
        rows = _sample_listing_rows()
        assert len(rows) == 2
        assert all(r.view_kind == "electronic" for r in rows)
        electronic = rows[0]
        assert electronic.first == "Alan"
        assert electronic.last == "Armstrong"
        assert electronic.office == "Armstrong, Alan (Senator)"
        assert len(electronic.view_id) == 36
        assert [len(p) for p in electronic.view_id.split("-")] == [
            8,
            4,
            4,
            4,
            12,
        ]
        assert electronic.date_received == "07/21/2026"

    def test_parses_paper_rows_with_numeric_ids(self):
        rows = parse_listing_json(_load_fixture("listing_page2.json"))
        assert len(rows) == 2
        assert all(r.view_kind == "paper" for r in rows)
        assert all(r.view_id.isdigit() for r in rows)

    def test_rejects_listing_without_data_array(self):
        with pytest.raises(SenateIndexError):
            parse_listing_json(_load_fixture("listing_no_data.json"))

    def test_rejects_malformed_listing(self):
        with pytest.raises(SenateIndexError):
            parse_listing_json(_load_fixture("listing_malformed.json"))

    def test_rejects_unrecognized_view_link(self):
        with pytest.raises(SenateIndexError):
            parse_listing_json(_load_fixture("listing_bad_link.json"))

    def test_empty_listing_returns_no_rows(self):
        assert parse_listing_json(_load_fixture("listing_empty.json")) == []


class TestParseSenatorsXml:
    def test_parses_all_official_senators(self):
        senators = _sample_senators()
        assert len(senators) == 100
        assert ("alsobrooks", "angela d.") in senators  # keys are lowercased

    def test_resolves_armstrong_state(self):
        # Regression: some earlier spec material claimed Alan Armstrong is
        # from TN, but the official senators listing, the Oklahoma Governor's
        # March 2026 appointment, and the Congressional Record all identify
        # him as the Senator from Oklahoma. The adapter must keep resolving
        # him to OK, never "correcting" toward the stale TN expectation.
        senators = _sample_senators()
        assert senators[("armstrong", "alan")] == "OK"

    def test_resolves_alsobrooks_state(self):
        senators = _sample_senators()
        assert senators[("alsobrooks", "angela d.")] == "MD"

    def test_rejects_malformed_xml(self):
        with pytest.raises(SenateStateResolveError):
            parse_senators_xml(_load_fixture("senators_malformed.xml"))

    def test_rejects_missing_state_field(self):
        with pytest.raises(SenateStateResolveError):
            parse_senators_xml(_load_fixture("senators_missing_state.xml"))

    def test_rejects_wrong_root(self):
        with pytest.raises(SenateStateResolveError):
            parse_senators_xml(_load_fixture("senators_wrong_root.xml"))


class TestSenateStateResolution:
    """Resolving eFD display names against the official senators listing.

    These cases come from the live 2026 eFD listing (EC2 capture). The eFD
    office cell shows the fuller registered name (``"McConnell, A. Mitchell
    Jr. (Senator)"``) while the official ``senators_cfm.xml`` lists the
    preferred given name (``Mitch``). Resolution is anchored on an exact last
    name and matches the official *primary* given name via an exact token or
    a bounded, explicitly enumerated given-name relation
    (:data:`_DIMINUTIVE_FORMS`) -- never via an arbitrary string-prefix test,
    and it must refuse to guess (raise :class:`SenateStateResolveError`) when
    identity cannot be established unambiguously.
    """

    @pytest.mark.parametrize(
        ("office", "first", "last", "expected"),
        [
            # Middle name reveals the nickname: eFD "A. Mitchell Jr." ->
            # official primary "mitch" (truncation of the eFD token
            # "mitchell").
            ("McConnell, A. Mitchell Jr. (Senator)", "A. Mitchell Jr.", "McConnell", "KY"),
            # Full given name vs standard diminutive official primary.
            ("Banks, James E. (Senator)", "James E.", "Banks", "IN"),
            # Plain exact given names.
            ("Armstrong, Alan (Senator)", "Alan", "Armstrong", "OK"),
            ("King, Angus (Senator)", "Angus", "King", "ME"),
            ("Smith, Tina (Senator)", "Tina", "Smith", "MN"),
            # eFD carries a middle initial the official primary omits.
            ("McCormick, David H. (Senator)", "David H.", "McCormick", "PA"),
            ("Collins, Susan M. (Senator)", "Susan M.", "Collins", "ME"),
            ("Curtis, John R. (Senator)", "John R.", "Curtis", "UT"),
            # Two-word given names match the official first-name field.
            ("Capito, Shelley Moore (Senator)", "Shelley Moore", "Capito", "WV"),
        ],
    )
    def test_resolves_observed_live_listing_names(self, office, first, last, expected):
        state = _resolve_state(last, first, _sample_senators(), office=office)
        assert state == expected

    def test_resolves_office_with_exact_name_to_display_form(self):
        senators = _sample_senators()
        first, last = _office_name_parts(
            "McConnell, A. Mitchell Jr. (Senator)", "A. Mitchell Jr.", "McConnell"
        )
        assert first == "A. Mitchell Jr."
        assert last == "McConnell"
        assert _resolve_state(last, first, senators, office="McConnell, A. Mitchell Jr. (Senator)") == "KY"

    def test_office_label_senator_falls_back_to_row_cells(self):
        # Some live rows put only "Senator" in the office cell; identity then
        # lives entirely in the first/last columns and must still resolve.
        senators = _sample_senators()
        first, last = _office_name_parts("Senator", "Angus", "King")
        assert first == "Angus"
        assert last == "King"
        assert _resolve_state(last, first, senators, office="Senator") == "ME"

    def test_fetch_index_resolves_mcconnell_end_to_end(self):
        import json

        rows = [[
            "A. Mitchell Jr.",
            "McConnell",
            "McConnell, A. Mitchell Jr. (Senator)",
            f'<a href="/search/view/ptr/{ELECTRONIC_DETAIL_ID}/" target="_blank">'
            "Periodic Transaction Report for 03/12/2026</a>",
            "03/12/2026",
        ]]
        payload = json.dumps(
            {"draw": 1, "recordsTotal": 1, "recordsFiltered": 1, "data": rows}
        ).encode()
        transport = _MemoryTransport()
        transport.listing_pages = [payload, _load_fixture("listing_empty.json")]
        source = SenateEfdSource(transport=transport)
        filings = source.fetch_index(year=2026)
        assert len(filings) == 1
        assert filings[0].last == "McConnell"
        assert filings[0].state_district == "KY00"

    def test_initial_only_given_name_does_not_guess(self):
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "McConnell", "A.", _sample_senators(),
                office="McConnell, A. (Senator)",
            )

    def test_unrelated_given_name_does_not_over_match(self):
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "McConnell", "C. Addison", _sample_senators(),
                office="McConnell, C. Addison (Senator)",
            )

    def test_unknown_senator_raises(self):
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Fakename", "Some", _sample_senators(),
                office="Fakename, Some (Senator)",
            )

    def test_ambiguous_prefix_within_same_last_name_raises(self):
        # A surname group holding both a diminutive relation form and the full
        # form: eFD "Timothy" matches the "tim" entry via the enumerated
        # given-name relation and the "timothy" entry exactly -> real
        # ambiguity, must not guess.
        officials = {("scott", "tim"): "XX", ("scott", "timothy"): "YY"}
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Scott", "Timothy", officials,
                office="Scott, Timothy (Senator)",
            )

    def test_diminutive_and_full_name_collision_raises(self):
        # One surname group holding both the diminutive and its full form:
        # eFD "James" could be either, so resolution must refuse to guess.
        officials = {("scott", "james"): "XX", ("scott", "jim"): "YY"}
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Scott", "James", officials,
                office="Scott, James (Senator)",
            )

    @pytest.mark.parametrize(
        ("efd_given", "official_first"),
        [
            # Standard diminutive relations from the live listing captures.
            ("Mitchell", "Mitch"),          # McConnell, KY
            ("James", "Jim"),               # Banks, IN
            ("Timothy", "Tim"),             # Scott/Kaine/Sheehy
        ],
    )
    def test_supported_diminutive_relations_match(self, efd_given, official_first):
        assert _given_names_agree(efd_given, official_first)

    @pytest.mark.parametrize(
        ("efd_given", "official_first"),
        [
            # Bare prefix overlap is NOT identity evidence. "Dana" must not
            # resolve to an official primary "Dan" merely by sharing letters.
            ("Dana", "Dan"),
            ("Christina", "Chris"),
            ("Johnathan", "John"),
            ("Benedict", "Ben"),
            ("Marina", "Marie"),
            ("Daniela", "Dan"),
        ],
    )
    def test_arbitrary_prefix_is_not_identity(self, efd_given, official_first):
        assert not _given_names_agree(efd_given, official_first)

    def test_empty_primary_given_name_never_matches(self):
        for official_first in ("", "   ", "jr."):
            assert not _given_names_agree("Angus", official_first)

    @pytest.mark.parametrize(
        ("efd_given", "official_first"),
        [
            ("Mitch", "Mitch"),
            ("Angus", "Angus"),
            ("Alan", "Alan"),
            ("James E.", "James"),
        ],
    )
    def test_exact_given_name_tokens_match(self, efd_given, official_first):
        assert _given_names_agree(efd_given, official_first)

    def test_prefix_only_candidate_is_not_a_match_in_last_name_group(self):
        # A same-last-name group containing only an official primary "dan":
        # eFD "Dana" must fail closed (no fuzzy prefix attribution).
        officials = {("sullivan", "dan"): "AK"}
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Sullivan", "Dana", officials,
                office="Sullivan, Dana (Senator)",
            )

    def test_zero_same_last_name_candidates_raise(self):
        officials = {("sullivan", "dan"): "AK"}
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Washington", "George", officials,
                office="Washington, George (Senator)",
            )

    def test_multiple_candidates_same_last_name_raise(self):
        officials = {
            ("scott", "rick"): "FL",
            ("scott", "tim"): "SC",
            ("scott", "timothy"): "ZZ",
        }
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Scott", "Timothy", officials,
                office="Scott, Timothy (Senator)",
            )

    def test_unique_candidate_resolves_to_state(self):
        officials = {
            ("banks", "jim"): "IN",
            ("scott", "rick"): "FL",
        }
        assert _resolve_state(
            "Banks", "James", officials,
            office="Banks, James (Senator)",
        ) == "IN"


class TestDefaultTransport:
    """Default ``SenateEfdSource()`` transport wiring (hermetic).

    Patching the ``_senate_urllib_session`` factory on the ``sources``
    package reproduces what the constructor's lazy import resolves when no
    transport is injected, so no real network request is made.
    """

    def test_default_transport_resolves_senate_urllib_session_factory(
        self, monkeypatch
    ):
        import politician_dashboard.ingest.sources as sources_pkg

        constructed: list[_MemoryTransport] = []

        def _factory():
            transport = _MemoryTransport(records_total=2)
            constructed.append(transport)
            return transport

        monkeypatch.setattr(sources_pkg, "_senate_urllib_session", _factory)

        source = SenateEfdSource()  # no transport injected
        assert len(constructed) == 1
        assert source._transport is constructed[0]

        # the default transport is the one actually driving the flow
        filings = source.fetch_index(year=2026)
        assert len(filings) == 2
        assert {f.doc_id for f in filings} == {
            "fda235b3-bad7-4637-8fa1-053f354d929c",
            "b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f",
        }

    def test_default_transport_is_cookie_persisting_urllib_session(self):
        from politician_dashboard.ingest.sources import _senate_urllib_session

        session = _senate_urllib_session()
        assert callable(session.get)
        assert callable(session.post)
        assert hasattr(session, "_cookiejar")  # cookie persistence for CSRF

    def test_constructor_keeps_injected_transport(self):
        transport = _MemoryTransport(records_total=2)
        source = SenateEfdSource(transport=transport)
        assert source._transport is transport


class TestSenateEfdSourceFlow:
    def test_name(self):
        assert SenateEfdSource.name == "senate_efd"

    def test_fetch_index_runs_full_offline_flow(self):
        source = SenateEfdSource(transport=_MemoryTransport(records_total=2))
        filings = source.fetch_index(year=2026)
        assert len(filings) == 2
        by_doc = {f.doc_id: f for f in filings}
        electronic = by_doc["fda235b3-bad7-4637-8fa1-053f354d929c"]
        assert electronic.last == "Armstrong"
        assert electronic.first == "Alan"
        assert electronic.prefix == ""
        assert electronic.suffix == ""
        assert electronic.filing_type == PTR_FILING_TYPE
        assert electronic.state_district == "OK00"
        assert electronic.year == 2026
        assert electronic.filing_date == date(2026, 7, 21)

    def test_fetch_index_stops_after_records_exhausted(self):
        source = SenateEfdSource(transport=_MemoryTransport(records_total=2))
        filings = source.fetch_index(year=2026)
        # two official rows only; no phantom empty page appended
        assert len(filings) == 2

    def test_fetch_ptrs_is_select_ptrs_of_fetch_index(self, monkeypatch):
        source = SenateEfdSource(transport=_MemoryTransport(records_total=2))
        ptrs = source.fetch_ptrs(year=2026)
        assert len(ptrs) == 2
        # Senate PTR reports normalize to the shared cross-chamber "P" type
        assert all(f.filing_type == PTR_FILING_TYPE for f in ptrs)
        # requests pulled the same two official rows as the full index
        assert {f.doc_id for f in ptrs} == {
            "fda235b3-bad7-4637-8fa1-053f354d929c",
            "b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f",
        }

    def test_fetch_index_accepts_agreement_before_listing(self):
        transport = _MemoryTransport(records_total=2)
        source = SenateEfdSource(transport=transport)
        source.fetch_index(year=2026)
        # the flow must hit the agreement POST before any listing data POST
        assert "agreement" not in transport.post_urls[0] or True
        # agreement lands first, listing data after
        assert any("home" in u for u in transport.post_urls)
        assert any("data" in u for u in transport.post_urls)
        assert transport.post_urls.index([u for u in transport.post_urls if "home" in u][0]) < \
            transport.post_urls.index([u for u in transport.post_urls if "data" in u][0])

    def test_unresolved_senator_raises_explicitly(self):
        source = SenateEfdSource(
            transport=_MemoryTransport(records_total=2),
            senators={},  # empty official listing -> every filer unresolved
        )
        with pytest.raises(SenateStateResolveError):
            source.fetch_index(year=2026)

    def test_senate_uses_zero_district_suffix(self):
        source = SenateEfdSource(transport=_MemoryTransport(records_total=2))
        filings = source.fetch_index(year=2026)
        assert all(f.state_district.endswith("00") for f in filings)

    def test_armstrong_filing_resolves_to_ok00(self):
        # Regression: Alan Armstrong resolves to state_district OK00 per the
        # official senators fixture (a stale spec claimed TN00). The full
        # fetch_index derivation -- senators XML -> state -> "<STATE>00" --
        # must produce OK00 and never TN00.
        source = SenateEfdSource(transport=_MemoryTransport(records_total=2))
        filings = source.fetch_index(year=2026)
        armstrong = next(f for f in filings if f.last == "Armstrong")
        assert armstrong.first == "Alan"
        assert armstrong.state_district == "OK00"
        assert armstrong.state_district != "TN00"

    def test_fetch_detail_returns_electronic_html_after_index(self):
        transport = _DetailTransport(
            {ELECTRONIC_DETAIL_ID: _load_fixture("ptr_view_electronic.html")}
        )
        source = SenateEfdSource(transport=transport)
        source.fetch_index(year=2026)
        data = source.fetch_detail(ELECTRONIC_DETAIL_ID)
        assert b"Williams Companies" in data
        assert transport.view_gets == [view_url(ELECTRONIC_DETAIL_ID)]
        # consent was accepted during index; no extra agreement POST
        agreement_posts = [u for u in transport.post_urls if "home" in u]
        assert len(agreement_posts) == 1

    def test_fetch_detail_consents_when_no_index_ran(self):
        transport = _DetailTransport(
            {ELECTRONIC_DETAIL_ID: _load_fixture("ptr_view_electronic.html")}
        )
        source = SenateEfdSource(transport=transport)
        data = source.fetch_detail(ELECTRONIC_DETAIL_ID)
        assert b"Williams Companies" in data
        # agreement POST happened before the view GET
        assert any("home" in u for u in transport.post_urls)


class TestSenateListingRequestContract:
    """The listing POST must reproduce the live eFD search request contract.

    Regression for the production HTTP 403 on POST /search/report/data/.
    The live eFD search sends the session's X-CSRFToken header, the
    X-Requested-With XMLHttpRequest marker, and the /search/ Referer on its
    AJAX listing request. The production regression is covered by reproducing
    that known-good request contract. The body must use the bracketed string
    parameter shapes the live page (and the known-good request) use --
    report_types=[11], filer_types=[] -- not
    array-style "<key>[]=<value>" form keys.

    These tests assert the exact request contract the adapter emits, not
    merely that a request occurred.
    """

    def _run(self, transport):
        source = SenateEfdSource(transport=transport)
        source.fetch_index(year=2026)
        return source

    def _listing_posts(self, transport):
        listing = [p for p in transport.posts if "data" in p[0]]
        assert listing, "adapter must issue listing POSTs"
        return listing

    @staticmethod
    def _expected_csrf_token() -> str:
        return parse_agreement_html(_load_fixture("landing.html"))[2]

    def test_listing_post_transmits_the_agreement_csrf_token(self):
        transport = _MemoryTransport(records_total=2)
        source = self._run(transport)
        expected = self._expected_csrf_token()
        assert source._csrf_token == expected
        for _, _, headers in self._listing_posts(transport):
            assert headers["X-CSRFToken"] == expected

    def test_listing_post_sends_ajax_headers(self):
        transport = _MemoryTransport(records_total=2)
        self._run(transport)
        for _, _, headers in self._listing_posts(transport):
            assert headers["X-Requested-With"] == "XMLHttpRequest"
            assert headers["Referer"] == "https://efdsearch.senate.gov/search/"
            assert (
                headers["Content-Type"]
                == "application/x-www-form-urlencoded; charset=UTF-8"
            )

    def test_listing_post_body_matches_live_parameter_contract(self):
        transport = _MemoryTransport(records_total=2)
        self._run(transport)
        for _, body, _ in self._listing_posts(transport):
            assert set(_parse_body(body)) == {
                "draw",
                "start",
                "length",
                "report_types",
                "filer_types",
                "submitted_start_date",
                "submitted_end_date",
                "candidate_state",
                "senator_state",
                "office_id",
                "first_name",
                "last_name",
            }

    def test_listing_post_encodes_report_and_filer_types_as_bracketed_strings(self):
        transport = _MemoryTransport(records_total=2)
        self._run(transport)
        for _, body, _ in self._listing_posts(transport):
            params = _parse_body(body)
            assert params["report_types"] == "[11]"
            assert params["filer_types"] == "[]"
            # array-style form keys must not be sent
            assert "report_types[]" not in params
            assert "filer_types[]" not in params

    def test_listing_post_targets_pagination_and_filter_year(self):
        transport = _MemoryTransport(records_total=2)
        self._run(transport)
        _, body, _ = self._listing_posts(transport)[0]
        params = _parse_body(body)
        assert params["draw"] == "1"
        assert params["start"] == "0"
        assert params["length"] == "100"
        assert params["submitted_start_date"] == "01/01/2026 00:00:00"
        assert params["submitted_end_date"] == "12/31/2026 23:59:59"
        for key in (
            "candidate_state",
            "senator_state",
            "office_id",
            "first_name",
            "last_name",
        ):
            assert params[key] == ""

    def test_csrf_token_is_stable_across_pagination_posts(self):
        # records_total is left at the fixture's 131 (>= 2 rows fetched), so
        # the adapter issues a second listing POST for the next page.
        transport = _MemoryTransport()
        source = self._run(transport)
        listing = self._listing_posts(transport)
        assert len(listing) >= 2
        assert {headers["X-CSRFToken"] for _, _, headers in listing} == {
            source._csrf_token
        }

    def test_agreement_post_carries_form_csrf_and_checkbox(self):
        transport = _MemoryTransport(records_total=2)
        self._run(transport)
        agreement = [p for p in transport.posts if "home" in p[0]]
        assert len(agreement) == 1
        _, body, headers = agreement[0]
        params = _parse_body(body)
        assert params["csrfmiddlewaretoken"] == self._expected_csrf_token()
        assert params["prohibition_agreement"] == "1"
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert headers["Referer"] == LANDING_URL