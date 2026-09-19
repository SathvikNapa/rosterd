/** rosterd-kernel (:8100) — dispatch, runs, pools, simulate-load. */
import { config } from '../config';
import { request } from './http';
import type {
  DispatchRequest,
  DispatchResponse,
  InstancesResponse,
  KernelManifestResponse,
  RunResponse,
  SimulateLoadResponse,
} from '../types';

const service = 'kernel';
const base = () => config.kernelUrl;

/** Ask's "Do it". `task.id` is required by kernel.TaskSpec — mint one. */
export function dispatch(body: DispatchRequest, signal?: AbortSignal) {
  return request<DispatchResponse>(`${base()}/dispatch`, {
    method: 'POST',
    service,
    signal,
    body,
  });
}

export function getRun(runId: string, signal?: AbortSignal) {
  return request<RunResponse>(`${base()}/runs/${encodeURIComponent(runId)}`, { service, signal });
}

export function killRun(runId: string, signal?: AbortSignal) {
  return request<{ run_id: string; status: 'killed'; reason: string }>(
    `${base()}/runs/${encodeURIComponent(runId)}/kill`,
    { method: 'POST', service, signal },
  );
}

/**
 * Federation's "Simulate flash sale" — deliberately this and not the manual
 * POST /agents/{id}/scale, so the scaler reacts to real load rather than the
 * UI setting a replica count by hand.
 */
export function simulateLoad(
  agentId: string,
  body: { count?: number; rate_per_second?: number; scale_down_after_idle_seconds_override?: number },
  signal?: AbortSignal,
) {
  return request<SimulateLoadResponse>(
    `${base()}/agents/${encodeURIComponent(agentId)}/simulate-load`,
    { method: 'POST', service, signal, body },
  );
}

/** Debug/fallback. Prefer the `agents` table filtered by agent_id. */
export function getInstances(agentId: string, signal?: AbortSignal) {
  return request<InstancesResponse>(`${base()}/agents/${encodeURIComponent(agentId)}/instances`, {
    service,
    signal,
  });
}

/**
 * The manifest currently governing this site. This is the only place with
 * per-constraint `source` + `confidence` (kernel adapts ingestion's free-form
 * constraints via legacy_constraints.py), which is what Review's and
 * Contracts' source badges read — see rosterd-param-frontend.md gap 4.
 */
export function getKernelManifest(signal?: AbortSignal) {
  return request<KernelManifestResponse>(`${base()}/manifest`, { service, signal });
}

export function health(signal?: AbortSignal) {
  return request<{ status: 'ok' | 'degraded'; budget_remaining: number }>(`${base()}/health`, {
    service,
    signal,
  });
}
