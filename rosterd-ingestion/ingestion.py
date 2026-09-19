"""Ingestion service — repo + constraints.yaml in, agent manifest out."""
from pydantic import BaseModel, ConfigDict, HttpUrl

from shared import GraphSpec


class AgentConstraints(BaseModel):
    """Deterministic rules the kernel checks at runtime. Extra keys allowed
    since different agents can declare different constraint shapes."""

    model_config = ConfigDict(extra="allow")

    max_refund_usd: float | None = None
    requires_prior_node: str | None = None


class AgentManifestEntry(BaseModel):
    id: str
    node: str
    purpose: str
    tools: list[str] = []
    direct_assignable: bool = False
    entry_only_via: list[str] = []
    constraints: AgentConstraints = AgentConstraints()


class IngestRequest(BaseModel):
    repo_url: HttpUrl
    constraints_yaml: str


class IngestResponse(BaseModel):
    manifest_id: str
    agents: list[AgentManifestEntry]
    graph: GraphSpec


class ManifestResponse(IngestResponse):
    """Same shape as IngestResponse. Returned by GET /manifest/{manifest_id}."""
