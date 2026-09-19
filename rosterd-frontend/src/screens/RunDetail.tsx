/**
 * Screen 5 — "Task Run and Violation" (Design.html frame 5). Reached from Ask
 * and Roster after a dispatch, or from any event row carrying a run_id.
 *
 * Two sources, deliberately: `GET /runs/{run_id}` for the run body (output,
 * violation, trace) and the live `events` table for the coordinator's view of
 * the same run. The events row is what carries a trace_id in the federated
 * case, so both are checked before deciding there is no trace to link to.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AgentCard } from '../components/AgentCard';
import { Banner, Label, TraceId, TraceLink } from '../components/ui';
import { getRun, killRun } from '../lib/api/kernel';
import { describeError } from '../lib/api/http';
import { config, isDemo } from '../lib/config';
import { clockTime, poolStatusLabel, poolTone, relativeTime, titleize } from '../lib/format';
import { useLive } from '../lib/live/LiveProvider';
import { demoRun } from '../lib/live/demo';
import { orderPools, poolsForSite } from '../lib/selectors';
import { useManifest } from '../lib/useManifest';
import { useSession } from '../lib/session';
import type { RunResponse } from '../lib/types';

/**
 * A demo agent can mark a prompt-injection line with this prefix; the frame
 * calls that block out in red. Without the prefix the line is just part of
 * the conversation — nothing is inferred from its wording.
 */
const INJECTION_PREFIX = /^\s*(INJECTED|INJECTION)\s*:\s*/i;

