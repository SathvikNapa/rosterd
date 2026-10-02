# rosterd - coordinator service

The federation layer. Receives a run/scale event from every site's kernel,
tracks each site's health, and pushes a tightened policy to every kernel
when the same violation shows up at 2+ distinct sites.

```
kernel-site-A ──┐
kernel-site-B ──┼──> coordinator-service ──> Postgres (agents/tasks/sites/events)
kernel-site-C ──┘         │                      │
                           │                      └──> live_ws.py relay ──> browsers
                           └── policy update ────────>
                                back to any site
```

The coordinator-service (Python/FastAPI, matching the conventions
`rosterd-ingestion` and `rosterd-kernel` already established) and the
Postgres schema (`../rosterd-postgres/schema.sql`, database name
`rosterd`) are both built and wired together. A browser can't open a raw
Postgres connection, so this service also relays Postgres's own
LISTEN/NOTIFY to connected frontend clients over a plain WebSocket
(`adapters/http_in/live_ws.py`) -- see "Notes for the team" below for why
it lives here rather than as its own service.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/run.sh                 # serves on :8300, /docs for the API explorer
.venv/bin/python -m pytest       # 38 tests, no Postgres/kernel/collector needed
```

Nothing above needs a live kernel, Postgres, or OTel collector --
`ROSTERD_COORDINATOR_SITE_KERNELS` unset just means there's nowhere to
broadcast a policy push to (harmless no-op), and Postgres writes fall
back to logging. Verified end-to-end against two real, independently
running processes (a live coordinator + a live `rosterd-kernel`, not
mocks), again across the full `docker compose` stack, and separately
against a real Postgres container -- see "Verified" below.

## What it does

```
POST /events
  ├─ record the site's health (status + rolling score)     write `sites`
  ├─ append to the event log                                write `events`
  └─ if the event carries a Violation:
       ├─ has the SAME rule been violated at 2+ distinct
       │  sites within the lookback window?                 patterns.py
       ├─ yes, and not already pushed within the cooldown:
       │    ├─ tighten the bound (lte: halve it, gte: double it)
       │    ├─ return it inline to the reporting kernel      EventResponse.policy_update
       │    └─ POST /policy to every OTHER known kernel      broadcaster.py
       └─ no: EventResponse.policy_update is null
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/events` | Kernel posts a run or scale event, may get a `policy_update` back |
| `GET` | `/sites` | Per-site status and score (debug/fallback -- the frontend subscribes to Postgres's `sites` table directly instead) |
| `POST` | `/policy/push` | Broadcast a policy update to every known kernel |
| `GET` | `/events` | *(debug)* recent event log, optionally `?site_id=` filtered |
| `GET` | `/healthz` | *(debug)* liveness + effective settings |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8300` | Listen port |
| `ROSTERD_COORDINATOR_SITE_KERNELS` | `{}` | JSON `{site_id: kernel_base_url}` -- who a pushed policy fans out to |
| `ROSTERD_COORDINATOR_OFFLINE_AFTER_SEC` | `30` | No event this long -> `GET /sites` reports `offline` |
| `ROSTERD_COORDINATOR_OFFLINE_SWEEP_SEC` | `5` | Background sweep interval that keeps the Postgres `sites` row current for a silent site |
| `ROSTERD_COORDINATOR_SCORE_WINDOW` | `20` | How many of a site's recent events feed its health score |
| `ROSTERD_COORDINATOR_PATTERN_THRESHOLD` | `2` | Distinct sites needed to call something a "shared failure pattern" |
| `ROSTERD_COORDINATOR_PATTERN_WINDOW_SEC` | `300` | Lookback window for counting distinct sites |
| `ROSTERD_COORDINATOR_PATTERN_COOLDOWN_SEC` | `60` | Don't re-push the same rule more than once within this long |
| `ROSTERD_COORDINATOR_TIGHTEN_FACTOR` | `0.5` | How hard an `lte`/`gte` bound tightens (0.5 = halve the ceiling / double the floor) |
| `ROSTERD_COORDINATOR_PUSH_TIMEOUT_SEC` | `5` | Per-kernel HTTP timeout when broadcasting |
| `ROSTERD_COORDINATOR_SPACETIMEDB_URL` / `_MODULE` / `_TOKEN` | - | Unset = log rows instead of writing them |
| `ROSTERD_COORDINATOR_OTEL_ENABLED` | `true` | Set `false` to skip OTel setup entirely |
| `ROSTERD_COORDINATOR_OTEL_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP collector endpoint |

### Adding a second site

The compose file at the repo root only stands up `kernel-site-a`. To watch
the "policy pushed to sites it *wasn't* reported from" half of the demo for
real, add a second kernel service (`kernel-site-b`, its own
`ROSTERD_SITE_ID`/port/network) and extend the coordinator's
`ROSTERD_COORDINATOR_SITE_KERNELS`:

```json
{"site-a": "http://kernel-site-a:8100", "site-b": "http://kernel-site-b:8100"}
```

Everything else -- pattern detection, the broadcast, the exclusion of the
reporting site -- already works for however many sites are configured; it
was written and tested against an arbitrary registry, not hardcoded to one.

## Design decisions worth flagging

Same spirit as `rosterd-ingestion` and `rosterd-kernel`'s own READMEs:

- **The rule-tightening formula is invented, not specified.** The brief
  says "push a policy update... when a shared failure pattern is
  detected," not what the new value should be. `patterns.py` halves an
  `lte` ceiling / doubles a `gte` floor on the bound that was in force when
  the violation happened (parsed straight out of the kernel's own
  `Violation.rule` string, e.g. `"...amount lte 100"` -> new bound `50`).
  `eq`/`in`/`not_in` violations are still detected and logged, just not
  auto-tightened -- there's no obvious "tighter" for those without knowing
  what a specific business rule means.
- **Which kernels exist is static config, not discovery.** `EventRequest`
  doesn't carry a reporting site's own kernel URL (that would mean widening
  Joy's contract), and there's no service registry in this repo. See
  "Adding a second site" above.
- **`GET /sites` and `GET /events` compute everything in-process**, not by
  reading Postgres back. The brief calls `GET /sites` "mostly for
  debugging, frontend subscribes directly [to Postgres] instead" --
  Postgres writes here are a side-channel for that direct subscription,
  kept in sync but never read from.
- **`offline` status is computed lazily** (`now - last_event >
  threshold`), not stored, so `GET /sites`/`GET /events` are never stale
  regardless of read timing. The one place that *does* need to notice a
  site going quiet without being asked is the Postgres `sites` row the
  frontend subscribes to -- `sweeper.py` is a small background thread for
  exactly that, same shape as the kernel's own `ScalerLoop`.
- **A kill without a `Violation` still counts against a site's score and
  status.** A manual `POST /runs/{id}/kill` on the kernel produces a
  `killed` event with no `violation` payload; that's still trouble for the
  site reporting it, just not a "shared failure pattern" candidate (there's
  no rule to compare across sites).

## Known limitations

- **Single process, in-memory state.** Site health, the event log, and the
  pattern detector's sighting windows all live in process memory -- fine
  for one coordinator (the brief's own architecture: coordinator is a
  single federation point, not per-site), not something a second worker
  process could share.
- **`tasks` table ownership is unaddressed.** The brief lists `tasks`
  among the tables the coordinator half owns, and the real schema has both
  the table and space for a writer (`TaskRow` is defined in
  `adapters/postgres/postgres.py` for reference) -- but nothing in the
  coordinator's own contract (`POST /events` / `GET /sites` / `POST
  /policy/push`) ever produces one. It's most likely populated directly by
  the frontend's Ask flow, not routed through the coordinator.
- **No frontend screens in this pass** (see the scope note up top), though
  the live-data wire it reads IS this service's: `adapters/postgres/postgres.py`
  writes `sites`/`events` for real when `ROSTERD_COORDINATOR_POSTGRES_DSN`
  is set (which `docker-compose.yml` does by default), and
  `adapters/http_in/live_ws.py` relays every write to connected browsers.

## Verified

- 38 tests (`pytest`) -- pattern detection (site-threshold, cooldown,
  window pruning, lte/gte tightening math, non-numeric ops), site
  status/score derivation, the broadcaster's fan-out/exclusion/best-effort
  behavior, the full HTTP contract, and the Postgres writer's SQL shape
  (upsert-by-`site_id` for `sites`, plain insert for `events`, the
  `violation` JSONB column -- see `tests/test_postgres.py`).
- **Two live, independently running processes** (this coordinator + a real
  `rosterd-kernel`, no mocks): posted the misdirection scenario's violation
  from two different `site_id`s, confirmed the second `POST /events`
  response carried the tightened `policy_update`, and confirmed the
  *actual kernel's* `GET /policy` reflected the override afterward.
- **The full `docker compose` stack**: built both images, brought up
  kernel + coordinator + otel-collector + Jaeger together, repeated the
  same two-site scenario over the containers' internal DNS
  (`http://kernel-site-a:8100`), and confirmed the push landed on the
  containerized kernel. Both services also showed up in Jaeger
  (`rosterd-kernel-site-a`, `rosterd-coordinator`) with real exported spans.
