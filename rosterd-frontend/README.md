# rosterd - frontend

The control plane UI: ingest a repo, review what was inferred from it, confirm it, ask for work in plain language, and watch the kernel enforce the contract across federated sites.

Built from the original seven-frame design mockup against the services that actually exist today.

```bash
npm install
npm run dev           # http://localhost:5173
```

## Screens

| Route | Frame | Reads | Writes |
| --- | --- | --- | --- |
| `/ingest` | 1. Ingest | - | `POST /ingest` |
| `/review` | 2. Review Inferred Contract | `GET /manifest/{id}`, kernel `GET /manifest` | `POST /manifest/{id}/confirm` |
| `/ask` | 3. Ask | `GET /manifest/{id}` | `POST /ask/parse`, kernel `POST /dispatch` |
| `/roster` | 4. Schedule and Roster | `agents`, `tasks` | kernel `POST /dispatch` |
| `/runs/:runId` | 5. Task Run and Violation | kernel `GET /runs/{id}`, `events` | kernel `POST /runs/{id}/kill` |
| `/contracts` | 6. Contracts and Access | `GET /manifest/{id}`, kernel `GET /manifest` | - |
| `/monitor` | *(no frame - see below)* | `agent_metrics` | - |

The original design mockup's seventh frame (a Federation dashboard) was
dropped along with this repo's other early-stage demo scaffolding
(`rosterd-demo-agent`, demo-fixture mode); the kernel's own
`POST /agents/{id}/simulate-load` endpoint it drove still exists --
see `rosterd-kernel/scripts/load_test.py` for a CLI that calls it.

Every write goes through a kernel or ingestion endpoint. The UI never writes to Postgres directly.

**Monitor has no Figma frame.** The product spec lists it as a screen, so it is built here in the same design system - live sparkline of load vs replicas, plus the scaling formula spelled out with current numbers, verbatim from `rosterd-kernel/scaler.py`. Every other screen is a direct implementation of its frame.

## How live data gets here

Two tiers, picked automatically, shown as a pill in the nav bar:

1. **`Live`** - a plain WebSocket to the coordinator's live-relay (`GET /ws`, `rosterd-coordinator/adapters/http_in/live_ws.py`), which itself bridges Postgres LISTEN/NOTIFY to the browser (a browser can't open a raw Postgres connection). **This is the default.** On connect it sends one snapshot message per table, then a live message per row as it changes.
2. **`REST fallback`** - coordinator `/sites` and `/events`, polled, for when the live connection itself can't be reached. Covers those two tables only; the banner says so.

`src/lib/live/ws.ts` owns tier 1; both tiers' rows go through the same normalizers (`src/lib/live/rows.ts`) - no screen knows which one it is reading from.

## Configuration

Copy `.env.example` to `.env.local`. Defaults match `docker-compose.yml` plus `rosterd-ingestion/scripts/run.sh`:

| Variable | Default | Notes |
| --- | --- | --- |
| `VITE_INGESTION_URL` | `http://localhost:8000` | not in compose yet - run it separately |
| `VITE_KERNEL_URL` | `http://localhost:8100` | |
| `VITE_COORDINATOR_URL` | `http://localhost:8300` | |
| `VITE_COORDINATOR_WS_URL` | `ws://localhost:8300/ws` | the live-relay WebSocket |
| `VITE_JAEGER_BASE_URL` | `http://localhost:16686` | `View trace` links point here |
| `VITE_SITE_ID` | `site-a` | the site this UI drives |
| `VITE_POLL_INTERVAL_MS` | `2000` | tier-2 poll interval |

All three services already send `Access-Control-Allow-Origin: *`, so no proxy is needed in dev.

## Where the backend and the frames disagree

The frames were drawn against the finished product; some of it isn't built yet. Every such spot is handled in code and commented at the point of use, not papered over:

- **Ingest is not "repo URL only."** `POST /ingest` requires `constraints_yaml`. The URL field stays primary; the YAML sits behind a disclosure with a working default. (gap 1)
- **`tasks` has no writer.** Scheduling from Ask or Roster dispatches to the kernel and keeps the commitment in `localStorage`, merged with any live `tasks` rows - live rows win on id collision. (gap 2)
- **`manifests` has no writer.** Review and Contracts read ingestion's `GET /manifest/{id}`. If a `manifests` row ever appears for that id, it is preferred. (gap 3)
- **Ingestion has no per-constraint `source`/`confidence`.** The kernel's `GET /manifest` does - it adapts the same manifest into `list[ConstraintRule]` via `legacy_constraints.py`. So Review and Contracts render from ingestion and overlay the kernel's provenance, matched on the bound value. A constraint with no kernel counterpart renders with no badge rather than a guessed one. (gap 4)
- **Confirming returns a NEW manifest id** (ingestion ADR-002), so the session tracks the draft and confirmed ids separately.
- **`kernel.TaskSpec` requires an `id`;** ingestion's `ParsedTask` has none, so the UI mints one at dispatch.
- **Nothing links a run to its task** - neither side carries the other's id - so the dispatch that creates both records the link in session state.

## Layout

```
src/
  lib/
    api/          one module per service, typed against its Pydantic models
    live/         ws.ts (WebSocket), rows.ts (row normalizers), LiveProvider.tsx
    types.ts      wire shapes, snake_case, mirrored from the services
    selectors.ts  pools, site rollups, the scaler formula readout
    rules.ts      manifest -> the rule rows Review and Contracts render
    session.tsx   manifest ids, local tasks, run->task links
  components/     nav shell, agent bubble, and the pieces the frames repeat
  screens/        one file per frame
  styles/         tokens.css - every value lifted from Design.html
```
