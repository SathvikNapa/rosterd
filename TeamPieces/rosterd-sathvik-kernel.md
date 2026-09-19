# rosterd — Person 1: Kernel service (Sathvik)

## High-level idea (context)

rosterd takes an existing LangGraph agent system and makes it safe to
run, schedulable in plain language, and federatable, without a manual
config file. Discovery reads the repo directly, a human confirms what
was inferred, the kernel enforces the confirmed contract at runtime.

## Your role

You own the kernel: the piece that makes every discovered agent safe
to run, and able to scale under load. One kernel instance runs per
site, managing a pool of that site's demo agent containers rather
than a single fixed instance. It is the only thing that can reach
those containers, and the only thing allowed to leave the site's
network, toward the coordinator.

One change from before: you subscribe only to **confirmed**
manifests. A `draft` manifest (fresh out of ingestion, not yet
reviewed by a human) is invisible to you, it governs nothing until
someone confirms it.

State for this service is in-memory (dicts keyed by `run_id` and
`agent_id`) except for pool state, which you write to SpacetimeDB on
every change so the pod count is live everywhere, not just something
you know internally.

```
frontend ──> kernel-service ──> demo-agent-service × pool (same site only)
                  │
                  ├──> coordinator-service (posts events, receives policy updates)
                  └──> SpacetimeDB (writes agent pool rows on every scale event)
```

## Design: constraint evaluation

Rules are generic (`field`, `op`, `value`), each carrying a `source`
(`schema` / `code` / `interrupt` / `default`) and `confidence` for
display purposes, the kernel's enforcement logic doesn't care which
source a rule came from, it evaluates all of them the same way. One
function, `evaluate_rule(rule, response) -> Violation | None`, pure
and unit-testable against fixture responses.

E-commerce example: `issue_refund`'s schema-inferred rule is
`tool_calls[0].args.amount <= 100`, same mechanism as any other rule,
the domain changed, nothing about the evaluator did.

## Design: kill switch

A real kill is container-level (Docker SDK,
`client.containers.get(name).kill()`), not a cooperative timeout. On
violation or timeout, kill the container, then start a fresh one for
the next dispatch. Reused by the scaler below for idle scale-down,
different trigger, same mechanism.

## Design: autoscaling

The kernel manages a **pool per `agent_id`**. A scaling policy per
agent (`min_replicas`, `max_replicas`, `target_concurrency`,
`scale_down_after_idle_seconds`) drives a loop:

- **Instance registry**: `instances: dict[str, list[AgentInstance]]`,
  keyed by `agent_id`, tracking `instance_id`, `container_name`,
  `status`, `started_at`
- **Dispatch picks or creates**: look for an idle instance first; if
  none and pool < `max_replicas`, spin up a new container; if pool is
  at `max_replicas`, queue
- **Scaler loop** (every few seconds): scale up when queued/in-flight
  tasks per instance exceeds `target_concurrency`; scale down (kill)
  an instance idle past `scale_down_after_idle_seconds` when pool >
  `min_replicas`
- **Every pool change writes an `AgentRow` to SpacetimeDB**: this is
  what makes "watch Fulfillment scale from 1 to 4 pods" a live
  subscription on the dashboard, not something you'd have to poll for

## Tasks

- Subscribe to this site's **confirmed** manifest in SpacetimeDB's
  `manifests` table (filter `status: confirmed`), update live on
  re-confirm
- `POST /dispatch` — validate `direct_assignable`/`entry_only_via`,
  pick or spin up an instance from the pool, call `POST /invoke` with
  a hard timeout
- Constraint evaluator — generic `evaluate_rule` per the design above
- Kill switch — container-level kill on violation, timeout, or budget
  breach, plus restart-for-next-dispatch logic
- Budget enforcer — track tool-call count and elapsed time per
  `run_id` in memory
- Scaler loop — grow/shrink each agent's pool per its scaling policy,
  write every change to SpacetimeDB's `agents` table
- `POST /policy` — accept a policy update pushed from the coordinator
- Post an event to the coordinator's `POST /events` after every run
  and every scale event
- `GET /health` — status and remaining budget
- `GET /agents/{agent_id}/instances` — current pool: instance count
  and status per instance
- `POST /agents/{agent_id}/scale` — manual override, this is what the
  Federation dashboard's "Simulate flash sale" button calls

## Dependencies

- Person 2's manifest format must be stable, and a manifest must
  reach `status: confirmed` in SpacetimeDB before you have anything
  to subscribe to
- Person 4's `/invoke` endpoint must be callable, running in its own
  container per instance, with real tool schemas so the constraint
  rules you enforce are meaningful, not placeholders
- Person 3 owns the `agents` table schema in SpacetimeDB, agree on
  the `AgentRow` shape together, you're its primary writer

## API contract

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/dispatch` | Accept or reject a task assignment, run it |
| GET | `/runs/{run_id}` | Check a run's status |
| POST | `/runs/{run_id}/kill` | Force-terminate a run |
| POST | `/policy` | Apply a policy update from the coordinator |
| GET | `/health` | Status and remaining budget |
| GET | `/agents/{agent_id}/instances` | Current pool size and per-instance status |
| POST | `/agents/{agent_id}/scale` | Manual scale-up, used by the flash-sale demo trigger |

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

### Your schema (`kernel.py`)

```python
"""Kernel service — one instance per site. Dispatches tasks, enforces
constraints from the confirmed manifest, scales agent pools, and can
kill a run or an idle instance."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from shared import Priority, RunStatus, Violation


class DispatchStatus(str, Enum):
    accepted = "accepted"
    rejected = "rejected"


class TaskSpec(BaseModel):
    id: str
    title: str
    description: str
    priority: Priority = Priority.medium
    source: str | None = None
    expectation_criteria: list[str] = []


class DispatchRequest(BaseModel):
    agent_id: str
    task: TaskSpec
    assignees: list[str]


class DispatchResponse(BaseModel):
    run_id: str
    status: DispatchStatus
    reason: str | None = None


class RunResponse(BaseModel):
    run_id: str
    agent_id: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime | None = None
    violation: Violation | None = None
    output: str | None = None


class KillResponse(BaseModel):
    run_id: str
    status: Literal["killed"] = "killed"
    reason: str


class PolicyUpdateRequest(BaseModel):
    rule: str
    value: float | str | bool
    reason: str


class PolicyUpdateResponse(BaseModel):
    applied: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    budget_remaining: float


class InstanceStatus(str, Enum):
    idle = "idle"
    working = "working"


class AgentInstance(BaseModel):
    instance_id: str
    agent_id: str
    container_name: str
    status: InstanceStatus
    started_at: datetime


class InstancesResponse(BaseModel):
    agent_id: str
    instances: list[AgentInstance]


class ScaleRequest(BaseModel):
    target_replicas: int


class ScaleResponse(BaseModel):
    agent_id: str
    replicas: int
```

### What you call out to

- Demo agent's `InvokeRequest`/`InvokeResponse` (see Person 4's doc)
- Coordinator's `EventRequest` shape (see Person 3's doc)
- SpacetimeDB's `agents` table (see Person 3's doc), you're the
  primary writer as pool size changes
- Docker SDK for Python, to create, kill, and restart demo agent
  containers on this site
