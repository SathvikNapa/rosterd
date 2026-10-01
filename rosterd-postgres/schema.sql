-- rosterd's shared live-state store.
--
-- Replaces rosterd-spacetimedb (a SpacetimeDB module) with plain Postgres:
-- the same 6 tables, upserted/inserted by the same two services that used
-- to call SpacetimeDB's reducers (rosterd-kernel, rosterd-coordinator),
-- now via ordinary `INSERT ... ON CONFLICT DO UPDATE` instead of an RPC
-- call. Live updates come from Postgres' own LISTEN/NOTIFY, fired by the
-- trigger at the bottom of this file -- no reducer layer needed.
--
-- A browser can't speak the Postgres wire protocol directly, so
-- rosterd-coordinator relays NOTIFY payloads to connected frontend clients
-- over a plain WebSocket (adapters/http_in/live_ws.py) -- see its own
-- docstring for why coordinator, specifically, owns that relay.

CREATE TABLE IF NOT EXISTS agents (
    -- One row per running instance, upserted by instance_id on every
    -- status change (kernel's AgentRow).
    instance_id      TEXT PRIMARY KEY,
    site_id          TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    name             TEXT NOT NULL,
    status           TEXT NOT NULL,  -- 'idle' | 'working' | 'killed'
    updated_at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS agents_site_id_idx ON agents (site_id);
CREATE INDEX IF NOT EXISTS agents_agent_id_idx ON agents (agent_id);

CREATE TABLE IF NOT EXISTS agent_metrics (
    -- Written every scaler tick, not just on change -- append-only.
    id                   BIGSERIAL PRIMARY KEY,
    site_id              TEXT NOT NULL,
    agent_id             TEXT NOT NULL,
    timestamp            TIMESTAMPTZ NOT NULL,
    in_flight_count      INTEGER NOT NULL,
    queued_count         INTEGER NOT NULL,
    target_concurrency   INTEGER NOT NULL,
    current_replicas     INTEGER NOT NULL,
    desired_replicas     INTEGER NOT NULL,
    min_replicas         INTEGER NOT NULL,
    max_replicas         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_metrics_site_id_idx ON agent_metrics (site_id);
CREATE INDEX IF NOT EXISTS agent_metrics_agent_id_idx ON agent_metrics (agent_id);

CREATE TABLE IF NOT EXISTS sites (
    -- One row per site, upserted by site_id on every coordinator event
    -- (coordinator's SiteSummary).
    site_id      TEXT PRIMARY KEY,
    status       TEXT NOT NULL,  -- 'healthy' | 'degraded' | 'offline'
    last_event   TIMESTAMPTZ,
    score        DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    -- Append-only event log (coordinator's EventLogEntry). `violation` is
    -- the same {rule, expected, actual} shape as rosterd-contracts.Violation,
    -- stored as JSONB rather than three flat columns since it's optional
    -- and only ever read back whole, never queried by field.
    id          BIGSERIAL PRIMARY KEY,
    site_id     TEXT NOT NULL,
    run_id      TEXT,
    status      TEXT NOT NULL,  -- RunStatus | 'scaled_up' | 'scaled_down'
    violation   JSONB,
    trace_id    TEXT,
    timestamp   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS events_site_id_idx ON events (site_id);

CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    site_id      TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL,
    assignees    TEXT[] NOT NULL DEFAULT '{}',
    criteria     TEXT[] NOT NULL DEFAULT '{}',
    priority     TEXT NOT NULL,
    source       TEXT,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_site_id_idx ON tasks (site_id);

CREATE TABLE IF NOT EXISTS manifests (
    -- Ingestion's table; nested agent/graph payload stored JSON-encoded
    -- rather than fully typed out, same reasoning as the SpacetimeDB
    -- module this replaces -- ingestion's shape is still evolving.
    manifest_id   TEXT PRIMARY KEY,
    repo_url      TEXT NOT NULL,
    status        TEXT NOT NULL,  -- 'draft' | 'confirmed'
    agents_json   TEXT NOT NULL,
    graph_json    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    version       INTEGER NOT NULL
);

-- ---------------------------------------------------------------------
-- Live updates: one NOTIFY per changed row, on a channel named after the
-- table. A listener (rosterd-coordinator's live_ws.py) does
-- `LISTEN agents; LISTEN agent_metrics; LISTEN sites; LISTEN events;
--  LISTEN tasks; LISTEN manifests;` once and gets every change as it
-- happens -- no polling, no reducer RPC layer.
-- ---------------------------------------------------------------------

CREATE OR REPLACE FUNCTION rosterd_notify_change() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(TG_TABLE_NAME, row_to_json(NEW)::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['agents', 'agent_metrics', 'sites', 'events', 'tasks', 'manifests']
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS notify_change ON %I;
             CREATE TRIGGER notify_change
             AFTER INSERT OR UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION rosterd_notify_change();',
            t, t
        );
    END LOOP;
END $$;
