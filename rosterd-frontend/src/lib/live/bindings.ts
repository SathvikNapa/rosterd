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
 * Verified live, not just "per the SDK's docs" (closes rosterd-param-frontend.md
 * gap 5): connected a standalone script through the real generated
 * DbConnection against a running SpacetimeDB instance, subscribed, fired a
 * real kernel dispatch, and watched `events.onInsert` fire in ~1ms over the
 * websocket. That run is what caught the actual bug this file shipped with
 * for a while -- `.withModuleName(...)` isn't a real method on the SDK's
 * builder (it's `.withDatabaseName(...)`), so `connectViaBindings` always
 * threw immediately and every session silently fell back to ./sql.ts's HTTP
 * polling, nav pill reading "Polling" forever, with no error surfaced
 * anywhere a person would see it (the throw is caught and swallowed by
 * design, so a broken subscribe degrades instead of blanking the UI -- see
 * LiveProvider.tsx). Fixed; a real websocket connection now reaches this
 * far and the nav pill should read "Live".
 *
 * Everything below is still deliberately loose about the generated surface
 * (property names, row casing) since the exact shape can drift between
 * `spacetime generate` runs. Rows go through the same normalizers the SQL
 * path uses, so both produce identical LiveTables.
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
    .withDatabaseName(config.spacetimeModule)
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
