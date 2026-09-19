# rosterd — frontend

The control plane UI: ingest a repo, review what was inferred from it, confirm it, ask for work in plain language, and watch the kernel enforce the contract across federated sites.

Built from the seven frames in `TeamPieces/Design.html` against the services that actually exist today (`rosterd-param-frontend.md` is the source of truth for what's really there; `rosterd-joy-coordinator-frontend.md` for what each screen is *for*).

```bash
npm install
npm run dev           # http://localhost:5173
```

No backend running? `VITE_ROSTERD_MODE=demo npm run dev` renders every screen from the Figma fixtures.

## Screens

| Route | Frame | Reads | Writes |
| --- | --- | --- | --- |
| `/ingest` | 1. Ingest | — | `POST /ingest` |
| `/review` | 2. Review Inferred Contract | `GET /manifest/{id}`, kernel `GET /manifest` | `POST /manifest/{id}/confirm` |
| `/ask` | 3. Ask | `GET /manifest/{id}` | `POST /ask/parse`, kernel `POST /dispatch` |
| `/roster` | 4. Schedule and Roster | `agents`, `tasks` | kernel `POST /dispatch` |
| `/runs/:runId` | 5. Task Run and Violation | kernel `GET /runs/{id}`, `events` | kernel `POST /runs/{id}/kill` |
| `/contracts` | 6. Contracts and Access | `GET /manifest/{id}`, kernel `GET /manifest` | — |
| `/federation` | 7. Federation Dashboard | `sites`, `agents`, `events` | kernel `POST /agents/{id}/simulate-load` |
| `/monitor` | *(no frame — see below)* | `agent_metrics` | — |

Every write goes through a kernel or ingestion endpoint. The UI never calls a SpacetimeDB reducer.

**Monitor has no Figma frame.** The product spec lists it as a screen, so it is built here in the same design system — live sparkline of load vs replicas, plus the scaling formula spelled out with current numbers, verbatim from `rosterd-kernel/scaler.py`. Every other screen is a direct implementation of its frame.

## How live data gets here

Three tiers, picked automatically, shown as a pill in the nav bar:

1. **`Live`** — websocket subscription through generated SpacetimeDB bindings.
2. **`Polling`** — SpacetimeDB HTTP SQL (`POST /v1/database/rosterd/sql`) every 2s. **This is the default**, because the bindings need the `spacetime` CLI to generate and `rosterd-param-frontend.md` flags the browser-subscribe path as unverified.
3. **`REST fallback`** — coordinator `/sites` and `/events` when SpacetimeDB itself is unreachable. Covers those two tables only; the banner says so.

To get tier 1:

```bash
npm run gen:bindings   # needs: curl -sSf https://install.spacetimedb.com | sh
npm run dev            # the pill should now read "Live"
```

`src/module_bindings/` is generated, not checked in. `src/lib/live/bindings.ts` detects it with `import.meta.glob`, so the app builds and runs identically whether or not it exists, and rows from either path go through the same normalizers (`src/lib/live/sql.ts`) — no screen knows which tier it is reading from.

## Configuration

Copy `.env.example` to `.env.local`. Defaults match `docker-compose.yml` plus `rosterd-ingestion/scripts/run.sh`:

| Variable | Default | Notes |
| --- | --- | --- |
| `VITE_INGESTION_URL` | `http://localhost:8000` | not in compose yet — run it separately |
| `VITE_KERNEL_URL` | `http://localhost:8100` | |
| `VITE_COORDINATOR_URL` | `http://localhost:8300` | |
| `VITE_SPACETIMEDB_URL` | `http://localhost:3000` | ws URL is derived from it |
| `VITE_SPACETIMEDB_MODULE` | `rosterd` | |
| `VITE_JAEGER_BASE_URL` | `http://localhost:16686` | `View trace` links point here |
| `VITE_SITE_ID` | `site-a` | the site this UI drives |
| `VITE_ROSTERD_MODE` | `live` | `demo` renders the Figma fixtures |
| `VITE_POLL_INTERVAL_MS` | `2000` | tier-2 poll interval |

All three services already send `Access-Control-Allow-Origin: *`, so no proxy is needed in dev.

## Where the backend and the frames disagree

The frames were drawn against the finished product; some of it isn't built yet. Every such spot is handled in code and commented at the point of use, not papered over:

- **Ingest is not "repo URL only."** `POST /ingest` requires `constraints_yaml`. The URL field stays primary; the YAML sits behind a disclosure with a working default. (gap 1)
- **`tasks` has no writer.** Scheduling from Ask or Roster dispatches to the kernel and keeps the commitment in `localStorage`, merged with any live `tasks` rows — live rows win on id collision. (gap 2)
- **`manifests` has no writer.** Review and Contracts read ingestion's `GET /manifest/{id}`. If a `manifests` row ever appears for that id, it is preferred. (gap 3)
- **Ingestion has no per-constraint `source`/`confidence`.** The kernel's `GET /manifest` does — it adapts the same manifest into `list[ConstraintRule]` via `legacy_constraints.py`. So Review and Contracts render from ingestion and overlay the kernel's provenance, matched on the bound value. A constraint with no kernel counterpart renders with no badge rather than a guessed one. (gap 4)
- **Confirming returns a NEW manifest id** (ingestion ADR-002), so the session tracks the draft and confirmed ids separately.
- **`kernel.TaskSpec` requires an `id`;** ingestion's `ParsedTask` has none, so the UI mints one at dispatch.
- **Nothing links a run to its task** — neither side carries the other's id — so the dispatch that creates both records the link in session state.

## Layout

```
src/
  lib/
    api/          one module per service, typed against its Pydantic models
    live/         SpacetimeDB: sql.ts (HTTP), bindings.ts (websocket), LiveProvider.tsx, demo.ts
    types.ts      wire shapes, snake_case, mirrored from the services
    selectors.ts  pools, site rollups, the scaler formula readout
    rules.ts      manifest -> the rule rows Review and Contracts render
    session.tsx   manifest ids, local tasks, run->task links
  components/     nav shell, agent bubble, and the pieces the frames repeat
  screens/        one file per frame
  styles/         tokens.css — every value lifted from Design.html
```
