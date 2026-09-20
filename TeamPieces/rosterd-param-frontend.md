# rosterd — Param: build the frontend (Joy's Person 3 spec, backend now real)

## Why this doc exists

`rosterd-joy-coordinator-frontend.md` specs three things: the coordinator
service, the SpacetimeDB module, and an 8-screen frontend. The first two
are built now — coordinator's real, and the SpacetimeDB module
(`rosterd-spacetimedb/`) is published and verified against a live server,
not just designed. The frontend is the one piece with zero code. This doc
is everything you need to build it against what actually exists, not
against the brief's aspirational shape — a few things drifted from the
brief during the build (flagged below wherever they matter).

Read `rosterd-joy-coordinator-frontend.md` first for the screen-by-screen
product spec (Review, Ask, Roster, Contracts, Federation, Monitor, plus
debug/trace-linking) — that document is still the source of truth for
*what each screen does*. This doc is the source of truth for *what's
actually there to build against*.

## What's running

```
frontend ──> ingestion-service (:8000)   confirm/draft manifests, /ask/parse
         ──> kernel-service (:8100)      dispatch, /agents/*, /policy
         ──> coordinator-service (:8300) /events, /sites, /policy/push
         ──> SpacetimeDB (:3000)         subscribe here for live tables
         ──> Jaeger (:16686)             "View trace" links
```

```bash
docker compose up --build   # from the repo root
```

brings up `spacetimedb`, `spacetimedb-publish` (one-shot, builds +
publishes the real module), `kernel-site-a`, `coordinator`,
`otel-collector`, `jaeger`. **`ingestion` is not in `docker-compose.yml`
yet** — run it separately:

```bash
cd rosterd-ingestion
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/run.sh   # :8000
```

(Add a Dockerfile + compose entry for it yourself if you'd rather have one
command bring up the whole thing — nothing stops you, just wasn't done
yet.)

Quick health check once everything's up:

```bash
curl localhost:8000/healthz   # ingestion
curl localhost:8100/healthz   # kernel
curl localhost:8300/healthz   # coordinator
curl localhost:3000/v1/ping   # spacetimedb
```

## SpacetimeDB: subscribe here, don't poll

Database name is **`rosterd`**, running at `ws://localhost:3000` (or
`ws://spacetimedb:3000` from inside the compose network — the frontend
itself will run on the host or its own container talking to `localhost:3000`
via the exposed port). Module source is `rosterd-spacetimedb/`.

Generate your client bindings — don't hand-write the wire format:

```bash
cd rosterd-spacetimedb
spacetime generate --lang typescript --out-dir ../rosterd-frontend/src/module_bindings --module-path ./spacetimedb
```

**Important:** the raw-HTTP calling convention documented in
`rosterd-spacetimedb/README.md` (JSON array body, `{"some": value}` for
present Option fields) is what kernel and coordinator's hand-rolled Python
HTTP client has to do because no generated Python bindings exist. You're
using the real generated TypeScript client SDK, which handles all of that
serialization for you — you should never construct that JSON by hand.
That gotcha section is not your problem; ignore it and use the generated
client's normal `conn.db.<table>.onInsert(...)` / subscription API.

The subscribe-and-render client flow (from the SDK's own docs — I have not
personally stood up a browser client against this module, only verified
the reducer-call and `spacetime sql` paths server-side, so treat this as a
starting point to verify yourself, not a tested snippet):

```typescript
import { DbConnection } from './module_bindings';

const conn = DbConnection.builder()
  .withUri('ws://localhost:3000')
  .withModuleName('rosterd')
  .onConnect((conn) => {
    conn.subscriptionBuilder()
      .onApplied(() => console.log('subscribed'))
      .subscribe([
        'SELECT * FROM agents',
        'SELECT * FROM agent_metrics',
        'SELECT * FROM sites',
        'SELECT * FROM events',
        'SELECT * FROM tasks',
        'SELECT * FROM manifests',
      ]);
  })
  .build();

conn.db.agents.onInsert((_ctx, row) => { /* Roster, Federation */ });
conn.db.agents.onUpdate((_ctx, oldRow, newRow) => { /* status/pod-count change, animate it */ });
conn.db.agentMetrics.onInsert((_ctx, row) => { /* Monitor sparkline */ });
conn.db.sites.onUpdate((_ctx, oldRow, newRow) => { /* Federation status */ });
conn.db.events.onInsert((_ctx, row) => { /* Federation activity feed, trace_id -> "View trace" */ });
```

### Tables (all public, all read-only from the frontend — every write goes
through a kernel/coordinator reducer call, never directly from the UI)

