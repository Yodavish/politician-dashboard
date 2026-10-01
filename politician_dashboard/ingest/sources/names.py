"""Shared bounded given-name matching helpers for source adapters.

Both the Senate eFD adapter (``senate_efd.py``) and the House Clerk adapter
(``house_clerk.py``) must reconcile the filer name a chamber portal displays
against official member rosters. The eFD/proxy display names are often the
fuller form the filer registered (e.g. ``A. Mitchell Jr.``, ``James E.``)
while official listings publish preferred primary given names (``Mitch``,
``Jim``). The helpers here implement one bounded, documented correspondence:
an official *primary* given token matches a filer token only when it equals it
exactly or is a standard English given-name relation enumerated in
:data:`_DIMINUTIVE_FORMS`, consulted in both directions. No string-prefix or
fuzzy matching is used, so ``dan`` is never treated as a match for ``Dana``.

This module is the single source of truth for the correspondence: the Senate
eFD adapter imports these helpers instead of carrying its own copy, so a
relation added here applies to both chambers and cannot drift between them.
"""

from __future__ import annotations

import re
import unicodedata

from . import legislator_names

# Generational suffixes that name the family line, not the person; they are
# dropped on both the display side and the official listing side before name
# comparison so e.g. "McConnell, A. Mitchell Jr." and the official "Mitch
# McConnell" compare on their given names alone. The vocabulary itself lives in
# :mod:`legislator_names`, which the snapshot generators also use, so a suffix
# can never be a name part in one component and a surname in another.
SUFFIX_TOKENS: frozenset[str] = legislator_names.SUFFIX_TOKENS

# Given-name relations that are NOT simple truncations of the formal name and
# therefore cannot be derived by the token-prefix rule alone. This is a general
# map of standard English given-name relations keyed by the formal token to the
# possible official primary tokens. Entries are generic nicknames/truncations
# -- never member-specific -- and bounded: matching is deliberately NOT a
# string-prefix test. The relation is consulted in both directions. A map entry
# only ever contributes a candidate together with an exact last-name anchor and
# an ambiguity check, so it can never attribute a filing to a member outside
# the same surname group.
_DIMINUTIVE_FORMS: dict[str, frozenset[str]] = {
    "james": frozenset({"jim"}),
    "william": frozenset({"bill", "billy", "will"}),
    "michael": frozenset({"mike"}),
    "robert": frozenset({"bob", "rob", "bobby", "robby"}),
    "charles": frozenset({"chuck", "charlie"}),
    "bernard": frozenset({"bernie"}),
    "bernardo": frozenset({"bernie"}),
    "richard": frozenset({"dick", "rich", "rick", "ricky"}),
    "andrew": frozenset({"andy", "drew"}),
    "stephen": frozenset({"steve", "steven"}),
    "steven": frozenset({"steve"}),
    "geoffrey": frozenset({"jeff"}),
    "jeffrey": frozenset({"jeff"}),
    "joseph": frozenset({"joe", "joey"}),
    "gerald": frozenset({"jerry"}),
    "thomas": frozenset({"tom", "thom", "tommy"}),
    "john": frozenset({"jack", "johnny"}),
    "jonathan": frozenset({"jon"}),
    "mitchell": frozenset({"mitch"}),
    "timothy": frozenset({"tim", "timmy"}),
    "christopher": frozenset({"chris"}),
    "joshua": frozenset({"josh"}),
    "daniel": frozenset({"dan", "danny"}),
    "ronald": frozenset({"ron", "ronnie"}),
    "peter": frozenset({"pete"}),
    "theodore": frozenset({"ted"}),
    "alexander": frozenset({"alex", "sandy"}),
    "samuel": frozenset({"sam", "sammy"}),
    "edward": frozenset({"ed", "eddie", "ted"}),
    "margaret": frozenset({"peg", "peggy", "maggie"}),
    "elizabeth": frozenset({"beth", "betty", "liz", "lizzie"}),
    "matthew": frozenset({"mat", "matt"}),
    "anthony": frozenset({"tony"}),
    "donald": frozenset({"don", "donnie"}),
    "david": frozenset({"dave"}),
    "randall": frozenset({"rand"}),
    "deborah": frozenset({"deb"}),
    "gregory": frozenset({"greg"}),
    "rohit": frozenset({"ro"}),
    "valerie": frozenset({"val", "valery"}),
    "patrick": frozenset({"pat"}),
    "cynthia": frozenset({"cindy"}),
    "kenneth": frozenset({"ken"}),
}


