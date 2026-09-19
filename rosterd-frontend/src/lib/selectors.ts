/**
 * Derivations over the live tables. Pure functions, no React — the screens
 * call these so the same rollup is never recomputed two slightly different
 * ways on two screens.
 */
import { titleize } from './format';
import type { AgentMetricsRow, AgentRow, AgentRowStatus, EventRow, LiveTables, SiteRow } from './types';

export interface AgentPool {
  agent_id: string;
  site_id: string;
  name: string;
  /** One entry per running instance — `agents` is per-instance, not per-type. */
  instances: AgentRow[];
  replicas: number;
  working: number;
  idle: number;
  killed: number;
  /** Pool-level status: working beats killed beats idle. */
  status: AgentRowStatus;
  updated_at: string | null;
}

/** Group the per-instance `agents` rows into one pool per agent_id. */
export function poolsForSite(agents: AgentRow[], siteId: string): AgentPool[] {
  const bySite = agents.filter((row) => row.site_id === siteId);
  const byAgent = new Map<string, AgentRow[]>();
  for (const row of bySite) {
    const list = byAgent.get(row.agent_id);
    if (list) list.push(row);
    else byAgent.set(row.agent_id, [row]);
  }

  return [...byAgent.entries()]
    .map(([agentId, instances]) => buildPool(agentId, siteId, instances))
    .sort((a, b) => a.agent_id.localeCompare(b.agent_id));
}

function buildPool(agentId: string, siteId: string, instances: AgentRow[]): AgentPool {
  const live = instances.filter((row) => row.status !== 'killed');
  const working = live.filter((row) => row.status === 'working').length;
  const killed = instances.length - live.length;
  const status: AgentRowStatus = working > 0 ? 'working' : live.length > 0 ? 'idle' : 'killed';
  const updated = instances
    .map((row) => row.updated_at)
    .filter(Boolean)
    .sort()
    .at(-1);

  return {
    agent_id: agentId,
    site_id: siteId,
    name: instances.find((row) => row.name)?.name ?? titleize(agentId),
    instances: [...instances].sort((a, b) => a.instance_id.localeCompare(b.instance_id)),
    replicas: live.length,
    working,
    idle: live.length - working,
    killed,
    status,
    updated_at: updated ?? null,
  };
}

/**
 * Reorders pools to match the manifest's own agent order (which follows the
 * graph), so every screen lists agents the way the contract does rather than
 * alphabetically. Agents not in the manifest keep their relative order at the
 * end.
 */
export function orderPools(pools: AgentPool[], order: string[]): AgentPool[] {
  if (order.length === 0) return pools;
  const rank = new Map(order.map((id, index) => [id, index]));
  return [...pools].sort(
    (a, b) => (rank.get(a.agent_id) ?? order.length) - (rank.get(b.agent_id) ?? order.length),
  );
}

/** The `agents` table's own `name` for an agent, falling back to its id. */
export function agentDisplayName(agents: AgentRow[], agentId: string): string {
  return agents.find((row) => row.agent_id === agentId && row.name)?.name ?? titleize(agentId);
}

/** Every site id we have seen, from either table, in stable order. */
export function siteIds(tables: LiveTables): string[] {
  const ids = new Set<string>();
  tables.sites.forEach((row) => ids.add(row.site_id));
  tables.agents.forEach((row) => ids.add(row.site_id));
  return [...ids].sort();
}

export interface SiteRollup {
  site_id: string;
  site: SiteRow | null;
  pools: AgentPool[];
  replicas: number;
  /** Scaled past one instance anywhere in the site. */
  scaledUp: boolean;
  /** in_flight + queued across the site's latest metrics rows. */
  load: number;
  /** current_replicas * target_concurrency across the same rows. */
  capacity: number;
  /** load / capacity, clamped to [0, 1] — the frames' thin progress bar. */
  saturation: number;
  lastEvent: EventRow | null;
}

export function siteRollup(tables: LiveTables, siteId: string): SiteRollup {
  const pools = poolsForSite(tables.agents, siteId);
  const latest = latestMetricsBySite(tables.agent_metrics, siteId);

  const load = latest.reduce((sum, row) => sum + row.in_flight_count + row.queued_count, 0);
  const capacity = latest.reduce(
    (sum, row) => sum + Math.max(1, row.current_replicas) * Math.max(1, row.target_concurrency),
    0,
  );

  return {
    site_id: siteId,
    site: tables.sites.find((row) => row.site_id === siteId) ?? null,
    pools,
    replicas: pools.reduce((sum, pool) => sum + pool.replicas, 0),
    scaledUp: pools.some((pool) => pool.replicas > 1),
    load,
    capacity,
    saturation: capacity > 0 ? Math.min(1, load / capacity) : 0,
    lastEvent: eventsForSite(tables.events, siteId).at(-1) ?? null,
  };
}

