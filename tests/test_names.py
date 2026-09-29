"""Tests for the shared name-matching helpers.

``names`` is the one normalization path both source adapters rely on to reduce
a chamber portal's spelling of a member's name and a reference roster's
spelling of the same name to a common vocabulary. These tests pin that
vocabulary: the diacritic folding it was built for, the apostrophe variants it
additionally folds, and the ordinary ASCII names it must leave alone.
"""

from __future__ import annotations

from politician_dashboard.ingest.sources import names


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
