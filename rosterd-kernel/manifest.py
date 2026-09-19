"""The confirmed-manifest shapes the kernel reads (Param's `AgentManifestEntry`,
per rosterd-param-ingestion.md / team-brief.md), plus a small in-process
index the rest of the kernel queries.

A note on drift, confirmed by actually running ingestion (not just reading
its code): its `POST /manifest/{id}/confirm` and `status: draft|confirmed`
now exist (manifest_source.py's ManifestSubscription enforces that trust
boundary), but `AgentManifestEntry.constraints` is still a free-form
`AgentConstraints` blob (`{max_refund_usd, requires_prior_node, ...}`), not
this file's `list[ConstraintRule]`, and there's still no `scaling` field at
all. This file implements the *finalized* brief shape, which is what
kernel.py's own contract and Param's own brief both describe.
`AgentManifestEntry`'s `constraints` validator below (see
legacy_constraints.py) adapts ingestion's real dict shape into
`ConstraintRule`s for the *known* legacy keys it can honestly translate; an
unrecognized dict key is skipped, not guessed at. See the README's "Notes
for the team" section for why this is a stopgap, not a fix to ingestion.
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from legacy_constraints import adapt_legacy_constraints, is_legacy_shape

from shared import GraphSpec


class ConstraintSource(str, Enum):
    schema = "schema"
    code = "code"
    interrupt = "interrupt"
    default = "default"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class ConstraintRule(BaseModel):
    """field is a dot/bracket path into a demo-agent InvokeResponse, e.g.
    'tool_calls[*].args.amount' (evaluate_rule in constraints.py resolves it)."""

    field: str
    op: Literal["lte", "gte", "eq", "in", "not_in"]
    value: float | str | list
    source: ConstraintSource
    confidence: Confidence


class ScalingPolicy(BaseModel):
    min_replicas: int = 1
    max_replicas: int = 1
    target_concurrency: int = 1
    scale_down_after_idle_seconds: int = 30


class ManifestStatus(str, Enum):
    draft = "draft"
    confirmed = "confirmed"


class AgentManifestEntry(BaseModel):
    id: str
    node: str
    purpose: str
    tools: list[str] = []
    direct_assignable: bool = False
    entry_only_via: list[str] = []
    constraints: list[ConstraintRule] = []
    scaling: ScalingPolicy = ScalingPolicy()

    @field_validator("constraints", mode="before")
    @classmethod
    def _adapt_legacy_constraints(cls, value: Any) -> Any:
        """Ingestion's real `constraints` is currently a dict (see the
        module docstring); a finalized-brief document already sends a
        list. Only the dict case is translated -- once ingestion moves to
        `list[ConstraintRule]` this validator simply never fires."""
        if is_legacy_shape(value):
            return adapt_legacy_constraints(value)
        return value


class ManifestDocument(BaseModel):
    """What a `manifests` row (or, today, ingestion's `GET /manifest/{id}`)
    looks like once decoded. `status` defaults to confirmed for a
    hand-built document (e.g. StaticManifestSource in tests) that has no
    reason to carry the field at all; ingestion's real responses always
    set it explicitly, and manifest_source.ManifestSubscription refuses to
    load anything that isn't `confirmed` -- see its module docstring."""

    manifest_id: str
    status: ManifestStatus = ManifestStatus.confirmed
    agents: list[AgentManifestEntry] = []
    graph: GraphSpec | None = None


class ManifestIndex:
    """Read-side cache of the confirmed manifest currently governing this
    site. Updated by manifest_source.ManifestSubscription on a background
    thread; read by dispatch, the scaler, and the debug `GET /manifest`
    endpoint from request-handling threads. All access goes through the
    lock, since FastAPI's sync endpoints each get their own thread.

    Indexed by `id` (the short handle -- 'fulfillment') because that is what
    DispatchRequest.agent_id and every other kernel.py shape calls
    `agent_id`, and it matches Joy's own AgentRow example
    (`agent_id='fulfillment'`). `node` (the exact LangGraph node name) is
    kept as a second index because ingestion's own README flags that `id`
    is a derived short form that *can* collide across nodes, and matching
    dispatch on `node` instead is one line to change here if that turns out
    to matter more than matching Joy's dashboard convention.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._manifest_id: str | None = None
        self._by_id: dict[str, AgentManifestEntry] = {}
        self._by_node: dict[str, AgentManifestEntry] = {}

    def update(self, document: ManifestDocument) -> None:
        with self._lock:
            self._manifest_id = document.manifest_id
            self._by_id = {agent.id: agent for agent in document.agents}
            self._by_node = {agent.node: agent for agent in document.agents}

    def is_loaded(self) -> bool:
        with self._lock:
            return self._manifest_id is not None

    @property
    def manifest_id(self) -> str | None:
        with self._lock:
            return self._manifest_id

    def get(self, agent_id: str) -> AgentManifestEntry | None:
        with self._lock:
            return self._by_id.get(agent_id)

    def get_by_node(self, node: str) -> AgentManifestEntry | None:
        with self._lock:
            return self._by_node.get(node)

    def agent_ids(self) -> list[str]:
        with self._lock:
            return list(self._by_id)

    def all_entries(self) -> list[AgentManifestEntry]:
        with self._lock:
            return list(self._by_id.values())
