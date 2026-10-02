/**
 * Every service URL in one place. Defaults match docker-compose.yml plus
 * ingestion's ./scripts/run.sh (ingestion is not wired into compose yet).
 */

const env = import.meta.env;

function str(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.length > 0 ? value : fallback;
}

export const config = {
  ingestionUrl: str(env.VITE_INGESTION_URL, 'http://localhost:8000'),
  kernelUrl: str(env.VITE_KERNEL_URL, 'http://localhost:8100'),
  coordinatorUrl: str(env.VITE_COORDINATOR_URL, 'http://localhost:8300'),
  /** The coordinator's live-relay WebSocket (adapters/http_in/live_ws.py) --
   * Postgres LISTEN/NOTIFY, relayed, since a browser can't open a raw
   * Postgres connection itself. */
  coordinatorWsUrl: str(env.VITE_COORDINATOR_WS_URL, 'ws://localhost:8300/ws'),
  jaegerBaseUrl: str(env.VITE_JAEGER_BASE_URL, 'http://localhost:16686'),
  /** The site this UI drives. The kernel in compose is `site-a`. */
  siteId: str(env.VITE_SITE_ID, 'site-a'),
  /**
   * 'live'  — talk to the real services (default).
   * 'demo'  — no backend at all; render the fixtures from the Figma frames.
   *           Useful for design review and for demoing the UI before
   *           `docker compose up` has finished.
   */
  mode: str(env.VITE_ROSTERD_MODE, 'live') as 'live' | 'demo',
  /** Poll interval for the coordinator-REST fallback, milliseconds (used
   * only when the live WebSocket itself can't connect). */
  pollIntervalMs: Number(str(env.VITE_POLL_INTERVAL_MS, '2000')),
};

export const isDemo = config.mode === 'demo';

/** Jaeger deep link for a trace id, or null when there is no trace. */
export function traceUrl(traceId: string | null | undefined): string | null {
  if (!traceId) return null;
  return `${config.jaegerBaseUrl}/trace/${traceId}`;
}
