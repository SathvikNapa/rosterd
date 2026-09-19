# rosterd — Person 3: Coordinator, state, and frontend

## High-level idea (context)

rosterd takes an existing LangGraph agent system (a repo plus
`constraints.yaml`) and makes it safe to run, schedulable, and
federatable. Discovery turns the repo into a manifest, the kernel
enforces that manifest at runtime, the frontend schedules work
against it, and a coordinator federates results across sites.

## Your role

You own the federation layer and the thing everyone actually looks at
during the demo: the coordinator (thin aggregator across sites), its
state, and the frontend (ingest, roster, contracts, federation
dashboard).

State does not need to be SpacetimeDB for this build. Keep the
coordinator's state in memory (a plain dict, or a single SQLite file
if you want it to survive a restart) and have the frontend poll
`GET /sites` and `GET /events` on a short interval (every 1-2s)
instead of subscribing to a live table. This removes an entire piece
of new tech from the critical path, the coordinator is still the
single source of truth, kernels and the frontend both talk to it over
plain REST, nothing changes about who calls what.

```
kernel-site-A ──┐
kernel-site-B ──┼──> coordinator-service (in-memory / SQLite state) ──> frontend (polls)
kernel-site-C ──┘         │
                           └──> policy update pushed back to any site
```

## Tasks

### Coordinator

- `POST /events` — receive a run event from any kernel, store it in
  memory, return a `policy_update` if this event matches a known
  failure pattern
- `GET /sites` — current status and score per site, computed from
  events held in memory
- `GET /events` — event log for the activity feed
- `POST /policy/push` — call each kernel's `POST /policy` when a
  shared failure pattern is detected across 2+ sites
- Pick in-memory dict vs SQLite based on whether you need state to
  survive a coordinator restart during the hackathon, dict is faster
  to build, SQLite is one file and survives a crash

### Frontend

- Ingest screen (repo URL + constraints paste)
- Roster screen: circular status bubbles per agent, calendar-style
  task scheduling with an assignees field and expectation-criteria
  checklist
- Contracts screen: table of each agent's tools, data access, spend
  limit, direct-assignable status, isolation level, sourced from the
  manifest
- Federation dashboard: per-site status, live scores, violation and
  policy-update badges, coordinator activity feed
- Poll `GET /sites` and `GET /events` on an interval for live-feeling
  updates, no subscription layer needed

## Dependencies

- Event shape comes from Person 1's kernel
- Manifest shape (for the Contracts and Roster screens) comes from
  Person 2's ingestion
- Build the frontend against fake data first, swap in real polling
  once Person 1 and 2 are producing real events

## API contract

### Coordinator endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/events` | Kernel posts a run event, may receive a policy update back |
| GET | `/sites` | Per-site status and score |
| GET | `/events` | Event log for the dashboard feed |
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
    """Posted by a kernel after every run."""

    site_id: str
    run_id: str
    agent_id: str
    status: RunStatus
    violation: Violation | None = None
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
    run_id: str
    status: RunStatus
    violation: Violation | None = None
    timestamp: datetime


class PolicyPushRequest(BaseModel):
    """Coordinator calling out to each kernel's POST /policy."""

    rule: str
    value: float | str | bool
    reason: str
```

### State store (in-memory or SQLite, this replaces SpacetimeDB)

`SiteSummary` and `EventLogEntry` above are also your storage shape,
you don't need a separate table schema. A simple approach:

```python
# in-memory version
events: list[EventLogEntry] = []
sites: dict[str, SiteSummary] = {}

# on POST /events: append to events, recompute sites[site_id]
# GET /sites returns list(sites.values())
# GET /events returns events (most recent first)
```

If you want it to survive a restart, swap the two module-level
variables for a single SQLite file with an `events` table matching
`EventLogEntry` and a `sites` table matching `SiteSummary`, same
read/write shape either way.

### Who sends you data

- Person 1's kernel posts `EventRequest` to `POST /events` after every
  run
- Person 2's ingestion manifest feeds the Roster and Contracts screens
