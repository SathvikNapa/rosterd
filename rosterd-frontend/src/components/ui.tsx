/** Small pieces the frames repeat. One definition each, no per-screen forks. */
import type { CSSProperties, ReactNode } from 'react';
import { traceUrl } from '../lib/config';
import { pillClass, shortTrace } from '../lib/format';
import type { Tone } from '../lib/format';

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={pillClass(tone)}>{children}</span>;
}

export function SmallBadge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`${pillClass(tone)} pill--sm`}>{children}</span>;
}

export function Label({ children }: { children: ReactNode }) {
  return <span className="label">{children}</span>;
}

/**
 * "View trace" — rendered wherever a row carries a trace_id, linking to
 * {JAEGER_BASE_URL}/trace/{trace_id}. Renders nothing when there is no trace,
 * so callers don't each need the same guard.
 */
export function TraceLink({ traceId, small = false }: { traceId: string | null | undefined; small?: boolean }) {
  const href = traceUrl(traceId);
  if (!href) return null;
  return (
    <a
      className={small ? 'trace-link trace-link--sm' : 'trace-link'}
      href={href}
      target="_blank"
      rel="noreferrer"
      title={`Open trace ${traceId} in Jaeger`}
    >
      <span aria-hidden="true">🔗</span>View trace
    </a>
  );
}

export function TraceId({ traceId }: { traceId: string | null | undefined }) {
  if (!traceId) return null;
  return (
    <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }} title={traceId}>
      trace_id: {shortTrace(traceId)}
    </span>
  );
}

/** The checked-box criterion line from the Ask and Roster frames. */
export function Criterion({ children }: { children: ReactNode }) {
  return (
    <div className="criterion">
      <span className="criterion__box" aria-hidden="true">
        ✓
      </span>
      {children}
    </div>
  );
}

export function Banner({ tone, children }: { tone: 'danger' | 'info' | 'warn'; children: ReactNode }) {
  return (
    <div className={`banner banner--${tone}`} role={tone === 'danger' ? 'alert' : undefined}>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/**
 * Per-instance concurrency dots: solid = working, hollow = idle, so parallel
 * draining reads as visible rather than inferred.
 */
export function ConcurrencyDots({
  instances,
}: {
  instances: Array<{ instance_id: string; status: string }>;
}) {
  return (
    <span className="row" style={{ gap: 5 }} aria-label={`${instances.length} instances`}>
      {instances.map((instance) => (
        <span
          key={instance.instance_id}
          title={`${instance.instance_id}: ${instance.status}`}
          style={dotStyle(instance.status)}
        />
      ))}
    </span>
  );
}

function dotStyle(status: string): CSSProperties {
  const base: CSSProperties = {
    width: 8,
    height: 8,
    borderRadius: '50%',
    display: 'inline-block',
    flexShrink: 0,
  };
  if (status === 'working') return { ...base, background: 'var(--ok)' };
  if (status === 'killed') return { ...base, background: 'var(--danger)' };
  return { ...base, background: 'transparent', border: '1.5px solid var(--accent-mid)' };
}

/** The thin load bar on each Federation site card. */
export function LoadBar({ value, tone }: { value: number; tone: Tone }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div
      className="loadbar"
      style={{ height: 6, borderRadius: 4, background: 'var(--border-soft)', overflow: 'hidden' }}
      title={`Pool load ${pct}% of current capacity`}
      role="img"
      aria-label={`Pool load ${pct} percent of current capacity`}
    >
      <div
        style={{
          width: `${pct}%`,
          height: '100%',
          background: tone === 'warn' ? 'var(--warn)' : 'var(--accent-mid)',
          transition: 'width 400ms ease',
        }}
      />
    </div>
  );
}
