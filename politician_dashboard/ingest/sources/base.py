"""Common interface for disclosure sources.

The House Clerk source is the V1 implementation. A Senate source can be
added later by subclassing :class:`DisclosureSource`; the shared
:func:`select_ptrs` filter keeps the PTR discovery semantics identical
across chambers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from politician_dashboard.ingest.models import Filing

PTR_FILING_TYPE = "P"


def select_ptrs(filings: list[Filing]) -> list[Filing]:
    """Keep only filings that are Periodic Transaction Reports."""
    return [f for f in filings if f.filing_type == PTR_FILING_TYPE]


class DisclosureSource(ABC):
    """A source of financial-disclosure filings indexed by calendar year."""

    name: str = "unnamed"

    @abstractmethod
    def fetch_index(self, year: int) -> list[Filing]:
        """Fetch and parse the complete filing index for ``year``."""

    def fetch_ptrs(self, year: int) -> list[Filing]:
        """Fetch the index for ``year`` and keep only PTR filings."""
        return select_ptrs(self.fetch_index(year))