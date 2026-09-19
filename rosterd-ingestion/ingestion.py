"""Ingestion service — repo + constraints.yaml in, agent manifest out.

Also handles the confirm gate and /ask/parse: a draft manifest governs nothing
until a human confirms it, and plain language becomes a proposed task.

Scope note: the revised brief also replaces constraints.yaml with inferred
ConstraintRules and moves storage to SpacetimeDB. Those are deliberately NOT
here yet — this build adds the two new endpoints on the existing architecture.
See docs/ADR-002-confirm-gate.md.
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl

from shared import GraphSpec, Priority


class ManifestStatus(str, Enum):
    """A draft manifest governs nothing. Only a confirmed one is live."""

    draft = "draft"
    confirmed = "confirmed"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


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
    status: ManifestStatus = ManifestStatus.draft
    agents: list[AgentManifestEntry]
    graph: GraphSpec


class ManifestResponse(IngestResponse):
    """Same shape as IngestResponse. Returned by GET /manifest/{manifest_id}."""


class ConfirmRequest(BaseModel):
    agents: list[AgentManifestEntry] = []  # possibly edited on the Review screen


class ConfirmResponse(BaseModel):
    manifest_id: str
    status: Literal["confirmed"] = "confirmed"


class ParsedTask(BaseModel):
    title: str
    description: str
    priority: Priority = Priority.medium
    expectation_criteria: list[str] = []


class AskRequest(BaseModel):
    manifest_id: str
    text: str


class AskResponse(BaseModel):
    agent_id: str
    task: ParsedTask
    confidence: Confidence
