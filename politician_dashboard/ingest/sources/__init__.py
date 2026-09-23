"""Ingestion source adapters and their default transports.

``_senate_urllib_session`` is the default cookie-persisting urllib transport
used by :class:`~politician_dashboard.ingest.sources.senate_efd.SenateEfdSource`
when no ``SenateHttpClient`` transport is injected. It is exposed here so the
source constructor can resolve it by name without importing the adapter
module (`senate_efd` imports ``sources.base`` directly, so this re-export
introduces no import cycle).
"""

from __future__ import annotations

from politician_dashboard.ingest.sources.senate_efd import (
    _UrllibSession as _senate_urllib_session,
)

__all__ = ["_senate_urllib_session"]