# Typographic apostrophe variants that must compare equal to the ASCII
# apostrophe. Neither NFKD nor the combining-mark filter rewrites them, so the
# two spellings would otherwise never compare equal: a chamber index writes
# "O'Halleran" (U+0027) while the reference roster writes the same name with
# U+2019. Folding them in the transliteration pass keeps one normalization
# path and reduces both sides to the same ASCII vocabulary.
_APOSTROPHE_VARIANTS = {
    "‘": "'",  # LEFT SINGLE QUOTATION MARK
    "’": "'",  # RIGHT SINGLE QUOTATION MARK
    "ʼ": "'",  # MODIFIER LETTER APOSTROPHE
    "＇": "'",  # FULLWIDTH APOSTROPHE
}


def _transliterate(value: str) -> str:
    """ASCII-fold a name: strip diacritics and fold apostrophe variants.

    The chamber eFD portals strip accents from last names (``Sánchez`` in the
    member roster resolves against a filing filed under ``Sanchez``), so both
    sides are reduced to the same ASCII vocabulary before matching. The portals
    and the reference rosters likewise disagree on which apostrophe codepoint
    spells a name, so :data:`_APOSTROPHE_VARIANTS` is folded onto the ASCII
    apostrophe in the same pass.
    """
    folded: list[str] = []
    for char in unicodedata.normalize("NFKD", value):
        if unicodedata.combining(char):
            continue
        folded.append(_APOSTROPHE_VARIANTS.get(char, char))
    return "".join(folded)


def normalize_name(value: str) -> str:
    """Collapse whitespace, lowercase and ASCII-fold for exact surname matching."""
    return re.sub(r"\s+", " ", _transliterate(value).strip().lower()).strip()


def given_name_tokens(value: str) -> set[str]:
    """Significant given-name tokens of a name.

    Tokens are lowercased, stripped of punctuation, and generational suffixes
    are removed so both the display name and the official first-name field are
    reduced to the same vocabulary. Middle initials and middle names are kept:
    they carry distinguishing evidence and are harmless to the
    primary-given-name comparison below.
    """
    tokens: set[str] = set()
    for token in _transliterate(value).strip().lower().split():
        token = token.strip(".,'\u2019")
        if not token or token in SUFFIX_TOKENS:
            continue
        tokens.add(token)
    return tokens


def primary_given_token(official_first: str) -> str:
    """First (given) token of an official first-name field."""
    token = normalize_name(official_first).split(" ", 1)[0]
    return token.strip(".,'\u2019")


def given_names_agree(
    filer_given: str, official_first: str, official_given: str | None = None
) -> bool:
    """Whether a filer's displayed given name matches a member's official name.

    Matching is deliberately bounded. The official member's given-name evidence
    is the *union* of the two official fields: ``official_given`` (the full
    given-name text an official listing publishes) and ``official_first`` (the
    formal first-name field). A filer given-name token matches an official one
    when it (a) equals it exactly, or (b) is a known standard given-name
    relation of it or vice versa (:data:`_DIMINUTIVE_FORMS`). No generic
    string-prefix test is applied, so ``dan`` never matches ``Dana``.

    Both official fields are consulted because each can be the abbreviated one
    and neither is authoritative on its own: a record whose official form is
    "Pat Fallon" has ``official_given`` "Pat" but ``official_first``
    "Patrick", and a filing recorded under "Patrick Fallon" matches only the
    latter. Preferring one field with ``official_given or official_first``
    discarded the other and made such a filing unresolvable. Taking the union
    adds evidence but never relaxes the surname anchor or the ambiguity check
    that guard attribution.

    Single-letter tokens (dangling initials such as ``C.``/``J.`` in
    "C. Scott Franklin") are ignored on the official side: a filer who uses
    only an initial cannot be attributed to a member whose real given name is
    elsewhere in the official full name, while a filer using the member's
    actual given name (which the source may list as a middle name) still
    matches. When the official given name consists solely of initials the
    stricter rule would admit nothing, so those are still compared to avoid an
    unreachable match.
    """
    filer_tokens = given_name_tokens(filer_given)
    if not filer_tokens:
        return False
    official_tokens = given_name_tokens(official_given or "") | given_name_tokens(
        official_first
    )
    if not official_tokens:
        return False
    meaningful = {token for token in official_tokens if len(token) > 1}
    if not meaningful:
        meaningful = official_tokens
    for token in filer_tokens:
        if token in meaningful:
            return True
        for official in meaningful:
            if official in _DIMINUTIVE_FORMS.get(token, ()):
                return True
            if token in _DIMINUTIVE_FORMS.get(official, ()):
                return True
    return False