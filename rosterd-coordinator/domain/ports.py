"""The interfaces application code depends on, never a concrete adapter.

Extracted out of adapters/postgres/postgres.py, which used to colocate
this Protocol with its own implementations -- application code type-hints
against this file so adapters stay swappable without application/domain
code ever importing from adapters/ (that would invert the hexagon). Named
for what it does, not what it talks to -- this used to be `SpacetimeWriter`
when the only adapter was SpacetimeDB-backed; the port itself never should
have been named after its one implementation.
"""
from typing import Protocol

from domain.coordinator import EventLogEntry, SiteSummary


class StateWriter(Protocol):
    def write_site(self, row: SiteSummary) -> None: ...

    def write_event(self, row: EventLogEntry) -> None: ...
