# rosterd — Person 4: Demo agentic system

## High-level idea (context)

rosterd takes an existing LangGraph agent system (a repo plus
`constraints.yaml`) and makes it safe to run, schedulable, and
federatable. Discovery turns the repo into a manifest, the kernel
enforces that manifest at runtime, the frontend schedules work
against it, and a coordinator federates results across sites.

## Your role

You build the example LangGraph system the whole platform is
demonstrated against. This is a deliverable in its own right, not
just a test fixture, it's what the audience sees the platform
discover, schedule against, and catch a failure in.

```
kernel-service ──> demo-agent-service (your build, same site only)
```

Your service is the only one that doesn't call anyone else. It just
answers.

## Tasks

- Build the 3-agent LangGraph system:
  - **Triage** — classifies an incoming request (billing, refund,
    technical)
  - **Refund** — issues refunds, tool-bound, capped by policy
  - **Escalation** — hands off to a human, not directly assignable
- Write `constraints.yaml` for this system (refund cap, direct-
  assignable flags, entry rules)
- `POST /invoke` — accept `{entry_node, input}`, run that node (and
  downstream routing if applicable), return output plus any tool
  calls made
- `GET /graph` — expose the same graph structure Person 2's ingestion
  extracts statically, so the two can be checked against each other
- Seed the misdirection scenario: a conversation where a fake
  "manager override" message tries to get Refund to approve an
  out-of-policy amount, this is the demo's core failure-caught moment
- Dockerfile for this service, isolated on its own site network per
  the compose file (only the kernel can reach you)

## Dependencies

- None to start, you can build in parallel with everyone else
- Coordinate with Person 2 early so your repo's actual folder/graph
  structure matches what their introspection expects
- Your `/invoke` response is what Person 1's kernel checks against
  the manifest's constraints, so keep the shape exact

## API contract

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/invoke` | Run one node with the given input |
| GET | `/graph` | Return this system's graph structure |

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

### Your schema (`demo_agent.py`)

```python
"""Demo agent service (Person 4's LangGraph system). Only ever answers,
never calls out. Wrapped and invoked by its site's kernel."""

from enum import Enum

from pydantic import BaseModel

from shared import GraphSpec


class EntryNode(str, Enum):
    triage = "triage"
    refund = "refund"
    escalation = "escalation"


class InvokeInput(BaseModel):
    text: str
    context: dict | None = None


class InvokeRequest(BaseModel):
    entry_node: EntryNode
    input: InvokeInput


class ToolCall(BaseModel):
    tool: str
    args: dict
    result: str | None = None


class InvokeResponse(BaseModel):
    output: str
    tool_calls: list[ToolCall] = []
    next_node: str | None = None


class GraphResponse(GraphSpec):
    """Same shape as GraphSpec. Returned by GET /graph, mirrors what
    ingestion extracted statically at setup time."""
```

### Who calls you

- Person 1's kernel calls `POST /invoke` on every dispatched task, and
  checks your response's tool calls / output against the manifest's
  constraints before marking the run done or killing it
