# rosterd — Person 2: Ingestion service

## High-level idea (context)

rosterd takes an existing LangGraph agent system and makes it safe to
run, schedulable in plain language, and federatable, without a manual
config file. Discovery reads the repo directly, a human confirms what
was inferred, the kernel enforces the confirmed contract at runtime.

## Your role

You own understanding, twice over: turning a repo into a structured,
confirmed manifest (ingestion), and turning a plain-language request
into a structured, confirmable task (`/ask/parse`). Both are the same
underlying move, unstructured input in, a human-gated structured
action out, which is the direct tie to the Auctor track.

```
frontend ──> POST /ingest ──> SpacetimeDB `manifests` (status: draft)
frontend ──> POST /manifest/{id}/confirm ──> status: confirmed ──> kernels subscribe
frontend ──> POST /ask/parse ──> proposed {agent_id, task, criteria} ──> frontend shows card
```

## Design: inference instead of a config file

No `constraints.yaml`. For each node discovered in the graph, pull
constraints from three sources, in order of confidence:

1. **Tool schema** (`source: "schema"`, confidence `high`): a
   LangChain tool's Pydantic `args_schema` field with `Field(le=100)`
   or similar becomes a `ConstraintRule` directly. This is the
   primary signal, and it's why Person 4's tool schemas matter, they
   ARE the contract now.
2. **Code guard** (`source: "code"`, confidence `medium`): AST-scan
   the node function body for a comparison against a threshold
   followed by a raise or an escalate-style return
   (`if amount > X: ...`). Best-effort, flagged as lower confidence
   on the Review screen so a human actually looks at it.
3. **`interrupt()` call** (`source: "interrupt"`, confidence `high`):
   presence of LangGraph's `interrupt()` in a node body marks it
   `direct_assignable: false`, and its predecessors in the graph
   become its `entry_only_via` list.

Anything not covered gets `source: "default"`, `confidence: "low"`,
this is the case the Review screen should make hardest to miss.

## Design: confirm gate

Ingestion writes every manifest as `status: draft`. Kernels only
subscribe to `status: confirmed` rows, a draft manifest governs
nothing. `POST /manifest/{id}/confirm` takes the (possibly edited)
agent list from the Review screen and flips the status. This is the
actual trust boundary: inference can be wrong, confirmation is a
deliberate human act before anything live depends on it.

## Tasks

- `POST /ingest` — clone the repo, load the compiled LangGraph graph
  via `get_graph()`, run the three-source inference above per node
- Assemble one `AgentManifestEntry` per agent (id, node, purpose from
  docstring, tools, `direct_assignable`, `entry_only_via`, constraint
  rules with source/confidence, scaling policy)
- Write the manifest to SpacetimeDB's `manifests` table, `status:
  draft`
- `POST /manifest/{manifest_id}/confirm` — write `status: confirmed`
  with the (possibly edited) agent list
- `POST /ask/parse` — given `{manifest_id, text}`, use the confirmed
  manifest's agent list and tool descriptions to parse plain language
  into `{agent_id, task, criteria, confidence}`
- Decide manifest versioning: new `manifest_id` per re-ingest/re-confirm
  (safer for demoing a live update) or overwrite in place

## Dependencies

- Person 4's repo needs real, typed tool schemas and at least one
  `interrupt()` call early, that's what your inference has to work
  with, test against it as soon as it exists even as a stub
- Person 1's kernel and Person 3's Contracts/Review screens both
  consume your manifest shape, flag changes before merging
- Person 3 owns the SpacetimeDB module setup, agree on the
  `manifests` table schema together early

## API contract

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | Repo URL in, draft manifest written to SpacetimeDB |
| POST | `/manifest/{manifest_id}/confirm` | Confirm (with edits) a draft manifest, makes it live |
| POST | `/ask/parse` | Plain text in, proposed task out |

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
"""Ingestion service — repo in, confirmed manifest out via SpacetimeDB.
Also handles /ask/parse: plain text in, a proposed task out."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, HttpUrl

from shared import GraphSpec, Priority


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
    """field is a dot/bracket path into InvokeResponse, e.g.
    'tool_calls[0].args.amount'."""

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


class IngestRequest(BaseModel):
    repo_url: HttpUrl


class IngestResponse(BaseModel):
    manifest_id: str
    status: ManifestStatus
    agents: list[AgentManifestEntry]
    graph: GraphSpec


class ConfirmRequest(BaseModel):
    agents: list[AgentManifestEntry]  # possibly edited on the Review screen


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
```

### SpacetimeDB `manifests` table (you write, others subscribe)

```python
from datetime import datetime

from pydantic import BaseModel


class ManifestRow(BaseModel):
    manifest_id: str
    repo_url: str
    status: ManifestStatus
    agents: list[AgentManifestEntry]
    graph: GraphSpec
    created_at: datetime
    version: int
```

Reducers: `store_manifest(...)` (status: draft), `confirm_manifest(...)`
(status: confirmed, agents possibly replaced with edited versions).

### Who reads your output

- Person 1's kernel subscribes to `manifests` filtered to `status:
  confirmed` to enforce constraints, `direct_assignable`/
  `entry_only_via`, and scaling policy
- Person 3's frontend subscribes to `manifests` for the Review,
  Contracts, and Roster screens, and calls `/ask/parse` for the Ask
  screen
