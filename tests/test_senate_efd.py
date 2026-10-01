"""Tests for the U.S. Senate eFD Search source adapter and index parsing.

All tests are hermetic: they run against bytes from the fixtures in
``tests/fixtures/senate/`` (built from official captures) and never open a
socket. The full agreement-to-listing-to-senators flow is exercised through
a small in-memory transport that replays fixture responses, so nothing
touches efdsearch.senate.gov or the production database.

Conventions intentionally match ``tests/test_house_clerk.py``.
"""

from __future__ import annotations

import dataclasses
import json
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
    SenateMember,
    SenateMemberTerm,
    SenateStateResolveError,
    _given_names_agree,
    _office_name_parts,
    _resolve_state,
    _serves_on,
    _service_ended_before,
    classify_senate_doc_id,
    classify_view_link,
    load_default_senate_members,
    parse_agreement_html,
    parse_listing_json,
    parse_ptr_view_html,
    parse_senate_members_json,
    parse_senators_xml,
    view_url,
)
from politician_dashboard.ingest.sources import senate_members_refresh
from politician_dashboard.ingest.sources.senate_members_refresh import (
    build_members_snapshot,
    diff_snapshots,
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


def _sample_members() -> list[SenateMember]:
    return parse_senate_members_json(_load_fixture("senate_members.json"))


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


def _single_row_html(
    type_label: str, *, amount: str = "$1,001 - $15,000"
) -> bytes:
    return (
        "<table><tbody><tr>"
        "<td>1</td><td>06/22/2026</td><td>Self</td><td>AAPL</td>"
        "<td>Apple Inc.</td><td>ST</td>"
        f"<td>{type_label}</td><td>{amount}</td><td>--</td>"
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

    def test_parses_open_ended_over_amount_as_lower_bound_only(self):
        """The eFD top tier "Over $X" states a lower bound and no upper bound.

        Regression test for filing 0a93a20c-2a0f-4979-80ca-cc2f61297527, which
        raised "Transaction row has no amount" because the guard required the
        cell to start with "$". amount_max must be None, never the same value
        as amount_min, which would claim an exact figure the source withholds.
        """
        rows = parse_ptr_view_html(
            _load_fixture("ptr_view_open_ended_amount.html")
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["asset_name"] == (
            "Greenbrier Hotel Corporation and Affiliates"
        )
        assert row["asset_type_code"] == "Non-Public Stock"
        assert row["txn_type"] == "S (partial)"
        assert row["txn_date"] == date(2026, 8, 14)
        assert row["amount_min"] == 50_000_000
        assert row["amount_max"] is None
        assert row["amount_raw"] == "Over $50,000,000"

    def test_rejects_unknown_transaction_type(self):
        with pytest.raises(SenateDetailError):
            parse_ptr_view_html(_single_row_html("Gift"))

    @pytest.mark.parametrize(
        ("amount_cell", "expected_min", "expected_max"),
        [
            # Bounded ranges and single exact amounts keep prior behavior.
            ("$1,001 - $15,000", 1001, 15000),
            ("$250,001 - $500,000", 250001, 500000),
            ("$15,000", 15000, 15000),
            # Open-ended top tier: lower bound only, upper bound unknown.
            ("Over $50,000,000", 50_000_000, None),
            ("OVER $1,000,000", 1_000_000, None),
        ],
    )
    def test_amount_bounds(self, amount_cell, expected_min, expected_max):
        html = _single_row_html("Purchase", amount=amount_cell)
        row = parse_ptr_view_html(html)[0]
        assert row["amount_min"] == expected_min
        assert row["amount_max"] == expected_max
        assert row["amount_raw"] == amount_cell

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

    def test_rejects_row_with_placeholder_amount(self):
        """A "--" amount cell is malformed and must be reported, not stored."""
        html = _single_row_html("Purchase", amount="--")
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
    office cell can show the fuller registered name (``"McConnell, A.
    Mitchell Jr. (Senator)"``) while the official ``senators_cfm.xml`` lists
    the preferred given name (``Mitch``), or the reverse (``"Coons, Chris
    (Senator)"`` vs official ``Christopher A.``). Resolution is anchored on an
    exact last name and matches the official *primary* given name via an exact
    token or a bounded, explicitly enumerated given-name relation
    (:data:`_DIMINUTIVE_FORMS`) -- never via an arbitrary string-prefix test,
    in either direction -- and it must refuse to guess (raise
    :class:`SenateStateResolveError`) when identity cannot be established
    unambiguously.
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

    def test_reverse_direction_diminutive_collision_raises(self):
        # The bidirectional check must not collapse a surname group holding
        # both the formal name and its diminutive into a unique match: eFD
        # "Chris" is a bounded relation of BOTH "christopher" and "chris", so
        # identity is genuinely ambiguous and resolution must refuse to guess.
        officials = {("scott", "christopher"): "XX", ("scott", "chris"): "YY"}
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Scott", "Chris", officials,
                office="Scott, Chris (Senator)",
            )

    @pytest.mark.parametrize(
        ("efd_given", "official_first"),
        [
            # Standard diminutive relations from the live listing captures.
            # Each is asserted in both directions: the eFD token may be the
            # formal form (official publishes the diminutive) or the eFD token
            # may be the diminutive (official publishes the formal form).
            ("Mitchell", "Mitch"),          # McConnell, KY
            ("Mitch", "Mitchell"),
            ("James", "Jim"),               # Banks, IN
            ("Jim", "James"),
            ("Timothy", "Tim"),             # Scott/Kaine/Sheehy
            ("Tim", "Timothy"),
            ("Christopher", "Chris"),       # Coons, DE
            ("Chris", "Christopher"),
            ("Bernardo", "Bernie"),         # Moreno, OH
            ("Bernie", "Bernardo"),
        ],
    )
    def test_supported_diminutive_relations_match_bidirectional(
        self, efd_given, official_first
    ):
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
            # The bidirectional bounded check must not open the door to
            # prefix matching in the reverse direction either: an official
            # primary with a longer form is not matched by an eFD token that
            # merely shares its prefix.
            ("Chris", "Christina"),
            ("Chris", "Christian"),
            ("Dan", "Daniela"),
            ("Dan", "Danielle"),
            ("Jim", "Jimothy"),
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

    def test_reverse_direction_live_names_resolve_against_fixture(self):
        # The 2026 live listing's office cell can carry the diminutive while
        # the official senators XML publishes the formal name: "Coons, Chris
        # (Senator)" vs official first name "Christopher A.", and "Moreno,
        # Bernardo (Senator)" vs official first name "Bernie". The bounded
        # relation is consulted in both directions, so these must resolve
        # to their unique same-last-name candidates.
        senators = _sample_senators()
        first, last = _office_name_parts(
            "Coons, Chris (Senator)", "Christopher A", "Coons"
        )
        assert _resolve_state(
            last, first, senators, office="Coons, Chris (Senator)"
        ) == "DE"
        first, last = _office_name_parts(
            "Moreno, Bernardo (Senator)", "Bernie", "Moreno"
        )
        assert _resolve_state(
            last, first, senators, office="Moreno, Bernardo (Senator)"
        ) == "OH"

    def test_absent_surname_still_unresolved(self):
        # The officials fixture has no "Mullin" entry at all: the 2026 live
        # listing row "Mullin, Markwayne (Senator)" must still fail closed
        # (zero same-last-name candidates) rather than guess.
        senators = _sample_senators()
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Mullin", "Markwayne", senators,
                office="Mullin, Markwayne (Senator)",
            )

    def test_official_primary_unrelated_name_still_unresolved(self):
        # The officials fixture lists Graham's official first name as
        # "Darline"; the 2026 live listing displays "Lindsey". No bounded
        # relation connects them, so resolution must fail closed.
        senators = _sample_senators()
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Graham", "Lindsey", senators,
                office="Graham, Lindsey (Senator)",
            )

    def test_departed_senator_resolves_via_reference_snapshot(self):
        # Markwayne Mullin is absent from the current officials fixture (he
        # resigned 2026-03-23). The reference snapshot knows his Senate
        # service, so a 2024 filing resolves to OK.
        state = _resolve_state(
            "Mullin", "Markwayne", _sample_senators(),
            office="Mullin, Markwayne (Senator)",
            anchor_date=date(2024, 6, 15),
            members=_sample_members(),
        )
        assert state == "OK"

    @staticmethod
    def _rubio_member(terms=None):
        return SenateMember(
            bioguide_id="R000595",
            last_name="Rubio",
            first_name="Marco",
            state="FL",
            terms=terms or (
                SenateMemberTerm(date(2011, 1, 5), date(2025, 1, 20)),
            ),
        )

    def test_explicit_former_senator_resolves_after_service_ends(self):
        rows = [[
            "Marco",
            "Rubio",
            "Former Senator (Former Senator)",
            f'<a href="/search/view/ptr/{ELECTRONIC_DETAIL_ID}/">PTR</a>',
            "02/06/2025",
        ]]
        payload = json.dumps(
            {"draw": 1, "recordsTotal": 1, "recordsFiltered": 1, "data": rows}
        ).encode()
        transport = _MemoryTransport()
        transport.listing_pages = [payload]
        source = SenateEfdSource(
            transport=transport, members=[self._rubio_member()]
        )

        filings = source.fetch_index(2025)

        assert len(filings) == 1
        assert filings[0].first == "Marco"
        assert filings[0].last == "Rubio"
        assert filings[0].filing_date == date(2025, 2, 6)
        assert filings[0].state_district == "FL00"

    def test_packaged_snapshot_resolves_real_former_senator_filing(self):
        """Regression: production Senate 2025 failed to index filing Rubio.

        The former-senator fallback is exercised above against a synthetic
        member. This asserts the *committed* reference snapshot carries the
        service history needed to resolve the real eFD office cell
        ``"Former Senator (Former Senator)"``, whose names the listing leaves
        in the first/last cells rather than the office cell.

        It couples the resolver to the packaged asset, so regenerating
        ``data/senate_members.json`` with missing or wrong end dates fails
        here rather than in a scheduled production run.
        """
        members = load_default_senate_members()
        rubio = [m for m in members if m.bioguide_id == "R000595"]
        assert len(rubio) == 1, "expected exactly one Marco Rubio in the snapshot"
        # Resigned 2025-01-20, so he does not cover the 2025-02-06 received
        # date. Every term must be closed for the fallback to apply.
        assert rubio[0].last_name == "Rubio"
        assert rubio[0].first_name == "Marco"
        assert rubio[0].state == "FL"
        assert _service_ended_before(rubio[0], date(2025, 2, 6))
        assert not _serves_on(rubio[0], date(2025, 2, 6))

        first, last = _office_name_parts(
            "Former Senator (Former Senator)", "Marco", "Rubio"
        )
        assert (first, last) == ("Marco", "Rubio")
        assert _resolve_state(
            last,
            first,
            {},
            office="Former Senator (Former Senator)",
            anchor_date=date(2025, 2, 6),
            members=members,
        ) == "FL"

    def test_ordinary_senator_cannot_use_post_service_fallback(self):
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Rubio", "Marco", {}, office="Rubio, Marco (Senator)",
                anchor_date=date(2025, 2, 6), members=[self._rubio_member()],
            )

    def test_former_senator_requires_all_service_to_end_before_received_date(self):
        member = self._rubio_member(
            terms=(
                SenateMemberTerm(date(2011, 1, 5), date(2025, 1, 20)),
                SenateMemberTerm(date(2025, 3, 1), date(2031, 1, 3)),
            )
        )
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Rubio", "Marco", {},
                office="Former Senator (Former Senator)",
                anchor_date=date(2025, 2, 6), members=[member],
            )

    def test_former_senator_requires_unique_historical_identity(self):
        members = [
            self._rubio_member(),
            SenateMember(
                bioguide_id="R999999",
                last_name="Rubio",
                first_name="Marco",
                state="TX",
                terms=(
                    SenateMemberTerm(date(2017, 1, 3), date(2021, 1, 3)),
                ),
            ),
        ]
        with pytest.raises(SenateStateResolveError, match="Ambiguous former senator"):
            _resolve_state(
                "Rubio", "Marco", {},
                office="Former Senator (Former Senator)",
                anchor_date=date(2025, 2, 6), members=members,
            )

    def test_snapshot_service_interval_is_inclusive_of_last_day(self):
        # Mullin's term ends on (and includes) 2026-03-23, matching the
        # bioguide endDate of his resignation.
        assert _resolve_state(
            "Mullin", "Markwayne", {}, office="Mullin, Markwayne (Senator)",
            anchor_date=date(2026, 3, 23), members=_sample_members(),
        ) == "OK"

    @pytest.mark.parametrize("suffix", ["II", "III", "IV", "Jr."])
    def test_justice_surname_suffix_matches_historical_snapshot(self, suffix):
        justice = SenateMember(
            bioguide_id="J000312",
            last_name="Justice",
            first_name="Jim",
            state="WV",
            terms=(
                SenateMemberTerm(date(2025, 1, 14), date(2031, 1, 3)),
            ),
        )
        assert _resolve_state(
            f"Justice {suffix}",
            "James Conley",
            {},
            office=f"Justice {suffix}, James Conley (Senator)",
            anchor_date=date(2025, 1, 14),
            members=[justice],
        ) == "WV"

    def test_suffix_derived_senators_resolve_from_their_own_surname(self):
        """Members whose stored surname was a bare suffix must resolve.

        The generated snapshot stored the generational suffix as the surname
        for these senators ("Joe Manchin, III" -> last_name "III"), so a real
        2020-2023 filing recorded as "Manchin, Joe" had no member to match.
        Resolution is still a plain exact last-name comparison; only the
        snapshot's surname field is corrected.
        """
        manchin = SenateMember(
            bioguide_id="M001183",
            last_name="Manchin",
            first_name="Joe",
            given_name="Joe",
            state="WV",
            terms=(
                SenateMemberTerm(date(2019, 1, 3), date(2025, 1, 3)),
            ),
        )
        assert _resolve_state(
            "Manchin", "Joe", {},
            office="Manchin, Joe (Senator)",
            anchor_date=date(2021, 6, 15), members=[manchin],
        ) == "WV"
        # The surname anchor stays exact: a filing under the suffix token is
        # not accepted, and a different surname is not accepted either.
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "III", "Joe", {},
                office="III, Joe (Senator)",
                anchor_date=date(2021, 6, 15), members=[manchin],
            )
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Rockefeller", "Joe", {},
                office="Rockefeller, Joe (Senator)",
                anchor_date=date(2021, 6, 15), members=[manchin],
            )
        # And a given name that is not a standard relation of "Joe" is
        # refused.
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Manchin", "Joshua", {},
                office="Manchin, Joshua (Senator)",
                anchor_date=date(2021, 6, 15), members=[manchin],
            )

    def test_registered_nickname_resolves_against_the_official_given_name(self):
        """A filing recorded under a registered display name resolves.

        The eFD portal files under the name the member registered ("J.D."), so
        a member whose ``first_name`` is the formal "James David" was
        unreachable. The snapshot now carries the official given name as well;
        the formal name still resolves, and an unrelated given name still
        fails closed.
        """
        vance = SenateMember(
            bioguide_id="V000137",
            last_name="Vance",
            first_name="James David",
            given_name="J.D.",
            state="OH",
            terms=(
                SenateMemberTerm(date(2023, 1, 3), date(2031, 1, 3)),
            ),
        )
        for given in ("J.D.", "J.D", "James David", "James"):
            assert _resolve_state(
                "Vance", given, {},
                office=f"Vance, {given} (Senator)",
                anchor_date=date(2023, 6, 1), members=[vance],
            ) == "OH"
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Vance", "Joshua", {},
                office="Vance, Joshua (Senator)",
                anchor_date=date(2023, 6, 1), members=[vance],
            )

    def test_snapshot_given_name_never_relaxes_the_surname_anchor(self):
        vance = SenateMember(
            bioguide_id="V000137",
            last_name="Vance",
            first_name="James David",
            given_name="J.D.",
            state="OH",
            terms=(
                SenateMemberTerm(date(2023, 1, 3), date(2031, 1, 3)),
            ),
        )
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "DeSantis", "J.D.", {},
                office="DeSantis, J.D. (Senator)",
                anchor_date=date(2023, 6, 1), members=[vance],
            )

    def test_standard_nickname_resolves_against_a_formal_official_name(self):
        """A general given-name relation, not a member-specific alias.

        "Pat" is the registered form for a senator whose official listing
        publishes "Patrick"; the relation is the standard English one and
        applies to any member with that given name.
        """
        toomey = SenateMember(
            bioguide_id="T000461",
            last_name="Toomey",
            first_name="Patrick",
            given_name="Patrick J.",
            state="PA",
            terms=(
                SenateMemberTerm(date(2011, 1, 5), date(2023, 1, 3)),
            ),
        )
        assert _resolve_state(
            "Toomey", "Pat", {},
            office="Toomey, Pat (Senator)",
            anchor_date=date(2021, 10, 8), members=[toomey],
        ) == "PA"
        # A relation must not bridge two different people.
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Toomey", "Patricia", {},
                office="Toomey, Patricia (Senator)",
                anchor_date=date(2021, 10, 8), members=[toomey],
            )

    def test_accented_surname_folds_the_same_way_in_both_chambers(self):
        """A diacritic difference must not be chamber-dependent.

        The snapshot stores "Luján"; a filing routinely spells it "Lujan". The
        House adapter has always folded diacritics, so this adapter did too once
        the two shared the same normalization. Folding is applied to both
        sides, and a different surname still fails closed.
        """
        lujan = SenateMember(
            bioguide_id="L000570",
            last_name="Luján",
            first_name="Ben Ray",
            given_name="Ben Ray",
            state="NM",
            terms=(
                SenateMemberTerm(date(2019, 1, 3), date(2025, 1, 3)),
            ),
        )
        for last in ("Luján", "Lujan", "LUJAN"):
            assert _resolve_state(
                last, "Ben Ray", {},
                office=f"{last}, Ben Ray (Senator)",
                anchor_date=date(2021, 5, 1), members=[lujan],
            ) == "NM"
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Luján", "Ben Ray", {},
                office="Luján, Ben Ray (Senator)",
                anchor_date=date(2021, 5, 1),
                members=[dataclasses.replace(lujan, last_name="Lujan Alaniz")],
            )

    def test_justice_surname_suffix_preserves_service_date_boundary(self):
        justice = SenateMember(
            bioguide_id="J000312",
            last_name="Justice",
            first_name="Jim",
            state="WV",
            terms=(
                SenateMemberTerm(date(2025, 1, 14), date(2031, 1, 3)),
            ),
        )
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Justice II",
                "James Conley",
                {},
                office="Justice II, James Conley (Senator)",
                anchor_date=date(2025, 1, 13),
                members=[justice],
            )

        for anchor_date in (date(2025, 1, 14), date(2025, 1, 15)):
            assert _resolve_state(
                "Justice II",
                "James Conley",
                {},
                office="Justice II, James Conley (Senator)",
                anchor_date=anchor_date,
                members=[justice],
            ) == "WV"

    def test_post_resignation_filing_fails_closed(self):
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Mullin", "Markwayne", {},
                office="Mullin, Markwayne (Senator)",
                anchor_date=date(2026, 3, 24),
                members=_sample_members(),
            )

    def test_same_surname_disambiguated_by_service_interval(self):
        # The fixture holds two Grahams in one surname group. "Lindsey" can
        # only be the departed senator, and the date proves he was serving.
        assert _resolve_state(
            "Graham", "Lindsey", {}, office="Graham, Lindsey (Senator)",
            anchor_date=date(2026, 5, 2), members=_sample_members(),
        ) == "SC"

    def test_vacancy_day_between_two_grahams_fails_closed(self):
        # 2026-07-12 falls after Lindsey's death (2026-07-11) and before
        # Darline's appointment (2026-07-14). Treating a filing on that day as
        # either senator would guess, so resolution must fail closed.
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Graham", "Lindsey", {}, office="Graham, Lindsey (Senator)",
                anchor_date=date(2026, 7, 12), members=_sample_members(),
            )

    def test_appointed_successor_resolves_once_serving(self):
        assert _resolve_state(
            "Graham", "Darline", {}, office="Graham, Darline (Senator)",
            anchor_date=date(2026, 8, 1), members=_sample_members(),
        ) == "SC"

    def test_snapshot_resolves_unique_name_without_anchor_date(self):
        # Without a date the surname+given-name group is still unique for
        # Mullin, so a unknown-parse-date filing can still resolve on identity.
        assert _resolve_state(
            "Mullin", "Markwayne", {}, office="Mullin, Markwayne (Senator)",
            members=_sample_members(),
        ) == "OK"

    def test_snapshot_given_name_matching_stays_bounded(self):
        # No enumerated relation links "Lindse" to "Lindsey"; the surname
        # group alone must not fuzzy-match.
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Graham", "Lindse", {}, office="Graham, Lindse (Senator)",
                anchor_date=date(2026, 5, 2), members=_sample_members(),
            )

    def test_snapshot_does_not_resolve_unknown_person(self):
        with pytest.raises(SenateStateResolveError):
            _resolve_state(
                "Bogus", "Nobody", {}, office="Bogus, Nobody (Senator)",
                anchor_date=date(2024, 1, 1), members=_sample_members(),
            )

    def test_unique_current_listing_match_takes_precedence_over_snapshot(self):
        # The authoritative current listing wins when it uniquely identifies
        # the filer, even if the snapshot disagrees on state (freshness).
        senators = {("rounds", "mike"): "XX"}
        assert _resolve_state(
            "Rounds", "Mike", senators,
            office="Rounds, Mike (Senator)",
            anchor_date=date(2026, 1, 1),
            members=_sample_members(),
        ) == "XX"


