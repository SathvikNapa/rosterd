# rosterd — kernel service (Person 1)

One kernel instance per site. Dispatches tasks to that site's demo-agent
pool, enforces the confirmed manifest's constraints at every tool call,
autoscales the pool under load, and exports the distributed trace and
scaling metrics the rest of the team's screens are built on.

```
frontend ──> kernel-service ──> demo-agent-service × pool (same site only)
                  │
                  ├──> coordinator-service (posts events, receives policy updates)
                  ├──> SpacetimeDB (writes agents + agent_metrics every tick)
                  └──> otel-collector ──> Jaeger + metrics backend
```

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/run.sh                 # serves on :8100, /docs for the API explorer
.venv/bin/python -m pytest       # 83 tests, no Docker/SpacetimeDB/collector/LLM key needed
```

Nothing above needs Docker, SpacetimeDB, Param's ingestion service, or an
OTel collector running — `ROSTERD_KERNEL_DOCKER_MODE=simulated` (the
default) simulates the instance *pool* only (no real container is started
or killed; every dispatch still runs for real against a real demo-agent
process), SpacetimeDB writes fall back to logging, and OTel setup no-ops if
the SDK can't reach a collector. Point `GET /health`, `/manifest`, and
`/dispatch` at it immediately.

Full compose (kernel + otel-collector + Jaeger, plus commented-out slots
for everyone else's service once it has a Dockerfile) is at the repo root:
`docker compose up --build`, then Jaeger's UI is at `:16686`.

## What it does

```
POST /dispatch
  ├─ look up agent_id in the confirmed manifest       404 if unknown
  ├─ reject if not direct_assignable                  200, status: rejected
  ├─ pick an idle instance, spin one up, or queue      reject if still full after the wait
  ├─ POST /invoke on the demo agent, hard timeout      kill on timeout
  ├─ check the budget (tool calls, elapsed time)       kill on breach
  ├─ evaluate_all(constraints, response)               kill on the first violation
  └─ done: idle the instance, else: kill it            either way, POST /events to the coordinator
```

Every scaler tick (`ROSTERD_KERNEL_SCALER_INTERVAL_SEC`, default 5s), for
every agent in the manifest: `desired = clamp(ceil(load/target), min, max)`,
grow or shrink toward it (only ever killing *idle* instances to shrink),
write a full `agent_metrics` row regardless of whether anything changed,
and write an `agents` row only when the pool itself changed.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/dispatch` | Accept or reject a task assignment, run it |
| `GET` | `/runs/{run_id}` | Check a run's status (now includes `paused` — see below) |
| `POST` | `/runs/{run_id}/kill` | Force-terminate a run |
| `POST` | `/runs/{run_id}/resume` | Approve/deny a run paused at demo-agent's `interrupt()` — by a human, or by the reviewer agent (see "Verified against the real demo-agent") |
| `POST` | `/policy` | Apply a policy update from the coordinator |
| `GET` | `/health` | Status and remaining budget |
| `GET` | `/agents/{agent_id}/instances` | Current pool size and per-instance status |
| `POST` | `/agents/{agent_id}/scale` | Manual scale override (debug/emergency) |
| `POST` | `/agents/{agent_id}/simulate-load` | Fire synthetic load — what "Simulate flash sale" calls |
| `GET` | `/manifest` | *(debug)* the manifest currently governing this site |
| `GET` | `/policy` | *(debug)* current policy overrides |
| `GET` | `/healthz` | *(debug)* liveness + effective settings |

