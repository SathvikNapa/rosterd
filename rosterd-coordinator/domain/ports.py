"""The interfaces application code depends on, never a concrete adapter.

Extracted out of adapters/spacetime/spacetime.py, which used to colocate
this Protocol with its own implementations -- application code type-hints
against this file so adapters stay swappable without application/domain
code ever importing from adapters/ (that would invert the hexagon).
"""
from typing import Protocol

from domain.coordinator import EventLogEntry, SiteSummary


class SpacetimeWriter(Protocol):
    def write_site(self, row: SiteSummary) -> None: ...

    def write_event(self, row: EventLogEntry) -> None: ...
