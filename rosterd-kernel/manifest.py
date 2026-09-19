"""The confirmed-manifest shapes the kernel reads (Param's `AgentManifestEntry`,
per rosterd-param-ingestion.md / team-brief.md), plus a small in-process
index the rest of the kernel queries.

A note on drift: as of this writing, the `ingestion.py` actually committed in
`rosterd-ingestion/` predates the finalized brief -- its `AgentManifestEntry`
carries a free-form `AgentConstraints` blob and no `scaling` field at all,
and there is no `POST /manifest/{id}/confirm` endpoint yet. This file
implements the *finalized* brief shape (`constraints: list[ConstraintRule]`,
`scaling: ScalingPolicy`, `status: ManifestStatus`), which is what kernel.py's
own contract and Param's own brief both describe. `manifest_source.py`
validates whatever ingestion actually returns against this shape and fails
loudly (not silently) if the two have not converged yet -- see its docstring
and the README's "Notes for the team" section.
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Literal

from pydantic import BaseModel

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