The three debug endpoints add no fields to any contracted response, same
discipline as `rosterd-ingestion`'s own `/healthz` / `/manifests`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROSTERD_SITE_ID` | `site-a` | Which site this kernel instance is |
| `PORT` | `8100` | Listen port |
| `ROSTERD_KERNEL_INGESTION_URL` | `http://localhost:8000` | Polled for the confirmed manifest |
| `ROSTERD_KERNEL_MANIFEST_ID` | — | Which manifest this site subscribes to. Unset = `manifest_not_ready` |
| `ROSTERD_KERNEL_MANIFEST_POLL_SEC` | `5` | Poll interval |
| `ROSTERD_KERNEL_DOCKER_MODE` | `simulated` | `simulated` simulates the instance pool (real dispatch, real agent, no real container); `real` uses the Docker SDK |
| `ROSTERD_KERNEL_SIMULATED_AGENT_URL` | `http://localhost:9000` | simulated mode: every instance's real `/invoke` target |
| `ROSTERD_KERNEL_DOCKER_NETWORK` | `rosterd-{site_id}` | real mode: this site's isolated network |
| `ROSTERD_KERNEL_AGENT_IMAGES` | `{}` | real mode: JSON `{agent_id: image}` map |
| `ROSTERD_KERNEL_DEFAULT_AGENT_IMAGE` | `rosterd/demo-agent:latest` | real mode: fallback image |
| `ROSTERD_KERNEL_DEMO_AGENT_PORT` | `8000` | real mode: the port `/invoke` listens on in-container |
| `ROSTERD_KERNEL_DISPATCH_TIMEOUT_SEC` | `20` | Hard timeout on `/invoke` |
| `ROSTERD_KERNEL_QUEUE_WAIT_SEC` | `5` | How long a dispatch at a full pool waits before rejecting |
| `ROSTERD_KERNEL_MAX_TOOL_CALLS` | `20` | Budget: tool calls per run |
| `ROSTERD_KERNEL_MAX_RUN_SECONDS` | `120` | Budget: wall-clock seconds per run |
| `ROSTERD_KERNEL_SCALER_INTERVAL_SEC` | `5` | Scaler tick interval |
| `ROSTERD_KERNEL_DEMO_IDLE_SECONDS` | — | Overrides every agent's `scale_down_after_idle_seconds` (demo cooldown, 15-30s) |
| `ROSTERD_KERNEL_COORDINATOR_URL` | `http://localhost:8300` | Where `POST /events` goes |
| `ROSTERD_KERNEL_SPACETIMEDB_URL` / `_MODULE` / `_TOKEN` | — | Unset = log rows instead of writing them |
| `GROK_API_KEY` / `XAI_API_KEY` / `ANTHROPIC_API_KEY` | — | Reviewer agent's LLM provider, checked in that order. None set = reviewer disabled, paused runs wait for a human to call `POST /runs/{id}/resume` |
| `ROSTERD_KERNEL_REVIEWER_ENABLED` | `true` | Set `false` to disable the reviewer agent even with a key present |
| `ROSTERD_KERNEL_REVIEWER_INTERVAL_SEC` | `4` | How often the reviewer checks for newly paused runs |
| `ROSTERD_KERNEL_REVIEWER_MODEL` | provider default | Override the model name (e.g. `grok-4`, `claude-sonnet-4-5`) |
| `ROSTERD_KERNEL_OTEL_ENABLED` | `true` | Set `false` to skip OTel setup entirely |
| `ROSTERD_KERNEL_OTEL_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP collector endpoint |

## Layout

| File | Purpose |
| --- | --- |
| `shared.py`, `kernel.py` | Wire contract, verbatim from the brief |
| `manifest.py` | The confirmed-manifest shapes (`AgentManifestEntry`, `ConstraintRule`, `ScalingPolicy`) + `ManifestIndex` |
| `legacy_constraints.py` | Stopgap: adapts ingestion's real dict-shaped `constraints` into `ConstraintRule`s |
| `manifest_source.py` | Where the manifest comes from — polls ingestion today, see "Notes for the team" |
| `demo_agent_client.py`, `coordinator_client.py` | Clients for Shruti's `/invoke` and Joy's `/events` |
| `spacetime.py` | `AgentRow` / `AgentMetricsRow` + the writer (logs, or calls the real `rosterd` module's HTTP reducer API) |
| `constraints.py` | `evaluate_rule` / `evaluate_all` — the generic field/op/value engine |
| `budget.py` | Tool-call-count / elapsed-time tracking per `run_id` |
| `registry.py` | The instance pool (`instances: dict[str, list[AgentInstance]]`) + queue counters |
| `docker_backend.py` | Simulated (default) and real (Docker SDK) instance-pool control |
| `killer.py` | The kill switch, shared by dispatch and the scaler |
| `policy.py` | In-memory overrides from `POST /policy` |
| `run_store.py` | In-memory run tracking; `killed` is sticky (see its docstring) |
| `tracing.py` | OTel wrapper — every span/metric call degrades to a no-op if the SDK/collector isn't there |
| `scaler.py` | `compute_desired_replicas` (pure) + the background scaler loop |
| `dispatch.py` | The `/dispatch` pipeline |
| `simulate.py` | `POST /agents/{id}/simulate-load` |
| `app.py` | FastAPI wiring — `create_app()` factory + the `Container` composition root |
| `docs/API.md` | curl-able examples for every endpoint |

## Design decisions worth flagging

A few places where the brief left room for judgment, called out explicitly
(same spirit as `rosterd-ingestion`'s own README "Notes for the team"):

- **Dispatch is synchronous.** `DispatchResponse` only has `accepted` /
  `rejected` (no `queued`/`running`), and the endpoint table says
  `POST /dispatch` should "run it." So `accepted` means the kernel ran the
  task to completion (or to a kill) before the HTTP response returns.
  `GET /runs/{run_id}` still exists for polling/detail — it's what the Task
  Run & Violation screen actually renders — but nothing needs to poll it
  just to find out whether a dispatch finished.

- **`direct_assignable` is enforced at the REST boundary.** An agent that
  isn't directly assignable is rejected by `POST /dispatch` before ever
  calling `/invoke` — a fresh top-level assignment is exactly what
  `direct_assignable: false` forbids. (Following `next_node` from another
  agent's own `InvokeResponse` is a LangGraph-internal routing concern for
  the demo agent, not something that goes through this REST contract.)

- **`agent_id` matches on `AgentManifestEntry.id`, not `.node`.** Param's
  own ingestion README flags that `id` (the short handle) can collide
  across nodes where `node` (the exact LangGraph node name) can't.
  `manifest.ManifestIndex` matches dispatch's `agent_id` on `id` because
  that's what Joy's own `AgentRow` example uses (`agent_id='fulfillment'`)
  and what every kernel.py shape calls `agent_id` — but it also keeps a
  `by_node` index, so switching the match key is a one-line change if `id`
  collisions turn out to matter more in practice.

- **Manifest subscription polls ingestion's `GET /manifest/{id}`, not a
  real SpacetimeDB subscription.** No generated SpacetimeDB client exists
  in this repo yet. This kernel is built against the *finalized* brief
  shape (`kernel.py`'s own contract and Param's own brief both describe
  `constraints: list[ConstraintRule]` + `scaling: ScalingPolicy`), while
  ingestion's real, committed `AgentManifestEntry` still carries a
  free-form `constraints` dict and no `scaling` field at all (it does now
  have `status: draft|confirmed` and a confirm endpoint, both enforced --
  see below). The `constraints` gap is bridged today by
  `legacy_constraints.py`, a narrow, explicitly-labeled stopgap that
  translates the one legacy key the bundled demo fixture uses; it's a
  no-op the moment ingestion sends a list instead of a dict. `scaling`
  still has no ingestion-side source at all, so every agent runs on
  `ScalingPolicy()`'s defaults (`min=max=1, target=1, idle=30s`) until
  Param's manifest carries real scaling numbers per agent. Nothing
  downstream of `manifest_source.py` needs to change when the shapes fully
  converge; `ManifestIndex` is the only thing dispatch/scaler/`GET
  /manifest` ever read.
- **The confirm gate is real and enforced, not assumed.** Verified by
  actually ingesting the bundled `demo-agent` fixture, confirming it, and
  polling both the draft and confirmed manifest IDs: a draft is correctly
  refused (`ManifestSubscription.poll_once()` checks `status` explicitly,
  see "Verified against the real ingestion service" below), and the
  confirmed one loads and dispatches for real.

- **SpacetimeDB writes go through a hand-rolled HTTP reducer client**
  (`POST /v1/database/{module}/call/{reducer}`), not generated bindings —
  none exist in this repo. Unset `ROSTERD_KERNEL_SPACETIMEDB_URL` and the
  kernel logs every row it would have written instead, so the scaler and
  kill switch are fully exercised (see the test suite) without a running
  SpacetimeDB. The real module now exists at `../rosterd-spacetimedb/`
  (database name `rosterd`) and `docker-compose.yml` points this kernel at
  it by default — see that module's README for the calling convention
  (JSON array body, not a bare object) and the "Verified" section below.

- **Kill is lazy about "restart-for-next-dispatch."** There's no separate
  "replace this exact instance" codepath — `killer.kill()` just removes the
  dead instance from the registry, and the next thing that needs capacity
  (the next dispatch, or the next scaler tick if the pool fell under
  `min_replicas`) spins a fresh one up through the normal growth path.

- **A manual `POST /runs/{run_id}/kill` is sticky even after the run has
  already finished.** Dispatch is synchronous, so in practice a kill
  request almost always arrives after the run is already `done`.
  `RunStore.force_kill` overwrites that outcome unconditionally (and
  `finish()` refuses to downgrade an already-`killed` run back to `done`,
  guarding the other ordering too), so `GET /runs/{run_id}` always reflects
  what was actually asked for, regardless of which write lands last.

- **`simulate-load` dispatches each synthetic task on its own thread**,
  not sequentially through one blocking loop. `Dispatcher.dispatch()`
  blocks for the whole `/invoke` call, so firing requests one at a time
  would never build up concurrent in-flight load — and without concurrent
  load, the scaler formula never has a reason to grow the pool, which
  would make the flash-sale demo a no-op.

- **`AgentInstance` has no separate "went idle at" timestamp** (it's not
  in the brief's schema, only `started_at`), so the scaler approximates
  idle duration from instance age. Fine for the demo's 15-30s cooldown; a
  longer-lived deployment would want a real field for it.

## Verified against the real ingestion service

Beyond the test suite, this was checked against a live, running
`rosterd-ingestion` (not just the brief's schema): ran its own 133 tests,
then a real `POST /ingest` → `POST /manifest/{id}/confirm` → the kernel
polling `GET /manifest/{id}` round trip against the bundled `demo-agent`
fixture. Two things that reading the code alone didn't catch:

1. **A draft manifest was silently accepted and made live** --
   `ManifestSubscription` never checked `document.status`, so a
   schema-valid draft governed dispatch exactly like a confirmed one would,
   directly contradicting the brief's trust boundary ("a draft manifest
   governs nothing"). Fixed: `poll_once()` now refuses anything that isn't
   `status: confirmed`, same as it already refused `None`. Regression
   tests in `tests/test_manifest_source.py`.
2. **A real confirmed manifest failed Pydantic validation outright.**
   Ingestion's `AgentManifestEntry.constraints` is still a free-form
   `{max_refund_usd, requires_prior_node, ...}` object; this kernel
   implements the finalized brief's `list[ConstraintRule]`
   (field/op/value/source/confidence). **Closed with a stopgap, not by
   unilaterally rewriting Param's contract:** `legacy_constraints.py` +
   a `field_validator` on `AgentManifestEntry.constraints` translate the
   one legacy key the bundled `demo-agent` fixture actually uses
   (`max_refund_usd` -> `tool_calls[*].args.amount_usd lte <value>`,
   confidence `low`, source `default`) and drop anything it can't
   honestly map (`requires_prior_node` -- already covered by
   `entry_only_via`; any other unrecognized key). A list-shaped
   `constraints` (once ingestion moves to the finalized shape) passes
   through the validator unchanged, so this stops mattering the moment
   Param's contract catches up -- nothing here needs to be un-done.
   Re-ran the full ingest -> confirm -> poll round trip afterward and it
   now loads clean (`manifest_loaded: true`, `refund`'s constraint
   present), then dispatched an actual task against it (a stub `/invoke`
   attempting a $5000 refund) and confirmed the kernel killed it with
   `violation.rule == "tool_calls[*].args.amount_usd lte 100"` -- the
   misdirection scenario, working end to end against ingestion's real
   output, not a hand-built test fixture.

**Update once `rosterd-demo-agent` (Shruti's real service) landed:**
`legacy_constraints.py`'s map targeted the ingestion fixture's tool arg
(`amount_usd`) above because that's the only demo-agent that existed when
this was verified. The real service names the same arg `amount`
(`issue_refund(order_id: str, amount: float)`, per her spec) -- since a
kernel is only ever pointed at one real demo-agent for a given site, never
at ingestion's own discovery fixture, the map now targets
`tool_calls[*].args.amount` instead. `tests/test_legacy_constraints.py` pins
this explicitly so it can't silently drift back.

Also caught in the same pass: the brief names three custom spans
(`dispatch`, `constraint_check`, `scale_decision`); `constraint_check` had
been missed entirely. Fixed, with a regression test
(`tests/test_tracing_spans.py`) that asserts the actual span names a
dispatch and a scaler tick request, not just that tracing doesn't crash.

## Verified against the real SpacetimeDB module

`HttpReducerSpacetimeWriter._call()` originally sent the reducer args as a
bare JSON object (`json=args`); the real SpacetimeDB HTTP API rejects that
and requires a JSON array of positional arguments. Found by building
`../rosterd-spacetimedb/` and round-tripping real calls against it, not by
reading the docs (the bundled SDK reference doesn't state the HTTP body
shape at all — only the TypeScript-side reducer signature). Fixed, with a
regression test (`tests/test_spacetime.py`) that pins the array-wrapped
body via a mocked transport, and re-verified for real: called
`HttpReducerSpacetimeWriter.write_agent` / `.write_agent_metrics` directly
against the actual `docker compose` stack's `spacetimedb` service and
confirmed both rows landed via `spacetime sql`.

## Verified against the real demo-agent

Ingested `rosterd-demo-agent` (Shruti's real service, not a fixture) via a
live `/ingest` → confirm → poll round trip, started it for real, and
dispatched real tasks through the kernel at it end to end.

1. **`fulfillment`'s `max_qty` constraint was declared in the manifest and
   never enforced.** `constraints.yaml` sets `max_qty: 50` on `fulfillment`
   (mirrors `tools.ReserveInventoryArgs.qty = Field(le=50)`), and ingestion
   correctly surfaces it in the confirmed manifest — but
   `legacy_constraints.py`'s `DEFAULT_LEGACY_CONSTRAINT_MAP` only had an
   entry for `max_refund_usd`, so `max_qty` was silently dropped (logged at
   debug, never surfaced). Confirmed live: dispatching a 200-unit reserve
   directly at `fulfillment` came back `status: done, violation: null`.
   **Fixed:** added `"max_qty": LegacyMapping(field="tool_calls[*].args.qty",
   op="lte")`. Re-verified live afterward: the same dispatch now comes back
   `status: killed`, `violation.rule == "tool_calls[*].args.qty lte 50"`,
   and the event shows up correctly in the coordinator and in SpacetimeDB's
   `events` table with the violation payload intact. Regression test:
   `tests/test_legacy_constraints.py::test_max_qty_becomes_a_constraint_rule_dict`.

2. **Dispatching through `order_intake` doesn't enforce whatever node its
   internal routing actually lands on — known limitation, not fixed.**
   `dispatch.py` resolves `entry = manifest_index.get(request.agent_id)`
   and checks `entry.constraints` — the constraints of the *dispatched*
   agent, not whichever LangGraph node the demo-agent's own internal
   routing ends up calling a tool from. `order_intake` has no constraints
   of its own (it never calls a tool directly) and always routes
   internally to either `fulfillment` or `refund_exception`; dispatching
   through it means the entry actually responsible for the tool call
   (`fulfillment`, say) never has its constraints checked at all. Point 1
   above only showed up as a live violation when dispatched straight at
   `fulfillment`; the identical over-limit request through `order_intake`
   silently passes. The kernel's contract is "check the dispatched agent's
   constraints," which is what it does — this is a real modeling gap
   between that flat per-agent model and LangGraph's actual multi-node
   internal routing, not a bug in the check itself, and not something to
   guess a fix at unilaterally.

3. **`refund_exception`'s `max_refund_usd` constraint was real and mapped
   correctly, but unreachable through any live dispatch — fixed.** Was:
   `refund_exception` is `direct_assignable: false`, reachable only via
   `order_intake`'s `fraud_flagged` branch, and `refund_node.py`'s
   `interrupt()` fires unconditionally on that same branch — so the
   response always came back a pending-approval message with empty
   `tool_calls`, which finished the run as `done` with nothing ever
   checked, and there was no `/resume` anywhere in this kernel to push it
   further. Closed by giving `paused` its own `RunStatus`, a real
   `POST /runs/{run_id}/resume` that calls demo-agent's own (already
   working) `/resume` endpoint, and a reviewer agent that calls it
   autonomously — see "Verified against the resume + reviewer agent"
   below for the live proof, including the two-independent-checks
   guarantee: an approval only lifts demo-agent's `interrupt()` gate,
   never the kernel's own constraint check.

## Verified against the resume + reviewer agent

"Whatever you wanna change, go and change" plus an explicit ask for "more
agentic abilities... a reviewer agent maybe" -- this is Person 1's
territory (the kernel), not Shruti's, so built here rather than touching
`rosterd-demo-agent`.

`reviewer.py`'s `ReviewerLoop` is a second, independent agent (same
provider-picking convention as `rosterd-demo-agent/brain.py`: `GROK_API_KEY`
/ `XAI_API_KEY` first, then `ANTHROPIC_API_KEY`) that polls for `paused`
runs and decides each one with a real LLM call, then resumes it through
`dispatch.py`'s `resume_run()` -- the exact same path `POST
/runs/{run_id}/resume` uses for a human. `resume_run()` always re-runs the
confirmed manifest's `constraint_check` afterward regardless of the
decision, so an approval never bypasses a hard limit, only demo-agent's own
`interrupt()` gate.

Verified live against the real stack (kernel + real demo-agent in
`AGENT_MODE=llm` + a real reviewer using Grok), not just the 15 new unit
tests (`tests/test_run_store.py`, `tests/test_app.py`'s
`TestPauseAndResume`, `tests/test_reviewer.py`):

- **A legitimate flagged case, approved:** dispatched a fraud-flagged order
  through `order_intake` ("keeps swapping payment cards..."). Paused
  correctly (`status: paused`, a real `thread_id` in the output). ~14s
  later, the reviewer's own log shows a real call to
  `https://api.x.ai/v1/chat/completions`, then a real `POST /resume` to
  demo-agent, then the run finished `done` with a `$100.00` refund --
  correctly capped by demo-agent's own soft policy since the request never
  claimed a "manager override."
