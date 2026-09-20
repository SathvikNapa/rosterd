/**
 * SpacetimeDB read path over HTTP SQL (`POST /v1/database/{module}/sql`).
 *
 * Why this exists when the brief says "subscribe, don't poll": the generated
 * TypeScript bindings need the `spacetime` CLI to produce, and
 * rosterd-param-frontend.md gap 5 flags the whole browser-subscribe path as
 * unverified ("should work per the SDK's own docs", not "watched it work").
 * So the UI reads through this by default and upgrades itself to the real
 * websocket subscription the moment `src/module_bindings/` exists — see
 * ./bindings.ts. Both produce the same row types, so no screen knows which
 * one it is reading from.
 *
 * Decoding is deliberately defensive. SpacetimeDB returns rows positionally
 * against a schema block, `Option` columns as the tagged sum `{some: v}` /
 * `{none: []}` (rosterd-spacetimedb/README.md), and u64 as either a JSON
 * number or a string depending on magnitude.
 */
import { config } from '../config';
import type {
  AgentMetricsRow,
  AgentRow,
  EventRow,
  LiveTables,
  ManifestRow,
  SiteRow,
  TaskRow,
  Violation,
} from '../types';

export const EMPTY_TABLES: LiveTables = {
  agents: [],
  agent_metrics: [],
  sites: [],
  events: [],
  tasks: [],
  manifests: [],
};

/** Keep the unbounded append-only tables from growing without limit. */
export const METRICS_WINDOW = 400;
export const EVENTS_WINDOW = 200;

interface SqlStatementResult {
  schema?: { elements?: Array<{ name?: unknown }> };
  rows?: unknown[];
}

export async function runSql(sql: string, signal?: AbortSignal): Promise<Record<string, unknown>[]> {
  const url = `${config.spacetimeUrl}/v1/database/${encodeURIComponent(config.spacetimeModule)}/sql`;
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain' },
    body: sql,
    signal,
  });
  if (!response.ok) {
    throw new Error(`SpacetimeDB SQL ${response.status}: ${await response.text().catch(() => '')}`);
  }
  const payload = (await response.json()) as SqlStatementResult[] | SqlStatementResult;
  const statements = Array.isArray(payload) ? payload : [payload];
  const out: Record<string, unknown>[] = [];
  for (const statement of statements) {
    const columns = (statement.schema?.elements ?? []).map((element, index) =>
      columnName(element?.name, index),
    );
    for (const row of statement.rows ?? []) {
      out.push(zipRow(columns, row));
    }
  }
  return out;
}

function columnName(name: unknown, index: number): string {
  const unwrapped = unwrapOption(name);
  return typeof unwrapped === 'string' ? unwrapped : `col_${index}`;
}

function zipRow(columns: string[], row: unknown): Record<string, unknown> {
  if (Array.isArray(row)) {
    const record: Record<string, unknown> = {};
    columns.forEach((column, index) => {
      record[column] = row[index];
    });
    return record;
  }
  if (row && typeof row === 'object') return row as Record<string, unknown>;
  return {};
}

/**
 * `{some: v}` -> v, `{none: []}` -> null (the reducer-call request body's
 * Option encoding, and what this file's own header comment describes).
 *
 * Confirmed LIVE against a real `POST /v1/database/rosterd/sql` response
 * that this is *not* what a query's row data actually uses: a row's Option
 * column comes back as a positional 2-tuple instead --
 * `[0, v]` for "some", `[1, []]` for "none" (SATS's tagged-variant row
 * encoding; the schema/type-descriptor portion of the same response uses
 * the `{some: ...}` object form, so the two shapes coexist in one payload
 * depending on which part of it you're looking at). Without this, every
 * Option-valued field read through the default HTTP-SQL tier -- sites'
 * last_event, events' run_id/violation/trace_id, tasks' source -- silently
 * rendered as `String([0, "..."])` instead of the real value.
 *
 * The `[0|1, ...]` check is safe for every Option field this module reads:
 * none of their "some" payloads are themselves a 2-element array whose
 * first element is the number 0 or 1 (they're strings or the Violation
 * object below), so this can't misfire on real data here.
 *
 * Anything else (a plain value already unwrapped by the generated
 * websocket client's own runtime) passes through unchanged.
 */
export function unwrapOption(value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value) && value.length === 2 && (value[0] === 0 || value[0] === 1)) {
    return value[0] === 0 ? value[1] : null;
  }
  if (typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length === 1 && keys[0] === 'some') return record.some;
    if (keys.length === 1 && keys[0] === 'none') return null;
  }
  return value;
}

function str(value: unknown, fallback = ''): string {
  const unwrapped = unwrapOption(value);
  if (unwrapped === null) return fallback;
  return typeof unwrapped === 'string' ? unwrapped : String(unwrapped);
}

function optStr(value: unknown): string | null {
  const unwrapped = unwrapOption(value);
  if (unwrapped === null || unwrapped === '') return null;
  return typeof unwrapped === 'string' ? unwrapped : String(unwrapped);
}

