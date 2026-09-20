/** Small pieces the frames repeat. One definition each, no per-screen forks. */
import { AnimatePresence, motion } from 'motion/react';
import type { CSSProperties, ComponentProps, ReactNode } from 'react';
import { traceUrl } from '../lib/config';
import { pillClass, shortTrace } from '../lib/format';
import type { Tone } from '../lib/format';
import { HOVER_LIFT, POP, SPRING, TAP_PRESS, useBadgePresence } from '../lib/motion';

/**
 * Keyed on tone+children so a status flip (idle -> working -> killed) pops
 * in fresh rather than the DOM node quietly changing color under a reader's
 * eye -- the same "make the change legible" idea as AnimatedNumber.
 */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  const presence = useBadgePresence();
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      <motion.span key={`${tone}:${String(children)}`} className={pillClass(tone)} {...presence} transition={POP}>
        {children}
      </motion.span>
    </AnimatePresence>
  );
}

export function SmallBadge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  const presence = useBadgePresence();
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      <motion.span
        key={`${tone}:${String(children)}`}
        className={`${pillClass(tone)} pill--sm`}
        {...presence}
        transition={POP}
      >
        {children}
      </motion.span>
    </AnimatePresence>
  );
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

/**
 * Entrance only, deliberately -- most call sites are `{cond && <Banner/>}`
 * with no <AnimatePresence> above them, so an exit animation would need
 * every call site changed for one that just vanishes today anyway. The
 * arrival (a message appearing) is the moment worth marking; the
 * departure usually coincides with navigating away, where it wouldn't be
 * seen regardless.
 */
export function Banner({ tone, children }: { tone: 'danger' | 'info' | 'warn'; children: ReactNode }) {
  return (
    <motion.div
      className={`banner banner--${tone}`}
      role={tone === 'danger' ? 'alert' : undefined}
      initial={{ opacity: 0, y: -6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={SPRING}
    >
      {children}
    </motion.div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/**
 * Per-instance concurrency dots: solid = working, hollow = idle, so parallel
 * draining reads as visible rather than inferred. A dot pops in when the
 * pool grows and pops out when it shrinks -- during a real load test this
 * is the row that's actually moving, so it's the one most worth animating.
 * `layout` lets the survivors slide into their new slot instead of jumping.
 */
export function ConcurrencyDots({
  instances,
}: {
  instances: Array<{ instance_id: string; status: string }>;
}) {
  return (
    <span className="row" style={{ gap: 5 }} aria-label={`${instances.length} instances`}>
      <AnimatePresence initial={false}>
        {instances.map((instance) => (
          <motion.span
            key={instance.instance_id}
            layout
            title={`${instance.instance_id}: ${instance.status}`}
            style={dotStyle(instance.status)}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            transition={POP}
          />
        ))}
      </AnimatePresence>
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

/** The thin load bar on each Federation site card. A spring, not a CSS
 * ease -- under a real flash-sale load test the value can change again
 * before the previous tween finishes, and a spring retargets smoothly
 * where a CSS transition restarts and visibly stutters. */
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
      <motion.div
        style={{
          height: '100%',
          background: tone === 'warn' ? 'var(--warn)' : 'var(--accent-mid)',
        }}
        initial={false}
        animate={{ width: `${pct}%` }}
        transition={SPRING}
      />
    </div>
  );
}

/**
 * `.btn`'s own hover/disabled color handling stays in CSS (className is
 * unchanged) -- this only adds the physical layer CSS can't: a lift toward
 * the cursor on hover, a real press on click, both interruptible mid-motion
 * because they're springs, not keyframed transitions. Every primary CTA in
 * the app (Analyze, Do it, Confirm, Schedule, Dispatch, Kill run, Simulate
 * flash sale) should be this, not a bare <button>.
 */
export function Button({
  className = '',
  disabled,
  children,
  ...rest
}: ComponentProps<typeof motion.button> & { className?: string }) {
  return (
    <motion.button
      className={`btn ${className}`.trim()}
      disabled={disabled}
      whileHover={disabled ? undefined : HOVER_LIFT}
      whileTap={disabled ? undefined : TAP_PRESS}
      {...rest}
    >
      {children}
    </motion.button>
  );
}
