# rosterd — Person 3: Coordinator, SpacetimeDB, and frontend

## High-level idea (context)

rosterd takes an existing LangGraph agent system and makes it safe to
run, schedulable in plain language, and federatable, without a manual
config file. Discovery reads the repo directly, a human confirms what
was inferred, the kernel enforces the confirmed contract at runtime.

## Your role

You own the federation layer, the live state everyone watches, and
every screen in the product, eight screens now: Review and Ask
(answering the two tracks directly), plus Monitor (the autoscaling
decision made visible). Review is where a human turns inferred code
into a confirmed, enforceable contract. Ask is the literal
conversation-to-action flow, plain text in, a bounded task out.

SpacetimeDB is scoped to four things: your dashboard tables (agents,
tasks, sites, events), `agent_metrics` (feeding Monitor's sparkline),
and the `manifests` table (Person 2's, you only subscribe).
`agents` is one row per running instance, not per agent type, this is
what makes the autoscaling pod count a live subscription instead of a
periodic poll, the actual best-use case for the SpacetimeDB track.

You're also the reason a violation is debuggable in one click, not a
manual Jaeger search: every event you receive carries a `trace_id`
from the kernel, store it, and render a "View trace" link wherever
that event shows up in the UI.

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
- Add OTel spans around event handling and policy-match logic, and
  propagate incoming trace context from the kernel's `/events` call
  so the coordinator's handling shows up as a child span of the same
  dispatch trace

### SpacetimeDB (dashboard + pool tables)

- Define `agents` (**one row per running instance**, `site_id,
  agent_id, instance_id, name, status, updated_at`), `tasks`, `sites`,
  `events`
- Define `agent_metrics` (`site_id, agent_id, timestamp,
  in_flight_count, queued_count, target_concurrency,
  current_replicas, desired_replicas, min_replicas, max_replicas`),
  written by Sathvik's kernel every scaler tick, this is what the
  Monitor screen's sparkline and live formula readout subscribe to
- Reducers: `update_agent_status`, `record_task`, `record_event`,
  `update_site_score`, `record_agent_metrics`, called by the kernel
  and coordinator, never written to directly by the frontend
- `manifests` is Person 2's table, you only subscribe to it

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
  **animate the count changing** (don't just swap the number, this is
  a live demo moment), calendar-style scheduling with assignees and
  expectation criteria
- **Contracts**: tools, spend limit, source, direct-assignable
  status, scaling range, subscribed to `manifests` (confirmed only)
- **Federation**: per-site status, live pod counts per agent,
  **per-instance concurrency dots** (solid = working, hollow = idle)
  so parallel draining reads as visible, not inferred, a **Simulate
  flash sale** button calling Person 1's
  `POST /agents/{agent_id}/simulate-load` (not the manual `/scale`
  endpoint, this should look like real load triggering the scaler),
  violation and policy-update badges, coordinator activity feed
- **Monitor** (new): per-agent card with a live sparkline of load vs
  replica count, and the scaling formula spelled out with current
  numbers ("load: 8, target: 2, desired: ceil(8/2) = 4, clamped to
  max 4"), subscribed to `agent_metrics`
- **Debug mode / trace linking**: everywhere an event with a
  `trace_id` is shown (Federation's activity feed, the Task Run &
  Violation screen), render a small "View trace" link that opens
  `{JAEGER_BASE_URL}/trace/{trace_id}` in a new tab, so a violation
  in the UI is one click from its exact span in Jaeger. `JAEGER_BASE_URL`
  is a small frontend env config, confirm the value with Sathvik once
  his collector/Jaeger container is up
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
    trace_id: str | None = None  # links this event to its Jaeger trace
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
    trace_id: str | None = None
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


class AgentMetricsRow(BaseModel):
    """Written by the kernel every scaler tick, not just on change.
    Powers the Monitor screen's sparkline and live formula readout."""

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
  `AgentRow` and `AgentMetricsRow` updates directly to SpacetimeDB as
  instance status, pool size, and scaling decisions change
- Person 2's ingestion writes `manifests` (draft, then confirmed),
  and answers `/ask/parse` for the Ask screen
