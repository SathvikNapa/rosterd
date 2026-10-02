/**
 * The live path: a plain browser WebSocket to the coordinator's relay
 * (adapters/http_in/live_ws.py), which itself bridges Postgres
 * LISTEN/NOTIFY to the browser (a browser can't open a raw Postgres
 * connection). Replaces bindings.ts, which spoke the generated SpacetimeDB
 * client's own subscription protocol — this is a much smaller surface:
 * one WebSocket, two message shapes.
 *
 * The relay sends one `{"type": "snapshot", "table": ..., "rows": [...]}`
 * per table immediately on connect (so a freshly-loaded page isn't empty
 * until the next write), then `{"type": "live", "table": ..., "row": {...}}`
 * as Postgres NOTIFYs arrive. This module folds both into one LiveTables
 * object kept in memory here, upserting by each table's primary key for
 * the five "current state" tables (agents/sites/tasks/manifests) and
 * appending (then windowing) for the two append-only ones
 * (agent_metrics/events) — mirroring exactly what rosterd-postgres/schema.sql
 * and adapters/postgres/postgres.py define as each table's write shape.
 */
import { config } from '../config';
import type { LiveTables } from '../types';
import {
  EVENTS_WINDOW,
  METRICS_WINDOW,
  toAgentMetricsRow,
  toAgentRow,
  toEventRow,
  toManifestRow,
  toSiteRow,
  toTaskRow,
} from './rows';

export interface LiveHandle {
  disconnect: () => void;
}

interface RawMessage {
  type: 'snapshot' | 'live';
  table: string;
  rows?: Record<string, unknown>[];
  row?: Record<string, unknown>;
}

function emptyTables(): LiveTables {
  return { agents: [], agent_metrics: [], sites: [], events: [], tasks: [], manifests: [] };
}

/**
 * Opens the connection and streams table updates to `onTables` (always a
 * fresh object, coalesced via requestAnimationFrame so a burst of
 * messages — the six-table snapshot landing all at once, or a real load
 * test — collapses into one React update instead of six-or-more).
 * `onError` fires on any connect/parse/close problem; the caller decides
 * whether to fall back (see LiveProvider.tsx) — this module never retries
 * on its own.
 */
export function connectLive(onTables: (tables: LiveTables) => void, onError: (error: unknown) => void): LiveHandle {
  let current = emptyTables();
  let scheduled = false;

  const emit = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      onTables(current);
    });
  };

  const ws = new WebSocket(config.coordinatorWsUrl);

  ws.onmessage = (event: MessageEvent<string>) => {
    try {
      const message = JSON.parse(event.data) as RawMessage;
      current = applyMessage(current, message);
      emit();
    } catch (error) {
      onError(error);
    }
  };
  ws.onerror = () => onError(new Error('live WebSocket error'));
  ws.onclose = () => onError(new Error('live WebSocket closed'));

  return {
    disconnect: () => {
      try {
        ws.close();
      } catch {
        /* already gone */
      }
    },
  };
}

function applyMessage(tables: LiveTables, message: RawMessage): LiveTables {
  if (message.type === 'snapshot') {
    return { ...tables, ...snapshotSlice(message.table, message.rows ?? []) };
  }
  if (!message.row) return tables;
  return { ...tables, ...liveSlice(tables, message.table, message.row) };
}

function snapshotSlice(table: string, rows: Record<string, unknown>[]): Partial<LiveTables> {
  switch (table) {
    case 'agents':
      return { agents: rows.map(toAgentRow) };
    case 'agent_metrics':
      return { agent_metrics: rows.map(toAgentMetricsRow).sort(byId).slice(-METRICS_WINDOW) };
    case 'sites':
      return { sites: rows.map(toSiteRow).sort(bySiteId) };
    case 'events':
      return { events: rows.map(toEventRow).sort(byId).slice(-EVENTS_WINDOW) };
    case 'tasks':
      return { tasks: rows.map(toTaskRow) };
    case 'manifests':
      return { manifests: rows.map(toManifestRow) };
    default:
      return {};
  }
}

function liveSlice(tables: LiveTables, table: string, raw: Record<string, unknown>): Partial<LiveTables> {
  switch (table) {
    case 'agents':
      return { agents: upsert(tables.agents, toAgentRow(raw), (r) => r.instance_id) };
    case 'agent_metrics':
      return {
        agent_metrics: [...tables.agent_metrics, toAgentMetricsRow(raw)].sort(byId).slice(-METRICS_WINDOW),
      };
    case 'sites':
      return { sites: upsert(tables.sites, toSiteRow(raw), (r) => r.site_id).sort(bySiteId) };
    case 'events':
      return { events: [...tables.events, toEventRow(raw)].sort(byId).slice(-EVENTS_WINDOW) };
    case 'tasks':
      return { tasks: upsert(tables.tasks, toTaskRow(raw), (r) => r.task_id) };
    case 'manifests':
      return { manifests: upsert(tables.manifests, toManifestRow(raw), (r) => r.manifest_id) };
    default:
      return {};
  }
}

/** Replace-by-key if present, append if not — every "current state" table
 * (agents/sites/tasks/manifests) is upserted this way on both ends (the
 * Postgres schema's own ON CONFLICT DO UPDATE, mirrored here). */
function upsert<T>(rows: T[], next: T, key: (row: T) => string): T[] {
  const id = key(next);
  const index = rows.findIndex((row) => key(row) === id);
  if (index === -1) return [...rows, next];
  const copy = [...rows];
  copy[index] = next;
  return copy;
}

function byId(a: { id: number }, b: { id: number }): number {
  return a.id - b.id;
}

function bySiteId(a: { site_id: string }, b: { site_id: string }): number {
  return a.site_id.localeCompare(b.site_id);
}
