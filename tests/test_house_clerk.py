"""Tests for the House Clerk source adapter and index parsing."""

from __future__ import annotations

import io
import json
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import pytest

from politician_dashboard.ingest.models import Filing
from politician_dashboard.ingest.sources import house_clerk, house_members_refresh, names
from politician_dashboard.ingest.sources.base import select_ptrs
from politician_dashboard.ingest.sources.house_clerk import (
    HouseClerkSource,
    HouseIndexError,
    HouseMemberResolveError,
    HouseMemberTerm,
    _serves_on,
    classify_doc_id,
    load_default_house_members,
    parse_filing_date,
    parse_house_members_json,
    parse_index_txt,
    parse_index_xml,
    parse_index_zip,
    resolve_house_member,
)
from politician_dashboard.ingest.sources.house_members_refresh import (
    build_members_snapshot,
    diff_snapshots,
)

FIXTURES = Path(__file__).parent / "fixtures"
HOUSE_FIXTURES = FIXTURES / "house"


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


def _house_members():
    """Parse the hermetic House membership fixture."""
    return parse_house_members_json(
        (HOUSE_FIXTURES / "house_members.json").read_bytes()
    )


def _resolve(state, last, first, *, anchor_date=None, members=None):
    return resolve_house_member(
        state,
        last,
        first,
        anchor_date=anchor_date,
        members=members if members is not None else _house_members(),
    )


