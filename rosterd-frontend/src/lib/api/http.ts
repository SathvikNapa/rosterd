/**
 * Thin fetch wrapper. All three services return the same error envelope on
 * an expected failure ({code, message, details} — see rosterd-ingestion's
 * errors.py and rosterd-kernel's errors.py), so branch on `code`, never on
 * a message string.
 */

export interface ServiceErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export class ServiceError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(status: number, payload: ServiceErrorPayload) {
    super(payload.message);
    this.name = 'ServiceError';
    this.code = payload.code;
    this.status = status;
    this.details = payload.details ?? {};
  }
}

/** Thrown when the service itself is unreachable (not running, CORS, DNS). */
export class TransportError extends Error {
  constructor(
    readonly service: string,
    cause: unknown,
  ) {
    super(`${service} is unreachable — is it running?`);
    this.name = 'TransportError';
    this.cause = cause;
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  signal?: AbortSignal;
  /** Label used in TransportError messages. */
  service: string;
}

export async function request<T>(url: string, options: RequestOptions): Promise<T> {
  const { method = 'GET', body, signal, service } = options;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new TransportError(service, cause);
  }

  if (!response.ok) {
    const payload = await readErrorPayload(response);
    throw new ServiceError(response.status, payload);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function readErrorPayload(response: Response): Promise<ServiceErrorPayload> {
  try {
    const data = (await response.json()) as unknown;
    if (data && typeof data === 'object') {
      const record = data as Record<string, unknown>;
      // FastAPI's own validation errors use {detail: ...} rather than the
      // services' {code, message, details} envelope.
      if (typeof record.code === 'string' && typeof record.message === 'string') {
        return record as unknown as ServiceErrorPayload;
      }
      if ('detail' in record) {
        return {
          code: `http_${response.status}`,
          message: describeDetail(record.detail),
          details: record as Record<string, unknown>,
        };
      }
    }
  } catch {
    /* fall through to the generic message below */
  }
  return { code: `http_${response.status}`, message: `${response.status} ${response.statusText}` };
}

function describeDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === 'object') {
          const entry = item as { loc?: unknown[]; msg?: string };
          const where = Array.isArray(entry.loc) ? entry.loc.join('.') : '';
          return where ? `${where}: ${entry.msg ?? 'invalid'}` : (entry.msg ?? 'invalid');
        }
        return String(item);
      })
      .join('; ');
  }
  return JSON.stringify(detail);
}

/** A human-readable line for any thrown value, for rendering in a banner. */
export function describeError(error: unknown): string {
  if (error instanceof ServiceError) return `${error.message} (${error.code})`;
  if (error instanceof TransportError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
