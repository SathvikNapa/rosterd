# rosterd — SpacetimeDB module

The real `rosterd` SpacetimeDB database: the tables `rosterd-kernel` and
`rosterd-coordinator` write to (`agents`, `agent_metrics`, `sites`,
`events`) plus two forward-looking tables neither service writes to yet
(`tasks`, `manifests`) so the reducers exist whenever they're wired up.

This replaces the "log instead of write" `LoggingSpacetimeWriter` stopgap
both services shipped with — see `rosterd-kernel/spacetime.py` and
`rosterd-coordinator/spacetime.py`. It has been published and verified
against both a local `spacetime start` server and the real
`docker compose` stack (see "Verified" below).

```
kernel-service ──┐
                  ├──> rosterd (this module) ──> frontend / dashboards
coordinator ──────┘
```

## Quick start

```bash
curl -sSf https://install.spacetimedb.com | sh -s -- -y   # installs `spacetime`
cd spacetimedb && npm install && cd ..

spacetime start &                                    # local dev server, :3000
spacetime publish rosterd --server local --yes

spacetime sql rosterd --server local --anonymous "SELECT * FROM agents"
```

Or via the full stack: `docker compose up --build` from the repo root
starts the real server (`spacetimedb`, the official `clockworklabs/spacetime`
image) and a one-shot `spacetimedb-publish` job that builds and publishes
this module into it before kernel or coordinator take their first write.
`kernel-site-a` and `coordinator` both wait on `spacetimedb-publish`
completing successfully.

## Tables

| Table | Written by | Shape mirrors |
| --- | --- | --- |
| `agents` | kernel, every scaler tick pool change | `AgentRow` (kernel's spacetime.py) |
| `agent_metrics` | kernel, every scaler tick | `AgentMetricsRow` (kernel's spacetime.py) |
| `sites` | coordinator, every `POST /events` | `SiteSummary` (coordinator.py) |
| `events` | coordinator, every `POST /events` | `EventLogEntry` (coordinator.py) |
| `tasks` | nobody yet | `TaskRow` (coordinator's spacetime.py, unused) |
| `manifests` | nobody yet | scaffolding for ingestion's confirmed manifests |

## Reducers

Every reducer takes exactly one structured argument named `row`, shaped
like the table it writes minus any server-assigned autoInc id:
`update_agent_status`, `record_agent_metrics`, `update_site_score`,
`record_event`, `record_task`, `store_manifest`. `agents`, `sites`, and
`tasks` upsert by primary key; `agent_metrics` and `events` are
append-only.

## The HTTP calling convention (the part that isn't obvious from the docs)

Confirmed empirically against a real `spacetime start` server before
writing this module (nothing in the bundled SDK docs states this plainly):

```
POST /v1/database/{module}/call/{reducer}
Content-Type: application/json

[ { ...the row, one JSON object... } ]
```

* **The body is a JSON array**, not a bare object — `json=args` (what both
  services' `HttpReducerSpacetimeWriter._call()` originally sent) gets
  rejected. Since every reducer here takes exactly one parameter, the array
  always has exactly one element.
* **The reducer name in the URL is case-sensitive and must be the
  canonical snake_case name.** These reducers are already declared
  snake_case in `spacetimedb/src/index.ts` (`update_agent_status`, not
  `updateAgentStatus`), so there's no camelCase → snake_case conversion to
  worry about here — but if you add a camelCase-named reducer, call
  `spacetime describe rosterd --server local --json` and use
  `ExplicitNames.entries[].Function.canonical_name`, not the TS source name.
* **Optional fields are a tagged sum type, not a nullable scalar.** A
  *present* value must be sent as `{"some": value}`; bare JSON `null` is
  accepted as shorthand for the `none` variant (so Pydantic's default
  `None` → `null` needs no extra handling on the "absent" side — only
  "present" needs wrapping). `sites.last_event` and `events.run_id` /
  `.violation` / `.trace_id` are the fields that need this; see
  `rosterd-coordinator/spacetime.py`'s `_wrap_option()`. `agents` and
  `agent_metrics` have no optional fields, so kernel's writer needs none of
  this.

## Verified

Both against a local `spacetime start` server and the real
`docker compose` stack (`clockworklabs/spacetime:v2.10.1` +
`spacetimedb-publish`):

* All six reducers called over raw HTTP with the array-wrapped body land
  the expected row (`spacetime sql rosterd ... "SELECT * FROM <table>"`
  round-tripped for every table, including the `some`/`none` option
  encoding for `sites.last_event`, `events.run_id`, `.violation`,
  `.trace_id`).
* `rosterd-coordinator`'s real `HttpReducerSpacetimeWriter`, exercised
  through its actual `POST /events` endpoint in the running Docker stack,
  wrote a real row visible via `spacetime sql`.
* `rosterd-kernel`'s real `HttpReducerSpacetimeWriter.write_agent` /
  `write_agent_metrics`, called directly against the same running
  container (its own scaler tick needs a loaded manifest, which needs
  ingestion running -- not yet wired into `docker-compose.yml`), also
  wrote real rows visible via `spacetime sql`.

## Notes for the team

* `manifests` and `tasks` are scaffolding: the tables and reducers exist,
  but nothing writes to them yet. Ingestion still stores confirmed
  manifests locally (see `rosterd-ingestion/README.md`); wiring it to also
  call `store_manifest` is a natural next step but wasn't in scope here.
* `docker-compose.yml`'s `spacetimedb` service reserves host port `3000`.
  The commented-out `frontend` placeholder also guesses port `3000` --
  whoever wires up the real frontend should pick a different one.
* Schema changes during development: `spacetime publish rosterd --server
  local --yes` will refuse a breaking column change without
  `--delete-data=always` (or `on-conflict`). Fine before there's real data
  worth keeping; not fine against the `docker compose` stack's persistent
  `spacetimedb-data` volume once there is.