class TestHouseMemberResolution:
    """Resolving eFD PTR filer names against the House reference roster.

    The fixture spans live members, an expelled member, a special-election
    winner sworn after the election date, a father/son pair sharing one name
    and state (Payne NJ), a delegate whose official given name is two words,
    a member who died in office, a member whose district renumbered across
    censuses, a member whose fuller given name sits across first/middle
    fields (Steube "W. Gregory"/"Greg"), a member who goes by a short form of
    his first name (Khanna "Ro"/"Rohit"), a member with a compound roll-call
    surname filed under its last token (McClain Delaney/"Delaney"), a member
    whose legal name is the short form of the roster's (Hoyle "Val T."/
    "Valerie"), and an accented roster surname matched against an unaccented
    filing (Sánchez) -- all resolved by state + surname (or its alternate) +
    service interval, with district treated as informational.
    """

    def test_resolves_current_representative_with_middle_initial(self):
        member = _resolve("AL", "Aderholt", "Robert B.", anchor_date=date(2025, 9, 10))
        assert member.bioguide_id == "A000055"
        assert member.state == "AL"

    def test_state_and_name_matching_is_case_and_whitespace_insensitive(self):
        member = _resolve("  al ", "  Aderholt ", "  Robert  ", anchor_date=date(2025, 9, 10))
        assert member.bioguide_id == "A000055"

    def test_wrong_state_fails_closed(self):
        with pytest.raises(HouseMemberResolveError):
            _resolve("CA", "Aderholt", "Robert", anchor_date=date(2025, 9, 10))

    def test_unknown_person_raises(self):
        with pytest.raises(HouseMemberResolveError):
            _resolve("AL", "Nofinger", "Zed", anchor_date=date(2025, 9, 10))

    def test_expelled_member_service_is_inclusive_of_last_day(self):
        # Santos was expelled 2023-12-01; the snapshot treats that day as
        # service. The day after the seat was vacant (Suozzi is not in the
        # fixture), so resolution must fail closed rather than guess.
        assert _resolve("NY", "Santos", "George", anchor_date=date(2023, 3, 1)).bioguide_id == "S001222"
        assert _resolve("NY", "Santos", "George", anchor_date=date(2023, 12, 1)).bioguide_id == "S001222"
        with pytest.raises(HouseMemberResolveError):
            _resolve("NY", "Santos", "George", anchor_date=date(2023, 12, 2))

    def test_special_election_member_serves_from_sworn_date_only(self):
        # Patronis won the 2025-04-01 special election but the snapshot (and
        # thus this adapter) marks service from his sworn-in date 2025-04-02,
        # matching how eFD filers self-report. The date before must stay a
        # vacancy. Bounds are inclusive on both ends.
        assert _resolve("FL", "Patronis", "Jimmy", anchor_date=date(2025, 4, 2)).bioguide_id == "P000622"
        assert _resolve("FL", "Patronis", "Jimmy", anchor_date=date(2027, 1, 3)).bioguide_id == "P000622"
        with pytest.raises(HouseMemberResolveError):
            _resolve("FL", "Patronis", "Jimmy", anchor_date=date(2025, 4, 1))
        with pytest.raises(HouseMemberResolveError):
            _resolve("FL", "Patronis", "Jimmy", anchor_date=date(2027, 1, 4))

    def test_ignores_district_renumbering(self):
        # McCarthy served CA-22 then CA-23 (2013 post-census) then CA-20
        # (2023 post-census). The StateDst digits are informational; the
        # resolver only needs the "CA" prefix and the service date.
        member = _resolve("CA", "McCarthy", "Kevin", anchor_date=date(2019, 7, 1))
        assert member.bioguide_id == "M001165"
        assert _resolve("CA", "McCarthy", "Kevin", anchor_date=date(2007, 6, 1)).bioguide_id == "M001165"
        assert _resolve("CA", "McCarthy", "Kevin", anchor_date=date(2023, 12, 31)).bioguide_id == "M001165"
        with pytest.raises(HouseMemberResolveError):
            _resolve("CA", "McCarthy", "Kevin", anchor_date=date(2024, 1, 1))

    def test_father_and_son_disambiguated_by_service_interval(self):
        assert _resolve("NJ", "Payne", "Donald", anchor_date=date(2011, 6, 1)).bioguide_id == "P000149"
        assert _resolve("NJ", "Payne", "Donald", anchor_date=date(2013, 6, 1)).bioguide_id == "P000604"
        # Between Sr.'s death (2012-03-06) and Jr.'s service the seat was
        # vacant; either identity would be a guess, so fail closed.
        with pytest.raises(HouseMemberResolveError):
            _resolve("NJ", "Payne", "Donald", anchor_date=date(2012, 6, 1))

    def test_same_surname_ambiguity_without_anchor_raises(self):
        # With no filing date the two Donald Paynes (NJ) are both candidates;
        # the adapter must not guess between father and son.
        with pytest.raises(HouseMemberResolveError):
            _resolve("NJ", "Payne", "Donald")

    def test_delegate_at_large_resolves_from_state_prefix(self):
        # An eFD "AS00" StateDst answers to the "AS" prefix; the delegate's
        # at-large district is informational.
        member = _resolve("AS", "Radewagen", "Aumua Amata", anchor_date=date(2025, 6, 1))
        assert member.bioguide_id == "R000600"

    def test_given_name_matching_stays_bounded(self):
        # No enumerated given-name relation links "Kev" to "Kevin", and no
        # string-prefix test may be applied.
        with pytest.raises(HouseMemberResolveError):
            _resolve("CA", "McCarthy", "Kev", anchor_date=date(2019, 7, 1))

    def test_initial_only_given_name_does_not_guess(self):
        with pytest.raises(HouseMemberResolveError):
            _resolve("AL", "Aderholt", "R.", anchor_date=date(2025, 9, 10))

    def test_unique_name_resolves_without_anchor_date(self):
        # A filing whose date failed to parse can still be identified when the
        # state + surname group is unique -- even for a departed member. His
        # full given name is "A. Donald" (the source records "A." as the
        # first-name field and "Donald" as the middle name).
        assert _resolve("NY", "Santos", "George").bioguide_id == "S001222"
        assert _resolve("VA", "McEachin", "A. Donald").bioguide_id == "M001200"
        # Bare initials never name a member: "A." cannot be attributed to a
        # member whose real registered given name is the "Donald" beside it.
        with pytest.raises(HouseMemberResolveError):
            _resolve("VA", "McEachin", "A.")

    def test_fuller_given_name_matches_displayed_full_name(self):
        # eFD files Steube as "Greg" though the Clerk records "W. Gregory".
        assert _resolve("FL", "Steube", "Greg", anchor_date=date(2024, 6, 1)).bioguide_id == "S001214"
        # The dangling initial alone cannot be used as a name.
        with pytest.raises(HouseMemberResolveError):
            _resolve("FL", "Steube", "W.", anchor_date=date(2024, 6, 1))

    def test_rohit_resolves_ro_khanna(self):
        assert _resolve("CA", "Khanna", "Rohit", anchor_date=date(2022, 6, 1)).bioguide_id == "K000389"

    def test_compound_surname_filing_uses_roster_alternate(self):
        # The roster surname is the roll-call compound "McClain Delaney", but
        # eFD splits the surname into "Delaney" + first "April McClain".
        member = _resolve("MD", "Delaney", "April McClain", anchor_date=date(2025, 6, 1))
        assert member.bioguide_id == "M001232"

    def test_public_short_given_name_matches_roster_full_given(self):
        # Val Hoyle files under her legal "Valerie" while the roster given name
        # is its short form: the enumerated valerie/val relation permits it.
        assert _resolve("OR", "Hoyle", "Valerie", anchor_date=date(2025, 9, 12)).bioguide_id == "H001094"

    def test_accented_roster_surname_matches_unaccented_filing(self):
        # eFD strips the diacritic ("Sanchez") while the roster keeps it.
        assert _resolve("CA", "Sanchez", "Linda T.", anchor_date=date(2026, 1, 9)).bioguide_id == "S001156"
        assert _resolve("CA", "Sánchez", "Linda T.", anchor_date=date(2026, 1, 9)).bioguide_id == "S001156"

    def test_empty_member_group_raises(self):
        with pytest.raises(HouseMemberResolveError):
            resolve_house_member(
                "AL", "Aderholt", "Robert", anchor_date=date(2025, 9, 10), members=[]
            )


