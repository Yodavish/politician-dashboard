"""Shared derivation of official identity fields from congress-legislators records.

Both reference snapshots are generated from the same community dataset
(``unitedstates/congress-legislators``) by
:mod:`politician_dashboard.ingest.sources.house_members_refresh` and
:mod:`politician_dashboard.ingest.sources.senate_members_refresh`. Both need the
same two derivations from a raw record's ``name`` block, and both must answer
them identically or the two chambers would disagree about who a person is:

* :func:`surname_from_official_full` -- which text is the person's surname.
* :func:`official_given_name` -- the full given-name text an official listing
  publishes (used as the match anchor by the resolvers).

Sharing them is what prevents a chamber from shipping its own answer to the
same question. The derivations are deliberately conservative: they only ever
prefer ``name.last`` over a derived token, and they never invent a name part
that is not present in the record.

Why ``official_full`` is consulted at all: the dataset's ``name.last`` is
authoritative except for artifacts (a candidate's own surname artifacts, or a
``official_full`` that carries a generational suffix after a comma, such as
``"Joe Manchin, III"`` / ``"John D. Rockefeller, IV"``). A naive
``official_full.endswith(last)`` test fails on exactly those records and the
fallback then stores the *suffix* as the surname, which makes a sitting member
invisible to the resolver. :func:`surname_from_official_full` therefore matches
``name.last`` as a contiguous phrase and only falls back to a derived token
when the phrase is genuinely absent, and a suffix-only token is never accepted
as a surname.
"""

from __future__ import annotations

import re

# Generational suffixes that name the family line, not the person. Dropped from
# both sides of a name comparison so e.g. "McConnell, A. Mitchell Jr." and the
# official "Mitch McConnell" compare on their given names alone, and so
# "Joe Manchin, III" yields the surname "Manchin".
SUFFIX_TOKENS: frozenset[str] = frozenset(
    {"jr", "sr", "junior", "senior", "ii", "iii", "iv", "v"}
)

# Every alphabetic run in a token must be a single letter for it to count as
# initials ("B.", "J", "J. J."). Uses a Unicode-aware alphabetic-run pattern so
# accented letters are not treated as digits/punctuation.
_NAME_RUN_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)

# Characters stripped from a name token before comparing it to SUFFIX_TOKENS
# or to another token. Covers the typographic apostrophe the chamber indexes use.
_TOKEN_TRIM = ".,'\u2019"


def normalized_token(token: str) -> str:
    """A single name token reduced to its comparison form."""
    return token.strip(_TOKEN_TRIM).lower()


def is_suffix_token(token: str) -> bool:
    """Whether a name token is a generational suffix rather than a name part.

    ``"III"`` and ``"Jr."`` are suffixes; ``"Ivan"`` and ``"Seniorita"`` are
    not, because the comparison is on the whole stripped token rather than a
    prefix.
    """
    return normalized_token(token) in SUFFIX_TOKENS


def surname_from_official_full(record: dict) -> str:
    """Best-effort official surname from a congress-legislators record.

    ``name.last`` is authoritative whenever it appears as a contiguous phrase of
    ``official_full`` without a real name following it -- the trailing ``"Jr."``
    of "Donald M. Payne, Jr." is a generational suffix, not a sibling name, and
    the trailing ``"III"`` of "Joe Manchin, III" is likewise not a surname.
    Compound surnames ("Wasserman Schultz") survive because the match is on the
    whole phrase, not a single token.

    Only when the phrase is genuinely absent is a token derived instead, and
    that token is never a bare generational suffix: an ``official_full`` that is
    nothing but a suffix (``"III"``) leaves ``name.last`` untouched. This is
    what keeps a suffix from ever becoming a person's surname.
    """
    name = record.get("name") or {}
    last = (name.get("last") or "").strip()
    official_full = (name.get("official_full") or "").strip()
    if not last or not official_full:
        return last
    tokens = [token for token in official_full.split() if token]
    phrase = last.split()
    positions: list[int] = []
    for index in range(len(tokens) - len(phrase) + 1):
        if all(
            normalized_token(tokens[index + offset]) == normalized_token(part)
            for offset, part in enumerate(phrase)
        ):
            positions.append(index)
    if positions:
        trailing = tokens[positions[0] + len(phrase):]
        if all(is_suffix_token(token) for token in trailing):
            return last
    meaningful = [token for token in tokens if not is_suffix_token(token)]
    if meaningful:
        return meaningful[-1]
    return last


def official_given_tokens(record: dict) -> list[str]:
    """Official given-name tokens of a record, in source order.

    Preferred derivation is the ``official_full`` name minus its surname and
    generational-suffix tokens ("April McClain Delaney" with the compound
    surname "McClain Delaney" leaves "April"; "Linda T. Sánchez" leaves "Linda
    T."). Using ``official_full`` is what preserves the form a chamber portal
    actually displays -- a registered nickname such as "J.D." for a record
    whose ``name.first`` is the formal "James David" -- because both portals
    file under that display form. The union record's optional ``middle`` field
    is not part of the registered name ("April Lynn" vs the official "April
    McClain"), so ``first`` + ``middle`` is only a fallback when
    ``official_full`` is empty or yields nothing.
    """
    name = record.get("name") or {}
    official_full = (name.get("official_full") or "").strip()
    if official_full:
        surname = (name.get("last") or "").strip()
        surname_tokens = {normalized_token(token) for token in surname.split()}
        given_tokens = []
        for token in official_full.split():
            cleaned = normalized_token(token)
            if not cleaned or cleaned in SUFFIX_TOKENS:
                continue
            if cleaned in surname_tokens:
                continue
            given_tokens.append(token)
        if given_tokens:
            return given_tokens
    first = (name.get("first") or "").strip()
    middle = (name.get("middle") or "").strip()
    if not first:
        return []
    if not middle:
        return [first]
    return [first, middle]


def official_given_name(record: dict) -> str:
    """Official full given-name text of a record.

    See :func:`official_given_tokens` for the derivation.
    """
    return " ".join(official_given_tokens(record))


def is_initial_token(token: str) -> bool:
    """Whether a name token is only initials, e.g. ``"B."``, ``"J"``, ``"J. J."``.

    Every alphabetic run in the token must be a single letter, so a real
    multi-letter name part never qualifies. A compound-initial middle name
    ("Eric J. J. Massa" records the middle as ``"J. J."``) is one token that
    must be recognised as initials, not as a full name.
    """
    runs = _NAME_RUN_PATTERN.findall(token)
    return bool(runs) and all(len(run) == 1 for run in runs)


def meaningful_name_tokens(text: str) -> list[str]:
    """The tokens of ``text`` that are name parts rather than suffixes.

    Used where a derivation needs "the last token that is an actual name" -- for
    example a filer's alternate spelling of a compound surname ("McClain
    Delaney" filed as "Delaney") -- so that decision is made against the same
    suffix vocabulary as the surname derivation instead of a second copy of it.
    """
    return [token for token in text.split() if token and not is_suffix_token(token)]
