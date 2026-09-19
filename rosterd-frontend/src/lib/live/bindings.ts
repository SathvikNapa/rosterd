/**
 * Optional websocket path: the real generated SpacetimeDB client.
 *
 * `src/module_bindings/` is NOT checked in — it is generated from
 * rosterd-spacetimedb/ by `npm run gen:bindings` (which needs the
 * `spacetime` CLI). This module detects it at build time via import.meta.glob
 * so the app compiles and runs either way, and `connectViaBindings` simply
 * reports "unavailable" when the folder is absent, leaving ./sql.ts to serve
 * the same rows over HTTP.
 *
 * Everything below is deliberately loose about the generated surface
 * (property names, row casing) because rosterd-param-frontend.md gap 5 flags
 * the browser-subscribe path as unverified. Rows go through the same
 * normalizers the SQL path uses, so both produce identical LiveTables.
 */
import { config, spacetimeWsUrl } from '../config';
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
} from './sql';

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const modules = import.meta.glob('../../module_bindings/index.ts');

export function bindingsAvailable(): boolean {
  return Object.keys(modules).length > 0;
}

export interface BindingsHandle {
  disconnect: () => void;
}

/** The six tables, as (generated camelCase property, LiveTables key) pairs. */
const TABLES: Array<[string, keyof LiveTables]> = [
  ['agents', 'agents'],
  ['agentMetrics', 'agent_metrics'],
  ['sites', 'sites'],
  ['events', 'events'],
  ['tasks', 'tasks'],
  ['manifests', 'manifests'],
];

const SUBSCRIPTIONS = [
  'SELECT * FROM agents',
  'SELECT * FROM agent_metrics',
  'SELECT * FROM sites',
  'SELECT * FROM events',
  'SELECT * FROM tasks',
  'SELECT * FROM manifests',
];

/**
 * Connects and streams whole-table snapshots to `onTables`. Resolves to null
 * when no bindings are generated; rejects when they exist but fail to
 * connect, so the caller can fall back and surface why.
 */
export async function connectViaBindings(
  onTables: (tables: LiveTables) => void,
  onError: (error: unknown) => void,
): Promise<BindingsHandle | null> {
  const entry = Object.values(modules)[0];
  if (!entry) return null;

  const generated = (await entry()) as Any;
  const DbConnection = generated?.DbConnection;
  if (!DbConnection?.builder) {
    throw new Error('module_bindings exists but exports no DbConnection — regenerate it.');
  }

  let connection: Any = null;

  const snapshot = () => {
    if (!connection?.db) return;
    try {
      onTables(readTables(connection));
    } catch (error) {
      onError(error);
    }
  };

  connection = DbConnection.builder()
    .withUri(spacetimeWsUrl())
    .withModuleName(config.spacetimeModule)
    .onConnect((conn: Any) => {
      conn
        .subscriptionBuilder()
        .onApplied(snapshot)
        .onError((_ctx: Any, error: unknown) => onError(error))
        .subscribe(SUBSCRIPTIONS);

      for (const [property] of TABLES) {
        const table = conn.db?.[property];
        if (!table) continue;
        table.onInsert?.(snapshot);
        table.onUpdate?.(snapshot);
        table.onDelete?.(snapshot);
      }
    })
    .onConnectError((_ctx: Any, error: unknown) => onError(error))
    .onDisconnect(() => onError(new Error('SpacetimeDB websocket disconnected')))
    .build();

  return {
    disconnect: () => {
      try {
        connection?.disconnect?.();
      } catch {
        /* already gone */
      }
    },
  };
}

function readTables(connection: Any): LiveTables {
  const raw: Record<string, Record<string, unknown>[]> = {};
  for (const [property, key] of TABLES) {
    raw[key] = iterate(connection.db?.[property]);
  }
  return {
    agents: raw.agents.map(toAgentRow),
    agent_metrics: raw.agent_metrics
      .map(toAgentMetricsRow)
      .sort((a, b) => a.id - b.id)
      .slice(-METRICS_WINDOW),
    sites: raw.sites.map(toSiteRow).sort((a, b) => a.site_id.localeCompare(b.site_id)),
    events: raw.events
      .map(toEventRow)
      .sort((a, b) => a.id - b.id)
      .slice(-EVENTS_WINDOW),
    tasks: raw.tasks.map(toTaskRow),
    manifests: raw.manifests.map(toManifestRow),
  };
}

function iterate(table: Any): Record<string, unknown>[] {
  if (!table) return [];
  const source = typeof table.iter === 'function' ? table.iter() : table;
  return Array.from(source ?? []) as Record<string, unknown>[];
}