class TestParseHouseMembersJson:
    def test_parses_fixture_identity_terms_and_district(self):
        members = _house_members()
        assert len(members) == 13
        by_id = {m.bioguide_id: m for m in members}
        assert by_id["A000055"].last_name == "Aderholt"
        assert by_id["A000055"].first_name == "Robert"
        assert by_id["A000055"].state == "AL"

        mccarthy = by_id["M001165"]
        assert len(mccarthy.terms) == 9
        assert mccarthy.terms[0] == HouseMemberTerm(
            date(2007, 1, 4), date(2009, 1, 3), 22
        )
        assert mccarthy.terms[-1] == HouseMemberTerm(
            date(2023, 1, 3), date(2023, 12, 31), 20
        )
        districts = [term.district for term in mccarthy.terms]
        assert districts == [22] * 3 + [23] * 5 + [20]

    def test_at_large_delegate_district_is_zero(self):
        members = _house_members()
        radewagon = next(
            m for m in members if m.bioguide_id == "R000600"
        )
        assert all(term.district == 0 for term in radewagon.terms)

    def test_parses_open_ended_term(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[{"start":"2023-01-03","end":null,'
            b'"district":0}]}]}'
        )
        members = parse_house_members_json(data)
        assert members[0].terms == (HouseMemberTerm(date(2023, 1, 3), None, 0),)
        assert _serves_on(members[0], date(2030, 1, 1))
        assert not _serves_on(members[0], date(2023, 1, 2))

    def test_packaged_reference_snapshot_loads(self):
        # The committed asset is part of the repo; its data must reconcile
        # with the out-of-band bioguide verification of the boundary members.
        members = load_default_house_members()
        assert len(members) == 1362  # 2000-01-01 through today's Congress
        by_id = {m.bioguide_id: m for m in members}
        assert by_id["P000622"].state == "FL"
        assert by_id["P000622"].first_name == "Jimmy"
        assert by_id["P000622"].terms[0] == HouseMemberTerm(
            date(2025, 4, 2), date(2027, 1, 3), 1
        )
        assert by_id["S001222"].terms[-1] == HouseMemberTerm(
            date(2023, 1, 3), date(2023, 12, 1), 3
        )
        assert by_id["G000578"].terms[-1] == HouseMemberTerm(
            date(2023, 1, 3), date(2024, 11, 13), 1
        )
        assert by_id["M001200"].terms[-1] == HouseMemberTerm(
            date(2021, 1, 3), date(2022, 11, 28), 4
        )
        assert by_id["M001165"].state == "CA"
        assert [t.district for t in by_id["M001165"].terms] == (
            [22] * 3 + [23] * 5 + [20]
        )
        # Surname regression: "Donald M. Payne, Jr." must keep the surname
        # "Payne", never the source's trailing suffix token.
        assert by_id["P000604"].last_name == "Payne"
        assert by_id["P000604"].state == "NJ"
        # Full enrolled given names and compound surnames survive generation
        # (the official full name minus the surname phrase).
        assert by_id["M001232"].given_name == "April"
        assert by_id["M001232"].last_name == "McClain Delaney"
        assert by_id["M001232"].last_name_alt == "Delaney"
        assert by_id["S001214"].given_name == "W. Gregory"
        assert by_id["H001094"].given_name == "Val T."
        assert by_id["S001156"].last_name == "Sánchez"
        assert by_id["M001200"].given_name == "A. Donald"
        # The snapshot covers the territories/delegates the eFD uses (AS00).
        assert by_id["R000600"].state == "AS"
        assert all(t.district == 0 for t in by_id["R000600"].terms)

    def test_boundary_members_are_all_present_in_members(self):
        data = house_clerk.REFERENCE_MEMBERS_JSON.read_bytes()
        payload = json.loads(data)
        ids = {m["bioguide_id"] for m in payload["members"]}
        assert set(payload["boundary_members"]) <= ids

    def test_malformed_json_raises(self):
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(b"{not json")

    def test_non_object_payload_raises(self):
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(b"[[1]]")

    def test_missing_identity_field_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"terms":[{"start":"2023-01-03","end":"2025-01-03","district":1}]}]}'
        )
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(data)

    def test_member_without_terms_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[]}]}'
        )
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(data)

    def test_term_without_start_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[{"end":null,"district":1}]}]}'
        )
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(data)

    def test_malformed_term_date_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[{"start":"2023/01/03","end":null,'
            b'"district":1}]}]}'
        )
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(data)

    def test_non_integer_district_raises(self):
        data = (
            b'{"members":[{"bioguide_id":"X","last_name":"A","first_name":"B",'
            b'"state":"SS","terms":[{"start":"2023-01-03","end":null,'
            b'"district":"4"}]}]}'
        )
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(data)

    def test_empty_members_raises(self):
        with pytest.raises(HouseMemberResolveError):
            parse_house_members_json(b'{"members":[]}')


def _house_sample_records() -> list[dict]:
    """Reusable tiny congress-legislators records for generator tests."""
    return [
        {
            "id": {"bioguide": "A000055"},
            "name": {"first": "Robert", "last": "Aderholt",
                     "official_full": "Robert B. Aderholt"},
            "terms": [
                {"type": "rep", "start": "2023-01-03", "end": "2027-01-03",
                 "district": 4, "state": "AL", "party": "Republican"}
            ],
        },
        {
            "id": {"bioguide": "R000600"},
            "name": {"first": "Aumua Amata", "last": "Radewagen",
                     "official_full": "Aumua Amata Coleman Radewagen"},
            "terms": [
                {"type": "rep", "start": "2025-01-03", "end": "2027-01-03",
                 "district": 0, "state": "AS"}
            ],
        },
        {
            "id": {"bioguide": "Z000001"},
            "name": {"first": "Darline", "last": "Graham Nordone",
                     "official_full": "Darline Graham"},
            "terms": [
                {"type": "rep", "start": "2026-01-03", "end": "2027-01-03",
                 "district": 1, "state": "XX"}
            ],
        },
        {
            "id": {"bioguide": "P000604"},
            "name": {"first": "Donald", "middle": "M.", "last": "Payne",
                     "suffix": "Jr.", "official_full": "Donald M. Payne, Jr."},
            "terms": [
                {"type": "rep", "start": "2023-01-03", "end": "2024-04-24",
                 "district": 10, "state": "NJ"}
            ],
        },
        {
            "id": {"bioguide": "H000001"},
            "name": {"first": "Senator", "last": "Bystander"},
            "terms": [
                {"type": "sen", "start": "2023-01-03", "end": "2029-01-03",
                 "district": 0, "state": "NV"}
            ],
        },
        {
            "id": {"bioguide": "O000001"},
            "name": {"first": "Old", "last": "Prior"},
            "terms": [
                {"type": "rep", "start": "1995-01-04", "end": "1999-12-31",
                 "district": 1, "state": "CA"}
            ],
        },
        {
            "id": {"bioguide": "O000002"},
            "name": {"first": "Heldover", "last": "Bridge"},
            "terms": [
                {"type": "rep", "start": "1999-01-06", "end": "2001-01-03",
                 "district": 2, "state": "CA"}
            ],
        },
        {
            # Compound roll-call surname: eFD may file it as just "Delaney",
            # so the snapshot records that alternate spelling.
            "id": {"bioguide": "M001232"},
            "name": {"first": "April", "middle": "Lynn", "last": "McClain Delaney",
                     "official_full": "April McClain Delaney"},
            "terms": [
                {"type": "rep", "start": "2025-01-03", "end": "2027-01-03",
                 "district": 6, "state": "MD"}
            ],
        },
        {
            # The registered given name lands in the middle: "W. Gregory".
            "id": {"bioguide": "S001214"},
            "name": {"first": "W.", "middle": "Gregory", "last": "Steube",
                     "official_full": "W. Gregory Steube"},
            "terms": [
                {"type": "rep", "start": "2019-01-03", "end": "2021-01-03",
                 "district": 17, "state": "FL"},
                {"type": "rep", "start": "2025-01-03", "end": "2027-01-03",
                 "district": 17, "state": "FL"}
            ],
        },
        {
            # official_full contradicts name.first/middle: "Valerie" but the
            # official given-name text stays "Val T.". The accent in the
            # surname is preserved for match-time transliteration.
            "id": {"bioguide": "H001094"},
            "name": {"first": "Valerie", "middle": "Anne", "last": "Hoyle",
                     "official_full": "Val T. Hoyle"},
            "terms": [
                {"type": "rep", "start": "2025-01-03", "end": "2027-01-03",
                 "district": 4, "state": "OR"}
            ],
        },
        {
            # Accented surname kept as-is through generation.
            "id": {"bioguide": "S001156"},
            "name": {"first": "Linda", "middle": "T.", "last": "Sánchez",
                     "official_full": "Linda T. Sánchez"},
            "terms": [
                {"type": "rep", "start": "2025-01-03", "end": "2027-01-03",
                 "district": 38, "state": "CA"}
            ],
        },
    ]


class TestHouseMembersSnapshot:
    def test_builds_expected_members_from_records(self):
        snapshot = build_members_snapshot(
            _house_sample_records(), generated_at="2026-09-23"
        )
        assert snapshot["generated_at"] == "2026-09-23"
        assert snapshot["coverage_start"] == "2000-01-01"
        assert "source" in snapshot and "provenance_note" in snapshot
        members = snapshot["members"]
        assert len(members) == 9
        by_id = {m["bioguide_id"]: m for m in members}
        # Senate term excluded; pre-2000-only service excluded.
        assert "H000001" not in by_id
        assert "O000001" not in by_id
        # Overlapping-2000 rep kept with its term's district.
        assert by_id["O000002"]["terms"] == [
            {"start": "1999-01-06", "end": "2001-01-03", "district": 2}
        ]
        # At-large delegate district preserved as 0.
        assert by_id["R000600"]["terms"][0]["district"] == 0
        # Source lastName artifacts corrected from official_full (surname token
        # retained even when official_full ends in a "Jr." suffix).
        assert by_id["Z000001"]["last_name"] == "Graham"
        assert by_id["P000604"]["last_name"] == "Payne"
        assert by_id["P000604"]["state"] == "NJ"
        # given_name is the official full name minus its surname phrase, never
        # the union's cosmetic middle (April Lynn vs official "April").
        assert by_id["M001232"]["given_name"] == "April"
        assert by_id["S001214"]["given_name"] == "W. Gregory"
        assert by_id["H001094"]["given_name"] == "Val T."
        assert by_id["S001156"]["given_name"] == "Linda T."
        # Compound surnames survive, and the eFD filer-spelling alternate is
        # emitted only when it differs from the roster surname.
        assert by_id["M001232"]["last_name"] == "McClain Delaney"
        assert by_id["M001232"]["last_name_alt"] == "Delaney"
        assert by_id["S001214"].get("last_name_alt") is None
        # Accent untouched by generation; transliteration happens at match time.
        assert by_id["S001156"]["last_name"] == "Sánchez"
        # Deterministic ordering by surname/given name/bioguide id.
        surnames = [m["last_name"] for m in members]
        assert surnames == sorted(surnames, key=str.lower)

    def test_boundary_members_provenance_is_recorded(self):
        snapshot = build_members_snapshot(_house_sample_records())
        boundaries = snapshot["boundary_members"]
        for bioguide in ("M001165", "S001222", "G000578", "P000622",
                         "M001200", "W000813"):
            assert bioguide in boundaries

    def test_missing_bioguide_raises(self):
        records = [{"name": {"first": "X", "last": "Y"},
                    "terms": [{"type": "rep", "start": "2023-01-03", "end": None,
                               "district": 1}]}]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_record_without_name_raises(self):
        records = [{"id": {"bioguide": "X"},
                    "name": {},
                    "terms": [{"type": "rep", "start": "2023-01-03", "end": None,
                               "district": 1}]}]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_rep_term_without_start_raises(self):
        records = [{
            "id": {"bioguide": "X"},
            "name": {"first": "X", "last": "Y"},
            "terms": [{"type": "rep", "end": "2025-01-03", "district": 1}],
        }]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_non_integer_district_raises(self):
        records = [{
            "id": {"bioguide": "X"},
            "name": {"first": "X", "last": "Y"},
            "terms": [{"type": "rep", "start": "2023-01-03", "end": None,
                       "district": "4"}],
        }]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_no_house_members_raises(self):
        records = [{
            "id": {"bioguide": "X"},
            "name": {"first": "X", "last": "Y"},
            "terms": [{"type": "sen", "start": "2023-01-03", "end": None}],
        }]
        with pytest.raises(ValueError):
            build_members_snapshot(records)

    def test_diff_is_empty_when_members_equal(self):
        build = lambda: build_members_snapshot(
            _house_sample_records(), generated_at="2026-09-23"
        )
        assert diff_snapshots(build(), build_members_snapshot(_house_sample_records())) == []

    def test_diff_reports_field_term_and_membership_changes(self):
        committed = build_members_snapshot(
            _house_sample_records(), generated_at="2026-09-23"
        )
        records = [dict(r) for r in _house_sample_records()]
        # Drift: Aderholt's district changes (census renumbering).
        for r in records:
            if r["id"]["bioguide"] == "A000055":
                r["terms"][0] = dict(r["terms"][0], district=5)
        fresh = build_members_snapshot(records, generated_at="2026-09-23")
        diffs = diff_snapshots(committed, fresh)
        assert any("A000055" in d and "terms" in d for d in diffs)

        lean = build_members_snapshot(
            [r for r in records if r["id"]["bioguide"] != "A000055"],
            generated_at="2026-09-23",
        )
        diffs = diff_snapshots(fresh, lean)
        assert any("- removed member Aderholt" in d for d in diffs)
        assert any("+ new member Aderholt" in d for d in diff_snapshots(lean, fresh))


class TestRefreshCli:
    """The refresh script's CLI surface, driven from local dataset files."""

    def _datasets(self, tmp_path):
        records = _house_sample_records()
        cur = tmp_path / "cur.json"
        hist = tmp_path / "hist.json"
        cur.write_text(json.dumps(records))
        hist.write_text(json.dumps([]))
        return str(cur), str(hist)

    def _committed(self, tmp_path, monkeypatch):
        snapshot = build_members_snapshot(
            _house_sample_records(), generated_at="2026-09-23"
        )
        path = tmp_path / "house_members.json"
        path.write_text(json.dumps(snapshot))
        monkeypatch.setattr(house_members_refresh, "SNAPSHOT_PATH", path)
        return path

    def test_check_passes_when_snapshot_matches(self, tmp_path, monkeypatch):
        cur, hist = self._datasets(tmp_path)
        self._committed(tmp_path, monkeypatch)
        assert house_members_refresh._main(["--cur", cur, "--hist", hist, "--check"]) == 0

    def test_check_fails_on_drift(self, tmp_path, monkeypatch):
        import copy

        cur, hist = self._datasets(tmp_path)
        committed = build_members_snapshot(
            _house_sample_records(), generated_at="2026-09-23"
        )
        committed["members"] = copy.deepcopy(committed["members"])
        for member in committed["members"]:
            if member["bioguide_id"] == "A000055":
                member["terms"] = [
                    {"start": "2025-01-03", "end": "2027-01-03", "district": 5}
                ]
        path = tmp_path / "house_members.json"
        path.write_text(json.dumps(committed))
        monkeypatch.setattr(house_members_refresh, "SNAPSHOT_PATH", path)
        assert house_members_refresh._main(["--cur", cur, "--hist", hist, "--check"]) == 1

    def test_output_writes_snapshot_file(self, tmp_path):
        cur, hist = self._datasets(tmp_path)
        out = tmp_path / "out.json"
        assert house_members_refresh._main(
            ["--cur", cur, "--hist", hist, "--output", str(out)]
        ) == 0
        payload = json.loads(out.read_text())
        assert len(payload["members"]) == 9

    def test_cur_without_hist_is_rejected(self, tmp_path):
        cur, _ = self._datasets(tmp_path)
        with pytest.raises(SystemExit):
            house_members_refresh._main(["--cur", cur, "--check"])


class TestNames:
    """Shared name-matching helpers used by the House resolver."""

    def test_normalize_name_transliterates_accents(self):
        assert names.normalize_name("Sánchez") == "sanchez"
        assert names.normalize_name("Van Epps") == "van epps"

    def test_given_names_agree_matches_full_official_given_name(self):
        # "Greg" is Greg Steube's official given name is spread across the
        # first ("W.") and middle ("Gregory") fields; matching uses the full
        # given-name text.
        assert names.given_names_agree("Greg", "W.", official_given="W. Gregory")
        assert not names.given_names_agree("Greg", "W.", official_given="W. William")

    def test_given_names_agree_without_official_given_falls_back_to_first(self):
        assert names.given_names_agree("Bob", "Robert")
        # The related token "Bob" still matches when "Robert" sits among the
        # fuller official given names; an unrelated name never does.
        assert names.given_names_agree("Bob", "Robert", official_given="Robert Tom")
        assert not names.given_names_agree("Zed", "Robert", official_given="Robert Tom")

    def test_initial_only_filer_fails_against_full_given(self):
        # A bare initial cannot be attributed to "A. Donald McEachin".
        assert not names.given_names_agree("A.", "A.", official_given="A. Donald")

    def test_accented_tokens_are_transliterated(self):
        assert names.given_names_agree("José", "Jose") or names.given_names_agree("Jose", "José")


class TestHouseFetchIndexWiring:
    """fetch_index validates PTR filings against the roster end-to-end."""

    @staticmethod
    def _zip_with_xml(xml: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("2025FD.xml", xml)
        return buf.getvalue()

    @staticmethod
    def _mock_urlopen(monkeypatch, payload: bytes):
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return payload

        def _open(request, timeout):  # noqa: ARG001
            return _Response()

        monkeypatch.setattr(house_clerk.urllib.request, "urlopen", _open)

    def test_attaches_bioguide_id_to_ptrs_and_leaves_non_ptrs_alone(self, monkeypatch):
        xml = (
            "<FinancialDisclosure>"
            "<Member><Last>Aderholt</Last><First>Robert B.</First><FilingType>P</FilingType>"
            "<StateDst>AL04</StateDst><Year>2025</Year><FilingDate>9/10/2025</FilingDate>"
            "<DocID>20032062</DocID></Member>"
            "<Member><Last>Radewagen</Last><First>Aumua Amata</First><FilingType>P</FilingType>"
            "<StateDst>AS00</StateDst><Year>2025</Year><FilingDate>6/1/2025</FilingDate>"
            "<DocID>20032063</DocID></Member>"
            "<Member><Last>Someone</Last><First>Else</First><FilingType>T</FilingType>"
            "<StateDst>AL04</StateDst><Year>2025</Year><FilingDate>9/10/2025</FilingDate>"
            "<DocID>10000001</DocID></Member>"
            "</FinancialDisclosure>"
        )
        self._mock_urlopen(monkeypatch, self._zip_with_xml(xml))
        source = HouseClerkSource(members=_house_members())
        filings = source.fetch_index(2025)
        by_doc = {f.doc_id: f for f in filings}
        assert by_doc["20032062"].bioguide_id == "A000055"
        assert by_doc["20032063"].bioguide_id == "R000600"
        assert by_doc["10000001"].bioguide_id is None
        assert by_doc["20032062"].state_district == "AL04"  # original preserved

    def test_post_service_ptr_of_uniquely_identified_member_resolves(self, monkeypatch):
        # Santos was expelled 2023-12-01 (the term end is inclusive). A PTR
        # filed the next day is a legitimate post-service late filing: the
        # date-anchored resolution finds no service, but the unique identity
        # admits it and preserves the member's bioguide_id.
        xml = (
            "<FinancialDisclosure>"
            "<Member><Last>Santos</Last><First>George</First><FilingType>P</FilingType>"
            "<StateDst>NY03</StateDst><Year>2023</Year><FilingDate>12/2/2023</FilingDate>"
            "<DocID>20032064</DocID></Member>"
            "</FinancialDisclosure>"
        )
        self._mock_urlopen(monkeypatch, self._zip_with_xml(xml))
        source = HouseClerkSource(members=_house_members())
        filings = source.fetch_index(2023)
        assert {f.doc_id: f for f in filings}["20032064"].bioguide_id == "S001222"

    def test_post_service_late_ptr_of_departed_member_resolves(self, monkeypatch):
        # McEachin died in office; his last term ended 2022-11-28. A PTR filed
        # after that date resolves to him because the identity is unique and
        # all of his service ended before the filing date.
        xml = (
            "<FinancialDisclosure>"
            "<Member><Last>McEachin</Last><First>A. Donald</First><FilingType>P</FilingType>"
            "<StateDst>VA04</StateDst><Year>2022</Year><FilingDate>12/5/2022</FilingDate>"
            "<DocID>20032065</DocID></Member>"
            "</FinancialDisclosure>"
        )
        self._mock_urlopen(monkeypatch, self._zip_with_xml(xml))
        source = HouseClerkSource(members=_house_members())
        filings = source.fetch_index(2022)
        assert {f.doc_id: f for f in filings}["20032065"].bioguide_id == "M001200"

    def test_post_service_fallback_rejects_ambiguous_identity(self, monkeypatch):
        # A PTR dated during the vacancy between the two Donald Paynes (NJ)
        # matches neither service interval; the unanchored fallback finds both
        # father and son, so the adapter must not guess between them.
        xml = (
            "<FinancialDisclosure>"
            "<Member><Last>Payne</Last><First>Donald</First><FilingType>P</FilingType>"
            "<StateDst>NJ10</StateDst><Year>2012</Year><FilingDate>6/1/2012</FilingDate>"
            "<DocID>20032066</DocID></Member>"
            "</FinancialDisclosure>"
        )
        self._mock_urlopen(monkeypatch, self._zip_with_xml(xml))
        source = HouseClerkSource(members=_house_members())
        with pytest.raises(HouseMemberResolveError):
            source.fetch_index(2012)

    def test_post_service_fallback_rejects_unknown_member(self, monkeypatch):
        xml = (
            "<FinancialDisclosure>"
            "<Member><Last>Nofinger</Last><First>Zed</First><FilingType>P</FilingType>"
            "<StateDst>AL04</StateDst><Year>2025</Year><FilingDate>9/10/2025</FilingDate>"
            "<DocID>20032067</DocID></Member>"
            "</FinancialDisclosure>"
        )
        self._mock_urlopen(monkeypatch, self._zip_with_xml(xml))
        source = HouseClerkSource(members=_house_members())
        with pytest.raises(HouseMemberResolveError):
            source.fetch_index(2025)

    def test_post_service_fallback_rejects_pre_service_filing(self, monkeypatch):
        # Patronis served from his sworn-in date 2025-04-02. A PTR dated the
        # day before is a pre-service filing: the unique identity matches, but
        # his service had not ended before the filing date, so it fails closed.
        xml = (
            "<FinancialDisclosure>"
            "<Member><Last>Patronis</Last><First>Jimmy</First><FilingType>P</FilingType>"
            "<StateDst>FL01</StateDst><Year>2025</Year><FilingDate>4/1/2025</FilingDate>"
            "<DocID>20032068</DocID></Member>"
            "</FinancialDisclosure>"
        )
        self._mock_urlopen(monkeypatch, self._zip_with_xml(xml))
        source = HouseClerkSource(members=_house_members())
        with pytest.raises(HouseMemberResolveError):
            source.fetch_index(2025)

    def test_ptr_without_usable_state_district_raises_index_error(self, monkeypatch):
        xml = (
            "<FinancialDisclosure>"
            "<Member><Last>Aderholt</Last><First>Robert B.</First><FilingType>P</FilingType>"
            "<StateDst></StateDst><Year>2025</Year><FilingDate>9/10/2025</FilingDate>"
            "<DocID>20032062</DocID></Member>"
            "</FinancialDisclosure>"
        )
        self._mock_urlopen(monkeypatch, self._zip_with_xml(xml))
        source = HouseClerkSource(members=_house_members())
        with pytest.raises(HouseIndexError):
            source.fetch_index(2025)


class TestClerkMemberDataLive:
    """Live cross-check of the packaged roster against the Clerk's official
    current-Congress MemberData.xml feed.

    The snapshot provenance documents the union dataset; this test re-anchors
    it against the official Clerk feed for the members whose names exercise
    the tricky match paths (initial/middlename given names, compound surnames,
    accents, public short forms). Each member's *filer-style* name (the form
    eFD shows) must resolve -- on today's date -- to the same bio guide id the
    Clerk feed reports for the same person. The test skips when the feed is
    unreachable/blocked, and ignores curated members no longer sitting in the
    current Congress (their service is historical and still covered).
    """

    URL = "https://clerk.house.gov/xml/lists/MemberData.xml"
    # (bioguide, state, eFD last name, eFD first name) -- the display form
    # under which that member actually files periodic transaction reports.
    TARGETS = [
        ("S001214", "FL", "Steube", "Greg"),
        ("K000389", "CA", "Khanna", "Rohit"),
        ("H001094", "OR", "Hoyle", "Valerie"),
        ("S001156", "CA", "Sánchez", "Linda"),
        ("M001232", "MD", "Delaney", "April McClain"),
        ("F000472", "FL", "Franklin", "Scott"),
        ("H001072", "AR", "Hill", "James French"),
        ("W000797", "FL", "Wasserman Schultz", "Debbie"),
        ("P000622", "FL", "Patronis", "Jimmy"),
        ("M001165", "CA", "McCarthy", "Kevin"),
    ]

    def test_filer_names_resolve_to_the_official_bioguide_ids(self):
        try:
            req = urllib.request.Request(
                self.URL, headers={"User-Agent": "Mozilla/5.0 (politician-dashboard/0.1.0; test)"}
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = resp.read()
        except Exception as exc:  # noqa: BLE001 - network/blockage → skip
            pytest.skip(f"Clerk MemberData feed unavailable: {exc}")

        root = ET.fromstring(data)
        clerk = {}
        for info in root.findall(".//member-info"):
            bioguide = (info.findtext("bioguideID") or "").strip()
            if bioguide in {target[0] for target in self.TARGETS}:
                state = info.find("state")
                clerk[bioguide] = {
                    "name": (info.findtext("official-name") or "").strip(),
                    "state": (state.get("postal-code") if state is not None else ""),
                }
        assert clerk, "Clerk MemberData feed contains none of the target members"

        members = load_default_house_members()
        by_id = {member.bioguide_id: member for member in members}
        today = date(2026, 9, 23)
        for bioguide, state, last, first in self.TARGETS:
            assert bioguide in by_id, f"{bioguide} missing from packaged snapshot"
            if bioguide not in clerk:
                # Not seated in the current Congress; history still in snapshot.
                continue
            resolved = resolve_house_member(
                state, last, first, anchor_date=today, members=members
            )
            assert resolved.bioguide_id == bioguide, (
                f"'{first} {last}' resolved to {resolved.bioguide_id} "
                f"(expected {bioguide}, Clerk lists {clerk[bioguide]})"
            )