/** Display helpers. Colors are the design tokens, never ad-hoc hexes. */
import type { AgentRowStatus, ConstraintSource, EventStatus, Priority } from './types';

/** Circle-avatar letter, e.g. "Refund/Exception" -> "R". */
export function initial(name: string): string {
  const trimmed = name.trim();
  return trimmed ? trimmed[0].toUpperCase() : '?';
}

/** "refund-exception" -> "Refund Exception" — only used when no name row exists. */
export function titleize(id: string): string {
  return id
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(' ');
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'never';
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.round(minutes / 60)}h ago`;
}

/** Trace ids are 32 hex chars; the frames show them as "7a3f…e21c". */
export function shortTrace(traceId: string | null | undefined): string {
  if (!traceId) return '—';
  return traceId.length <= 12 ? traceId : `${traceId.slice(0, 4)}…${traceId.slice(-4)}`;
}

export type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger';

export interface Palette {
  tone: Tone;
  /** Avatar fill, avatar ring, avatar glyph — the frames' bubble colors. */
  bg: string;
  border: string;
  fg: string;
}

/**
 * `border` is a mark (a 2px ring) and may use the vivid step; `fg` is text
 * sitting on `bg` and must use the darker step, or the glyph drops below
 * 4.5:1 on the Tuscan surfaces. See the contrast note in tokens.css.
 */
const PALETTES: Record<Tone, Palette> = {
  neutral: { tone: 'neutral', bg: 'var(--surface-muted)', border: 'var(--border-neutral)', fg: 'var(--text-muted)' },
  accent: { tone: 'accent', bg: 'var(--accent-soft)', border: 'var(--accent)', fg: 'var(--accent-dark)' },
  ok: { tone: 'ok', bg: 'var(--ok-soft)', border: 'var(--ok)', fg: 'var(--ok-dark)' },
  warn: { tone: 'warn', bg: 'var(--warn-soft)', border: 'var(--warn)', fg: 'var(--warn-dark)' },
  danger: { tone: 'danger', bg: 'var(--danger-soft)', border: 'var(--danger)', fg: 'var(--danger)' },
};

export function palette(tone: Tone): Palette {
  return PALETTES[tone];
}

/**
 * The frames color an agent bubble by what the pool is *doing*: idle is
 * neutral grey, working is green, a pool scaled past one instance is amber,
 * and a killed instance is red.
 */
export function poolTone(status: AgentRowStatus, replicas: number): Tone {
  if (status === 'killed') return 'danger';
  if (replicas > 1) return 'warn';
  if (status === 'working') return 'ok';
  return 'neutral';
}

export function poolStatusLabel(status: AgentRowStatus, replicas: number): string {
  if (status === 'killed') return 'Killed';
  if (replicas > 1) return 'Scaled up';
  if (status === 'working') return 'Working';
  return 'Idle';
}

export function eventTone(status: EventStatus): Tone {
  switch (status) {
    case 'killed':
      return 'danger';
    case 'done':
      return 'ok';
    case 'working':
      return 'accent';
    case 'paused':
      return 'warn';
    case 'scaled_up':
      return 'warn';
    default:
      return 'neutral';
  }
}

export function priorityTone(priority: Priority | string): Tone {
  if (priority === 'high') return 'warn';
  if (priority === 'low') return 'neutral';
  return 'accent';
}

export function confidenceTone(confidence: string): Tone {
  if (confidence === 'high') return 'ok';
  if (confidence === 'medium') return 'warn';
  return 'danger';
}

/**
 * The Review/Contracts "Source" badge. Labels and colors are exactly the
 * four the frames draw; the enum is the kernel's ConstraintSource
 * (manifest.py), which is the only place per-constraint provenance exists
 * today — ingestion's own response has none (param-frontend.md gap 4).
 */
export function sourceBadge(source: ConstraintSource | 'graph' | null): { label: string; tone: Tone } {
  switch (source) {
    case 'schema':
      return { label: 'Tool schema', tone: 'accent' };
    case 'code':
      return { label: 'Code guard', tone: 'warn' };
    case 'interrupt':
      return { label: 'interrupt() detected', tone: 'danger' };
    case 'graph':
      return { label: 'Graph', tone: 'neutral' };
    default:
      return { label: 'Default', tone: 'neutral' };
  }
}

export function pillClass(tone: Tone): string {
  return tone === 'neutral' ? 'pill' : `pill pill--${tone}`;
}
