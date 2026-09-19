// rosterd's real SpacetimeDB module.
//
// This replaces the "log instead of write" stopgap in rosterd-kernel and
// rosterd-coordinator (see their spacetime.py). Field names here are a
// verbatim match of the Pydantic row shapes those two services already
// serialize (AgentRow / AgentMetricsRow in rosterd-kernel/spacetime.py,
// SiteSummary / EventLogEntry / TaskRow in rosterd-coordinator/spacetime.py)
// so `row.model_dump(mode="json")` on the Python side needs zero translation
// on the way in.
//
// Column and reducer-argument identifiers are deliberately snake_case here
// (not idiomatic TS style) rather than camelCase, so the wire JSON key
// SpacetimeDB expects is spelled out directly instead of relying on any
// camelCase -> snake_case canonicalization for table columns (verified only
// for reducer/function *names*, not column names -- see calling convention
// below). This is the safe choice: no guessing about a conversion layer.
//
// Calling convention (verified empirically against a local `spacetime start`
// server before writing this module -- see rosterd-kernel/spacetime.py and
// rosterd-coordinator/spacetime.py for the Python-side callers):
//   POST /v1/database/{module}/call/{canonical_reducer_name}
//   body: JSON array with exactly one element -- the row object.
// The canonical reducer name is the TS export identifier's name (already
// snake_case here, e.g. `update_agent_status`), auto-lowercased/underscored
// by SpacetimeDB if it weren't already -- confirmed via
// `spacetime describe <module> --json`'s
// ExplicitNames.entries[].Function.canonical_name during probing.

import { schema, table, t } from 'spacetimedb/server';

// ---------------------------------------------------------------------------
// Shared nested type
// ---------------------------------------------------------------------------

const Violation = t.object('Violation', {
  rule: t.string(),
  expected: t.string(),
  actual: t.string(),
});

// ---------------------------------------------------------------------------
// Tables
// ---------------------------------------------------------------------------

const agents = table(
  { name: 'agents', public: true },
  {
    // One row per running instance -- upserted by instance_id on every
    // status change (kernel's AgentRow).
    instance_id: t.string().primaryKey(),
    site_id: t.string().index('btree'),
    agent_id: t.string().index('btree'),
    name: t.string(),
    status: t.string(), // "idle" | "working" | "killed"
    updated_at: t.string(), // ISO 8601, matches Pydantic's JSON datetime
  }
);

const agent_metrics = table(
  { name: 'agent_metrics', public: true },
  {
    // Written every scaler tick, not just on change -- append-only.
    id: t.u64().primaryKey().autoInc(),
    site_id: t.string().index('btree'),
    agent_id: t.string().index('btree'),
    timestamp: t.string(),
    in_flight_count: t.u32(),
    queued_count: t.u32(),
    target_concurrency: t.u32(),
    current_replicas: t.u32(),
    desired_replicas: t.u32(),
    min_replicas: t.u32(),
    max_replicas: t.u32(),
  }
);

const sites = table(
  { name: 'sites', public: true },
  {
    // One row per site -- upserted by site_id on every coordinator event
    // (coordinator's SiteSummary).
    site_id: t.string().primaryKey(),
    status: t.string(), // "healthy" | "degraded" | "offline"
    last_event: t.option(t.string()),
    score: t.f64(),
  }
);

const events = table(
  { name: 'events', public: true },
  {
    // Append-only event log (coordinator's EventLogEntry).
    id: t.u64().primaryKey().autoInc(),
    site_id: t.string().index('btree'),
    run_id: t.option(t.string()),
    status: t.string(), // RunStatus | "scaled_up" | "scaled_down"
    violation: t.option(Violation),
    trace_id: t.option(t.string()),
    timestamp: t.string(),
  }
);

const tasks = table(
  { name: 'tasks', public: true },
  {
    // Defined per the brief; not yet written by any service (see
    // coordinator's spacetime.py TaskRow docstring) -- scaffolding so a
    // future writer has a real table to upsert into.
    task_id: t.string().primaryKey(),
    site_id: t.string().index('btree'),
    agent_id: t.string(),
    title: t.string(),
    status: t.string(),
    assignees: t.array(t.string()),
    criteria: t.array(t.string()),
    priority: t.string(),
    source: t.option(t.string()),
    created_at: t.string(),
  }
);

