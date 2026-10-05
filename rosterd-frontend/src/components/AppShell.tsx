/**
 * The 64px top bar every frame in Design.html shares, plus the page body.
 *
 * Nav order and styling are the frames' own. "Monitor" is the one addition:
 * the product spec (rosterd-joy-coordinator-frontend.md) lists it as a
 * screen, but Design.html has no frame for it — it is built here in the same
 * system. Nothing else deviates.
 *
 * Two things here are animated (motion.dev):
 *   - the active-nav chip is a single shared element that slides between
 *     links via `layoutId`, rather than one background flicking off and
 *     another flicking on;
 *   - the routed screen cross-fades on navigation. `mode="wait"` holds the
 *     incoming screen until the outgoing one has left, so the two never
 *     overlap and the page never jumps height mid-transition.
 */
import { AnimatePresence, motion } from 'motion/react';
import { NavLink, useLocation, useOutlet } from 'react-router-dom';
import { useLive } from '../lib/live/LiveProvider';
import type { LiveTransport } from '../lib/live/LiveProvider';
import { config } from '../lib/config';
import { LIVE_PULSE, SPRING, usePageVariants } from '../lib/motion';

const LINKS = [
  { to: '/ingest', label: 'Ingest' },
  { to: '/review', label: 'Review' },
  { to: '/ask', label: 'Ask' },
  { to: '/roster', label: 'Roster' },
  { to: '/contracts', label: 'Contracts' },
  { to: '/monitor', label: 'Monitor' },
];

const TRANSPORT_COPY: Record<LiveTransport, { label: string; tone: string; title: string }> = {
  websocket: {
    label: 'Live',
    tone: 'ok',
    title: 'Connected to the coordinator’s live relay (Postgres LISTEN/NOTIFY, over a plain WebSocket).',
  },
  'coordinator-rest': {
    label: 'REST fallback',
    tone: 'warn',
    title: `The live connection is unreachable; polling the coordinator's sites/events every ${config.pollIntervalMs}ms instead.`,
  },
  connecting: { label: 'Connecting…', tone: 'neutral', title: 'Establishing the live connection.' },
  disconnected: {
    label: 'Disconnected',
    tone: 'danger',
    title: 'Neither the live connection nor the coordinator is reachable. Every live table is empty.',
  },
};

export function AppShell() {
  const { transport, error } = useLive();
  const status = TRANSPORT_COPY[transport];
  const location = useLocation();
  const outlet = useOutlet();
  const pageVariants = usePageVariants();

  return (
    <div className="app">
      <header className="nav">
        <NavLink to="/ingest" className="nav__brand">
          <motion.span
            className="nav__mark"
            aria-hidden="true"
            whileHover={{ rotate: 12, scale: 1.1 }}
            transition={SPRING}
          />
          <span className="nav__word">rosterd</span>
        </NavLink>

        <nav className="nav__links" aria-label="Screens">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) => (isActive ? 'nav__link nav__link--active' : 'nav__link')}
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.span
                      layoutId="nav-active-chip"
                      className="nav__link-chip"
                      transition={SPRING}
                      aria-hidden="true"
                    />
                  )}
                  <span className="nav__link-label">{link.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="nav__right">
          <span
            className={status.tone === 'neutral' ? 'pill' : `pill pill--${status.tone}`}
            title={error ? `${status.title}\n\n${error}` : status.title}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            {/* Only the genuinely-live transport gets the heartbeat -- a
                polling or fallback transport claiming to "pulse" would be
                dishonest about what it actually is. */}
            {transport === 'websocket' && (
              <motion.span
                aria-hidden="true"
                animate={LIVE_PULSE}
                style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor', flexShrink: 0 }}
              />
            )}
            {status.label}
          </span>
          <span className="nav__avatar" aria-hidden="true" />
        </div>
      </header>

      {/* Keyed on pathname, not on the full location: a search-param change
          (e.g. ?manifest=…) should not replay the whole screen transition. */}
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={location.pathname}
          className="page-shell"
          variants={pageVariants}
          initial="initial"
          animate="animate"
          exit="exit"
        >
          {outlet}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
