# rosterd — team brief

## High-level idea

rosterd takes an existing LangGraph agent system and turns it into
something you can safely run, schedule work against by talking to it
plainly, scale automatically under load, and operate at scale across
multiple isolated sites, with the same telemetry story a real
platform team would expect.

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
  replicas, except the replicas are agent instances. A Monitor screen
  shows the actual scaling decision, not just the outcome.
- **Federate**: run the same roster at multiple sites. Sites share
  only scores and failure patterns with a thin coordinator, never
  task content. A failure caught at one site pushes a policy update
  to the others.
- **Observe**: every dispatch is a distributed trace, every scaling
  decision is an exported metric. SpacetimeDB gives you the live
  in-app view, OpenTelemetry gives you the standards-based export any
  downstream stack (Grafana, Honeycomb, Datadog) can ingest without
  knowing rosterd's schema.

Two tracks this build speaks to directly: **SpacetimeDB** (live,
multiplayer state for the manifest, the autoscaling pool, and the
scaling-decision metrics, not an incidental data layer) and
**Auctor** (the Ask screen is a literal conversation-to-action flow,
and ingestion itself turns an unstructured repo into a structured,
confirmed contract).

Demo domain: **e-commerce order fulfillment**, three agents (Order
Intake, Fulfillment, Refund/Exception), because it gives autoscaling
a real trigger (flash sale) and gives ingestion real schema signal to
infer from.

## Architecture

```
frontend ──┬──> ingestion-service   (repo → draft manifest → SpacetimeDB;
           │                         also: POST /ask/parse for the Ask screen)
           ├──> kernel-service × N  (one per site, subscribes to its
           │                         site's CONFIRMED manifest only)
           ├──> coordinator-service (federation)
           └──> SpacetimeDB         (subscribed directly: manifests,
                                      dashboard tables, agent_metrics)

kernel-service ──> demo-agent-service × pool   (same site, kernel is the only thing that can reach it)
kernel-service ──> coordinator-service         (posts run + scale events)
coordinator-service ──> kernel-service         (pushes policy updates back)
coordinator-service ──> SpacetimeDB            (writes dashboard tables via reducers)

all services ──> otel-collector ──> Jaeger (traces) + metrics backend
```

Each site is one Docker network containing its kernel and a pool of
demo agent containers for that site. The kernel is the only thing in
that network allowed to reach the agents, and the only thing allowed
to leave it, toward the coordinator.

**No constraints.yaml.** Ingestion parses tool argument schemas,
conditional edge functions, and `interrupt()` calls to produce a
manifest with every rule tagged by source and confidence, written to
SpacetimeDB as `draft`. A human confirms it on the Review screen
before any kernel enforces it.