function num(value: unknown, fallback = 0): number {
  const unwrapped = unwrapOption(value);
  if (typeof unwrapped === 'number') return unwrapped;
  if (typeof unwrapped === 'bigint') return Number(unwrapped);
  if (typeof unwrapped === 'string') {
    const parsed = Number(unwrapped);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function strArray(value: unknown): string[] {
  const unwrapped = unwrapOption(value);
  return Array.isArray(unwrapped) ? unwrapped.map((item) => str(item)) : [];
}

function optViolation(value: unknown): Violation | null {
  const unwrapped = unwrapOption(value);
  if (!unwrapped || typeof unwrapped !== 'object') return null;
  // A raw SQL query row encodes the nested Violation product type the same
  // positional way as the row itself -- [rule, expected, actual], not a
  // named object -- confirmed live alongside the Option tuple encoding
  // above. The generated websocket client's own runtime should hand back a
  // real {rule, expected, actual} object instead, so both are handled.
  if (Array.isArray(unwrapped)) {
    const [rule, expected, actual] = unwrapped;
    return { rule: str(rule), expected: str(expected), actual: str(actual) };
  }
  const record = unwrapped as Record<string, unknown>;
  return {
    rule: str(record.rule),
    expected: str(record.expected),
    actual: str(record.actual),
  };
}

// ------------------------------------------------------------ row coders
// Exported so ./bindings.ts can push rows from the websocket SDK through the
// exact same normalization the SQL path uses.

export function toAgentRow(raw: Record<string, unknown>): AgentRow {
  return {
    instance_id: str(raw.instance_id ?? raw.instanceId),
    site_id: str(raw.site_id ?? raw.siteId),
    agent_id: str(raw.agent_id ?? raw.agentId),
    name: str(raw.name),
    status: str(raw.status, 'idle') as AgentRow['status'],
    updated_at: str(raw.updated_at ?? raw.updatedAt),
  };
}

export function toAgentMetricsRow(raw: Record<string, unknown>): AgentMetricsRow {
  return {
    id: num(raw.id),
    site_id: str(raw.site_id ?? raw.siteId),
    agent_id: str(raw.agent_id ?? raw.agentId),
    timestamp: str(raw.timestamp),
    in_flight_count: num(raw.in_flight_count ?? raw.inFlightCount),
    queued_count: num(raw.queued_count ?? raw.queuedCount),
    target_concurrency: num(raw.target_concurrency ?? raw.targetConcurrency, 1),
    current_replicas: num(raw.current_replicas ?? raw.currentReplicas),
    desired_replicas: num(raw.desired_replicas ?? raw.desiredReplicas),
    min_replicas: num(raw.min_replicas ?? raw.minReplicas),
    max_replicas: num(raw.max_replicas ?? raw.maxReplicas),
  };
}

export function toSiteRow(raw: Record<string, unknown>): SiteRow {
  return {
    site_id: str(raw.site_id ?? raw.siteId),
    status: str(raw.status, 'healthy') as SiteRow['status'],
    last_event: optStr(raw.last_event ?? raw.lastEvent),
    score: num(raw.score),
  };
}

export function toEventRow(raw: Record<string, unknown>): EventRow {
  return {
    id: num(raw.id),
    site_id: str(raw.site_id ?? raw.siteId),
    run_id: optStr(raw.run_id ?? raw.runId),
    status: str(raw.status) as EventRow['status'],
    violation: optViolation(raw.violation),
    trace_id: optStr(raw.trace_id ?? raw.traceId),
    timestamp: str(raw.timestamp),
  };
}

export function toTaskRow(raw: Record<string, unknown>): TaskRow {
  return {
    task_id: str(raw.task_id ?? raw.taskId),
    site_id: str(raw.site_id ?? raw.siteId),
    agent_id: str(raw.agent_id ?? raw.agentId),
    title: str(raw.title),
    status: str(raw.status, 'scheduled'),
    assignees: strArray(raw.assignees),
    criteria: strArray(raw.criteria),
    priority: str(raw.priority, 'medium'),
    source: optStr(raw.source),
    created_at: str(raw.created_at ?? raw.createdAt),
  };
}

export function toManifestRow(raw: Record<string, unknown>): ManifestRow {
  return {
    manifest_id: str(raw.manifest_id ?? raw.manifestId),
    repo_url: str(raw.repo_url ?? raw.repoUrl),
    status: str(raw.status, 'draft') as ManifestRow['status'],
    agents_json: str(raw.agents_json ?? raw.agentsJson, '[]'),
    graph_json: str(raw.graph_json ?? raw.graphJson, '{}'),
    created_at: str(raw.created_at ?? raw.createdAt),
    version: num(raw.version, 1),
  };
}

/**
 * One full read of every table the UI renders.
 *
 * `agent_metrics` and `events` are append-only and grow fast (the kernel
 * writes metrics on EVERY scaler tick, changed or not), so they are windowed
 * here rather than selected unbounded.
 */
export async function fetchAllTables(signal?: AbortSignal): Promise<LiveTables> {
  const [agents, metrics, sites, events, tasks, manifests] = await Promise.all([
    runSql('SELECT * FROM agents', signal),
    runSql(`SELECT * FROM agent_metrics LIMIT ${METRICS_WINDOW}`, signal),
    runSql('SELECT * FROM sites', signal),
    runSql(`SELECT * FROM events LIMIT ${EVENTS_WINDOW}`, signal),
    runSql('SELECT * FROM tasks', signal),
    runSql('SELECT * FROM manifests', signal),
  ]);

  return {
    agents: agents.map(toAgentRow),
    agent_metrics: metrics.map(toAgentMetricsRow).sort((a, b) => a.id - b.id),
    sites: sites.map(toSiteRow).sort((a, b) => a.site_id.localeCompare(b.site_id)),
    events: events.map(toEventRow).sort((a, b) => a.id - b.id),
    tasks: tasks.map(toTaskRow),
    manifests: manifests.map(toManifestRow),
  };
}