export function RunDetail() {
  const { runId = '' } = useParams();
  const session = useSession();
  const { tables } = useLive();

  const [run, setRun] = useState<RunResponse | null>(isDemo ? demoRun : null);
  const [error, setError] = useState<string | null>(null);
  const [killing, setKilling] = useState(false);

  const { manifest } = useManifest(session.activeManifestId);
  const pools = useMemo(
    () => orderPools(poolsForSite(tables.agents, config.siteId), manifest?.agents.map((agent) => agent.id) ?? []),
    [tables.agents, manifest],
  );
  const event = useMemo(
    () => [...tables.events].reverse().find((row) => row.run_id === runId) ?? null,
    [tables.events, runId],
  );

  useEffect(() => {
    if (isDemo || !runId) return;
    const controller = new AbortController();

    const poll = () => {
      getRun(runId, controller.signal)
        .then((response) => {
          setRun(response);
          setError(null);
        })
        .catch((cause) => {
          if (!controller.signal.aborted) setError(describeError(cause));
        });
    };

    poll();
    // The run's own status is REST-only; only its coordinator event lands in
    // SpacetimeDB, so this one screen polls while the run is still open.
    const timer = window.setInterval(poll, config.pollIntervalMs);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [runId]);

  const kill = async () => {
    setKilling(true);
    try {
      await killRun(runId);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setKilling(false);
    }
  };

  const violation = run?.violation ?? event?.violation ?? null;
  const traceId = run?.trace_id ?? event?.trace_id ?? null;
  const taskId = session.runLinks[runId];
  const task = taskId
    ? (tables.tasks.find((item) => item.task_id === taskId) ??
      session.localTasks.find((item) => item.task_id === taskId))
    : undefined;
  const lines = (run?.output ?? '').split('\n').filter((line) => line.trim());

  if (!runId) {
    return (
      <main className="page">
        <Banner tone="info">No run selected.</Banner>
      </main>
    );
  }

  return (
    <main className="page" style={{ flexDirection: 'row', gap: 32, alignItems: 'flex-start' }}>
      <section style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div>
          <h2 style={{ margin: '0 0 4px', fontSize: 21, fontWeight: 700, color: 'var(--text-strong)' }}>
            {task?.title ?? `Run ${runId}`}
          </h2>
          <div className="mono" style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16 }}>
            {run ? `${run.agent_id} · started ${clockTime(run.started_at)}` : 'loading…'}
          </div>

          {error && <Banner tone="danger">{error}</Banner>}

          <div className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Label>Conversation</Label>
            {lines.length === 0 && (
              <div className="muted">
                {run?.status === 'working' ? 'Agent is still working…' : 'This run produced no output.'}
              </div>
            )}
            {lines.map((line, index) =>
              INJECTION_PREFIX.test(line) ? (
                <div
                  key={index}
                  style={{
                    borderRadius: 'var(--r-md)',
                    background: 'var(--danger-soft)',
                    padding: '14px 16px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 6,
                  }}
                >
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: 'var(--danger)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    Injected instruction detected
                  </div>
                  <div style={{ fontSize: 14, color: 'var(--text)', lineHeight: 1.5 }}>
                    {line.replace(INJECTION_PREFIX, '')}
                  </div>
                </div>
              ) : (
                <div key={index} style={{ fontSize: 14, color: 'var(--text-body)', lineHeight: 1.5 }}>
                  {line}
                </div>
              ),
            )}
          </div>
        </div>

        {violation ? (
          <div
            style={{
              border: '1px solid var(--danger-border)',
              borderRadius: 'var(--r-lg)',
              background: 'var(--danger-bg)',
              padding: 24,
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
            }}
          >
            <div className="row row--between">
              <div className="row" style={{ gap: 10 }}>
                <span aria-hidden="true" style={{ color: 'var(--danger)', fontSize: 18 }}>
                  ✗
                </span>
                <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--danger)' }}>
                  Killed — constraint violated
                </span>
              </div>
              <TraceLink traceId={traceId} />
            </div>
            <div
              className="mono"
              style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7, paddingLeft: 28 }}
            >
              {describeViolation(violation)}
            </div>
            <div className="row" style={{ gap: 8, paddingLeft: 28, fontSize: 13, color: 'var(--text-muted)' }}>
              <span>
                Instance terminated · task marked failed · {clockTime(run?.ended_at ?? event?.timestamp ?? null)}
              </span>
              <TraceId traceId={traceId} />
            </div>
          </div>
        ) : (
          <div className="card row row--between">
            <div className="row" style={{ gap: 10 }}>
              <span aria-hidden="true" style={{ color: statusColor(run?.status), fontSize: 18 }}>
                {run?.status === 'done' ? '✓' : run?.status === 'killed' ? '✗' : '●'}
              </span>
              <span style={{ fontSize: 16, fontWeight: 700, color: statusColor(run?.status) }}>
                {run ? labelFor(run.status) : 'Loading run…'}
              </span>
              {run?.ended_at && <span className="muted">finished {relativeTime(run.ended_at)}</span>}
            </div>
            <div className="row" style={{ gap: 12 }}>
              <TraceLink traceId={traceId} />
              {run?.status === 'working' && (
                <button type="button" className="btn btn--ghost" onClick={kill} disabled={killing || isDemo}>
                  {killing ? 'Killing…' : 'Kill run'}
                </button>
              )}
            </div>
          </div>
        )}

        <Link to="/roster" className="linkish" style={{ alignSelf: 'flex-start' }}>
          ← Back to roster
        </Link>
      </section>

      <aside style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <h3 className="label" style={{ fontSize: 13 }}>
          Team
        </h3>
        {pools.map((pool) => {
          const isRunAgent = pool.agent_id === run?.agent_id;
          const tone = isRunAgent && run?.status === 'killed' ? 'danger' : poolTone(pool.status, pool.replicas);
          return (
            <AgentCard
              key={pool.agent_id}
              name={pool.name}
              tone={tone}
              replicas={pool.replicas}
              statusLabel={
                isRunAgent && run?.status === 'killed' ? 'Killed' : poolStatusLabel(pool.status, pool.replicas)
              }
              detail={
                isRunAgent
                  ? run?.status === 'killed'
                    ? 'Terminated by kernel, awaiting reset'
                    : `Running ${titleize(runId)}`
                  : `updated ${relativeTime(pool.updated_at)}`
              }
            />
          );
        })}
        {pools.length === 0 && (
          <div className="card">
            <div className="empty" style={{ padding: 16 }}>
              No live instances for <code>{config.siteId}</code>.
            </div>
          </div>
        )}
      </aside>
    </main>
  );
}

/**
 * The kernel builds `rule` as "<field> <op> <value>" and `expected` as
 * "<op> <value>" (rosterd-kernel/constraints.py), so trimming `expected` off
 * the end of `rule` recovers the bare field — and falls back to the whole
 * rule for any violation not shaped that way.
 */
function describeViolation(violation: { rule: string; expected: string; actual: string }): string {
  const field = violation.rule.endsWith(violation.expected)
    ? violation.rule.slice(0, -violation.expected.length).trim()
    : violation.rule;
  return `${field}: ${violation.actual} exceeds inferred limit ${violation.expected}`;
}

function statusColor(status: string | undefined): string {
  if (status === 'killed') return 'var(--danger)';
  if (status === 'done') return 'var(--ok)';
  return 'var(--accent)';
}

function labelFor(status: string): string {
  if (status === 'done') return 'Completed within contract';
  if (status === 'killed') return 'Killed';
  return 'Working';
}
