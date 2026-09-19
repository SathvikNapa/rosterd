# rosterd — team brief

## High-level idea

rosterd takes an existing LangGraph agent system and turns it into
something you can safely run, schedule work against by talking to it
plainly, and operate at scale across multiple isolated sites.

- **Discover**: statically read the repo, no constraints file to
  write by hand. Tool argument schemas, conditional edges, and
  `interrupt()` calls already declare most of what a manual YAML
  used to. A human reviews and confirms the inferred contract before
  it goes live.
- **Wrap**: put each agent behind a kernel that enforces that
  confirmed contract at every tool call, and can kill the agent for
  real if it breaks one.
- **Ask**: turn a plain-language request into a scheduled, bounded
  task, agent assigned and criteria extracted, confirmed before it
  runs. Conversation in, action out.
- **Scale**: agents are pods. A load spike on one agent (a flash sale
  hitting Fulfillment) grows that agent's pool automatically, and
  shrinks it back down after, the same way Kubernetes scales
  replicas, except the replicas are agent instances.
- **Federate**: run the same roster at multiple sites. Sites share
  only scores and failure patterns with a thin coordinator, never
  task content. A failure caught at one site pushes a policy update
  to the others.

Two tracks this build speaks to directly: **SpacetimeDB** (live,
multiplayer state for both the manifest and the autoscaling pool, not
an incidental data layer) and **Auctor** (the Ask screen is a literal
conversation-to-action flow, and ingestion itself turns an
unstructured repo into a structured, confirmed contract).

Demo domain: **e-commerce order fulfillment**, three agents (Order
Intake, Fulfillment, Refund/Exception), because it gives autoscaling
a real trigger (flash sale) and gives ingestion real schema signal to
infer from (a refund cap and a stock check are naturally typed fields
in the tool code, not invented for the demo).

## Architecture

```
frontend ──┬──> ingestion-service   (repo → draft manifest → SpacetimeDB;
           │                         also: POST /ask/parse for the Ask screen)
           ├──> kernel-service × N  (one per site, subscribes to its
           │                         site's CONFIRMED manifest only)
           ├──> coordinator-service (federation)
           └──> SpacetimeDB         (subscribed directly: manifests, dashboard tables)

kernel-service ──> demo-agent-service × pool   (same site, kernel is the only thing that can reach it)
kernel-service ──> coordinator-service         (posts run events, incl. pool scale events)
coordinator-service ──> kernel-service         (pushes policy updates back)
coordinator-service ──> SpacetimeDB            (writes dashboard tables via reducers)
```

Each site is one Docker network containing its kernel and a pool of
demo agent containers for that site (one container per active agent
instance, scaled up or down by the kernel). The kernel is the only
thing in that network allowed to reach the agents, and the only thing
allowed to leave it, toward the coordinator.

**No constraints.yaml.** Ingestion parses the repo's tool argument
schemas (typed field limits), conditional edge functions (routing),
and `interrupt()` calls (approval gates) to produce a manifest with
every rule tagged by where it came from (`schema`, `code`, `default`)
and a confidence level. This is written to SpacetimeDB as `draft`. A
human reviews it on the Review screen and confirms it, only then does
any kernel subscribe to and enforce it. Auto-inferred, human-gated,
not hand-typed.

SpacetimeDB is scoped to three things: manifest distribution
(ingestion writes, kernel and frontend subscribe, live propagation on
re-ingest), the federation dashboard (agents/tasks/sites/events, live
for every viewer), and the autoscaling pool itself, each running
agent instance is a row, so "watch the pool grow and shrink" is a
subscription, not a poll. Point-to-point calls between services
(dispatch, constraint checks, policy push) stay plain REST.

Shared schema reference: the five Pydantic files already shared
(`shared.py`, `ingestion.py`, `kernel.py`, `demo_agent.py`,
`coordinator.py`). Every service imports `shared` so status enums and
types can't drift apart between services.

## Team split

| Person | Owns |
|---|---|
| 1 (Sathvik) | Kernel service — dispatch, constraint checks, kill switch, autoscaling, policy updates |
| 2 | Ingestion service — repo introspection, auto-inference, manifest confirm flow, `/ask/parse` |
| 3 | Coordinator service, SpacetimeDB dashboard + pool tables, frontend (Ingest, Review, Ask, Roster, Contracts, Federation) |
| 4 | Demo agentic system (e-commerce LangGraph) and its Docker image |

Build order: Person 4's demo agent needs real, typed tool schemas
early, since Person 2's inference has nothing to infer from
otherwise, this is the first dependency to unblock. Person 2's
manifest shape (including `source`/`confidence` per rule and the
`scaling` field) should be nailed down next, Person 1's kernel and
Person 3's Contracts/Review screens both build against it. Person 3
can build every screen against fake data from the start and swap in
real subscriptions once Person 1 and 2 are producing real events.

---

## Person 1 — Kernel service (Sathvik)