| Table | Row shape | Who writes it | Screen(s) |
| --- | --- | --- | --- |
| `agents` | `instance_id` (PK), `site_id`, `agent_id`, `name`, `status` (`"idle"⎮"working"⎮"killed"`), `updated_at` | kernel, every scaler tick pool change. **One row per running instance**, not per agent type — a pool of 3 is 3 rows sharing `agent_id`. | Roster (pod-count badge), Federation (concurrency dots: `status == "working"` = solid, `"idle"` = hollow) |
| `agent_metrics` | `id` (autoInc PK), `site_id`, `agent_id`, `timestamp`, `in_flight_count`, `queued_count`, `target_concurrency`, `current_replicas`, `desired_replicas`, `min_replicas`, `max_replicas` | kernel, **every** scaler tick regardless of change (append-only, so this grows fast — query with a `LIMIT`/time window per agent, don't subscribe to the whole table unbounded) | Monitor (sparkline + the literal formula readout, verbatim from `rosterd-kernel/scaler.py`: `load = in_flight_count + queued_count`; `desired = clamp(ceil(load / target_concurrency), min_replicas, max_replicas)`, `0` if `load == 0`. `desired_replicas` is already computed and stored on the row — you don't have to recompute it, just show the inputs alongside it for the "load: 8, target: 2, desired: ceil(8/2) = 4, clamped to max 4" readout.) |
| `sites` | `site_id` (PK), `status` (`"healthy"⎮"violation"⎮"offline"`), `last_event` (optional), `score` | coordinator, every `POST /events` | Federation (per-site status), Roster |
| `events` | `id` (autoInc PK), `site_id`, `run_id` (optional), `status`, `violation` (optional `{rule, expected, actual}`), `trace_id` (optional), `timestamp` | coordinator, every `POST /events` | Federation activity feed + "View trace" (`trace_id`), the Task Run & Violation screen |
| `tasks` | `task_id` (PK), `site_id`, `agent_id`, `title`, `status`, `assignees[]`, `criteria[]`, `priority`, `source` (optional), `created_at` | **nobody yet.** Table + `record_task` reducer exist; no service calls it. Roster's calendar-style scheduling has nothing live to subscribe to until something writes here — see "Gaps" below. | Roster |
| `manifests` | `manifest_id` (PK), `repo_url`, `status`, `agents_json`, `graph_json`, `created_at`, `version` | **nobody yet.** Table + `store_manifest` reducer exist (I built them as scaffolding); ingestion still stores confirmed manifests locally, not here. `agents_json`/`graph_json` are JSON-encoded strings, not a typed nested structure — ingestion's shape was still evolving when this was built, so parse them client-side. | Review, Contracts |

Reducers (called by kernel/coordinator only, never the frontend, listed
for completeness): `update_agent_status`, `record_agent_metrics`,
`update_site_score`, `record_event`, `record_task`, `store_manifest`.

## REST endpoints (everything that isn't a SpacetimeDB subscription)

### Ingestion (`:8000`) — Ingest, Review, Contracts, Ask screens

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/ingest` | **Body is `{repo_url, constraints_yaml}` — `constraints_yaml` is required.** The brief's Ingest screen says "repo URL only, no file upload"; that's not what's actually implemented (see Gaps). Returns `IngestResponse` (`manifest_id`, `status: "draft"`, `agents: AgentManifestEntry[]`, `graph: GraphSpec`). |
| `GET` | `/manifest/{id}` | Same shape as above (`ManifestResponse`). Poll or call once after ingest/confirm. |
| `POST` | `/manifest/{id}/confirm` | Body `{agents: AgentManifestEntry[]}` (edited rows from Review). Returns `{manifest_id, status: "confirmed"}`. This is the Review screen's "Confirm and go live" button. |
| `POST` | `/ask/parse` | Body `{manifest_id, text}`. Returns `{agent_id, task: {title, description, priority, expectation_criteria[]}, confidence: "high"⎮"medium"⎮"low"}`. This is the Ask screen's proposed-task card. |

**A real, public `repo_url` to test Ingest against:** `https://github.com/SathvikNapa/rosterd-example` — a standalone mirror of `rosterd-demo-agent` (kept in sync by copying, not a submodule), pushed specifically so ingestion has a real `git clone`-able URL instead of needing `ROSTERD_LOCAL_REPO_ROOT` pointed at a locally-staged fixture. This is now the frontend's Ingest-screen default. Verified live: `POST /ingest` against it discovers all three agents (`order_intake`, `fulfillment`, `refund_exception`) exactly like the monorepo copy does.