- **A real Postgres**: applied `rosterd-postgres/schema.sql` to a real
  `postgres:16-alpine` container, called `PostgresStateWriter.write_site` /
  `.write_event` directly against it (including a `violation` payload,
  landing correctly as JSONB), and confirmed both rows via `SELECT` --
  and separately confirmed the NOTIFY trigger fires on both an insert and
  an update-on-conflict, which is what `live_ws.py` depends on.

## Layout

Hexagonal: `domain/` (pure logic + wire contracts + `ports.py`) →
`application/` (orchestration, depends on ports) → `adapters/` (concrete
implementations) → `main.py` (thin entrypoint). See the root README's
"Architecture" section for the convention every rosterd service shares.

| File | Purpose |
| --- | --- |
| `domain/coordinator.py` | Wire contract (shared types now come from `rosterd-contracts`, see `libs/rosterd-contracts/`) |
| `domain/sites.py` | Per-site status/score derivation (`SiteRegistry`) |
| `domain/patterns.py` | Shared-failure-pattern detection + rule parsing/tightening (`PatternDetector`) |
| `domain/store.py` | Bounded in-memory event log backing `GET /events` |
| `domain/ports.py` | The `StateWriter` Protocol application code depends on |
| `application/sweeper.py` | Background thread keeping the Postgres `sites` rows current for silent sites |
| `adapters/http_out/broadcaster.py` | Fans a policy update out to every known kernel (`PolicyBroadcaster`) |
| `adapters/postgres/postgres.py` | `StateWriter` implementations (logs, or upserts/inserts into the real `rosterd` Postgres schema) |
| `adapters/http_in/live_ws.py` | Relays Postgres LISTEN/NOTIFY to connected browsers over `GET /ws` -- see "Notes for the team" |
| `adapters/observability/tracing.py` | OTel wrapper -- degrades to a no-op if the SDK/collector isn't there; adds context *extraction* over the kernel's inject-only version |
| `adapters/http_in/app.py` | FastAPI wiring -- `create_app()` factory + the `Container` composition root |
| `main.py` | Thin entrypoint (`uvicorn main:app`) |
| `docs/API.md` | curl-able examples for every endpoint |
