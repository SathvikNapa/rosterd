/**
 * Screen 6 — "Agent contracts" (Design.html frame 6).
 *
 * Reads the confirmed manifest. The frame's Scaling column comes from the
 * kernel's ScalingPolicy (ingestion's manifest has no `scaling` field at
 * all), so it shows "—" for an agent the kernel isn't governing.
 */
import { useMemo } from 'react';
import { AgentBubble } from '../components/AgentBubble';
import { Badge, Banner, Empty } from '../components/ui';
import { relativeTime, sourceBadge } from '../lib/format';
import { useLive } from '../lib/live/LiveProvider';
import { rulesForAgent, spendLimit } from '../lib/rules';
import { agentDisplayName, poolsForSite } from '../lib/selectors';
import { config } from '../lib/config';
import { useSession } from '../lib/session';
import { useManifest } from '../lib/useManifest';
import type { KernelManifestEntry } from '../lib/types';

export function Contracts() {
  const session = useSession();
  const { tables } = useLive();
  const { manifest, kernelEntries, kernelManifestId, error } = useManifest(session.activeManifestId);

  const pools = useMemo(() => poolsForSite(tables.agents, config.siteId), [tables.agents]);
  const kernelById = useMemo(
    () => new Map(kernelEntries.map((entry) => [entry.id, entry])),
    [kernelEntries],
  );
  const manifestRow = manifest
    ? tables.manifests.find((row) => row.manifest_id === manifest.manifest_id)
    : undefined;

  if (!manifest) {
    return (
      <main className="page">
        <div className="page__head">
          <h2>Agent contracts</h2>
          <p>Nothing ingested yet.</p>
        </div>
        <Banner tone="info">
          Analyze a repository on <a href="/ingest">Ingest</a> — its contracts land here once confirmed.
        </Banner>
      </main>
    );
  }

  return (
    <main className="page">
      <div className="page__head">
        <h2>Agent contracts</h2>
        <p>
          Inferred from your code, not typed by hand, then confirmed by you and enforced by the kernel at every
          tool call.
        </p>
      </div>

      {error && <Banner tone="danger">{error}</Banner>}

      <div className="card card--flush">
        <table className="table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Tools granted</th>
              <th>Spend limit</th>
              <th>Source</th>
              <th>Direct assignable</th>
              <th>Scaling</th>
            </tr>
          </thead>
          <tbody>
            {manifest.agents.map((agent) => {
              const kernelEntry = kernelById.get(agent.id);
              const pool = pools.find((item) => item.agent_id === agent.id);
              const rules = rulesForAgent(agent, kernelEntry);
              const primary = rules.find((rule) => rule.source && rule.source !== 'graph') ?? rules[0];
              const badge = sourceBadge(primary?.source ?? null);
              const limit = spendLimit(agent);

              return (
                <tr key={agent.id}>
                  <td>
                    <div className="row" style={{ gap: 12 }}>
                      <AgentBubble
                        name={agentDisplayName(tables.agents, agent.id)}
                        tone={pool && pool.replicas > 1 ? 'warn' : pool?.status === 'working' ? 'ok' : 'neutral'}
                        size={32}
                      />
                      <span style={{ fontWeight: 600, color: 'var(--text-strong)' }}>
                        {agentDisplayName(tables.agents, agent.id)}
                      </span>
                    </div>
                  </td>
                  <td className="mono" style={{ color: 'var(--text-muted)' }}>
                    {agent.tools.length ? agent.tools.join(', ') : '—'}
                  </td>
                  <td style={{ fontSize: 13, color: limit ? 'var(--text-strong)' : 'var(--text-faint)' }}>
                    {limit ?? '—'}
                  </td>
                  <td>{primary?.source ? <Badge tone={badge.tone}>{badge.label}</Badge> : <span className="faint">—</span>}</td>
                  <td>
                    {agent.direct_assignable ? (
                      <span style={{ color: 'var(--ok)', fontSize: 13 }}>✓ Yes</span>
                    ) : (
                      <span
                        style={{ color: 'var(--text-muted)', fontSize: 13 }}
                        title="Enforced by the kernel at the /dispatch boundary"
                      >
                        🔒 {agent.entry_only_via.length ? `Via ${agent.entry_only_via.join(' or ')}` : 'Gated'}
                      </span>
                    )}
                  </td>
                  <td style={{ fontSize: 13, color: 'var(--text-body)' }}>{scalingLabel(kernelEntry)}</td>
                </tr>
              );
            })}
            {manifest.agents.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <Empty>This manifest has no agents.</Empty>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card row row--between" style={{ padding: '22px 26px', gap: 32 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-strong)' }}>
            Source: static analysis, not a config file
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            Tool schemas, conditional edges, and interrupt() calls, confirmed by you on the Review screen before
            going live.
            {kernelManifestId && kernelManifestId !== manifest.manifest_id && (
              <>
                {' '}
                <strong>The kernel is currently governing {kernelManifestId}</strong>, not this manifest — source
                badges and scaling below come from that one.
              </>
            )}
          </div>
        </div>
        <div className="row" style={{ gap: 12, flexShrink: 0 }}>
          <span className="mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {manifestRow ? `manifest v${manifestRow.version} · ` : ''}
            {manifest.manifest_id.slice(0, 12)}
            {manifestRow?.created_at ? ` · ${relativeTime(manifestRow.created_at)}` : ''}
          </span>
          <Badge tone={manifest.status === 'confirmed' ? 'accent' : 'warn'}>
            {manifest.status === 'confirmed' ? 'Confirmed' : 'Draft'}
          </Badge>
        </div>
      </div>
    </main>
  );
}

function scalingLabel(entry: KernelManifestEntry | undefined): string {
  if (!entry) return '—';
  const { min_replicas: min, max_replicas: max } = entry.scaling;
  const range = min === max ? `${min}` : `${min} – ${max}`;
  return max > min ? `${range} · auto` : range;
}