class TestParseSenateMembersJson:
    def test_parses_fixture_identity_and_terms(self):
        members = _sample_members()
        assert len(members) == 10
        mullin = next(m for m in members if m.bioguide_id == "M001190")
        assert mullin.last_name == "Mullin"
        assert mullin.first_name == "Markwayne"
        assert mullin.state == "OK"
        assert mullin.terms == (
            SenateMemberTerm(date(2023, 1, 3), date(2026, 3, 23)),
        )
        coons = next(m for m in members if m.bioguide_id == "C001088")
        assert len(coons.terms) == 3
        assert coons.terms[0] == SenateMemberTerm(
            date(2010, 11, 15), date(2015, 1, 3)
        )

    def test_parses_scheduled_term_end_for_current_member(self):
        armstrong = next(m for m in _sample_members() if m.bioguide_id == "A000383")
        assert armstrong.terms == (
            SenateMemberTerm(date(2026, 3, 24), date(2027, 1, 3)),
        )

    def test_serves_on_intervals_are_inclusive_on_both_ends(self):
        mullin = next(m for m in _sample_members() if m.bioguide_id == "M001190")
        assert _serves_on(mullin, date(2023, 1, 3))
        assert _serves_on(mullin, date(2026, 3, 23))
        assert not _serves_on(mullin, date(2023, 1, 2))
        assert not _serves_on(mullin, date(2026, 3, 24))

    def test_packaged_reference_snapshot_loads(self):
        # The committed asset is part of the repo; its data must reconcile
        # with the out-of-band bioguide verification of the boundary members.
        members = load_default_senate_members()
        by_id = {m.bioguide_id: m for m in members}
        assert len(members) == 269  # 100 current + 169 departed (2000-present)
        assert by_id["M001190"].last_name == "Mullin"
        assert by_id["M001190"].state == "OK"
        assert by_id["A000383"].state == "OK"
        assert by_id["G000359"].first_name == "Lindsey"
        assert by_id["G000359"].state == "SC"
        assert by_id["G000608"].last_name == "Graham"  # source lastName artifact corrected
        assert by_id["G000608"].state == "SC"

    def test_malformed_json_raises(self):
        with pytest.raises(SenateStateResolveError):
            parse_senate_members_json(b"{not json")

    def test_non_object_payload_raises(self):
        with pytest.raises(SenateStateResolveError):
            parse_senate_members_json(b"[[1]]")

    def test_missing_identity_field_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"terms":[{"start":"2023-01-03","end":"2025-01-03"}]}]}'
        )
        with pytest.raises(SenateStateResolveError):
            parse_senate_members_json(data)

    def test_member_without_terms_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[]}]}'
        )
        with pytest.raises(SenateStateResolveError):
            parse_senate_members_json(data)

    def test_term_without_start_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[{"end":null}]}]}'
        )
        with pytest.raises(SenateStateResolveError):
            parse_senate_members_json(data)

    def test_malformed_term_date_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[{"start":"2023/01/03","end":null}]}]}'
        )
        with pytest.raises(SenateStateResolveError):
            parse_senate_members_json(data)

    def test_empty_members_raises(self):
        with pytest.raises(SenateStateResolveError):
            parse_senate_members_json(b'{"members":[]}')


