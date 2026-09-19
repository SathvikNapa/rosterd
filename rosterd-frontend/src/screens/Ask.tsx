/**
 * Screen 3 — "What do you need done?" (Design.html frame 3).
 *
 * POST /ask/parse turns plain language into a PROPOSAL, never a dispatch.
 * "Do it" is the dispatch, and it goes to the kernel — which re-checks the
 * contract and can still reject. Only confirmed manifests can be asked
 * against (409 manifest_not_confirmed otherwise).
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AgentBubble } from '../components/AgentBubble';
import { Badge, Banner, Criterion, Label } from '../components/ui';
import { parseAsk } from '../lib/api/ingestion';
import { dispatch } from '../lib/api/kernel';
import { ServiceError, describeError } from '../lib/api/http';
import { config, isDemo } from '../lib/config';
import { demoAsk } from '../lib/live/demo';
import { priorityTone } from '../lib/format';
import { useLive } from '../lib/live/LiveProvider';
import { agentDisplayName } from '../lib/selectors';
import { useSession } from '../lib/session';
import type { AskResponse, TaskRow, TaskSpec } from '../lib/types';

const PLACEHOLDER = "Refund order #4482, it's a duplicate charge, escalate if it's over policy";

export function Ask() {
  const navigate = useNavigate();
  const session = useSession();
  const { tables, refresh } = useLive();

  const [text, setText] = useState('');
  const [proposal, setProposal] = useState<AskResponse | null>(isDemo ? demoAsk : null);
  const [title, setTitle] = useState(isDemo ? demoAsk.task.title : '');
  const [criteria, setCriteria] = useState<string[]>(isDemo ? demoAsk.task.expectation_criteria : []);
  const [editing, setEditing] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);

  const manifestId = session.confirmedManifestId;

  const parse = async () => {
    if (!manifestId) return;
    setParsing(true);
    setError(null);
    setRejection(null);
    try {
      const response = await parseAsk(manifestId, text.trim() || PLACEHOLDER);
      setProposal(response);
      setTitle(response.task.title);
      setCriteria(response.task.expectation_criteria);
      setEditing(false);
    } catch (cause) {
      setProposal(null);
      setError(
        cause instanceof ServiceError && cause.code === 'manifest_not_confirmed'
          ? 'This manifest is still a draft — confirm it on Review before asking against it.'
          : cause instanceof ServiceError && cause.code === 'no_assignable_agent'
            ? 'No agent in this manifest is directly assignable, so nothing can be proposed. ' +
              'direct_assignable comes from constraints.yaml — set it on Ingest and re-analyze.'
            : describeError(cause),
      );
    } finally {
      setParsing(false);
    }
  };

  const doIt = async () => {
    if (!proposal) return;
    setDispatching(true);
    setError(null);
    setRejection(null);

    const task: TaskSpec = {
      // kernel.TaskSpec requires an id; ingestion's ParsedTask has none.
      id: `task-${Date.now().toString(36)}`,
      title,
      description: proposal.task.description,
      priority: proposal.task.priority,
      source: text.trim() || null,
      expectation_criteria: criteria,
    };

    try {
      const response = await dispatch({
        agent_id: proposal.agent_id,
        task,
        assignees: [proposal.agent_id],
      });

      if (response.status === 'rejected') {
        setRejection(response.reason ?? 'The kernel rejected this dispatch.');
        return;
      }

      // `tasks` has no writer (param-frontend.md gap 2), so the commitment is
      // kept client-side and merged into Roster alongside any live rows.
      const row: TaskRow = {
        task_id: task.id,
        site_id: config.siteId,
        agent_id: proposal.agent_id,
        title: task.title,
        status: 'working',
        assignees: [proposal.agent_id],
        criteria,
        priority: task.priority,
        source: task.source ?? null,
        created_at: new Date().toISOString(),
      };
      session.addLocalTask(row);
      session.setLastRunId(response.run_id);
      session.linkRun(response.run_id, task.id);
      refresh();
      navigate(`/runs/${encodeURIComponent(response.run_id)}`);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setDispatching(false);
    }
  };

  return (
    <main className="page" style={{ alignItems: 'center', padding: '96px 64px' }}>
      <div style={{ width: '100%', maxWidth: 720, display: 'flex', flexDirection: 'column', gap: 28 }}>
        <div style={{ textAlign: 'center' }}>
          <h1 style={{ margin: '0 0 10px', fontSize: 30, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>
            What do you need done?
          </h1>
          <p style={{ margin: 0, fontSize: 14, color: 'var(--text-muted)' }}>
            Say it plainly. We turn it into a scheduled task, assigned and bounded by contract.
          </p>
        </div>

        <div
          className="card card--xl row"
          style={{ padding: '8px 8px 8px 20px', gap: 12 }}
        >
          <input
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && manifestId) void parse();
            }}
            placeholder={PLACEHOLDER}
            aria-label="What do you need done?"
            style={{
              flex: 1,
              fontSize: 15,
              color: 'var(--text)',
              border: 'none',
              outline: 'none',
              background: 'transparent',
              fontFamily: 'var(--font-sans)',
            }}
          />
          <button
            type="button"
            className="btn"
            style={{ height: 44, padding: '0 22px', borderRadius: 11, flexShrink: 0 }}
            onClick={parse}
            disabled={parsing || !manifestId || isDemo}
            title={
              !manifestId
                ? 'Confirm a manifest on Review first.'
                : isDemo
                  ? 'Demo mode: no ingestion service is being called.'
                  : undefined
            }
          >
            {parsing ? 'Parsing…' : 'Parse →'}
          </button>
        </div>

        {!manifestId && (
          <Banner tone="info">
            No confirmed manifest yet. Only a confirmed manifest can be asked against — run{' '}
            <a href="/ingest">Ingest</a>, then confirm on <a href="/review">Review</a>.
          </Banner>
        )}
        {error && <Banner tone="danger">{error}</Banner>}
        {rejection && (
          <Banner tone="danger">
            Dispatch rejected by the kernel: {rejection}
          </Banner>
        )}

        {proposal && (
          <div className="card card--xl" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div className="row row--between">
              <Label>Proposed action</Label>
              <Badge tone={proposal.confidence === 'high' ? 'ok' : proposal.confidence === 'medium' ? 'warn' : 'danger'}>
                {capitalize(proposal.confidence)} confidence
              </Badge>
            </div>

            <div className="row" style={{ gap: 14 }}>
              <AgentBubble name={agentDisplayName(tables.agents, proposal.agent_id)} tone="accent" size={44} />
              <div>
                <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-strong)' }}>
                  {agentDisplayName(tables.agents, proposal.agent_id)}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{proposal.task.description}</div>
              </div>
              <span className="spacer" />
              <Badge tone={priorityTone(proposal.task.priority)}>{capitalize(proposal.task.priority)}</Badge>
            </div>

            <div className="field">
              <Label>Task</Label>
              {editing ? (
                <input
                  className="input"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  aria-label="Task title"
                />
              ) : (
                <div style={{ fontSize: 14, color: 'var(--text)' }}>{title}</div>
              )}
            </div>

            <div className="field">
              <Label>Extracted criteria</Label>
              {editing ? (
                <textarea
                  className="textarea"
                  style={{ fontFamily: 'var(--font-sans)', fontSize: 13, minHeight: 90 }}
                  value={criteria.join('\n')}
                  onChange={(event) => setCriteria(event.target.value.split('\n').filter((line) => line.trim()))}
                  aria-label="Expectation criteria, one per line"
                />
              ) : criteria.length ? (
                criteria.map((criterion) => <Criterion key={criterion}>{criterion}</Criterion>)
              ) : (
                <span className="muted">No criteria extracted.</span>
              )}
            </div>

            <div className="row row--end" style={{ gap: 12, marginTop: 4 }}>
              <button type="button" className="btn btn--ghost" onClick={() => setEditing((open) => !open)}>
                {editing ? 'Done editing' : 'Edit'}
              </button>
              <button type="button" className="btn" onClick={doIt} disabled={dispatching || isDemo}>
                {dispatching ? 'Dispatching…' : 'Do it'}
              </button>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

function capitalize(value: string): string {
  return value[0].toUpperCase() + value.slice(1);
}
