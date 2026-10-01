"""Tests for the shared name-matching helpers.

``names`` is the one normalization path both source adapters rely on to reduce
a chamber portal's spelling of a member's name and a reference roster's
spelling of the same name to a common vocabulary. These tests pin that
vocabulary: the diacritic folding it was built for, the apostrophe variants it
additionally folds, and the ordinary ASCII names it must leave alone.
"""

from __future__ import annotations

import pytest

from politician_dashboard.ingest.sources import (
    house_members_refresh,
    legislator_names,
    names,
    senate_members_refresh,
)


class TestNormalizeName:
    """``normalize_name`` is the exact-equality key used for surname matching."""

    def test_accented_surname_is_folded_to_ascii(self):
        # The eFD portal files "Sanchez" while the roster carries "Sánchez".
        assert names.normalize_name("Sánchez") == names.normalize_name("Sanchez")
        assert names.normalize_name("Sánchez") == "sanchez"

    def test_curly_apostrophe_folds_to_ascii_apostrophe(self):
        # A chamber index writes "O'Halleran" (U+0027) while the reference
        # roster writes the same surname with U+2019. Neither NFKD nor the
        # combining-mark filter rewrites these, so without an explicit fold the
        # two spellings could never compare equal.
        assert names.normalize_name("O’Halleran") == names.normalize_name(
            "O'Halleran"
        )
        assert names.normalize_name("O’Halleran") == "o'halleran"

    def test_all_folded_apostrophe_variants_agree(self):
        # U+2018 LEFT SINGLE QUOTATION MARK, U+2019 RIGHT SINGLE QUOTATION
        # MARK, U+02BC MODIFIER LETTER APOSTROPHE and U+FF07 FULLWIDTH
        # APOSTROPHE are the variants seen across the sources.
        for variant in ("‘", "’", "ʼ", "＇"):
            assert names.normalize_name(f"D{variant}Esposito") == names.normalize_name(
                "D'Esposito"
            )

    def test_real_world_apostrophe_surnames_fold_together(self):
        # Each pair is one surname spelled two ways across a source boundary.
        assert names.normalize_name("O’Rourke") == names.normalize_name("O'Rourke")
        assert names.normalize_name("D’Esposito") == names.normalize_name("D'Esposito")
        assert names.normalize_name("O’Halleran") == names.normalize_name("O'Halleran")

    def test_ordinary_ascii_names_are_unchanged(self):
        # The fold must not disturb names that are already plain ASCII.
        for value in (
            "Luna",
            "Wasserman Schultz",
            "Van Duyne",
            "St Mosley",
            "Paulina Luna",
            "O'Brien",
            "Sanchez",
        ):
            assert names.normalize_name(value) == value.lower()

    def test_folding_is_combined_with_accent_and_case_folding(self):
        # Transliteration still applies to a name that also carries an
        # apostrophe and a diacritic.
        assert names.normalize_name("Núñez O’Brien") == names.normalize_name(
            "Nunez O'Brien"
        )

    def test_compound_surname_spacing_is_preserved(self):
        assert names.normalize_name("Wasserman   Schultz") == "wasserman schultz"


class TestGivenNameTokens:
    """Given-name tokenisation shares the same transliteration pass."""

    def test_apostrophe_variants_tokenise_identically(self):
        assert names.given_name_tokens("O’Brien") == names.given_name_tokens(
            "O'Brien"
        )

    def test_accents_are_still_stripped_from_tokens(self):
        assert names.given_name_tokens("José") == names.given_name_tokens("Jose")


class TestSuffixTokens:
    """The generational-suffix vocabulary shared by both chambers."""

    def test_suffixes_are_recognised(self):
        for token in ("Jr.", "Sr.", "II", "iii", "IV", "V", "Junior", "Senior"):
            assert legislator_names.is_suffix_token(token)

    def test_real_name_parts_are_not_suffixes(self):
        # Whole-token comparison, never a prefix: a surname or given name that
        # merely starts with a suffix is a real name part.
        for token in ("Ivan", "Ivanovich", "Seniorita", "Vale", "Virgil"):
            assert not legislator_names.is_suffix_token(token)

    def test_names_module_and_shared_module_agree(self):
        assert names.SUFFIX_TOKENS == legislator_names.SUFFIX_TOKENS