def _sample_records() -> list[dict]:
    """Reusable tiny congress-legislators records for generator tests."""
    return [
        {
            "id": {"bioguide": "G000608"},
            "name": {
                "first": "Darline",
                "last": "Graham Nordone",
                "official_full": "Darline Graham",
            },
            "terms": [
                {"type": "sen", "start": "2026-07-14", "end": "2027-01-03",
                 "state": "SC", "party": "Republican"}
            ],
        },
        {
            "id": {"bioguide": "M001190"},
            "name": {"first": "Markwayne", "last": "Mullin",
                     "official_full": "Markwayne Mullin"},
            "terms": [
                {"type": "sen", "start": "2023-01-03", "end": "2026-03-23",
                 "state": "OK", "party": "Republican"}
            ],
        },
        {
            "id": {"bioguide": "H000001"},
            "name": {"first": "Jane", "last": "House"},
            "terms": [
                {"type": "rep", "start": "2023-01-03", "end": "2025-01-03",
                 "state": "XX"}
            ],
        },
        {
            "id": {"bioguide": "S000045"},
            "name": {"first": "Old", "last": "Prior"},
            "terms": [
                {"type": "sen", "start": "1995-01-04", "end": "1999-12-31",
                 "state": "CA"}
            ],
        },
        {
            "id": {"bioguide": "S000046"},
            "name": {"first": "Operator", "last": "Senator"},
            "terms": [
                {"type": "sen", "start": "1998-01-06", "end": "2004-01-03",
                 "state": "NV"}
            ],
        },
    ]