**What you're building**: the piece that makes every discovered agent
safe to run and able to scale under load. One kernel instance per
site, managing a pool of that site's demo agent containers rather
than a single fixed instance.

**Design — constraint evaluation**: rules are generic (`field`, `op`,
`value`, plus `source`/`confidence` for display), not hardcoded per
agent. The kernel runs one generic `evaluate_rule(rule, response) ->
Violation | None` against whatever the agent's response contains.

**Design — kill switch**: a real kill is a container-level kill
(Docker SDK), not a cooperative timeout an agent could ignore. On
violation or timeout, kill the container, then start a fresh one for
the next dispatch.

**Design — autoscaling**: the kernel manages a pool per `agent_id`. A
scaling policy per agent (`min_replicas`, `max_replicas`,
`target_concurrency`, `scale_down_after_idle_seconds`) drives a loop
that spins up a new instance when concurrency exceeds target and
kills idle instances after the cooldown. Every pool change (spin-up,
kill) is written as an `AgentRow` update in SpacetimeDB so the
Fulfillment pod count is live everywhere, this is the actual "watch
Kubernetes-style scaling happen" demo moment.

**Tasks**
- Subscribe to this site's **confirmed** manifest in SpacetimeDB's
  `manifests` table (draft manifests are invisible to you), update
  live if it's re-confirmed
- `POST /dispatch` — validate `direct_assignable` / `entry_only_via`,
  pick or spin up an instance from the pool, call `POST /invoke`
- Constraint evaluator — generic rule evaluation per the design above
- Kill switch — container-level kill on violation, timeout, or budget
  breach, plus restart-for-next-dispatch logic
- Budget enforcer — track tool-call count and elapsed time per
  `run_id` in memory
- Scaler loop — scale a pool up/down per agent's scaling policy,
  write pool state to SpacetimeDB on every change
- `POST /policy` — accept a policy update pushed from the coordinator
- Post an `EventRequest` to the coordinator's `POST /events` after
  every run (done or killed) and after every scale event
- `GET /health` — status and remaining budget
- `GET /agents/{agent_id}/instances` — current pool size and status
- `POST /agents/{agent_id}/scale` — manual scale-up, for triggering
  the flash-sale demo moment live

**Contract**: `kernel.py` + `shared.py`. Calls out to `demo_agent.py`'s
`InvokeRequest`/`InvokeResponse`, and to `coordinator.py`'s
`EventRequest`.

**Depends on**: Person 2's manifest shape being stable and confirmed
manifests appearing in SpacetimeDB, Person 4's `/invoke` endpoint
running in its own container per instance.

---

## Person 2 — Ingestion service

**What you're building**: the service that reads code and turns it
into structured, contract-bound understanding, twice over: once for
a repo at setup (ingestion), once for a plain-language request at
runtime (`/ask/parse`). Both are the same underlying idea, unstructured
input in, a confirmed structured action out, which is also your
strongest tie to the Auctor track.

**Design — inference instead of a config file**: for each discovered
node, pull constraints from three sources, in order of confidence:

1. **Tool schema** (high confidence): a LangChain tool's Pydantic
   `args_schema` field with `Field(le=100)` or similar becomes a
   `ConstraintRule` directly, this is a typed guarantee, not a guess
2. **Code guard** (medium confidence): AST-scan the node function body
   for comparisons against a threshold followed by a raise or an
   escalate-style return (`if amount > X: ...`), best-effort
3. **`interrupt()` call** (high confidence, different kind of rule):
   presence of LangGraph's `interrupt()` in a node marks it
   `direct_assignable: false` and infers `entry_only_via` from that
   node's predecessors in the graph

Anything not covered by one of these gets a `default` source and a
`low` confidence flag, so the Review screen can point it out plainly.

**Tasks**
- `POST /ingest` — clone the repo, load the compiled LangGraph graph
  via `get_graph()`, run the three-source inference above per node
- Assemble one `AgentManifestEntry` per agent (id, node, purpose from
  docstring, tools, `direct_assignable`, `entry_only_via`, constraint
  rules with source/confidence, scaling policy)
- Write the manifest to SpacetimeDB's `manifests` table with
  `status: draft`
- `POST /manifest/{manifest_id}/confirm` — accept the (possibly
  edited) agent list from the Review screen, write `status: confirmed`.
  This is the only way a manifest becomes visible to kernels
- `POST /ask/parse` — accept `{manifest_id, text}`, use the confirmed
  manifest's agent list to parse plain language into a proposed
  `{agent_id, task, criteria}`, returned for the frontend's Ask screen
  to show as a confirmable card before dispatch
- Decide manifest versioning: does a re-ingest or re-confirm create a
  new `manifest_id`, or overwrite in place

**Contract**: `ingestion.py`, built on `shared.GraphSpec`/`GraphEdge`.
This is the manifest format every other service consumes, flag
changes to the whole team before merging.

**Depends on**: Person 4's repo having real, typed tool schemas (this
is what makes inference demoable instead of hand-waved) and at least
one `interrupt()` call, early enough to test introspection against.

