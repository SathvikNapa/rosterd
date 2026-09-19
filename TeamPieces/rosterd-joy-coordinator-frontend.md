# rosterd — Person 3: Coordinator, SpacetimeDB, and frontend

## High-level idea (context)

rosterd takes an existing LangGraph agent system and makes it safe to
run, schedulable in plain language, and federatable, without a manual
config file. Discovery reads the repo directly, a human confirms what
was inferred, the kernel enforces the confirmed contract at runtime.

## Your role

You own the federation layer, the live state everyone watches, and
every screen in the product, now seven screens instead of five: two
are new (Review, Ask), directly answering the two tracks. Review is
where a human turns inferred code into a confirmed, enforceable
contract. Ask is the literal conversation-to-action flow, plain text
in, a bounded task out.

SpacetimeDB is scoped to three things: your dashboard tables
(agents, tasks, sites, events), the `manifests` table (Person 2's,
you only subscribe), and specifically, `agents` is now one row per
running instance, not per agent type, this is what makes the
autoscaling pod count a live subscription instead of a periodic poll,
the actual best-use case for the SpacetimeDB track.

```
kernel-site-A ──┐
kernel-site-B ──┼──> coordinator-service ──> SpacetimeDB (agents/tasks/sites/events)
kernel-site-C ──┘         │                         │
                           └── policy update ────────┘
                                back to any site      frontend (subscribed, every screen live)

ingestion-service ──> SpacetimeDB `manifests` (draft → confirmed) ──> frontend (Review, Contracts, Roster)
```

## Tasks

### Coordinator

- `POST /events` — receive a run or scale event from any kernel,
  write to SpacetimeDB's `events` table via `record_event`, return a
  `policy_update` if it matches a known failure pattern
- `GET /sites` — thin read of `sites`, mostly for debugging, frontend
  subscribes directly
- `POST /policy/push` — call each kernel's `POST /policy` when a
  shared failure pattern is detected across 2+ sites

### SpacetimeDB (dashboard + pool tables)

- Define `agents` (**one row per running instance**, `site_id,
  agent_id, instance_id, name, status, updated_at`), `tasks`, `sites`,
  `events`
- Reducers: `update_agent_status`, `record_task`, `record_event`,
  `update_site_score`, called by the kernel and coordinator, never
  written to directly by the frontend
- `manifests` is Person 2's table, you subscribe for the Review,
  Contracts, and Roster screens, agree on its shape with them early
  since you likely own the SpacetimeDB module setup

### Frontend

- **Ingest**: repo URL only, no file upload
- **Review** (new): table of inferred rules per agent, each with a
  source badge (`schema` / `code guard` / `interrupt() detected` /
  `default`, colored by confidence), edit affordance per row,
  "Confirm and go live" calls Person 2's `POST /manifest/{id}/confirm`
- **Ask** (new): one input, "what do you need done," calls Person
  2's `POST /ask/parse`, shows the proposed agent and extracted
  criteria as a card, "Do it" dispatches via Person 1's kernel
- **Roster**: circular status bubbles per agent, stacked pod-count
  badge when a pool has scaled past one instance ("Fulfillment ×3"),
  calendar-style scheduling with assignees and expectation criteria
- **Contracts**: tools, spend limit, source, direct-assignable
  status, scaling range, subscribed to `manifests` (confirmed only)
- **Federation**: per-site status, live pod counts per agent, a
  **Simulate flash sale** button (fires a burst of dispatches at one
  site to trigger autoscaling live), violation and policy-update
  badges, coordinator activity feed
- Subscribe directly to SpacetimeDB tables everywhere, no polling

## Dependencies

- Event and `AgentRow` (pool) shape from Person 1
- Manifest shape and `/ask/parse` response shape from Person 2
- Build every screen against fake data first, swap in real
  subscriptions once Person 1 and 2 are producing real events

## API contract

### Coordinator endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/events` | Kernel posts a run or scale event, may receive a policy update back |
| GET | `/sites` | Per-site status and score (debug/fallback, frontend subscribes instead) |
| POST | `/policy/push` | Coordinator pushes a policy update to a kernel |

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

### Your schema (`coordinator.py`)

```python
"""Coordinator service — thin aggregator. Receives events from every
kernel, never task content, and can push a policy update back."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from shared import RunStatus, SiteStatus, Violation


class EventRequest(BaseModel):
    """Posted by a kernel after every run or scale event."""

    site_id: str
    run_id: str | None = None  # None for a pool scale event
    agent_id: str
    status: RunStatus | Literal["scaled_up", "scaled_down"]
    violation: Violation | None = None
    pool_size: int | None = None
    timestamp: datetime


class PolicyUpdate(BaseModel):
    rule: str
    value: float | str | bool


class EventResponse(BaseModel):
    received: Literal[True] = True
    policy_update: PolicyUpdate | None = None


class SiteSummary(BaseModel):
    site_id: str
    status: SiteStatus
    last_event: datetime | None = None
    score: float


class EventLogEntry(BaseModel):
    site_id: str
    run_id: str | None = None
    status: RunStatus | Literal["scaled_up", "scaled_down"]
    violation: Violation | None = None
    timestamp: datetime


class PolicyPushRequest(BaseModel):
    rule: str
    value: float | str | bool
    reason: str
```

### SpacetimeDB tables you own

```python
from datetime import datetime

from pydantic import BaseModel


class AgentRow(BaseModel):
    """One row PER RUNNING INSTANCE, not per agent type. A pool of 3
    Fulfillment instances is 3 rows sharing agent_id='fulfillment'."""

    site_id: str
    agent_id: str
    instance_id: str
    name: str
    status: str  # "idle" | "working" | "killed"
    updated_at: datetime


class TaskRow(BaseModel):
    task_id: str
    site_id: str
    agent_id: str
    title: str
    status: str
    assignees: list[str]
    criteria: list[str]
    priority: str
    source: str | None = None
    created_at: datetime

# `sites` table   -> SiteSummary
# `events` table  -> EventLogEntry
```

### Who sends you data

- Person 1's kernel posts `EventRequest` to `POST /events` and writes
  `AgentRow` updates directly to SpacetimeDB as instance status and
  pool size change
- Person 2's ingestion writes `manifests` (draft, then confirmed),
  and answers `/ask/parse` for the Ask screen