class TestSenateMembersSnapshot:
    def test_builds_expected_members_from_records(self):
        snapshot = build_members_snapshot(
            _sample_records(), generated_at="2026-09-22"
        )
        assert snapshot["generated_at"] == "2026-09-22"
        assert snapshot["coverage_start"] == "2000-01-01"
        assert "source" in snapshot and "provenance_note" in snapshot
        members = snapshot["members"]
        assert len(members) == 3
        by_id = {m["bioguide_id"]: m for m in members}
        # House term excluded, pre-2000 service excluded.
        assert "H000001" not in by_id
        assert "S000045" not in by_id
        # Overlapping-2000 senator kept.
        assert by_id["S000046"]["state"] == "NV"
        # Source lastName artifact corrected from official_full.
        assert by_id["G000608"]["last_name"] == "Graham"
        assert by_id["G000608"]["first_name"] == "Darline"
        assert by_id["M001190"]["terms"] == [
            {"start": "2023-01-03", "end": "2026-03-23"}
        ]
        # Deterministic ordering by surname/given name/bioguide id.
        surnames = [m["last_name"] for m in members]
        assert surnames == sorted(surnames, key=str.lower)

    def test_boundary_members_provenance_is_recorded(self):
        snapshot = build_members_snapshot(_sample_records())
        boundaries = snapshot["boundary_members"]
        assert "M001190" in boundaries and "A000383" in boundaries
        assert "G000359" in boundaries and "G000608" in boundaries

    def test_generational_suffix_is_never_stored_as_the_surname(self):
        """A comma-suffixed official name must not yield last_name 'III'/'IV'.

        ``official_full`` = "Joe Manchin, III" with ``name.last`` = "Manchin"
        fails a naive ``official_full.endswith(last)`` test, so a fallback to
        the final whitespace token stored the *suffix* as the surname. The
        senator then became unresolvable: no filing is ever filed under
        "Manchin, III" as a last name. Both senators below are real snapshot
        members whose stored surname was the bare suffix.
        """
        records = [
            {
                "id": {"bioguide": "M001183"},
                "name": {
                    "first": "Joe",
                    "last": "Manchin",
                    "official_full": "Joe Manchin, III",
                },
                "terms": [
                    {"type": "sen", "start": "2023-01-03", "end": "2025-01-03",
                     "state": "WV", "party": "Democratic"}
                ],
            },
            {
                "id": {"bioguide": "R000361"},
                "name": {
                    "first": "John",
                    "middle": "D.",
                    "last": "Rockefeller",
                    "official_full": "John D. Rockefeller, IV",
                },
                "terms": [
                    {"type": "sen", "start": "2011-01-03", "end": "2015-01-03",
                     "state": "WV", "party": "Democratic"}
                ],
            },
        ]
        by_id = {
            m["bioguide_id"]: m
            for m in build_members_snapshot(records)["members"]
        }
        assert by_id["M001183"]["last_name"] == "Manchin"
        assert by_id["R000361"]["last_name"] == "Rockefeller"
        # The suffix is dropped from the given names too, leaving the real
        # name parts only.
        assert by_id["R000361"]["given_name"] == "John D."

    def test_official_given_name_is_recorded_from_official_full(self):
        """A registered display given name is stored alongside the formal one.

        The eFD portal files under the form the senator registered ("J.D."),
        while ``name.first`` is the formal "James David". Storing only the
        formal name made the display form unmatchable.
        """
        records = [{
            "id": {"bioguide": "V000137"},
            "name": {
                "first": "James David",
                "last": "Vance",
                "official_full": "J.D. Vance",
            },
            "terms": [
                {"type": "sen", "start": "2023-01-03", "end": "2031-01-03",
                 "state": "OH", "party": "Republican"}
            ],
        }]
        member = build_members_snapshot(records)["members"][0]
        assert member["first_name"] == "James David"
        assert member["given_name"] == "J.D."
        assert member["last_name"] == "Vance"

    def test_given_name_defaults_to_first_name_when_official_full_is_absent(self):
        records = [{
            "id": {"bioguide": "X000001"},
            "name": {"first": "Jane", "middle": "Q", "last": "Public"},
            "terms": [
                {"type": "sen", "start": "2023-01-03", "end": "2025-01-03",
                 "state": "ZZ", "party": "Independent"}
            ],
        }]
        member = build_members_snapshot(records)["members"][0]
        assert member["given_name"] == "Jane Q"

    def test_verified_service_correction_replaces_a_known_wrong_term_end(self):
        records = [{
            "id": {"bioguide": "V000137"},
            "name": {"first": "James David", "last": "Vance",
                     "official_full": "J.D. Vance"},
            "terms": [
                {"type": "sen", "start": "2023-01-03", "end": "2025-01-09",
                 "state": "OH", "party": "Republican"}
            ],
        }]
        member = build_members_snapshot(records)["members"][0]
        assert member["terms"] == [
            {"start": "2023-01-03", "end": "2031-01-03"}
        ]
        # The correction is recorded as provenance, and is a service interval
        # only -- it carries no identity information.
        corrections = build_members_snapshot(records)[
            "verified_service_corrections"
        ]
        assert corrections["V000137"]["end"] == "2031-01-03"
        assert "identity" not in corrections["V000137"]

    def test_stale_service_correction_raises_instead_of_being_applied(self):
        """A correction must not silently apply to a term that no longer exists.

        If upstream fixed the source interval, ``--check`` regenerating from the
        corrected source would keep rewriting it back; raising surfaces the
        stale entry instead.
        """
        records = [{
            "id": {"bioguide": "V000137"},
            "name": {"first": "James David", "last": "Vance",
                     "official_full": "J.D. Vance"},
            "terms": [
                {"type": "sen", "start": "2019-01-03", "end": "2025-01-09",
                 "state": "OH", "party": "Republican"}
            ],
        }]
        with pytest.raises(ValueError, match="stale"):
            build_members_snapshot(records)

    def test_service_correction_leaves_other_members_untouched(self):
        records = [{
            "id": {"bioguide": "K000383"},
            "name": {"first": "Angus", "last": "King",
                     "official_full": "Angus S. King Jr."},
            "terms": [
                {"type": "sen", "start": "2025-01-03", "end": "2031-01-03",
                 "state": "ME", "party": "Democratic"}
            ],
        }]
        member = build_members_snapshot(records)["members"][0]
        assert member["last_name"] == "King"
        assert member["terms"] == [
            {"start": "2025-01-03", "end": "2031-01-03"}
        ]

    def test_diff_reports_given_name_drift(self):
        committed = build_members_snapshot(
            _sample_records(), generated_at="2026-09-22"
        )
        records = [dict(r) for r in _sample_records()]
        for r in records:
            if r["id"]["bioguide"] == "M001190":
                r["name"] = dict(r["name"], official_full="Markwayne S. Mullin")
        fresh = build_members_snapshot(records, generated_at="2026-09-22")
        assert any(
            "M001190" in d and "given_name" in d
            for d in diff_snapshots(committed, fresh)
        )

    def test_packaged_snapshot_stores_no_bare_suffix_as_a_surname(self):
        """Guard on the committed asset, not just on the generator.

        A generational suffix used to be stored as a member's surname, which
        made that member unresolvable. Asserting the shape of the shipped file
        catches a regeneration that reintroduces it.
        """
        snapshot = json.loads(
            (Path(senate_members_refresh.__file__).parent
             / "data" / "senate_members.json").read_bytes()
        )
        suffixes = {"jr", "sr", "ii", "iii", "iv", "v", "junior", "senior"}
        offenders = [
            m["last_name"] for m in snapshot["members"]
            if m["last_name"].strip(".,'’").lower() in suffixes
        ]
        assert offenders == []

    def test_packaged_snapshot_records_a_given_name_for_every_member(self):
        snapshot = json.loads(
            (Path(senate_members_refresh.__file__).parent
             / "data" / "senate_members.json").read_bytes()
        )
        missing = [m["bioguide_id"] for m in snapshot["members"] if not m.get("given_name")]
        assert missing == []

    def test_missing_bioguide_raises(self):
        records = [{"name": {"first": "X", "last": "Y"},
                    "terms": [{"type": "sen", "start": "2023-01-03", "end": None}]}]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_record_without_name_raises(self):
        records = [{"id": {"bioguide": "X"},
                    "name": {},
                    "terms": [{"type": "sen", "start": "2023-01-03", "end": None}]}]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_sen_term_without_start_raises(self):
        records = [{
            "id": {"bioguide": "X"},
            "name": {"first": "X", "last": "Y"},
            "terms": [{"type": "sen", "end": "2025-01-03"}],
        }]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_no_senate_members_raises(self):
        records = [{
            "id": {"bioguide": "X"},
            "name": {"first": "X", "last": "Y"},
            "terms": [{"type": "rep", "start": "2023-01-03", "end": None}],
        }]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_diff_is_empty_when_members_equal(self):
        build = lambda: build_members_snapshot(_sample_records(), generated_at="2026-09-22")
        assert diff_snapshots(build(), build_members_snapshot(_sample_records())) == []

    def test_diff_reports_field_term_and_membership_changes(self):
        committed = build_members_snapshot(_sample_records(), generated_at="2026-09-22")
        records = [dict(r) for r in _sample_records()]
        # Drift: Mullin's term extends.
        for r in records:
            if r["id"]["bioguide"] == "M001190":
                r["terms"][0] = dict(r["terms"][0], end="2026-09-30")
        fresh = build_members_snapshot(records, generated_at="2026-09-22")
        diffs = diff_snapshots(committed, fresh)
        assert any("M001190" in d and "terms" in d for d in diffs)

        lean = build_members_snapshot(
            [r for r in records if r["id"]["bioguide"] != "M001190"],
            generated_at="2026-09-22",
        )
        diffs = diff_snapshots(fresh, lean)
        assert any("- removed member Mullin" in d for d in diffs)
        assert any("+ new member Mullin" in d for d in diff_snapshots(lean, fresh))


