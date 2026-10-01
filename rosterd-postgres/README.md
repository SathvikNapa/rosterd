# rosterd-postgres

The shared live-state store every rosterd service reads and writes:
`agents`, `agent_metrics`, `sites`, `events`, `tasks`, `manifests`.

Replaces `rosterd-spacetimedb` (a SpacetimeDB module). Same six tables,
same column shapes, but plain Postgres: ordinary SQL instead of a bespoke
module language and reducer-call API.

## Schema

`schema.sql` creates the six tables plus one trigger function
(`rosterd_notify_change`) attached to all six: every `INSERT`/`UPDATE`
fires `pg_notify(table_name, row_as_json)`. That's the entire live-update
mechanism — no reducers, no RPC layer.

## Applying it

```bash
psql "$DATABASE_URL" -f schema.sql
```

`docker-compose.yml` does this automatically for the local stack (mounted
into the official `postgres` image's `/docker-entrypoint-initdb.d/`, which
runs once against a fresh data volume).

## Who writes here

`rosterd-kernel` (`postgres.py`: `agents`, `agent_metrics`) and
`rosterd-coordinator` (`adapters/postgres/postgres.py`: `sites`, `events`)
today. `tasks` and `manifests` are scaffolding — defined for when a writer
exists, same as they were in the SpacetimeDB module this replaces.

## Who reads here, live

A browser can't open a raw Postgres connection, so `rosterd-coordinator`
runs a small relay (`adapters/http_in/live_ws.py`): one server-side
`LISTEN` connection per table, fanned out to connected frontend clients
over a plain WebSocket (`GET /ws`). The frontend's `lib/live/` connects
there instead of to Postgres directly.

## Verifying it's actually live

```bash
psql "$DATABASE_URL" -c "LISTEN agents;"
# in another session:
psql "$DATABASE_URL" -c "UPDATE agents SET status = 'killed' WHERE instance_id = '...';"
# back in the first session, within a second: a NOTIFY with the fresh row as JSON.
```
