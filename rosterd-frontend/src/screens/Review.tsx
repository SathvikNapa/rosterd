/**
 * Screen 2 — "Review what we inferred" (Design.html frame 2).
 *
 * The trust boundary: discovery infers, a human confirms, and only then does
 * anything live depend on it. Confirming returns a NEW manifest id
 * (ingestion ADR-002), so the session follows that id from here on.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AgentBubble } from '../components/AgentBubble';
import { GraphPreview } from '../components/GraphPreview';
import { Badge, Banner, Button, Empty } from '../components/ui';
import { StaggerBody, StaggerTr } from '../components/motion';
import { confirmManifest } from '../lib/api/ingestion';
import { describeError } from '../lib/api/http';
import { confidenceTone, sourceBadge } from '../lib/format';
import { allRules, applyRuleEdit, formatValue } from '../lib/rules';
import { useLive } from '../lib/live/LiveProvider';
import { agentDisplayName } from '../lib/selectors';
import { useSession } from '../lib/session';
import { useManifest } from '../lib/useManifest';
import type { AgentManifestEntry } from '../lib/types';

export function Review() {
  const navigate = useNavigate();
  const session = useSession();
  const { tables } = useLive();
  const manifestId = session.draftManifestId ?? session.activeManifestId;
  const { manifest, kernelEntries, suggestedTasks, loading, error } = useManifest(manifestId);

  /** Local edits, sent as the `agents` body of the confirm call. */
  const [agents, setAgents] = useState<AgentManifestEntry[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftValue, setDraftValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (manifest) setAgents(manifest.agents);
  }, [manifest]);

  const rules = useMemo(() => allRules(agents, kernelEntries), [agents, kernelEntries]);
  const unreviewed = rules.filter((rule) => rule.source === null).length;
  const confirmed = manifest?.status === 'confirmed';
  // /ask/parse only proposes direct_assignable agents, so a manifest with none
  // is a dead end one screen later. Say so here, before it is confirmed.
  const noneAssignable = agents.length > 0 && agents.every((agent) => !agent.direct_assignable);

  const startEdit = (key: string, value: unknown) => {
    setEditing(key);
    setDraftValue(formatValue(value));
  };

  const commitEdit = (agentId: string, constraintKey: string) => {
    setAgents((previous) => applyRuleEdit(previous, agentId, constraintKey, draftValue));
    setDirty(true);
    setEditing(null);
  };

  const confirm = async () => {
    if (!manifestId) return;
    setBusy(true);
    setConfirmError(null);
    try {
      // Send the edited rows. An unedited review can send them unchanged —
      // ingestion treats an empty list as "confirm exactly what was discovered".
      const response = await confirmManifest(manifestId, dirty ? agents : []);
      session.setConfirmedManifestId(response.manifest_id);
      session.setManifest(null);
      navigate('/ask');
    } catch (cause) {
      setConfirmError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  if (!manifestId) {
    return (
      <main className="page">
        <div className="page__head">
          <h2>Review what we inferred</h2>
          <p>Nothing ingested yet.</p>
        </div>
        <Banner tone="info">
          Start on <a href="/ingest">Ingest</a> — analyze a repository and its draft manifest lands here.
        </Banner>
      </main>
    );
  }

  return (
    <main className="page">
      <div className="page__head">
        <h2>Review what we inferred</h2>
        <p>
          Nothing here was typed by hand. Confirm it, or edit a rule before it goes live and starts governing
          real dispatches.
        </p>
      </div>

      {error && <Banner tone="danger">{error}</Banner>}
      {confirmError && <Banner tone="danger">{confirmError}</Banner>}
      {noneAssignable && (
        <Banner tone="warn">
          No agent in this manifest is directly assignable, so Ask will have nothing to propose against it.
          <code>direct_assignable</code> comes from <code>constraints.yaml</code> — set it there on{' '}
          <a href="/ingest">Ingest</a> and re-analyze.
        </Banner>
      )}

      {/*
       * The agentic repo schema -- what discovery actually found, laid out
       * before the (editable) inferred-rules table below it. This is the
       * trust-boundary moment the screen's own header comment describes:
       * seeing the real shape (nodes, wiring, each agent's tools and access)
       * is what "a human confirms" should mean, not just skimming a flat
       * list of constraint key/value pairs with no structure behind them.
       */}
      <div className="card card--flush">
        <div style={{ padding: '20px 24px 0' }} className="label">
          Discovered agent schema
        </div>
        <div style={{ padding: '4px 24px 20px' }}>
          <GraphPreview graph={manifest?.graph ?? null} />
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Node</th>
              <th>Tools</th>
              <th>Access</th>
              <th>Purpose</th>
              <th>Try it</th>
            </tr>
          </thead>
          <StaggerBody>
            {agents.map((agent) => (
              <StaggerTr key={agent.id}>
                <td>
                  <div className="row" style={{ gap: 10 }}>
                    <AgentBubble name={agentDisplayName(tables.agents, agent.id)} tone="neutral" size={28} />
                    <span style={{ fontWeight: 600 }}>{agentDisplayName(tables.agents, agent.id)}</span>
                  </div>
                </td>
                <td className="mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {agent.node}
                </td>
                <td className="mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {agent.tools.length ? agent.tools.join(', ') : '—'}
                </td>
                <td>
                  {agent.direct_assignable ? (
                    <span style={{ color: 'var(--ok)', fontSize: 13 }}>Direct</span>
                  ) : (
                    <span
                      style={{ color: 'var(--text-muted)', fontSize: 13 }}
                      title="Enforced by the kernel at the /dispatch boundary"
                    >
                      Gated{agent.entry_only_via.length ? ` · via ${agent.entry_only_via.join(' or ')}` : ''}
                    </span>
                  )}
                </td>
                <td style={{ fontSize: 13, color: 'var(--text-body)' }}>{agent.purpose || '—'}</td>
                <td style={{ fontSize: 12, color: 'var(--text-muted)', maxWidth: 240 }}>
                  {(suggestedTasks[agent.id] ?? []).length ? (
                    <ul style={{ margin: 0, paddingLeft: 16 }}>
                      {(suggestedTasks[agent.id] ?? []).map((task) => (
                        <li key={task} style={{ marginBottom: 2 }}>
                          {task}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    '—'
                  )}
                </td>
              </StaggerTr>
            ))}
            {agents.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <Empty>{loading ? 'Loading manifest…' : 'No agents discovered for this manifest.'}</Empty>
                </td>
              </tr>
            )}
          </StaggerBody>
        </table>
      </div>

      <div className="card card--flush">
        <table className="table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Inferred rule</th>
              <th>Source</th>
              <th>Confidence</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <StaggerBody>
            {rules.map((rule) => {
              const badge = sourceBadge(rule.source);
              const isEditing = editing === rule.key;
              return (
                <StaggerTr key={rule.key}>
                  <td style={{ fontWeight: 600 }} title={rule.agentId}>
                    {agentDisplayName(tables.agents, rule.agentId)}
                  </td>
                  <td className="mono">
                    {isEditing ? (
                      <input
                        className="input"
                        style={{ height: 32, fontSize: 12 }}
                        value={draftValue}
                        autoFocus
                        onChange={(event) => setDraftValue(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') commitEdit(rule.agentId, rule.constraintKey);
                          if (event.key === 'Escape') setEditing(null);
                        }}
                        onBlur={() => commitEdit(rule.agentId, rule.constraintKey)}
                        aria-label={`Value for ${rule.constraintKey}`}
                      />
                    ) : (
                      rule.label
                    )}
                  </td>
                  <td>
                    {rule.source ? (
                      <Badge tone={badge.tone}>{badge.label}</Badge>
                    ) : (
                      <span className="muted faint" title="No per-constraint provenance in ingestion's response">
                        —
                      </span>
                    )}
                  </td>
                  <td
                    style={{
                      fontSize: 13,
                      color: rule.confidence ? `var(--${toneVar(rule.confidence)})` : 'var(--text-faint)',
                    }}
                  >
                    {rule.confidence ? capitalize(rule.confidence) : '—'}
                  </td>
                  <td>
                    {rule.editable && !confirmed ? (
                      <button type="button" className="linkish" onClick={() => startEdit(rule.key, rule.value)}>
                        Edit
                      </button>
                    ) : (
                      <span className="muted faint" style={{ fontSize: 12 }}>
                        {confirmed ? 'Locked' : 'Structural'}
                      </span>
                    )}
                  </td>
                </StaggerTr>
              );
            })}
            {rules.length === 0 && (
              <tr>
                <td colSpan={5}>
                  <Empty>{loading ? 'Loading manifest…' : 'No constraints inferred for this manifest.'}</Empty>
                </td>
              </tr>
            )}
          </StaggerBody>
        </table>
      </div>

      <div className="card row row--between" style={{ padding: '20px 24px' }}>
        <div className="muted">
          {rules.length} rule{rules.length === 1 ? '' : 's'} inferred, {unreviewed} without recorded provenance.
          Confirming writes a new confirmed manifest; kernels pick it up on their next poll.
        </div>
        <div className="row" style={{ gap: 12 }}>
          <Button className="btn--ghost" onClick={() => navigate('/ingest')}>
            Back
          </Button>
          <Button
            onClick={confirm}
            disabled={busy || confirmed || !manifest}
            title={confirmed ? 'This manifest is already confirmed.' : undefined}
          >
            {busy ? 'Confirming…' : confirmed ? 'Already live' : 'Confirm and go live'}
          </Button>
        </div>
      </div>
    </main>
  );
}

function toneVar(confidence: string): string {
  return confidenceTone(confidence) === 'ok' ? 'ok' : confidenceTone(confidence) === 'warn' ? 'warn' : 'danger';
}

function capitalize(value: string): string {
  return value[0].toUpperCase() + value.slice(1);
}
