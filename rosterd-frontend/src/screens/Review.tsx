/**
 * Screen 2 — "Review what we inferred" (Design.html frame 2).
 *
 * The trust boundary: discovery infers, a human confirms, and only then does
 * anything live depend on it. Confirming returns a NEW manifest id
 * (ingestion ADR-002), so the session follows that id from here on.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Banner, Empty } from '../components/ui';
import { confirmManifest } from '../lib/api/ingestion';
import { describeError } from '../lib/api/http';
import { isDemo } from '../lib/config';
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
  const { manifest, kernelEntries, loading, error } = useManifest(manifestId);

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
          <tbody>
            {rules.map((rule) => {
              const badge = sourceBadge(rule.source);
              const isEditing = editing === rule.key;
              return (
                <tr key={rule.key}>
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
                </tr>
              );
            })}
            {rules.length === 0 && (
              <tr>
                <td colSpan={5}>
                  <Empty>{loading ? 'Loading manifest…' : 'No constraints inferred for this manifest.'}</Empty>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card row row--between" style={{ padding: '20px 24px' }}>
        <div className="muted">
          {rules.length} rule{rules.length === 1 ? '' : 's'} inferred, {unreviewed} without recorded provenance.
          Confirming writes a new confirmed manifest; kernels pick it up on their next poll.
        </div>
        <div className="row" style={{ gap: 12 }}>
          <button type="button" className="btn btn--ghost" onClick={() => navigate('/ingest')}>
            Back
          </button>
          <button
            type="button"
            className="btn"
            onClick={confirm}
            disabled={busy || isDemo || confirmed || !manifest}
            title={
              isDemo
                ? 'Demo mode: no ingestion service is being called.'
                : confirmed
                  ? 'This manifest is already confirmed.'
                  : undefined
            }
          >
            {busy ? 'Confirming…' : confirmed ? 'Already live' : 'Confirm and go live'}
          </button>
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
