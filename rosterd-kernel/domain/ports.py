"""The interfaces application code depends on, never a concrete adapter.

Extracted out of the three files that used to colocate each Protocol with
its own implementations -- application code type-hints against this file
so adapters stay swappable without application/domain code ever importing
from adapters/ (that would invert the hexagon). Same pattern
rosterd-coordinator's domain/ports.py already established; this is the
richer case, with three ports instead of one.

`AgentRow`/`AgentMetricsRow` live here, not in a separate file, because
they're tightly coupled to `StateWriter`'s own signature -- unlike
rosterd-coordinator's `SiteSummary`/`EventLogEntry`, which are also real
HTTP wire-contract types reused elsewhere, these two are pure Postgres-row
shapes with no life outside this port.
"""
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from domain.kernel import AgentInstance
from domain.manifest import ManifestDocument


class DockerBackend(Protocol):
    def start_instance(self, agent_id: str) -> AgentInstance: ...

    def kill_instance(self, instance: AgentInstance) -> None: ...

    def invoke_base_url(self, instance: AgentInstance) -> str: ...


class ManifestSource(Protocol):
    def fetch(self) -> ManifestDocument | None: ...


class AgentRow(BaseModel):
    """One row per running instance."""

    site_id: str
    agent_id: str
    instance_id: str
    name: str
    status: str  # "idle" | "working" | "killed"
    updated_at: datetime


class AgentMetricsRow(BaseModel):
    """Written every scaler tick, not just on change."""

    site_id: str
    agent_id: str
    timestamp: datetime
    in_flight_count: int
    queued_count: int
    target_concurrency: int
    current_replicas: int
    desired_replicas: int
    min_replicas: int
    max_replicas: int


class StateWriter(Protocol):
    def write_agent(self, row: AgentRow) -> None: ...

    def write_agent_metrics(self, row: AgentMetricsRow) -> None: ...
