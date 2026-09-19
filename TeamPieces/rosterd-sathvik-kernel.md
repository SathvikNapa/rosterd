# rosterd — Person 1: Kernel service (Sathvik)

## High-level idea (context)

rosterd takes an existing LangGraph agent system and makes it safe to
run, schedulable in plain language, auto-scalable under load, and
federatable, with a real observability story. Discovery reads the
repo directly, a human confirms what was inferred, the kernel
enforces the confirmed contract at runtime.

## Your role

You own the kernel: the piece that makes every discovered agent safe
to run, able to scale, and traceable end to end. One kernel instance
per site, managing a pool of that site's demo agent containers. You
also own the OTel collector setup and the tracing conventions the
whole team follows, since most traces originate at dispatch, here.

State is in-memory except pool state and scaling metrics, which you
write to SpacetimeDB on every scaler tick so both are live everywhere,
not just known internally.

```
frontend ──> kernel-service ──> demo-agent-service × pool (same site only)
                  │
                  ├──> coordinator-service (posts events, receives policy updates)
                  ├──> SpacetimeDB (writes agents + agent_metrics every tick)
                  └──> otel-collector ──> Jaeger + metrics backend
```

## Design: constraint evaluation

Generic `field`/`op`/`value` rules, each carrying `source`
(`schema`/`code`/`interrupt`/`default`) and `confidence` for display.
One function, `evaluate_rule(rule, response) -> Violation | None`,
pure and unit-testable.

## Design: kill switch

Container-level kill (Docker SDK), not a cooperative timeout. Reused
by the scaler for idle scale-down.

## Design: autoscaling

A pool per `agent_id`, driven by a scaling policy
(`min_replicas`, `max_replicas`, `target_concurrency`,
`scale_down_after_idle_seconds`):

- **Instance registry**: `instances: dict[str, list[AgentInstance]]`
- **Dispatch picks or creates** an idle instance, or spins up a new
  one if pool < `max_replicas`, or queues if at cap
- **Scaler loop** (every few seconds): compute
  `desired_replicas = clamp(ceil(load / target_concurrency),
  min_replicas, max_replicas)`, spin up or kill instances to close
  the gap with current
- **Every tick, not just on change**, write an `agent_metrics` row to
  SpacetimeDB: `load`, `target_concurrency`, `current_replicas`,
  `desired_replicas`, `min_replicas`, `max_replicas`. This is what
  Joy's Monitor screen sparkline and formula readout are built on,
  the kernel already computes this every tick, this task is just
  making it visible instead of internal
- **On any actual pool change**, also write/update the relevant
  `AgentRow` in SpacetimeDB's `agents` table

## Design: demo-friendly scaling

`scale_down_after_idle_seconds` needs to be short enough to actually
watch happen live (15-30s), not a realistic production value.
Support overriding it per-run (env var, or a param on the
`simulate-load` call below), so the hackathon demo isn't waiting
through a multi-minute cooldown on stage.

## Design: observability

FastAPI auto-instrumentation covers HTTP spans for free. Add custom
spans for `dispatch` (root span for the whole call chain),
`constraint_check`, and `scale_decision`. Propagate `traceparent` on
every outbound call, to the demo agent's `/invoke` and to the
coordinator's `/events`, so one dispatch is one traceable path across
services. On a kill, set the span status to error and attach
`violation.rule`, `violation.expected`, `violation.actual` as span
attributes, this is what lets the demo pull up the exact trace in
Jaeger for the misdirection scenario instead of only showing a UI
card. Export the same scaling numbers as OTel metrics
(`rosterd.agent.replicas`, `rosterd.agent.load`,
`rosterd.agent.desired_replicas`, `rosterd.budget.remaining`), so an
external Grafana dashboard could plot the identical curve the
in-app Monitor screen shows, two export paths, same underlying data.

## Design: debug mode / trace linking

Nothing currently connects a run in the UI to its trace in Jaeger, a
person would have to manually search Jaeger by timestamp. Fix: grab
the `dispatch` root span's `trace_id` (from the current OTel context)
the moment a dispatch starts, and thread it through everything that
run produces:

