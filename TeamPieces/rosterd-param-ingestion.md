# rosterd — Person 2: Ingestion service

## High-level idea (context)

rosterd takes an existing LangGraph agent system (a repo plus
`constraints.yaml`) and makes it safe to run, schedulable, and
federatable. Discovery turns the repo into a manifest, the kernel
enforces that manifest at runtime, the frontend schedules work
against it, and a coordinator federates results across sites.

## Your role

You own ingestion: the one-time step that turns a LangGraph repo plus
a constraints file into the structured manifest every other service
reads. This is the contract everyone else builds against, so changes
here ripple across the whole team.

```
frontend ──> ingestion-service ──> (manifest stored, read by kernel + frontend)
```

## Tasks

- `POST /ingest` — clone the given repo, load the compiled LangGraph
  graph, call `get_graph()` to extract nodes, edges, and each node's
  bound tools
- Parse `constraints.yaml`, validate node names in it against the
  discovered graph
- Merge graph structure and constraints into one `AgentManifestEntry`
  per agent (id, node, purpose, tools, `direct_assignable`,
  `entry_only_via`, constraints)
- `GET /manifest/{manifest_id}` — return a previously generated
  manifest without re-ingesting
- Decide and document manifest versioning (what happens if the repo
  changes and someone re-ingests)

## Dependencies

- Person 4's repo structure should exist (even a stub) early so you
  can test introspection against something real
- Your manifest shape is consumed by Person 1's kernel (constraint
  checks) and Person 3's frontend (Contracts screen), flag changes to
  both before merging

## API contract

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | Repo URL + constraints YAML in, manifest out |
| GET | `/manifest/{manifest_id}` | Re-fetch a manifest without re-ingesting |

### Shared types (`shared.py`, import this, don't redefine it)

```python
"""Shared types used across ingestion, kernel, demo-agent, and coordinator schemas."""

from datetime import datetime
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
```

### Your schema (`ingestion.py`)

```python
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
```

### Who reads your output

- Person 1's kernel loads the manifest to enforce `constraints` and
  `direct_assignable` / `entry_only_via` at dispatch time
- Person 3's frontend renders the manifest on the Contracts screen
  (tools, data access, spend limit, isolation) and the Roster screen
  (agent bubbles)
