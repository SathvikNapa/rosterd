/**
 * The 64px top bar every frame in Design.html shares, plus the page body.
 *
 * Nav order and styling are the frames' own. "Monitor" is the one addition:
 * the product spec (rosterd-joy-coordinator-frontend.md) lists it as a
 * screen, but Design.html has no frame for it — it is built here in the same
 * system. Nothing else deviates.
 */
import { NavLink, Outlet } from 'react-router-dom';
import { useLive } from '../lib/live/LiveProvider';
import type { LiveTransport } from '../lib/live/LiveProvider';
import { config } from '../lib/config';

const LINKS = [
  { to: '/ingest', label: 'Ingest' },
  { to: '/review', label: 'Review' },
  { to: '/ask', label: 'Ask' },
  { to: '/roster', label: 'Roster' },
  { to: '/contracts', label: 'Contracts' },
  { to: '/federation', label: 'Federation' },
  { to: '/monitor', label: 'Monitor' },
];

const TRANSPORT_COPY: Record<LiveTransport, { label: string; tone: string; title: string }> = {
  demo: { label: 'Demo data', tone: 'warn', title: 'VITE_ROSTERD_MODE=demo — no backend is being read.' },
  websocket: {
    label: 'Live',
    tone: 'ok',
    title: 'Subscribed to SpacetimeDB over websocket via generated bindings.',
  },
  'sql-poll': {
    label: 'Polling',
    tone: 'accent',
    title: `Reading SpacetimeDB over HTTP SQL every ${config.pollIntervalMs}ms. Run npm run gen:bindings for the websocket subscription.`,
  },
  'coordinator-rest': {
    label: 'REST fallback',
    tone: 'warn',
    title: 'SpacetimeDB is unreachable; reading sites and events from the coordinator.',
  },
  connecting: { label: 'Connecting…', tone: 'neutral', title: 'Establishing a connection to SpacetimeDB.' },
  disconnected: {
    label: 'Disconnected',
    tone: 'danger',
    title: 'Neither SpacetimeDB nor the coordinator is reachable. Every live table is empty.',
  },
};

export function AppShell() {
  const { transport, error } = useLive();
  const status = TRANSPORT_COPY[transport];

  return (
    <div className="app">
      <header className="nav">
        <NavLink to="/ingest" className="nav__brand">
          <span className="nav__mark" aria-hidden="true" />
          <span className="nav__word">rosterd</span>
        </NavLink>

        <nav className="nav__links" aria-label="Screens">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) => (isActive ? 'nav__link nav__link--active' : 'nav__link')}
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        <div className="nav__right">
          <span
            className={status.tone === 'neutral' ? 'pill' : `pill pill--${status.tone}`}
            title={error ? `${status.title}\n\n${error}` : status.title}
          >
            {status.label}
          </span>
          <span className="nav__avatar" aria-hidden="true" />
        </div>
      </header>

      <Outlet />
    </div>
  );
}