`AgentManifestEntry`: `{id, node, purpose, tools[], direct_assignable, entry_only_via[], constraints: {max_refund_usd?, requires_prior_node?, ...any extra keys}}`.
Note `constraints` here is a **free-form object**, not the
`list[ConstraintRule]` (field/op/value/source/confidence) shape the kernel
actually enforces internally — kernel translates it via a stopgap adapter
(`rosterd-kernel/legacy_constraints.py`). For Review's source badges
(`schema`/`code guard`/`interrupt() detected`/`default`, colored by
confidence), there's currently **no `source`/`confidence` per constraint
in ingestion's real response** — that inference (3-source constraint
detection) isn't built yet either. You'll likely need to either render a
simplified Review row for now (no badge) or coordinate with whoever picks
up ingestion's remaining gaps to add it.

### Kernel (`:8100`) — Roster, Federation ("Simulate flash sale"), Monitor

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/dispatch` | `{agent_id, task: TaskSpec, assignees[]}` → `{run_id, status: "accepted"⎮"rejected", reason?}`. This is what "Do it" on the Ask screen calls. |
| `GET` | `/runs/{run_id}` | Poll for the Task Run & Violation screen if you're not just watching `events` land in SpacetimeDB. |
| `POST` | `/runs/{run_id}/kill` | |
| `POST` | `/agents/{agent_id}/simulate-load` | Body `{count?, rate_per_second?, scale_down_after_idle_seconds_override?}` → `{agent_id, dispatched}`. **This is Federation's "Simulate flash sale" button** — brief is explicit this should be the one wired up, not the manual `/agents/{agent_id}/scale`. |
| `GET` | `/agents/{agent_id}/instances` | Debug/fallback; prefer subscribing to `agents` filtered by `agent_id`. |

### Coordinator (`:8300`) — mostly read via SpacetimeDB, these are fallback/debug

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/sites` | Thin read of `sites` — subscribe to the table instead per the brief; this is fallback only. |
| `GET` | `/events?site_id=&limit=` | Debug read of `events` (same fallback role as `/sites`) — not in the original brief's API contract table but exists in the real service; subscribe to `events` instead. |
| `POST` | `/events`, `POST /policy/push` | Kernel-to-coordinator and coordinator-to-kernel, not called by the frontend. |

## Trace linking

`JAEGER_BASE_URL=http://localhost:16686`. Confirmed working (Jaeger UI is
up and both kernel/coordinator spans show under service names
`rosterd-kernel-site-a` and `rosterd-coordinator`). Wherever an `events`
row or a `GET /runs/{run_id}` response carries a non-null `trace_id`,
render `"View trace"` linking to `{JAEGER_BASE_URL}/trace/{trace_id}`.

## Gaps to know about before you build against them

1. **Ingest isn't "repo URL only" in practice.** `POST /ingest` requires
   `constraints_yaml` in the request body today. Either the frontend
   collects it (textarea, or a file-read-into-string on the client) or
   someone changes ingestion to make it optional/inferred. Worth a quick
   sync with whoever owns ingestion before you build the Ingest screen's
   final form.
2. **`tasks` has no writer.** If Roster's scheduling view needs live data,
   something has to start calling `record_task` — most likely the
   frontend's own Ask flow after a successful `/dispatch`, since nothing
   else in the backend produces a `TaskSpec`-shaped commitment today.
   Confirm ownership before assuming it'll just show up.
3. **`manifests` has no writer.** Review/Contracts subscribing to
   `manifests` will see nothing until ingestion is wired to call
   `store_manifest` on confirm. Until then, drive Review/Contracts off
   `GET /manifest/{id}` directly (matches the brief's fallback note: "no
   frontend in this pass" was true when that was written, but the
   underlying REST fallback still works fine).
4. **Per-constraint source/confidence isn't in ingestion's response**
   (see the Ingestion table above) — Review's colored source badges need
   either a fallback rendering or an ingestion-side change.
5. **The subscription/client flow above is unverified.** I built and
   proved the write side (reducers, via raw HTTP and via kernel/
   coordinator's real code) and the CLI read side (`spacetime sql`). I did
   not stand up a browser client and subscribe. Budget time to debug the
   generated-bindings step — it's the one part of this handoff that's
   "should work per the SDK's own docs," not "watched it work."

## Everything else the brief already tells you

Screen-by-screen requirements (Review's edit affordance, Roster's animated
pod-count, Federation's per-instance concurrency dots, Monitor's formula
readout, Ask's confidence card) are unchanged from
`rosterd-joy-coordinator-frontend.md` — that spec's still right about
*what* each screen shows. This doc is only the *is it actually there*
layer underneath it.
