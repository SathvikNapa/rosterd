/**
 * rosterd-coordinator (:8300). Read paths here are the documented FALLBACK
 * for when SpacetimeDB is unreachable — the Federation screen subscribes to
 * the `sites` and `events` tables instead. POST /events and POST /policy/push
 * are kernel-to-coordinator traffic and are deliberately not exposed here.
 */
import { config } from '../config';
import { request } from './http';
import type { EventLogEntry, SiteSummary } from '../types';

const service = 'coordinator';
const base = () => config.coordinatorUrl;

export function getSites(signal?: AbortSignal) {
  return request<SiteSummary[]>(`${base()}/sites`, { service, signal });
}

export function getEvents(params: { siteId?: string; limit?: number } = {}, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (params.siteId) query.set('site_id', params.siteId);
  query.set('limit', String(params.limit ?? 100));
  return request<EventLogEntry[]>(`${base()}/events?${query}`, { service, signal });
}

export function health(signal?: AbortSignal) {
  return request<{ status: string; known_kernels: string[] }>(`${base()}/healthz`, { service, signal });
}
