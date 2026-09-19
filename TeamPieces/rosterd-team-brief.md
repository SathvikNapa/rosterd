# rosterd — team brief

## High-level idea

rosterd takes an existing LangGraph agent system (a repo plus a
`constraints.yaml`) and turns it into something you can safely run,
schedule work against, and operate across multiple isolated sites.

- **Discover**: read the repo's compiled LangGraph graph and a
  constraints file, produce a structured manifest of every agent (its
  tools, its purpose, its rules).
- **Wrap**: put each agent behind a kernel that enforces those rules at
  every tool call, and can kill the agent for real if it breaks one.
- **Schedule**: assign tasks to agents the way you'd assign a calendar
  event to a person, with expectation criteria attached, not just fire
  a script.
- **Federate**: run the same roster at multiple sites. Sites share only
  scores and failure patterns with a thin coordinator, never task
  content or transcripts. A failure caught at one site pushes a policy
  update to the others.

Analogy: Kubernetes' declarative infrastructure idea, applied to
agents, plus federated learning's privacy-preserving aggregation,
applied to agent behavior instead of model weights.

## Architecture

```
frontend ──┬──> ingestion-service   (one-time, at setup)
           ├──> kernel-service × N  (one per site)
           └──> coordinator-service (federation, polled for live state)

kernel-service ──> demo-agent-service × pool   (same site, kernel is the only thing that can reach it)
kernel-service ──> coordinator-service         (posts run events)
coordinator-service ──> kernel-service         (pushes policy updates back)
```

Each site is one Docker network containing its kernel and a pool of
demo agent containers for that site (one container per active agent
instance, scaled up or down by the kernel). The kernel is the only
thing in that network allowed to reach the agents, and the only thing
allowed to leave it, toward the coordinator. This is what makes "only
aggregates cross the boundary" a property of the network, not just a
claim in a README.

State does not use SpacetimeDB for this build. The coordinator holds
state in memory (or a single SQLite file), the frontend polls
`GET /sites` and `GET /events` every 1-2s. Same source of truth,
simpler stack, nothing changes about who calls what.

Shared schema reference: the five Pydantic files already shared
(`shared.py`, `ingestion.py`, `kernel.py`, `demo_agent.py`,
`coordinator.py`). Every service imports `shared` so status enums and
types can't drift apart between services.

## Team split

| Person | Owns |
|---|---|
| 1 (Sathvik) | Kernel service — dispatch, constraint checks, kill switch, autoscaling, policy updates |
| 2 | Ingestion service — repo/spec parsing, manifest generation |
| 3 | Coordinator service, in-memory/SQLite state, frontend |
| 4 | Demo agentic system (LangGraph) and its Docker image |

Build order: Person 2's manifest format and Person 4's demo agent
should be nailed down first, since Person 1's kernel needs both to
exist before it can dispatch anything real. Person 3 can build the
frontend against fake data from the start and swap in real polling
once Person 1 and 2 are producing real events.

---

## Person 1 — Kernel service (Sathvik)

**What you're building**: the piece that makes every discovered agent
safe to run and able to scale under load. One kernel instance per
site, managing a pool of that site's demo agent containers rather
than a single fixed instance.

**Design — constraint evaluation**: rules are declared generically in
`constraints.yaml` (`field`, `op`, `value`), not hardcoded per agent
(e.g. `refund_amount <= max_refund_usd`). The kernel runs one generic
`evaluate_rule(rule, response) -> Violation | None` against whatever
the agent's response contains, walking the field path and applying
the op.

**Design — kill switch**: a real kill is a container-level kill
(Docker SDK, `client.containers.get(name).kill()`), not a cooperative
timeout an agent could ignore. On violation or timeout, kill the
container, then start a fresh one for the next dispatch (or rely on a
restart policy).

**Design — autoscaling**: the kernel manages a pool per `agent_id`,
not one fixed container. A scaling policy per agent in the manifest
(`min_replicas`, `max_replicas`, `target_concurrency`,
`scale_down_after_idle_seconds`) drives a small loop that spins up a
new instance when concurrency exceeds target and kills idle instances
after the cooldown, reusing the same kill mechanism above for a
different trigger.

