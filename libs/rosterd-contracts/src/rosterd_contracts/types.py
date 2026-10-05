"""Shared wire-contract types used across rosterd-ingestion, rosterd-kernel,
and rosterd-coordinator.

The single source of truth for these shapes -- see this package's own
README for why this is a real installed package rather than the
hand-duplicated `shared.py` copies it replaces. The *shape* has to stay
identical across every service or status enums drift apart between them;
a version bump here is how that's now enforced instead of hoped for.
"""
from enum import Enum

from pydantic import BaseModel


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RunStatus(str, Enum):
    working = "working"
    done = "done"
    killed = "killed"
    #: Interrupted for human/reviewer approval (langgraph.types.interrupt()).
    #: Distinct from `done` -- a paused run has no tool_calls yet and nothing
    #: has actually happened; see rosterd-kernel/dispatch.py's resume path.
    paused = "paused"


class SiteStatus(str, Enum):
    healthy = "healthy"
    violation = "violation"
    offline = "offline"


class Violation(BaseModel):
    rule: str
    expected: str
    actual: str


class GraphEdge(BaseModel):
    source: str
    target: str
    condition: str | None = None


class GraphSpec(BaseModel):
    nodes: list[str]
    edges: list[GraphEdge]