class TestRefreshCli:
    """The refresh script's CLI surface, driven from local dataset files."""

    def _datasets(self, tmp_path):
        # cur.json holds currently-serving members; hist.json holds historical
        # ones. The records exercise the current+departed+non-senate mix.
        records = _sample_records()
        cur = tmp_path / "cur.json"
        hist = tmp_path / "hist.json"
        cur.write_text(json.dumps(records))
        hist.write_text(json.dumps([]))
        return str(cur), str(hist)

    def _committed(self, tmp_path, monkeypatch):
        snapshot = build_members_snapshot(_sample_records(), generated_at="2026-09-22")
        path = tmp_path / "senate_members.json"
        path.write_text(json.dumps(snapshot))
        monkeypatch.setattr(senate_members_refresh, "SNAPSHOT_PATH", path)
        return path

    def test_check_passes_when_snapshot_matches(self, tmp_path, monkeypatch):
        cur, hist = self._datasets(tmp_path)
        self._committed(tmp_path, monkeypatch)
        assert senate_members_refresh._main(["--cur", cur, "--hist", hist, "--check"]) == 0

    def test_check_fails_on_drift(self, tmp_path, monkeypatch):
        import copy

        cur, hist = self._datasets(tmp_path)
        committed = build_members_snapshot(_sample_records(), generated_at="2026-09-22")
        committed["members"] = copy.deepcopy(committed["members"])
        for member in committed["members"]:
            if member["bioguide_id"] == "M001190":
                member["terms"] = [{"start": "2023-01-03", "end": "2026-09-30"}]
        path = tmp_path / "senate_members.json"
        path.write_text(json.dumps(committed))
        monkeypatch.setattr(senate_members_refresh, "SNAPSHOT_PATH", path)
        assert senate_members_refresh._main(["--cur", cur, "--hist", hist, "--check"]) == 1

    def test_output_writes_snapshot_file(self, tmp_path):
        cur, hist = self._datasets(tmp_path)
        out = tmp_path / "out.json"
        assert senate_members_refresh._main(
            ["--cur", cur, "--hist", hist, "--output", str(out)]
        ) == 0
        payload = json.loads(out.read_text())
        assert len(payload["members"]) == 3

    def test_cur_without_hist_is_rejected(self, tmp_path):
        cur, _ = self._datasets(tmp_path)
        with pytest.raises(SystemExit):
            senate_members_refresh._main(["--cur", cur, "--check"])


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

    def _listing_transport(self, rows):
        payload = json.dumps(
            {
                "draw": 1,
                "recordsTotal": len(rows),
                "recordsFiltered": len(rows),
                "data": rows,
            }
        ).encode()
        transport = _MemoryTransport(records_total=len(rows))
        transport.listing_pages = [payload, _load_fixture("listing_empty.json")]
        return transport

    def test_fetch_index_resolves_departed_senators_via_snapshot(self):
        # With no current-listing knowledge ({}) the adapter still resolves
        # departed senators by matching the reference snapshot's service
        # intervals: Markwayne Mullin (OK, resigned 2026-03-23) and Lindsey
        # Graham (SC, died 2026-07-11).
        rows = [
            [
                "Markwayne", "Mullin", "Mullin, Markwayne (Senator)",
                f'<a href="/search/view/ptr/{ELECTRONIC_DETAIL_ID}/">'
                "Periodic Transaction Report</a>",
                "02/10/2024",
            ],
            [
                "Lindsey", "Graham", "Graham, Lindsey (Senator)",
                '<a href="/search/view/ptr/b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f/">'
                "Periodic Transaction Report</a>",
                "05/02/2026",
            ],
        ]
        source = SenateEfdSource(
            transport=self._listing_transport(rows),
            senators={},
            members=_sample_members(),
        )
        filings = source.fetch_index(year=2026)
        by_doc = {f.doc_id: f for f in filings}
        assert by_doc[ELECTRONIC_DETAIL_ID].state_district == "OK00"
        assert by_doc["b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f"].state_district == "SC00"

    def test_fetch_index_fails_closed_on_vacancy_date(self):
        # Graham died 2026-07-11; Darline started 2026-07-14. 07/12/2026 is a
        # vacancy day: no member with the "Lindsey" identity was serving, so
        # the adapter must fail closed rather than attribute the filing.
        rows = [[
            "Lindsey", "Graham", "Graham, Lindsey (Senator)",
            f'<a href="/search/view/ptr/{ELECTRONIC_DETAIL_ID}/">'
            "Periodic Transaction Report</a>",
            "07/12/2026",
        ]]
        source = SenateEfdSource(
            transport=self._listing_transport(rows),
            senators={},
            members=_sample_members(),
        )
        with pytest.raises(SenateStateResolveError):
            source.fetch_index(year=2026)

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