SpacetimeDB is scoped to four things: manifest distribution, the
federation dashboard, the autoscaling pool (`agents`, one row per
running instance), and `agent_metrics` (load vs desired replicas per
tick, feeding the Monitor screen's live sparkline). Point-to-point
calls between services stay plain REST.

**Observability**: every service is OTel-instrumented, trace context
propagates across the whole dispatch chain (kernel → demo agent →
coordinator), so a killed run is a single traceable path with the
violation as a span attribute, not just a UI card. The kernel also
exports the same scaling numbers (`rosterd.agent.replicas`,
`rosterd.agent.load`, `rosterd.agent.desired_replicas`) as OTel
metrics, so an external Grafana dashboard could plot the same
autoscaling curve the in-app Monitor screen shows. SpacetimeDB is the
live in-app view, OTel is the standards-based export, two layers,
not a duplicate of the same thing.

**Debug mode**: the kernel threads its dispatch span's `trace_id`
through `RunResponse` and every `EventRequest` it posts, the
coordinator stores it on the event, and the frontend renders a
one-click "View trace" link wherever that event appears (Federation's
activity feed, the Task Run & Violation screen). A violation in the
UI is one click from its exact Jaeger span, not a manual search by
timestamp in a separate tool.

Shared schema reference: the five Pydantic files already shared
(`shared.py`, `ingestion.py`, `kernel.py`, `demo_agent.py`,
`coordinator.py`). Every service imports `shared` so status enums and
types can't drift apart between services.

## Team split

| Person | Owns |
|---|---|
| 1 (Sathvik) | Kernel service — dispatch, constraint checks, kill switch, autoscaling, policy updates, OTel collector + tracing conventions |
| 2 (Param) | Ingestion service — repo introspection, auto-inference, manifest confirm flow, `/ask/parse` |
| 3 (Joy) | Coordinator service, SpacetimeDB (dashboard + pool + metrics tables), frontend (7 screens) |
| 4 (Shruti) | Demo agentic system (e-commerce LangGraph) and its Docker image |

Build order: Shruti's demo agent needs real, typed tool schemas
early, Param's inference has nothing to infer from otherwise. Param's
manifest shape should be nailed down next. Joy should stand up the
SpacetimeDB module (including `agents` as one row per instance,
before Sathvik's scaler has anywhere to write pool changes) and the
OTel collector + Jaeger container early, both are shared
infrastructure everyone else's instrumentation depends on existing.
Joy can build every screen against fake data first and swap in real
subscriptions once Sathvik and Param are producing real events.

---

## Person 1 — Kernel service (Sathvik)

**What you're building**: the piece that makes every discovered agent
safe to run, able to scale under load, and observable end to end. One
kernel instance per site, managing a pool of that site's demo agent
containers, owner of the OTel collector setup and tracing conventions
since most traces originate here.

**Design — constraint evaluation**: generic `field`/`op`/`value`
rules, each carrying `source`/`confidence` for display. One function,
`evaluate_rule(rule, response) -> Violation | None`.

**Design — kill switch**: container-level kill (Docker SDK), not a
cooperative timeout. Reused by the scaler for idle scale-down.

**Design — autoscaling**: a pool per `agent_id`, driven by a scaling
policy (`min_replicas`, `max_replicas`, `target_concurrency`,
`scale_down_after_idle_seconds`). Every scaler tick writes both a
pool-change row to SpacetimeDB's `agents` table (on change) and a
full `agent_metrics` row (every tick, whether or not it changed
anything): load, target, current replicas, **desired replicas**
(`ceil(load / target_concurrency)`, clamped to
`[min_replicas, max_replicas]`), so the Monitor screen can show the
actual formula with live numbers, not just the outcome.

**Design — demo-friendly scaling**: `scale_down_after_idle_seconds`
should be overridable per-run (env var or request param) so the
hackathon demo uses a short cooldown (15-30s) instead of a realistic
production value, scale-down needs to be watchable, not dead air.

**Design — observability**: FastAPI auto-instrumentation covers HTTP
spans, add custom spans for `dispatch` (root span), `constraint_check`,
`scale_decision`, propagate `traceparent` on every outbound call
(to the demo agent, to the coordinator). A killed run's span carries
`violation.rule`, `violation.expected`, `violation.actual` as
attributes, this is what lets the demo pull up the exact trace for
the misdirection scenario in Jaeger.

**Tasks**
- Subscribe to this site's **confirmed** manifest in SpacetimeDB
- `POST /dispatch` — validate, pick or spin up an instance, call
  `POST /invoke` with a hard timeout, propagate trace context
- Constraint evaluator — generic rule evaluation
- Kill switch — container-level kill, plus restart-for-next-dispatch
- Budget enforcer — tool-call count and elapsed time per `run_id`
- Scaler loop — grow/shrink pools, write `agents` and `agent_metrics`
  rows to SpacetimeDB every tick
- `POST /agents/{agent_id}/simulate-load` — fire N synthetic
  dispatches at a configurable rate, letting the scaler loop react on
  its own; this is what the Federation dashboard's "Simulate flash
  sale" button actually calls
- `POST /agents/{agent_id}/scale` — manual override, kept as an
  emergency/debug path, not the primary demo mechanism
- `POST /policy` — accept a policy update from the coordinator
- Post an `EventRequest` after every run and scale event
- `GET /health`, `GET /agents/{agent_id}/instances`
- Stand up the `otel-collector` + Jaeger containers in compose,
  define span/attribute naming conventions the whole team follows
- Export scaling metrics (`rosterd.agent.replicas`,
  `rosterd.agent.load`, `rosterd.agent.desired_replicas`,
  `rosterd.budget.remaining`) via the OTel SDK

**Contract**: `kernel.py` + `shared.py`. Calls out to `demo_agent.py`'s
request/response shapes, and to `coordinator.py`'s `EventRequest`.

**Depends on**: Param's manifest reaching `confirmed` status, Shruti's
`/invoke` running with real tool schemas, Joy's `agents`/`agent_metrics`
table schemas being agreed before the scaler writes to them.

---

## Person 2 — Ingestion service (Param)

**What you're building**: the service that reads code and turns it
into structured, contract-bound understanding, twice over: a repo at
setup (ingestion), and a plain-language request at runtime
(`/ask/parse`).

**Design — inference instead of a config file**: three sources, in
order of confidence, tool schema (`Field(le=100)` style constraints,
high), code guard (AST-scanned threshold comparisons, medium),
`interrupt()` call (marks an agent as not directly assignable, high).
Anything else gets `source: default`, `confidence: low`.

**Design — confirm gate**: every manifest writes as `status: draft`.
`POST /manifest/{id}/confirm` takes the (possibly edited) agent list
from the Review screen and flips it to `confirmed`, the only status a
kernel will subscribe to.

**Tasks**
- `POST /ingest` — clone the repo, extract graph + run inference
- Assemble `AgentManifestEntry` per agent, write to SpacetimeDB as
  `draft`
- `POST /manifest/{manifest_id}/confirm`
- `POST /ask/parse` — plain text in, `{agent_id, task, criteria,
  confidence}` out
- Decide manifest versioning
- Add OTel spans for `ingest`, `infer_constraints`, `parse_ask`,
  propagate trace context if `/ask/parse` leads to a dispatch

**Contract**: `ingestion.py`.

**Depends on**: Shruti's repo having real, typed tool schemas and an
`interrupt()` call early. Joy's `manifests` table schema, agreed
together since Joy owns the SpacetimeDB module.

---

## Person 3 — Coordinator, SpacetimeDB, and frontend (Joy)

**What you're building**: the federation layer, the live state
everyone watches, every screen in the product (now 8 with Monitor),
and the SpacetimeDB module other services write to.

**Tasks — coordinator**
- `POST /events` — receive a run/scale event, write to `events`,
  return a `policy_update` if matched
- `GET /sites`, `POST /policy/push`
- OTel spans around event handling and policy-match logic

**Tasks — SpacetimeDB**
- `agents` (one row per running instance), `tasks`, `sites`, `events`
- `agent_metrics` (`site_id, agent_id, timestamp, in_flight_count,
  queued_count, target_concurrency, current_replicas,
  desired_replicas, min_replicas, max_replicas`), written by Sathvik's
  kernel every scaler tick, read by the Monitor screen for its
  sparkline
- Reducers for all of the above, `manifests` stays Param's table, you
  only subscribe

**Tasks — frontend**
- **Ingest**: repo URL only
- **Review**: inferred rules with source/confidence badges, confirm
- **Ask**: plain text in, proposed action card, dispatch
- **Roster**: bubbles with a stacked, **animated** pod-count badge
  (the transition itself should be visible, not just the end value),
  calendar scheduling
- **Contracts**: tools, spend limit, source, scaling range
- **Federation**: per-site status, live pod counts, per-instance
  concurrency dots (solid = working, hollow = idle), **Simulate
  flash sale** button (calls `/simulate-load`, not `/scale`),
  violation/policy-update badges
- **Monitor** (new): per-agent card with a live sparkline of load vs
  replicas, and the scaling formula spelled out with current numbers
  ("load: 8, target: 2, desired: ceil(8/2) = 4, clamped to max 4"),
  subscribed to `agent_metrics`
- Subscribe directly to SpacetimeDB tables everywhere, no polling

**Contract**: `coordinator.py`.

**Depends on**: event, `AgentRow`, and `agent_metrics` shape from
Sathvik; manifest and `/ask/parse` shape from Param.

---

## Person 4 — Demo agentic system (Shruti)

**What you're building**: the order-fulfillment LangGraph system.
Tool schemas and the one `interrupt()` call ARE the contract now.

**Tasks**
- Order Intake, Fulfillment (`reserve_inventory`, real qty
  constraint), Refund/Exception (`issue_refund`, `amount: float =
  Field(le=100)`, `interrupt()` before anything outside that range)
- `POST /invoke`, `GET /graph`
- Seed the misdirection scenario, tripping the schema-inferred limit
  specifically
- Dockerfile, isolated on its own site network
- Add OTel auto-instrumentation to `/invoke` so its span nests under
  the kernel's dispatch trace (propagate the incoming `traceparent`)

**Contract**: `demo_agent.py`.

**Depends on**: nothing to start, get tool schemas and `interrupt()`
in early, Param's inference needs something real to test against.

---

## Demo flow

1. Paste a repo URL → ingestion infers 3 agents → **Review** →
   confirm → manifest live, roster populates
2. **Ask**: "refund order #4482, duplicate charge" → proposed action
   card → confirm → dispatches
3. Seeded misdirection scenario → out-of-policy refund attempted →
   kernel kills it against the schema-inferred rule → click **"View
   trace"** directly on the violation card and land on the exact
   Jaeger span, not a manual search by timestamp
4. Event reaches the coordinator → 2+ sites show a policy update
   pushed, zero order data crossed
5. **Flash sale**: hit "Simulate flash sale" → `/simulate-load` fires
   a burst at Site A → **Monitor screen** shows load spike, the
   desired-replica line jump ahead of current, then the kernel catch
   up → Roster and Federation show the animated pod-count badge climb
   ×1 → ×4 live, same SpacetimeDB subscription updating every screen
   at once → after the burst, scale back down within the shortened
   demo cooldown, both directions shown

## Rehearsal checklist

- Confirm the demo-mode scale-down cooldown is set (short, not
  production) before walking through
- Do one full timed dry run of the flash-sale sequence, note how long
  scale-up and scale-down actually take live
- Record a clean backup clip of the full sequence as a fallback
- Decide ahead of time who clicks "Simulate flash sale" live vs who
  narrates, so it isn't fumbled mid-sentence
- Confirm the Jaeger UI is reachable and the misdirection trace is
  easy to find before relying on it live (bookmark the trace ID or
  filter by service name in advance)