---

## Person 3 — Coordinator, SpacetimeDB, and frontend

**What you're building**: the federation layer, the live state
everyone watches, and every screen in the product.

**Tasks — coordinator**
- `POST /events` — receive a run or scale event from any kernel,
  return a `policy_update` if it matches a known failure pattern
- `GET /sites` — current status, score, and pool sizes per site
- `POST /policy/push` — call each kernel's `POST /policy` when a
  shared failure pattern is detected across 2+ sites

**Tasks — SpacetimeDB (dashboard + pool tables)**
- Define `agents` (one row per running instance, not per agent type,
  this is what makes the pool count live), `tasks`, `sites`, `events`
- Reducers: `update_agent_status`, `record_task`, `record_event`,
  `update_site_score`, called by the kernel and coordinator, never
  written to directly by the frontend
- `manifests` is Person 2's table, you only subscribe to it

**Tasks — frontend**
- **Ingest**: repo URL only, no file upload, "Analyzing repository"
  state, discovery preview
- **Review** (new): inferred rules in a table with source badges
  (schema / code guard / interrupt detected) and confidence, edit
  affordance per row, "Confirm and go live" calls Person 2's confirm
  endpoint
- **Ask** (new): single input, "what do you need done," calls
  `/ask/parse`, shows the proposed agent + extracted criteria as a
  card, "Do it" dispatches
- **Roster**: circular status bubbles per agent, stacked pod count
  when a pool has scaled past one instance (e.g. "Fulfillment ×3"),
  calendar-style scheduling with assignees and expectation criteria
- **Contracts**: tools, spend limit, source, direct-assignable,
  scaling range, subscribed to `manifests`
- **Federation**: per-site status, per-agent pod counts, a "Simulate
  flash sale" button that fires a burst of tasks to demo autoscaling
  live, violation and policy-update badges, coordinator activity feed
- Subscribe directly to SpacetimeDB tables everywhere, no polling

**Contract**: `coordinator.py`. Frontend reads are SpacetimeDB
subscriptions; writes go through kernel/coordinator/ingestion REST
endpoints, never direct table writes from the frontend.

**Depends on**: event and pool-row shape from Person 1, manifest and
`/ask/parse` shape from Person 2.

---

## Person 4 — Demo agentic system (e-commerce)

**What you're building**: an order-fulfillment LangGraph system,
written so ingestion's automatic inference has real signal to pull
from. This is a deliverable in its own right, the tool schemas you
write ARE the contract, there's no separate file to keep in sync.

**Tasks**
- Build the 3-agent LangGraph system:
  - **Order Intake** — classifies an incoming order (standard,
    high-value, fraud-flagged)
  - **Fulfillment** — reserves inventory, tool `reserve_inventory(sku,
    qty: int)` with a real schema constraint (`qty <= stock_on_hand`,
    or a fixed cap if stock isn't modeled), this is the agent that
    autoscales under a flash sale burst
  - **Refund/Exception** — issues refunds, tool `issue_refund(order_id,
    amount: float = Field(le=100))`, and calls `interrupt()` before
    approving anything outside that range, this is what makes it
    correctly infer as not-directly-assignable
- `POST /invoke` — accept `{entry_node, input}`, run that node (and
  downstream routing), return output plus tool calls made
- `GET /graph` — expose the same graph structure ingestion extracts
  statically, to confirm the two never drift apart
- Seed the misdirection scenario: a conversation where a fake
  "manager override" message tries to get Refund/Exception to approve
  an out-of-policy amount, this is the demo's core failure-caught
  moment, and it should trip the schema-inferred limit specifically,
  not a hardcoded check
- Dockerfile for this service, isolated on its own site network

**Contract**: `demo_agent.py`. This is the only service that doesn't
call anyone else, it just answers `/invoke` and `/graph`.

**Depends on**: nothing else technically, start immediately. The
quality of your tool schemas and your one `interrupt()` call are what
make Person 2's whole pitch ("no manual config") actually true, treat
them as the real deliverable, not incidental code.

---

## Demo flow

1. Paste only a repo URL → ingestion infers 3 agents, their tools,
   and their rules with source tags → **Review screen** shows them,
   confirm → manifest goes live, kernels subscribe, roster populates
2. **Ask screen**: type "refund order #4482, duplicate charge" →
   proposed action card shows Refund/Exception assigned with the
   inferred $100 criteria → confirm → dispatches
3. Run the seeded misdirection scenario → the agent attempts an
   out-of-policy refund → kernel's constraint check (against the
   schema-inferred rule, not a hardcoded one) catches it → kill
4. Event reaches the coordinator → 2+ sites show a policy update
   pushed, zero order data having crossed between sites
5. **Flash sale**: hit "Simulate flash sale" on the Federation
   dashboard → Fulfillment's pool grows live in SpacetimeDB, visible
   as pod count on every screen at once → after the burst, it scales
   back down, both directions shown, not just scale-up