export function eventsForSite(events: EventRow[], siteId: string): EventRow[] {
  return events.filter((row) => row.site_id === siteId);
}

/** Newest first — what the activity feed renders. */
export function recentEvents(events: EventRow[], limit = 12): EventRow[] {
  return [...events]
    .sort((a, b) => compareTimestamps(a, b))
    .slice(-limit)
    .reverse();
}

function compareTimestamps(a: EventRow, b: EventRow): number {
  const byTime = Date.parse(a.timestamp) - Date.parse(b.timestamp);
  return Number.isNaN(byTime) || byTime === 0 ? a.id - b.id : byTime;
}

/** The newest metrics row per agent at a site. */
export function latestMetricsBySite(metrics: AgentMetricsRow[], siteId: string): AgentMetricsRow[] {
  const byAgent = new Map<string, AgentMetricsRow>();
  for (const row of metrics) {
    if (row.site_id !== siteId) continue;
    const seen = byAgent.get(row.agent_id);
    if (!seen || row.id >= seen.id) byAgent.set(row.agent_id, row);
  }
  return [...byAgent.values()].sort((a, b) => a.agent_id.localeCompare(b.agent_id));
}

export function metricsSeries(
  metrics: AgentMetricsRow[],
  siteId: string,
  agentId: string,
  window = 40,
): AgentMetricsRow[] {
  return metrics
    .filter((row) => row.site_id === siteId && row.agent_id === agentId)
    .sort((a, b) => a.id - b.id)
    .slice(-window);
}

export interface ScalerReadout {
  load: number;
  target: number;
  raw: number;
  desired: number;
  min: number;
  max: number;
  current: number;
  clamped: 'min' | 'max' | null;
  /** "load: 8, target: 2, desired: ceil(8/2) = 4, clamped to max 4" */
  sentence: string;
}

/**
 * The Monitor screen's formula readout, verbatim from rosterd-kernel/scaler.py:
 *   load    = in_flight_count + queued_count
 *   desired = clamp(ceil(load / target_concurrency), min_replicas, max_replicas)
 *   desired = 0 when load == 0
 * `desired_replicas` is already stored on the row; this recomputes only the
 * intermediate `raw` value so the arithmetic can be shown, and reports the
 * row's own `desired_replicas` as the answer.
 */
export function scalerReadout(row: AgentMetricsRow): ScalerReadout {
  const load = row.in_flight_count + row.queued_count;
  const target = Math.max(1, row.target_concurrency);
  const raw = load === 0 ? 0 : Math.ceil(load / target);
  const desired = row.desired_replicas;

  let clamped: 'min' | 'max' | null = null;
  if (load > 0 && raw > row.max_replicas) clamped = 'max';
  else if (load > 0 && raw < row.min_replicas) clamped = 'min';

  const head = `load: ${load}, target: ${target}, desired: ${
    load === 0 ? '0 (no load)' : `ceil(${load}/${target}) = ${raw}`
  }`;
  const tail =
    clamped === 'max'
      ? `, clamped to max ${row.max_replicas}`
      : clamped === 'min'
        ? `, clamped to min ${row.min_replicas}`
        : '';

  return {
    load,
    target,
    raw,
    desired,
    min: row.min_replicas,
    max: row.max_replicas,
    current: row.current_replicas,
    clamped,
    sentence: `${head}${tail}`,
  };
}

/** Human line for an event row, as the coordinator feed renders it. */
export function describeEvent(event: EventRow): string {
  if (event.violation) {
    return `${event.site_id} → violation: ${event.violation.rule}`;
  }
  switch (event.status) {
    case 'scaled_up':
      return `${event.site_id} → pool scaled up`;
    case 'scaled_down':
      return `${event.site_id} → pool scaled down`;
    case 'killed':
      return `${event.site_id} → run killed${event.run_id ? ` (${event.run_id})` : ''}`;
    case 'done':
      return `${event.site_id} → run done${event.run_id ? ` (${event.run_id})` : ''}`;
    default:
      return `${event.site_id} → ${event.status}`;
  }
}
