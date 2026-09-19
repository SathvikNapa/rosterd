/**
 * Monitor — per-agent load vs replica count, and the scaling formula spelled
 * out with the current numbers.
 *
 * NOTE: this screen has no frame in Design.html. The product spec
 * (rosterd-joy-coordinator-frontend.md) lists it as a screen, so it is built
 * here in the same design system rather than left out; every other screen is
 * a direct implementation of a frame.
 *
 * The formula is verbatim from rosterd-kernel/scaler.py. `desired_replicas`
 * is already computed and stored on each row — this shows the inputs next to
 * it rather than recomputing the answer.
 */
import { useMemo } from 'react';
import { Badge, Banner, Empty, Label } from '../components/ui';
import { config } from '../lib/config';
import { relativeTime } from '../lib/format';
import { useLive } from '../lib/live/LiveProvider';
import { agentDisplayName, latestMetricsBySite, metricsSeries, scalerReadout } from '../lib/selectors';
import type { AgentMetricsRow } from '../lib/types';

export function Monitor() {
  const { tables } = useLive();
  const latest = useMemo(
    () => latestMetricsBySite(tables.agent_metrics, config.siteId),
    [tables.agent_metrics],
  );

  return (
    <main className="page">
      <div className="page__head">
        <h2>Scaler monitor</h2>
        <p>
          One card per agent pool at <code>{config.siteId}</code>, straight from <code>agent_metrics</code> —
          the kernel writes a row every scaler tick, changed or not.
        </p>
      </div>

      {latest.length === 0 && (
        <Banner tone="info">
          No <code>agent_metrics</code> rows for <code>{config.siteId}</code> yet. Trigger a flash sale on{' '}
          <a href="/federation">Federation</a> and the scaler starts reporting.
        </Banner>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
        {latest.map((row) => (
          <MonitorCard
            key={row.agent_id}
            row={row}
            name={agentDisplayName(tables.agents, row.agent_id)}
            series={metricsSeries(tables.agent_metrics, config.siteId, row.agent_id)}
          />
        ))}
      </div>
    </main>
  );
}

function MonitorCard({ row, name, series }: { row: AgentMetricsRow; name: string; series: AgentMetricsRow[] }) {
  const readout = scalerReadout(row);

  return (
    <div className="card card--xl" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div className="row row--between">
        <div className="row" style={{ gap: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-strong)' }}>{name}</span>
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            {row.agent_id}
          </span>
          <Badge tone={readout.clamped === 'max' ? 'warn' : readout.desired > readout.current ? 'accent' : 'neutral'}>
            {readout.current} running → {readout.desired} desired
          </Badge>
        </div>
        <span className="muted">updated {relativeTime(row.timestamp)}</span>
      </div>

      <div className="col" style={{ gap: 8 }}>
        <div className="row" style={{ gap: 18 }}>
          <LegendSwatch color="var(--accent)" label="load (in flight + queued)" />
          <LegendSwatch color="var(--warn)" label="replicas running" dashed />
        </div>
        <Sparkline series={series} />
      </div>

      <div
        className="mono"
        style={{
          fontSize: 13,
          color: 'var(--text)',
          background: 'var(--surface-muted)',
          borderRadius: 'var(--r-md)',
          padding: '12px 16px',
          lineHeight: 1.7,
        }}
      >
        {readout.sentence}
      </div>

      <div className="row" style={{ gap: 28, flexWrap: 'wrap' }}>
        <Stat label="In flight" value={row.in_flight_count} />
        <Stat label="Queued" value={row.queued_count} />
        <Stat label="Target concurrency" value={row.target_concurrency} />
        <Stat label="Replicas" value={`${row.current_replicas} / ${row.max_replicas}`} />
        <Stat label="Min – max" value={`${row.min_replicas} – ${row.max_replicas}`} />
      </div>
    </div>
  );
}

function LegendSwatch({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="row" style={{ gap: 6, fontSize: 11, color: 'var(--text-muted)' }}>
      <span
        aria-hidden="true"
        style={{
          width: 16,
          height: 0,
          borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${color}`,
          display: 'inline-block',
        }}
      />
      {label}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="col" style={{ gap: 4 }}>
      <Label>{label}</Label>
      <span className="mono" style={{ fontSize: 18, color: 'var(--text-strong)' }}>
        {value}
      </span>
    </div>
  );
}

/**
 * Load (area) against replica count (step line) on one shared x-axis. Both
 * are scaled to the same max so the replica line reads as "did the pool keep
 * up", which is the whole question this screen answers.
 */
function Sparkline({ series }: { series: AgentMetricsRow[] }) {
  const width = 640;
  const height = 96;

  if (series.length < 2) {
    return <Empty>Not enough ticks yet to draw a trend.</Empty>;
  }

  const loads = series.map((row) => row.in_flight_count + row.queued_count);
  const replicas = series.map((row) => row.current_replicas);
  const ceiling = Math.max(1, ...loads, ...replicas);
  const step = width / (series.length - 1);

  const point = (value: number, index: number) => [index * step, height - (value / ceiling) * (height - 8) - 4];

  const loadPath = loads.map((value, index) => `${index === 0 ? 'M' : 'L'}${point(value, index).join(',')}`).join(' ');
  const areaPath = `${loadPath} L${width},${height} L0,${height} Z`;
  const replicaPath = replicas
    .flatMap((value, index) => {
      const [x, y] = point(value, index);
      return index === 0 ? [`M${x},${y}`] : [`L${x},${point(replicas[index - 1], index)[1]}`, `L${x},${y}`];
    })
    .join(' ');

  return (
    <svg
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Load and replica count over the last ${series.length} scaler ticks`}
    >
      <path d={areaPath} fill="var(--accent-soft)" />
      <path d={loadPath} fill="none" stroke="var(--accent)" strokeWidth={2} />
      <path d={replicaPath} fill="none" stroke="var(--warn)" strokeWidth={2} strokeDasharray="4 3" />
    </svg>
  );
}
