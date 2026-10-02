/**
 * Normalizes a raw row (straight off ws.ts's WebSocket, already plain JSON
 * from Postgres via psycopg's dict_row) into the typed row shapes the UI
 * renders.
 *
 * Was sql.ts, reading SpacetimeDB over HTTP SQL — that API encoded an
 * Option column as a tagged sum (`{some: v}` / `{none: []}` in a schema
 * block, `[0, v]` / `[1, []]` positionally in row data) and u64s as
 * strings past a magnitude threshold. Postgres has neither: a NULL is
 * just `null`, and every number here fits a plain JS number. The decode
 * helpers below are correspondingly simpler than their SpacetimeDB-era
 * equivalents — kept as named helpers anyway so every row coder stays a
 * one-line-per-field mapping instead of inlining `?? null` / `?? 0`
 * everywhere.
 */
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

function str(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback;
  return typeof value === 'string' ? value : String(value);
}

function optStr(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  return typeof value === 'string' ? value : String(value);
}

function num(value: unknown, fallback = 0): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => str(item)) : [];
}

function optViolation(value: unknown): Violation | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  return { rule: str(record.rule), expected: str(record.expected), actual: str(record.actual) };
}

export function toAgentRow(raw: Record<string, unknown>): AgentRow {
  return {
    instance_id: str(raw.instance_id),
    site_id: str(raw.site_id),
    agent_id: str(raw.agent_id),
    name: str(raw.name),
    status: str(raw.status, 'idle') as AgentRow['status'],
    updated_at: str(raw.updated_at),
  };
}

export function toAgentMetricsRow(raw: Record<string, unknown>): AgentMetricsRow {
  return {
    id: num(raw.id),
    site_id: str(raw.site_id),
    agent_id: str(raw.agent_id),
    timestamp: str(raw.timestamp),
    in_flight_count: num(raw.in_flight_count),
    queued_count: num(raw.queued_count),
    target_concurrency: num(raw.target_concurrency, 1),
    current_replicas: num(raw.current_replicas),
    desired_replicas: num(raw.desired_replicas),
    min_replicas: num(raw.min_replicas),
    max_replicas: num(raw.max_replicas),
  };
}

export function toSiteRow(raw: Record<string, unknown>): SiteRow {
  return {
    site_id: str(raw.site_id),
    status: str(raw.status, 'healthy') as SiteRow['status'],
    last_event: optStr(raw.last_event),
    score: num(raw.score),
  };
}

export function toEventRow(raw: Record<string, unknown>): EventRow {
  return {
    id: num(raw.id),
    site_id: str(raw.site_id),
    run_id: optStr(raw.run_id),
    status: str(raw.status) as EventRow['status'],
    violation: optViolation(raw.violation),
    trace_id: optStr(raw.trace_id),
    timestamp: str(raw.timestamp),
  };
}

export function toTaskRow(raw: Record<string, unknown>): TaskRow {
  return {
    task_id: str(raw.task_id),
    site_id: str(raw.site_id),
    agent_id: str(raw.agent_id),
    title: str(raw.title),
    status: str(raw.status, 'scheduled'),
    assignees: strArray(raw.assignees),
    criteria: strArray(raw.criteria),
    priority: str(raw.priority, 'medium'),
    source: optStr(raw.source),
    created_at: str(raw.created_at),
  };
}

export function toManifestRow(raw: Record<string, unknown>): ManifestRow {
  return {
    manifest_id: str(raw.manifest_id),
    repo_url: str(raw.repo_url),
    status: str(raw.status, 'draft') as ManifestRow['status'],
    agents_json: str(raw.agents_json, '[]'),
    graph_json: str(raw.graph_json, '{}'),
    created_at: str(raw.created_at),
    version: num(raw.version, 1),
  };
}
