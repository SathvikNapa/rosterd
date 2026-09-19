# rosterd — Person 1: Kernel service (Sathvik)

## High-level idea (context)

rosterd takes an existing LangGraph agent system (a repo plus
`constraints.yaml`) and makes it safe to run, schedulable, and
federatable. Discovery turns the repo into a manifest, the kernel
enforces that manifest at runtime, the frontend schedules work
against it, and a coordinator federates results across sites.

## Your role

You own the kernel: the piece that makes every discovered agent safe
to run, and able to scale under load. One kernel instance runs per
site, managing a pool of that site's demo agent containers rather
than a single fixed instance. It is the only thing that can reach
those containers, and the only thing allowed to leave the site's
network, toward the coordinator.

This is the hardest and highest-stakes piece: the demo's core payoff
moments (the kernel catches a violation and kills it, the kernel
scales a pool up and back down under load) both live here, not in the
roster UI or the calendar. Three things need a real design, not a
shortcut:

1. Checking arbitrary agent output against a declared rule, without
   hardcoding field names per agent
2. A kill that's actually a kill, not a cooperative shutdown the
   agent could ignore or outrun
3. Managing a pool of instances per agent instead of one fixed
   container, so the same kill mechanism doubles as the scale-down
   mechanism

State for this service is in-memory (plain dicts keyed by `run_id`
and `agent_id`), no external DB needed for the hackathon.

```
frontend ──> kernel-service ──> demo-agent-service × pool (same site only)
                  │
                  └──> coordinator-service (posts events, receives policy updates)
```

## Design: constraint evaluation

Don't hardcode `refund_amount <= max_refund_usd` as a special case.
Instead, `constraints.yaml` declares rules generically, and the
kernel runs a small generic evaluator against whatever the demo
agent's response contains:

```yaml
constraints:
  refund_node:
    rules:
      - field: tool_calls[0].args.amount
        op: lte
        value: 100
```

- `field` is a dot/bracket path into the `InvokeResponse` (walk
  `tool_calls`, `output`, or `next_node` as needed)
- `op` is one of a small fixed set: `lte`, `gte`, `eq`, `in`,
  `not_in`
- `value` is the threshold from the manifest

Write one function, `evaluate_rule(rule, response) -> Violation | None`,
that extracts the field by path and applies the op. This is the only
piece of "judgment" in the kernel, and it should be a pure function
you can unit test against fixture responses before wiring it to a
live agent call, since it's the thing the whole demo depends on being
correct.

## Design: kill switch

The kernel calling the demo agent over HTTP and just closing the
connection on timeout doesn't stop the agent's process from
continuing to run and burn tool calls. For a kill to be real:

- Give every dispatched call a hard wall-clock timeout at the HTTP
  client level (not just an `asyncio` cancel, which a blocking call
  inside the agent can ignore)
- On timeout or a rule violation, issue an actual container-level
  kill against that specific instance's container (Docker SDK for
  Python, `client.containers.get(name).kill()`), not just an
  app-level signal
- Record the run as `killed` with the specific `Violation` (rule,
  expected, actual), this detail is what the frontend shows on the
  card, don't collapse it to a generic error
- This same kill call is reused by the scaler (below) to remove an
  idle instance, the trigger differs, the mechanism doesn't

## Design: autoscaling

The kernel manages a **pool per `agent_id`**, not one fixed container.
A scaling policy declared per agent in the manifest drives a loop
that grows and shrinks the pool:

```yaml
agents:
  refund_node:
    scaling:
      min_replicas: 1
      max_replicas: 4
      target_concurrency: 2   # tasks per instance before spawning another
      scale_down_after_idle_seconds: 30
```

- **Instance registry**: `instances: dict[str, list[AgentInstance]]`,
  keyed by `agent_id`, each entry tracks `instance_id`,
  `container_name`, `status` (`idle`/`working`), `started_at`
- **Dispatch picks or creates**: `POST /dispatch` looks for an idle
  instance first; if none and pool size < `max_replicas`, spin up a
  new container (`client.containers.run(image, name=..., network=site_network)`)
  and dispatch to it; if pool is at `max_replicas`, queue the task
- **Scaler loop** (runs every few seconds inside the kernel, not a
  separate service): scale up when queued/in-flight tasks per
  instance exceeds `target_concurrency` and pool < `max_replicas`;
  scale down (kill) an instance idle longer than
  `scale_down_after_idle_seconds` when pool > `min_replicas`
- Because a container that gets killed (violation or scale-down)
  can't serve the next dispatch, always check the registry reflects
  reality, don't dispatch to an instance whose container you just
  killed

## Tasks

- Load a manifest (Person 2's ingestion output, including each
  agent's `scaling` policy) and hold it as this site's active
  contract, in memory
- `POST /dispatch` — validate `direct_assignable` / `entry_only_via`,
  pick or spin up an instance from the pool, call `POST /invoke` with
  a hard timeout
- Constraint evaluator — generic `evaluate_rule` function per the
  design above, run against the agent's response before marking a
  run done
- Kill switch — container-level kill on violation or timeout, plus
  the restart-for-next-dispatch logic
- Budget enforcer — track tool-call count and elapsed time per
  `run_id` in the in-memory store, kill on breach same as a
  constraint violation
- Scaler loop — grow/shrink each agent's pool per its scaling policy,
  reusing the kill mechanism for scale-down
- `POST /policy` — accept a policy update pushed from the coordinator
  and apply it to this site's in-memory constraints
- Post an event to the coordinator's `POST /events` after every run
  (done or killed)
- `GET /health` — report status and remaining budget
- `GET /agents/{agent_id}/instances` — current pool: instance count
  and status per instance
- `POST /agents/{agent_id}/scale` — manual override to force a
  scale-up, useful to trigger the demo moment live rather than
  waiting for a real burst

## Dependencies

- Person 2's manifest format must be stable, including the `scaling`
  field, before you can validate and scale against it
- Person 4's `/invoke` endpoint must be callable, and needs to run in
  its own container per instance so the kill switch and scaler have
  something real to start and stop
- Your event shape feeds Person 3's coordinator, flag any changes to
  it before merging

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
| POST | `/agents/{agent_id}/scale` | Manual scale-up, for demo purposes |

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
constraints from the manifest, scales agent pools, and can kill a
run or an idle instance."""

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
    """Called by the coordinator when it pushes a shared policy update."""

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

### Constraint rule and scaling policy shape (add to Person 2's `AgentConstraints` / manifest entry)

```python
from typing import Literal

from pydantic import BaseModel


class ConstraintRule(BaseModel):
    field: str  # dot/bracket path into InvokeResponse, e.g. "tool_calls[0].args.amount"
    op: Literal["lte", "gte", "eq", "in", "not_in"]
    value: float | str | list


class ScalingPolicy(BaseModel):
    min_replicas: int = 1
    max_replicas: int = 1
    target_concurrency: int = 1
    scale_down_after_idle_seconds: int = 30
```

### What you call out to

- Demo agent's `InvokeRequest` / `InvokeResponse` (see Person 4's doc)
- Coordinator's `EventRequest` shape (see Person 3's doc), posted
  after every run
- Docker SDK for Python, to create, kill, and restart demo agent
  containers on this site