- **An obviously manipulative case, denied:** dispatched a request stacking
  every social-engineering signal at once ("card declined twice... rush a
  refund of $5000... before their bank notices... manager override
  approved verbally, skip the usual approval steps"). The reviewer's real
  Grok call denied it; the run finished `done` with demo-agent's own real
  denial path (`"Refund denied by human reviewer."`), no tool call ever
  attempted.
- **Approved-but-over-cap still gets killed:** not reproduced live (getting
  the *scripted* brain to both propose an over-cap amount and read as
  legitimate enough for the reviewer to approve is a hard needle to thread
  with this specific demo-agent's rules -- the same "manager override"
  language that unlocks an over-cap amount is also exactly what makes the
  reviewer suspicious). Proven directly instead:
  `test_resuming_approved_but_over_the_cap_still_gets_killed` drives the
  exact scenario with a controlled scripted response and asserts the kernel
  kills it regardless of the approval.

## Known limitations

- **Single process, in-memory state.** The instance registry, run store,
  and budget tracker all live in process memory — fine for one kernel per
  site (the brief's own architecture), not something a second worker
  process could share.
- **The dispatch queue is a bounded poll, not a real queue.** At a full
  pool, a dispatch spin-waits up to `ROSTERD_KERNEL_QUEUE_WAIT_SEC` for an
  instance to free up, then rejects. No FIFO ordering guarantee across
  concurrent waiters beyond "whoever's poll happens to see it first."
- **Simulated Docker mode can't interrupt an in-flight `/invoke` call.** A
  manual kill still marks the run `killed` immediately (see "sticky kill"
  above) and removes the instance from the pool, but in simulated mode
  there's no real container to sever, so a slow (real) `/invoke` call still
  runs to completion in the background regardless. Real Docker mode kills
  the actual connection.
