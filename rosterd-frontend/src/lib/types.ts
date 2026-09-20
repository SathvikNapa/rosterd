/**
 * Wire types, mirrored by hand from the services' Pydantic models:
 *   rosterd-ingestion/{ingestion,shared}.py
 *   rosterd-kernel/{kernel,manifest,shared}.py
 *   rosterd-coordinator/{coordinator,shared}.py
 * and from rosterd-spacetimedb/spacetimedb/src/index.ts for the table rows.
 *
 * Keep field names snake_case: these are the wire shapes, not view models.
 */

export type Priority = 'low' | 'medium' | 'high';
export type Confidence = 'high' | 'medium' | 'low';
/** 'paused' is new: a run interrupted at demo-agent's interrupt(), waiting
 *  on POST /runs/{id}/resume -- from a human, or from the kernel's own
 *  reviewer agent (reviewer.py), which usually resolves it within seconds. */
export type RunStatus = 'working' | 'done' | 'killed' | 'paused';
export type SiteStatus = 'healthy' | 'violation' | 'offline';
export type ManifestStatus = 'draft' | 'confirmed';
export type InstanceStatus = 'idle' | 'working';
/** `agents.status` also carries "killed", which is not an InstanceStatus. */
export type AgentRowStatus = InstanceStatus | 'killed';
export type EventStatus = RunStatus | 'scaled_up' | 'scaled_down';

export interface Violation {
  rule: string;
  expected: string;
  actual: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  condition?: string | null;
}

export interface GraphSpec {
  nodes: string[];
  edges: GraphEdge[];
}

// ------------------------------------------------------------- ingestion

/**
 * `constraints` is a free-form object in ingestion's real response
 * (AgentConstraints allows extra keys) — not the kernel's
 * list[ConstraintRule]. See rosterd-param-frontend.md gap 4.
 */
export interface AgentConstraints {
  max_refund_usd?: number | null;
  requires_prior_node?: string | null;
  [key: string]: unknown;
}

export interface AgentManifestEntry {
  id: string;
  node: string;
  purpose: string;
  tools: string[];
  direct_assignable: boolean;
  entry_only_via: string[];
  constraints: AgentConstraints;
}

export interface ManifestResponse {
  manifest_id: string;
  status: ManifestStatus;
  agents: AgentManifestEntry[];
  graph: GraphSpec;
}

export interface ConfirmResponse {
  /** Confirming derives a NEW manifest id; it does not mutate the draft. */
  manifest_id: string;
  status: 'confirmed';
}

export interface ParsedTask {
  title: string;
  description: string;
  priority: Priority;
  expectation_criteria: string[];
}

export interface AskResponse {
  agent_id: string;
  task: ParsedTask;
  confidence: Confidence;
}

export interface Provenance {
  [key: string]: unknown;
  commit?: string | null;
  constraints_sha256?: string | null;
  version?: number | null;
  warnings?: string[];
}

// ---------------------------------------------------------------- kernel

/** kernel.TaskSpec — note `id` is required; ingestion's ParsedTask has none,
 *  so the UI mints one when it turns a proposal into a dispatch. */
export interface TaskSpec {
  id: string;
  title: string;
  description: string;
  priority: Priority;
  source?: string | null;
  expectation_criteria: string[];
}

export interface DispatchRequest {
  agent_id: string;
  task: TaskSpec;
  assignees: string[];
}

export interface DispatchResponse {
  run_id: string;
  status: 'accepted' | 'rejected';
  reason?: string | null;
}

export interface RunResponse {
  run_id: string;
  agent_id: string;
  status: RunStatus;
  started_at: string;
  ended_at?: string | null;
  violation?: Violation | null;
  output?: string | null;
  trace_id?: string | null;
}

export interface AgentInstance {
  instance_id: string;
  agent_id: string;
  container_name: string;
  status: InstanceStatus;
  started_at: string;
}

export interface InstancesResponse {
  agent_id: string;
  instances: AgentInstance[];
}

export interface SimulateLoadResponse {
  agent_id: string;
  dispatched: number;
}

/** kernel/manifest.py ConstraintRule — carries the source + confidence
 *  that ingestion's own response does not. */
export type ConstraintSource = 'schema' | 'code' | 'interrupt' | 'default';

export interface ConstraintRule {
  field: string;
  op: 'lte' | 'gte' | 'eq' | 'in' | 'not_in';
  value: number | string | unknown[];
  source: ConstraintSource;
  confidence: Confidence;
}

export interface ScalingPolicy {
  min_replicas: number;
  max_replicas: number;
  target_concurrency: number;
  scale_down_after_idle_seconds: number;
}

export interface KernelManifestEntry {
  id: string;
  node: string;
  purpose: string;
  tools: string[];
  direct_assignable: boolean;
  entry_only_via: string[];
  constraints: ConstraintRule[];
  scaling: ScalingPolicy;
}

export interface KernelManifestResponse {
  manifest_id: string | null;
  loaded: boolean;
  agents: KernelManifestEntry[];
}

// ----------------------------------------------------------- coordinator

export interface SiteSummary {
  site_id: string;
  status: SiteStatus;
  last_event?: string | null;
  score: number;
}

export interface EventLogEntry {
  site_id: string;
  run_id?: string | null;
  status: EventStatus;
  violation?: Violation | null;
  trace_id?: string | null;
  timestamp: string;
}

// --------------------------------------------------- SpacetimeDB tables

export interface AgentRow {
  instance_id: string;
  site_id: string;
  agent_id: string;
  name: string;
  status: AgentRowStatus;
  updated_at: string;
}

export interface AgentMetricsRow {
  id: number;
  site_id: string;
  agent_id: string;
  timestamp: string;
  in_flight_count: number;
  queued_count: number;
  target_concurrency: number;
  current_replicas: number;
  desired_replicas: number;
  min_replicas: number;
  max_replicas: number;
}

export interface SiteRow {
  site_id: string;
  status: SiteStatus;
  last_event: string | null;
  score: number;
}

export interface EventRow {
  id: number;
  site_id: string;
  run_id: string | null;
  status: EventStatus;
  violation: Violation | null;
  trace_id: string | null;
  timestamp: string;
}

export interface TaskRow {
  task_id: string;
  site_id: string;
  agent_id: string;
  title: string;
  status: string;
  assignees: string[];
  criteria: string[];
  priority: string;
  source: string | null;
  created_at: string;
}

export interface ManifestRow {
  manifest_id: string;
  repo_url: string;
  status: ManifestStatus;
  /** JSON-encoded strings, not nested objects — parse client-side. */
  agents_json: string;
  graph_json: string;
  created_at: string;
  version: number;
}

export interface LiveTables {
  agents: AgentRow[];
  agent_metrics: AgentMetricsRow[];
  sites: SiteRow[];
  events: EventRow[];
  tasks: TaskRow[];
  manifests: ManifestRow[];
}