- Include `trace_id` in the `RunResponse` returned from
  `GET /runs/{run_id}`
- Include `trace_id` in the `EventRequest` posted to the coordinator
  after every run

This is what lets the frontend render a one-click "View trace" link
straight from a run or a violation to its exact span in Jaeger,
rather than a separate tool a person has to know to go check.

## Tasks

- Subscribe to this site's **confirmed** manifest in SpacetimeDB
- `POST /dispatch` — validate `direct_assignable`/`entry_only_via`,
  pick or spin up an instance, call `POST /invoke` with a hard
  timeout, propagate trace context
- Constraint evaluator — generic rule evaluation per the design above
- Kill switch — container-level kill on violation, timeout, or budget
  breach, plus restart-for-next-dispatch logic
- Budget enforcer — track tool-call count and elapsed time per
  `run_id`
- Scaler loop — grow/shrink pools, write `agents` and `agent_metrics`
  every tick, respect the demo-mode cooldown override
- `POST /agents/{agent_id}/simulate-load` — fire N synthetic
  dispatches at a configurable rate, letting the scaler loop react on
  its own; this is what the Federation dashboard's "Simulate flash
  sale" button calls, not `/scale`
- `POST /agents/{agent_id}/scale` — manual override, kept as an
  emergency/debug path
- `POST /policy` — accept a policy update from the coordinator
- Post an event to the coordinator's `POST /events` after every run
  and every scale event, including the run's `trace_id`
- `GET /health`, `GET /agents/{agent_id}/instances`
- Stand up `otel-collector` + Jaeger in the compose file, define the
  span/attribute naming conventions (e.g. `rosterd.*` namespace) the
  rest of the team follows for their own spans
- Export scaling metrics via the OTel SDK

## Dependencies

- Param's manifest must reach `status: confirmed` in SpacetimeDB
  before you have anything to subscribe to
- Shruti's `/invoke` must be callable with real tool schemas, so the
  constraints you enforce are meaningful, and must propagate incoming
  trace context so her spans nest correctly
- Joy owns the `agents` and `agent_metrics` table schemas, agree on
  both together before your scaler writes to them; she also owns the
  otel-collector's downstream wiring into the frontend if any is
  needed, confirm the collector endpoint address early

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
| POST | `/agents/{agent_id}/scale` | Manual scale override (debug/emergency) |
| POST | `/agents/{agent_id}/simulate-load` | Fire synthetic load, primary demo trigger for autoscaling |

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
constraints from the confirmed manifest, scales agent pools, exports
scaling metrics and traces, and can kill a run or an idle instance."""

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
    trace_id: str | None = None  # links this run to its Jaeger trace


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


class SimulateLoadRequest(BaseModel):
    count: int = 10
    rate_per_second: float = 3.0
    scale_down_after_idle_seconds_override: int | None = None


class SimulateLoadResponse(BaseModel):
    agent_id: str
    dispatched: int
```

### Constraint rule and scaling policy shape (matches Param's `AgentManifestEntry.constraints` / `.scaling`)

```python
from enum import Enum
from typing import Literal

from pydantic import BaseModel


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
```

### SpacetimeDB rows you write (schemas owned by Joy, agree on shape together)

```python
from datetime import datetime

from pydantic import BaseModel


class AgentRow(BaseModel):
    """One row per running instance."""

    site_id: str
    agent_id: str
    instance_id: str
    name: str
    status: str  # "idle" | "working" | "killed"
    updated_at: datetime


class AgentMetricsRow(BaseModel):
    """Written every scaler tick, not just on change."""

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
```

### What you call out to

- Demo agent's `InvokeRequest`/`InvokeResponse` (see Shruti's doc)
- Coordinator's `EventRequest` shape (see Joy's doc)
- SpacetimeDB's `agents` and `agent_metrics` tables, you're the
  primary writer for both
- Docker SDK for Python, to create, kill, and restart demo agent
  containers on this site
- OTel SDK, for spans and metric export; the `otel-collector`
  endpoint address, which you own in compose
