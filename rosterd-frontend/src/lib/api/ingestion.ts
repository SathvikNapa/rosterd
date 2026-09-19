/** rosterd-ingestion (:8000) — Ingest, Review, Ask, Contracts. */
import { config } from '../config';
import { request } from './http';
import type {
  AgentManifestEntry,
  AskResponse,
  ConfirmResponse,
  ManifestResponse,
  Provenance,
} from '../types';

const service = 'ingestion';
const base = () => config.ingestionUrl;

/**
 * `constraints_yaml` is REQUIRED by the service today, even though the brief's
 * Ingest screen says "repo URL only" — see rosterd-param-frontend.md gap 1.
 * The Ingest screen collects it and sends a documented default when the user
 * leaves the field empty.
 */
export function ingest(repoUrl: string, constraintsYaml: string, signal?: AbortSignal) {
  return request<ManifestResponse>(`${base()}/ingest`, {
    method: 'POST',
    service,
    signal,
    body: { repo_url: repoUrl, constraints_yaml: constraintsYaml },
  });
}

export function getManifest(manifestId: string, signal?: AbortSignal) {
  return request<ManifestResponse>(`${base()}/manifest/${encodeURIComponent(manifestId)}`, {
    service,
    signal,
  });
}

/**
 * Review's "Confirm and go live". Returns a NEW manifest_id — confirming
 * derives an immutable confirmed manifest rather than mutating the draft
 * (ingestion ADR-002), so callers must follow the returned id from here on.
 * Send an empty `agents` array to confirm exactly what was discovered.
 */
export function confirmManifest(manifestId: string, agents: AgentManifestEntry[], signal?: AbortSignal) {
  return request<ConfirmResponse>(`${base()}/manifest/${encodeURIComponent(manifestId)}/confirm`, {
    method: 'POST',
    service,
    signal,
    body: { agents },
  });
}

/** Ask's proposed-task card. 409 `manifest_not_confirmed` if still a draft. */
export function parseAsk(manifestId: string, text: string, signal?: AbortSignal) {
  return request<AskResponse>(`${base()}/ask/parse`, {
    method: 'POST',
    service,
    signal,
    body: { manifest_id: manifestId, text },
  });
}

export function getProvenance(manifestId: string, signal?: AbortSignal) {
  return request<Provenance>(`${base()}/manifest/${encodeURIComponent(manifestId)}/provenance`, {
    service,
    signal,
  });
}

export function listManifests(signal?: AbortSignal) {
  return request<{ manifests: unknown[] }>(`${base()}/manifests`, { service, signal });
}

export function health(signal?: AbortSignal) {
  return request<{ status: string }>(`${base()}/healthz`, { service, signal });
}