**Tasks**
- Load a manifest (from Person 2's ingestion output) and hold it as
  this site's active contract, in memory
- `POST /dispatch` — validate `direct_assignable` / `entry_only_via`,
  pick or spin up an instance from the pool, call `POST /invoke`
- Constraint evaluator — generic rule evaluation per the design above
- Kill switch — container-level kill on violation, timeout, or budget
  breach, plus restart-for-next-dispatch logic
- Budget enforcer — track tool-call count and elapsed time per
  `run_id` in memory
- Scaler loop — scale a pool up/down per agent's scaling policy
- `POST /policy` — accept a policy update pushed from the coordinator
- Post an `EventRequest` to the coordinator's `POST /events` after
  every run (done or killed)
- `GET /health` — status and remaining budget
- `GET /agents/{agent_id}/instances` — current pool size and status
- `POST /agents/{agent_id}/scale` — manual scale-up, useful to trigger
  the demo moment live rather than waiting for real load

**Contract**: `kernel.py` (this service's request/response models) +
`shared.py`. Calls out to `demo_agent.py`'s `InvokeRequest` /
`InvokeResponse` shape, and to `coordinator.py`'s `EventRequest`
shape.

**Depends on**: Person 2's manifest format being stable (including
the `scaling` field), Person 4's `/invoke` endpoint being callable and
running in its own container so the kill/scale mechanism has
something real to start and stop.

---

## Person 2 — Ingestion service

**What you're building**: the one-time step that turns a LangGraph
repo plus a constraints file into the structured manifest every other
service reads.

**Tasks**
- `POST /ingest` — clone the given repo, load the compiled LangGraph
  graph, call `get_graph()` to extract nodes, edges, and each node's
  bound tools
- Parse `constraints.yaml`, validate node names in it against the
  discovered graph
- Merge graph structure and constraints into one `AgentManifestEntry`
  per agent (id, node, purpose, tools, `direct_assignable`,
  `entry_only_via`, constraints, `scaling` policy)
- `GET /manifest/{manifest_id}` — return a previously generated
  manifest without re-ingesting
- Decide and document manifest versioning (what happens if the repo
  changes and someone re-ingests)

**Contract**: `ingestion.py`, built on `shared.GraphSpec` /
`GraphEdge`. This is the manifest format every other person's service
consumes, so changes here need to be flagged to the whole team before
merging.

**Depends on**: Person 4's repo structure existing (even a stub) to
test introspection against early.

---

## Person 3 — Coordinator, state, and frontend

**What you're building**: the federation layer and the thing everyone
actually looks at during the demo.

**Tasks — coordinator**
- `POST /events` — receive a run event from any kernel, store it,
  return a `policy_update` if this event matches a known failure
  pattern
- `GET /sites` — current status and score per site
- `GET /events` — event log for the activity feed
- `POST /policy/push` — call each kernel's `POST /policy` when a
  shared failure pattern is detected across 2+ sites

**Tasks — state**
- Hold state in memory (dict) or a single SQLite file, `SiteSummary`
  and `EventLogEntry` from `coordinator.py` are also the storage shape
- `POST /events` appends to the event log and recomputes that site's
  summary, no separate table schema or reducers needed

**Tasks — frontend**
- Ingest screen (repo URL + constraints paste)
- Roster screen: circular status bubbles per agent (stacked count
  when an agent's pool has scaled past one instance, e.g. "Refund
  ×3"), calendar-style task scheduling with an assignees field and
  expectation-criteria checklist
- Contracts screen: table of each agent's tools, data access, spend
  limit, direct-assignable status, isolation level, scaling policy,
  sourced from the manifest
- Federation dashboard: per-site status, live scores, violation and
  policy-update badges, coordinator activity feed
- Poll `GET /sites` and `GET /events` every 1-2s for live-feeling
  updates, no subscription layer needed

**Contract**: `coordinator.py`. Frontend reads are polled REST calls;
writes to the platform go through kernel/coordinator REST endpoints,
never direct state writes from the frontend.

**Depends on**: event shape from Person 1's kernel, manifest shape
from Person 2 for the Contracts screen.

---

## Person 4 — Demo agentic system

**What you're building**: the example LangGraph system the whole
platform is demonstrated against. This is a deliverable in its own
right, not just test fixture.

**Tasks**
- Build the 3-agent LangGraph system: Triage (classifies a request),
  Refund (issues refunds, tool-bound, capped by policy), Escalation
  (hands off to a human, not directly assignable)
- Write `constraints.yaml` for this system (refund cap, direct-
  assignable flags, entry rules)
- `POST /invoke` — accept `{entry_node, input}`, run that node (and
  downstream routing if applicable), return output + any tool calls
  made
- `GET /graph` — expose the same graph structure Person 2's ingestion
  extracts statically, as a way to confirm the two never drift apart
- Seed the misdirection scenario: a conversation where a fake
  "manager override" message tries to get Refund to approve an
  out-of-policy amount, used as the demo's core failure-caught moment
- Dockerfile for this service, isolated on its own site network per
  the compose file

**Contract**: `demo_agent.py`. This is the only service that doesn't
call anyone else, it just answers `/invoke` and `/graph`.

**Depends on**: nothing else technically, this can start immediately
and in parallel with everyone else. Coordinate with Person 2 early so
your repo's actual folder/graph structure matches what their
introspection expects.

---

## Demo flow (what all four pieces need to support together)

1. Paste repo URL + constraints into the frontend → ingestion returns
   a manifest → roster populates with 3 bubbles
2. Schedule a normal refund task → kernel dispatches to the demo
   agent → completes → coordinator state updates → dashboard shows
   "Done"
3. Run the seeded misdirection scenario → demo agent attempts the
   out-of-policy refund → kernel's constraint check catches it → kill,
   not just a log line
4. Event reaches the coordinator → 2+ sites show a policy update
   pushed → dashboard shows the propagation, with zero task content
   having crossed between sites
5. **Black Friday autoscaling**: fire a burst of refund tasks at one
   site in quick succession → the kernel's scaler spins up more
   instances as concurrency exceeds target → the Refund bubble shows
   the pool grow ("×1" → "×3") and tasks draining in parallel → after
   the burst, idle instances scale back down after the cooldown,
   showing both directions, not just scale-up