const manifests = table(
  { name: 'manifests', public: true },
  {
    // Ingestion's table (Param owns manifest content); scaffolding only --
    // ingestion still stores locally and nothing writes here yet. Nested
    // agent/graph payload is stored JSON-encoded rather than fully typed
    // out in SpacetimeDB, since ingestion's shape is still evolving.
    manifest_id: t.string().primaryKey(),
    repo_url: t.string(),
    status: t.string(), // "draft" | "confirmed"
    agents_json: t.string(),
    graph_json: t.string(),
    created_at: t.string(),
    version: t.u32(),
  }
);

const spacetimedb = schema({
  agents,
  agent_metrics,
  sites,
  events,
  tasks,
  manifests,
});
export default spacetimedb;

// ---------------------------------------------------------------------------
// Reducer argument types -- one object param per reducer, field names
// matching the table row exactly (minus server-assigned autoInc ids).
// ---------------------------------------------------------------------------

const AgentInput = t.object('AgentInput', {
  site_id: t.string(),
  agent_id: t.string(),
  instance_id: t.string(),
  name: t.string(),
  status: t.string(),
  updated_at: t.string(),
});

const AgentMetricsInput = t.object('AgentMetricsInput', {
  site_id: t.string(),
  agent_id: t.string(),
  timestamp: t.string(),
  in_flight_count: t.u32(),
  queued_count: t.u32(),
  target_concurrency: t.u32(),
  current_replicas: t.u32(),
  desired_replicas: t.u32(),
  min_replicas: t.u32(),
  max_replicas: t.u32(),
});

const SiteInput = t.object('SiteInput', {
  site_id: t.string(),
  status: t.string(),
  last_event: t.option(t.string()),
  score: t.f64(),
});

const EventInput = t.object('EventInput', {
  site_id: t.string(),
  run_id: t.option(t.string()),
  status: t.string(),
  violation: t.option(Violation),
  trace_id: t.option(t.string()),
  timestamp: t.string(),
});

const TaskInput = t.object('TaskInput', {
  task_id: t.string(),
  site_id: t.string(),
  agent_id: t.string(),
  title: t.string(),
  status: t.string(),
  assignees: t.array(t.string()),
  criteria: t.array(t.string()),
  priority: t.string(),
  source: t.option(t.string()),
  created_at: t.string(),
});

const ManifestInput = t.object('ManifestInput', {
  manifest_id: t.string(),
  repo_url: t.string(),
  status: t.string(),
  agents_json: t.string(),
  graph_json: t.string(),
  created_at: t.string(),
  version: t.u32(),
});

// ---------------------------------------------------------------------------
// Reducers -- names match the strings rosterd-kernel/spacetime.py and
// rosterd-coordinator/spacetime.py already call verbatim.
// ---------------------------------------------------------------------------

export const update_agent_status = spacetimedb.reducer(
  { row: AgentInput },
  (ctx, { row }) => {
    const existing = ctx.db.agents.instance_id.find(row.instance_id);
    if (existing) {
      ctx.db.agents.instance_id.update(row);
    } else {
      ctx.db.agents.insert(row);
    }
  }
);

export const record_agent_metrics = spacetimedb.reducer(
  { row: AgentMetricsInput },
  (ctx, { row }) => {
    ctx.db.agent_metrics.insert({ id: 0n, ...row });
  }
);

export const update_site_score = spacetimedb.reducer(
  { row: SiteInput },
  (ctx, { row }) => {
    const existing = ctx.db.sites.site_id.find(row.site_id);
    if (existing) {
      ctx.db.sites.site_id.update(row);
    } else {
      ctx.db.sites.insert(row);
    }
  }
);

export const record_event = spacetimedb.reducer(
  { row: EventInput },
  (ctx, { row }) => {
    ctx.db.events.insert({ id: 0n, ...row });
  }
);

export const record_task = spacetimedb.reducer(
  { row: TaskInput },
  (ctx, { row }) => {
    const existing = ctx.db.tasks.task_id.find(row.task_id);
    if (existing) {
      ctx.db.tasks.task_id.update(row);
    } else {
      ctx.db.tasks.insert(row);
    }
  }
);

export const store_manifest = spacetimedb.reducer(
  { row: ManifestInput },
  (ctx, { row }) => {
    const existing = ctx.db.manifests.manifest_id.find(row.manifest_id);
    if (existing) {
      ctx.db.manifests.manifest_id.update(row);
    } else {
      ctx.db.manifests.insert(row);
    }
  }
);
