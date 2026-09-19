# rosterd — coordinator service (Person 3, coordinator half)

The federation layer. Receives a run/scale event from every site's kernel,
tracks each site's health, and pushes a tightened policy to every kernel
when the same violation shows up at 2+ distinct sites.

```
kernel-site-A ──┐
kernel-site-B ──┼──> coordinator-service ──> SpacetimeDB (agents/tasks/sites/events)
kernel-site-C ──┘         │
                           └── policy update ────────>
                                back to any site
```

**Scope note:** rosterd-joy-coordinator-frontend.md covers three things --
this service, the SpacetimeDB module schema, and an 8-screen frontend. Only
the coordinator-service (Python/FastAPI, matching the conventions
`rosterd-ingestion` and `rosterd-kernel` already established) is built
here. The SpacetimeDB module and the frontend are a different stack and
scope entirely and aren't attempted in this pass -- see "Notes for the
team" below for exactly where the seams are.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/run.sh                 # serves on :8300, /docs for the API explorer
.venv/bin/python -m pytest       # 34 tests, no SpacetimeDB/kernel/collector needed
```

Nothing above needs a live kernel, SpacetimeDB, or OTel collector --
`ROSTERD_COORDINATOR_SITE_KERNELS` unset just means there's nowhere to
broadcast a policy push to (harmless no-op), and SpacetimeDB writes fall
back to logging. Verified end-to-end against two real, independently
running processes (a live coordinator + a live `rosterd-kernel`, not
mocks) and again across the full `docker compose` stack -- see "Verified"
below.

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
| `GET` | `/sites` | Per-site status and score (debug/fallback -- the frontend subscribes to SpacetimeDB's `sites` table directly instead) |
| `POST` | `/policy/push` | Broadcast a policy update to every known kernel |
| `GET` | `/events` | *(debug)* recent event log, optionally `?site_id=` filtered |
| `GET` | `/healthz` | *(debug)* liveness + effective settings |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8300` | Listen port |
| `ROSTERD_COORDINATOR_SITE_KERNELS` | `{}` | JSON `{site_id: kernel_base_url}` -- who a pushed policy fans out to |
| `ROSTERD_COORDINATOR_OFFLINE_AFTER_SEC` | `30` | No event this long -> `GET /sites` reports `offline` |
| `ROSTERD_COORDINATOR_OFFLINE_SWEEP_SEC` | `5` | Background sweep interval that keeps the SpacetimeDB `sites` row current for a silent site |
| `ROSTERD_COORDINATOR_SCORE_WINDOW` | `20` | How many of a site's recent events feed its health score |
| `ROSTERD_COORDINATOR_PATTERN_THRESHOLD` | `2` | Distinct sites needed to call something a "shared failure pattern" |
| `ROSTERD_COORDINATOR_PATTERN_WINDOW_SEC` | `300` | Lookback window for counting distinct sites |
| `ROSTERD_COORDINATOR_PATTERN_COOLDOWN_SEC` | `60` | Don't re-push the same rule more than once within this long |
| `ROSTERD_COORDINATOR_TIGHTEN_FACTOR` | `0.5` | How hard an `lte`/`gte` bound tightens (0.5 = halve the ceiling / double the floor) |
| `ROSTERD_COORDINATOR_PUSH_TIMEOUT_SEC` | `5` | Per-kernel HTTP timeout when broadcasting |
| `ROSTERD_COORDINATOR_SPACETIMEDB_URL` / `_MODULE` / `_TOKEN` | — | Unset = log rows instead of writing them |
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
  reading SpacetimeDB back. The brief calls `GET /sites` "mostly for
  debugging, frontend subscribes directly [to SpacetimeDB] instead" --
  SpacetimeDB writes here are a side-channel for that direct subscription,
  kept in sync but never read from.
- **`offline` status is computed lazily** (`now - last_event >
  threshold`), not stored, so `GET /sites`/`GET /events` are never stale
  regardless of read timing. The one place that *does* need to notice a
  site going quiet without being asked is the SpacetimeDB `sites` row the
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
- **`tasks` table ownership is unaddressed.** Joy's brief lists `tasks`
  among the SpacetimeDB tables she owns (`TaskRow` is defined in
  `spacetime.py` for reference), but nothing in the coordinator's own
  contract (`POST /events` / `GET /sites` / `POST /policy/push`) ever
  produces one. It's most likely populated directly by the frontend's Ask
  flow, not routed through the coordinator -- worth confirming with Joy.
- **No SpacetimeDB module or frontend in this pass** (see the scope note
  up top). `spacetime.py`'s `HttpReducerSpacetimeWriter` is ready to point
  at Joy's module the moment it exists (`ROSTERD_COORDINATOR_SPACETIMEDB_URL`
  / `_MODULE`), calling `update_site_score` and `record_event` by name per
  the brief's reducer list.

## Verified

- 34 tests (`pytest`) -- pattern detection (site-threshold, cooldown,
  window pruning, lte/gte tightening math, non-numeric ops), site
  status/score derivation, the broadcaster's fan-out/exclusion/best-effort
  behavior, and the full HTTP contract.
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

## Layout

| File | Purpose |
| --- | --- |
| `shared.py`, `coordinator.py` | Wire contract, verbatim from the brief |
| `sites.py` | Per-site status/score derivation (`SiteRegistry`) |
| `patterns.py` | Shared-failure-pattern detection + rule parsing/tightening (`PatternDetector`) |
| `broadcaster.py` | Fans a policy update out to every known kernel (`PolicyBroadcaster`) |
| `store.py` | Bounded in-memory event log backing `GET /events` |
| `sweeper.py` | Background thread keeping the SpacetimeDB `sites` rows current for silent sites |
| `spacetime.py` | `SiteSummary`/`EventLogEntry` writer (logs, or calls SpacetimeDB's HTTP reducer API) |
| `tracing.py` | OTel wrapper -- degrades to a no-op if the SDK/collector isn't there; adds context *extraction* over the kernel's inject-only version |
| `app.py` | FastAPI wiring -- `create_app()` factory + the `Container` composition root |
| `docs/API.md` | curl-able examples for every endpoint |
