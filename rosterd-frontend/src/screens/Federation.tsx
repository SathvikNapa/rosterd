/**
 * Screen 7 — "Federated sites" (Design.html frame 7).
 *
 * One card per site, built from the live `sites` and `agents` tables. The
 * concurrency dots are per-instance (solid = working, hollow = idle) so
 * parallel draining is visible rather than inferred, and a pool past one
 * instance shows its pod count instead.
 *
 * "Simulate flash sale" calls POST /agents/{agent_id}/simulate-load —
 * deliberately not the manual /scale endpoint, so what you watch is the
 * scaler reacting to real load.
 */
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Badge, Banner, ConcurrencyDots, Empty, LoadBar, TraceLink } from '../components/ui';
import { simulateLoad } from '../lib/api/kernel';
import { describeError } from '../lib/api/http';
import { config, isDemo } from '../lib/config';
import { clockTime, relativeTime } from '../lib/format';
import type { Tone } from '../lib/format';
import { useLive } from '../lib/live/LiveProvider';
import { describeEvent, orderPools, recentEvents, siteIds, siteRollup } from '../lib/selectors';
import type { SiteRollup } from '../lib/selectors';
import { useManifest } from '../lib/useManifest';
import { useSession } from '../lib/session';

export function Federation() {
  const { tables, refresh } = useLive();
  const session = useSession();
  const { manifest } = useManifest(session.activeManifestId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const ids = useMemo(() => siteIds(tables), [tables]);
  const order = useMemo(() => manifest?.agents.map((agent) => agent.id) ?? [], [manifest]);
  const rollups = useMemo(
    () =>
      ids.map((id) => {
        const rollup = siteRollup(tables, id);
        return { ...rollup, pools: orderPools(rollup.pools, order) };
      }),
    [ids, tables, order],
  );
  // The frame reads oldest-at-top, so the newest line lands at the bottom.
  const feed = useMemo(() => recentEvents(tables.events, 8).reverse(), [tables.events]);

  /** The busiest pool at this UI's own site is what a flash sale should hit. */
  const localSite = rollups.find((rollup) => rollup.site_id === config.siteId);
  const targetAgent =
    [...(localSite?.pools ?? [])].sort((a, b) => b.replicas - a.replicas)[0]?.agent_id ?? 'fulfillment';

  const flashSale = async () => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const response = await simulateLoad(targetAgent, { count: 12, rate_per_second: 4 });
      setNote(`Dispatched ${response.dispatched} synthetic tasks at ${response.agent_id}. Watch the pool scale.`);
      refresh();
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page">
      <div className="row row--between" style={{ alignItems: 'flex-start' }}>
        <div className="page__head">
          <h2>Federated sites</h2>
          <p style={{ maxWidth: 560 }}>
            Same roster running independently across {rollups.length || 'multiple'} sites. Only scores and
            failure patterns cross the boundary, never task content.
          </p>
        </div>
        <button
          type="button"
          className="btn btn--warn"
          onClick={flashSale}
          disabled={busy || isDemo}
          title={
            isDemo
              ? 'Demo mode: no kernel is being called.'
              : `POST /agents/${targetAgent}/simulate-load on ${config.kernelUrl}`
          }
        >
          ⚡ {busy ? 'Dispatching…' : 'Simulate flash sale'}
        </button>
      </div>

      {error && <Banner tone="danger">{error}</Banner>}
      {note && <Banner tone="info">{note}</Banner>}

      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {rollups.map((rollup) => (
          <SiteCard key={rollup.site_id} rollup={rollup} />
        ))}
        {rollups.length === 0 && (
          <div className="card card--xl" style={{ flex: 1 }}>
            <Empty>
              No sites reporting yet. The coordinator writes a <code>sites</code> row on every event a kernel
              posts.
            </Empty>
          </div>
        )}
      </div>

      <div className="card card--xl row row--between" style={{ padding: '24px 28px', gap: 32, alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-strong)' }}>Coordinator</div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4 }}>
            Aggregates pool size, scores, and failure patterns only, no order data crosses sites.
          </div>
        </div>
        <div
          className="mono"
          style={{ fontSize: 12, color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: 8, textAlign: 'right' }}
        >
          {feed.map((event) => (
            <div key={`${event.site_id}-${event.id}`} className="row row--end" style={{ gap: 10 }}>
              <span>
                [{clockTime(event.timestamp)}] {describeEvent(event)}
              </span>
              {event.run_id && (
                <Link
                  to={`/runs/${encodeURIComponent(event.run_id)}`}
                  className="linkish"
                  style={{ fontFamily: 'var(--font-sans)', fontSize: 11 }}
                >
                  open run
                </Link>
              )}
              <TraceLink traceId={event.trace_id} small />
            </div>
          ))}
          {feed.length === 0 && <span className="faint">No events yet.</span>}
        </div>
      </div>
    </main>
  );
}

function SiteCard({ rollup }: { rollup: SiteRollup }) {
  const status = rollup.site?.status ?? 'healthy';
  // A site can be scaled up AND in violation at once; the frame shows one
  // badge because its site only was one thing. Show both when both are true.
  const badges = siteBadges(status, rollup.scaledUp);
  const accent = badges[0].tone;

  return (
    <div
      className="card card--xl"
      style={{
        flex: '1 1 300px',
        border: accent === 'accent' ? '1px solid var(--border)' : `1.5px solid var(--${accent})`,
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
      }}
    >
      <div className="row row--between">
        <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-strong)' }}>
          {siteLabel(rollup.site_id)}
        </span>
        <span className="row" style={{ gap: 6 }}>
          {badges.map((badge) => (
            <Badge key={badge.label} tone={badge.tone}>
              {badge.label}
            </Badge>
          ))}
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {rollup.pools.map((pool) => (
          <div key={pool.agent_id} className="row row--between" style={{ fontSize: 13, color: 'var(--text-body)' }}>
            <span>{pool.name}</span>
            {pool.replicas > 1 ? (
              <span className="row" style={{ gap: 8 }}>
                <ConcurrencyDots instances={pool.instances.filter((instance) => instance.status !== 'killed')} />
                <span className="mono" style={{ fontSize: 11, color: 'var(--warn)', fontWeight: 700 }}>
                  ×{pool.replicas} pods
                </span>
              </span>
            ) : (
              <ConcurrencyDots instances={pool.instances} />
            )}
          </div>
        ))}
        {rollup.pools.length === 0 && <span className="muted faint">No instances reported.</span>}
      </div>

      <LoadBar value={rollup.saturation} tone={rollup.scaledUp ? 'warn' : 'accent'} />

      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        {rollup.lastEvent
          ? `Last event: ${describeEvent(rollup.lastEvent)} · ${relativeTime(rollup.lastEvent.timestamp)}`
          : rollup.site?.last_event
            ? `Last event ${relativeTime(rollup.site.last_event)}`
            : 'No load event yet'}
      </div>
    </div>
  );
}

/**
 * The frame's badge is not `SiteStatus` verbatim: "Scaled up" is a fact about
 * the `agents` table, not the coordinator's health status. Both can hold, so
 * both render — health first, since that is what decides the card's border.
 */
function siteBadges(status: string, scaledUp: boolean): Array<{ label: string; tone: Tone }> {
  const health: { label: string; tone: Tone } =
    status === 'violation'
      ? { label: 'Violation', tone: 'danger' }
      : status === 'offline'
        ? { label: 'Offline', tone: 'neutral' }
        : { label: 'Healthy', tone: 'accent' };

  if (!scaledUp) return [health];
  const scaled: { label: string; tone: Tone } = { label: 'Scaled up', tone: 'warn' };
  return health.tone === 'accent' ? [scaled] : [health, scaled];
}

function siteLabel(siteId: string): string {
  const match = /^site-([a-z])$/i.exec(siteId);
  return match ? `Site ${match[1].toUpperCase()}` : siteId;
}