class TestSurnameFromOfficialFull:
    """Which text of a raw record is the person's surname.

    Both snapshots are generated from the same community dataset, so this
    derivation has to be right in one place only.
    """

    @staticmethod
    def _record(last, official_full, first="Given", **extra):
        return {
            "id": {"bioguide": "X000000"},
            "name": {"first": first, "last": last, "official_full": official_full,
                     **extra},
        }

    @pytest.mark.parametrize(
        ("last", "official_full", "expected"),
        [
            # The defect: a comma-separated generational suffix is not a
            # surname, and must not become one.
            ("Manchin", "Joe Manchin, III", "Manchin"),
            ("Rockefeller", "John D. Rockefeller, IV", "Rockefeller"),
            ("King", "Angus S. King Jr.", "King"),
            ("Casey", "Robert P. Casey, Jr.", "Casey"),
            # A compound surname survives intact.
            ("Van Hollen", "Chris Van Hollen", "Van Hollen"),
            ("Wasserman Schultz", "Maxine Waters", "Waters"),
            # Source artifact in name.last: the official name is authoritative.
            ("Graham Nordone", "Darline Graham", "Graham"),
            # Ordinary records keep the roster surname.
            ("McConnell", "Mitch McConnell", "McConnell"),
            ("Fallon", "Pat Fallon", "Fallon"),
        ],
    )
    def test_surname_derivation(self, last, official_full, expected):
        assert legislator_names.surname_from_official_full(
            self._record(last, official_full)
        ) == expected

    def test_missing_fields_are_not_invented(self):
        # No official_full: the roster surname is all there is.
        assert legislator_names.surname_from_official_full(
            self._record("Public", "")
        ) == "Public"
        # No roster surname and no way to attribute one confidently: nothing is
        # invented. The snapshot generator then rejects the record outright
        # rather than storing a guess.
        assert legislator_names.surname_from_official_full(
            self._record("", "John D. Rockefeller, IV")
        ) == ""
        # An official_full that is only a suffix leaves the roster surname alone
        # rather than storing the suffix.
        assert legislator_names.surname_from_official_full(
            self._record("Manchin", "III")
        ) == "Manchin"

    def test_both_chambers_derive_the_same_surname(self):
        records = [
            self._record("Manchin", "Joe Manchin, III", first="Joe"),
            self._record("Rockefeller", "John D. Rockefeller, IV", first="John"),
            self._record("Graham Nordone", "Darline Graham", first="Darline"),
            self._record("Van Hollen", "Chris Van Hollen", first="Chris"),
        ]
        for record in records:
            assert house_members_refresh._surname_from_official_full(
                record
            ) == legislator_names.surname_from_official_full(record)
            assert senate_members_refresh._surname_from_official_full(
                record
            ) == legislator_names.surname_from_official_full(record)


class TestOfficialGivenTokens:
    """The official given-name text a filing is matched against."""

    @staticmethod
    def _record(name):
        return {"id": {"bioguide": "X000000"}, "name": name}

    def test_registered_display_name_is_preferred_over_the_formal_name(self):
        # The portal files under the registered form.
        assert legislator_names.official_given_name(
            self._record(
                {"first": "James David", "last": "Vance",
                 "official_full": "J.D. Vance"}
            )
        ) == "J.D."
        assert legislator_names.official_given_name(
            self._record(
                {"first": "Gregg", "last": "Steube",
                 "official_full": "Greg Steube"}
            )
        ) == "Greg"

    def test_surname_and_suffixes_are_removed_from_the_given_names(self):
        assert legislator_names.official_given_tokens(
            self._record(
                {"first": "John", "last": "Rockefeller",
                 "official_full": "John D. Rockefeller, IV"}
            )
        ) == ["John", "D."]
        # A compound surname is removed as a whole, not token by token.
        assert legislator_names.official_given_tokens(
            self._record(
                {"first": "April", "last": "McClain Delaney",
                 "official_full": "April McClain Delaney"}
            )
        ) == ["April"]

    def test_first_and_middle_are_the_fallback(self):
        # No official_full, and an optional middle the registered name may not
        # contain.
        assert legislator_names.official_given_tokens(
            self._record({"first": "April", "middle": "Lynn", "last": "Delaney"})
        ) == ["April", "Lynn"]
        assert legislator_names.official_given_tokens(
            self._record({"first": "April", "last": "Delaney"})
        ) == ["April"]
        # An official_full that yields nothing usable falls back too.
        assert legislator_names.official_given_tokens(
            self._record(
                {"first": "April", "middle": "Lynn", "last": "Delaney",
                 "official_full": "Delaney"}
            )
        ) == ["April", "Lynn"]
        assert legislator_names.official_given_tokens(
            self._record({"last": "Delaney", "official_full": "Delaney"})
        ) == []

    def test_both_chambers_derive_the_same_given_names(self):
        record = self._record(
            {"first": "James David", "last": "Vance",
             "official_full": "J.D. Vance"}
        )
        assert house_members_refresh._official_given_tokens(
            record
        ) == legislator_names.official_given_tokens(record)


class TestInitialAndMeaningfulTokens:
    def test_initial_tokens(self):
        for token in ("B.", "J", "J. J.", "D"):
            assert legislator_names.is_initial_token(token)
        for token in ("Bob", "Jo", "Dwan"):
            assert not legislator_names.is_initial_token(token)
        # Dots separate initials, so this is still two initials.
        assert legislator_names.is_initial_token("J.J.")

    def test_meaningful_tokens_drop_suffixes_only(self):
        # Only the suffix is dropped; the remaining tokens are returned as
        # written in the source, which is what the filer-alternate derivation
        # compares against the roster surname.
        assert legislator_names.meaningful_name_tokens(
            "John D. Rockefeller IV"
        ) == ["John", "D.", "Rockefeller"]
        assert legislator_names.meaningful_name_tokens("III") == []
        assert legislator_names.meaningful_name_tokens("") == []
