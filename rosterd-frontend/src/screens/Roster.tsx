/**
 * Screen 4 — "Schedule a task" (Design.html frame 4).
 *
 * The Team column is live: it groups the per-instance `agents` rows into one
 * pool per agent, so a pool of 3 reads as one bubble with an animated ×3
 * badge rather than three cards.
 *
 * The calendar is half-live by necessity. `tasks` has no writer
 * (rosterd-param-frontend.md gap 2), so scheduling here dispatches to the
 * kernel and keeps the commitment in session state, merged with any live
 * `tasks` rows that do appear. Live rows win on id collision.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AgentBubble } from '../components/AgentBubble';
import { AgentCard } from '../components/AgentCard';
import { Badge, Banner, Criterion, Label } from '../components/ui';
import { dispatch } from '../lib/api/kernel';
import { describeError } from '../lib/api/http';
import { config, isDemo } from '../lib/config';
import { initial, poolStatusLabel, poolTone, priorityTone, relativeTime, shortTime, titleize } from '../lib/format';
import { useLive } from '../lib/live/LiveProvider';
import { agentDisplayName, orderPools, poolsForSite } from '../lib/selectors';
import { useSession } from '../lib/session';
import { useManifest } from '../lib/useManifest';
import type { TaskRow } from '../lib/types';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];
const HOURS = [9, 10, 11, 12, 13];
const SLOT_HEIGHT = 56;

export function Roster() {
  const navigate = useNavigate();
  const session = useSession();
  const { tables, refresh } = useLive();
  const { manifest } = useManifest(session.activeManifestId);

  const pools = useMemo(
    () => orderPools(poolsForSite(tables.agents, config.siteId), manifest?.agents.map((agent) => agent.id) ?? []),
    [tables.agents, manifest],
  );
  const tasks = useMemo(() => mergeTasks(tables.tasks, session.localTasks), [tables.tasks, session.localTasks]);

  const [day, setDay] = useState(() => DAYS[Math.min(Math.max(new Date().getDay() - 1, 0), 4)]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<TaskRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dayTasks = tasks.filter((task) => dayOf(task.created_at) === day);
  const selected = draft ?? dayTasks.find((task) => task.task_id === selectedId) ?? dayTasks[0] ?? null;

  const agentOptions = manifest?.agents ?? [];

  const newTask = () => {
    const now = new Date();
    now.setMinutes(0, 0, 0);
    setDraft({
      task_id: `task-${Date.now().toString(36)}`,
      site_id: config.siteId,
      agent_id: agentOptions.find((agent) => agent.direct_assignable)?.id ?? '',
      title: '',
      status: 'draft',
      assignees: [],
      criteria: [],
      priority: 'medium',
      source: null,
      created_at: now.toISOString(),
    });
    setSelectedId(null);
    setError(null);
  };

  const patchDraft = (changes: Partial<TaskRow>) => {
    setDraft((previous) => (previous ? { ...previous, ...changes } : previous));
  };

  const schedule = async () => {
    if (!draft) return;
    const agentId = draft.assignees[0] ?? draft.agent_id;
    if (!agentId || !draft.title.trim()) {
      setError('A task needs a title and at least one assignee.');
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const response = await dispatch({
        agent_id: agentId,
        task: {
          id: draft.task_id,
          title: draft.title.trim(),
          description: draft.title.trim(),
          priority: (draft.priority as TaskRow['priority']) === 'high' ? 'high' : draft.priority === 'low' ? 'low' : 'medium',
          source: draft.source,
          expectation_criteria: draft.criteria,
        },
        assignees: draft.assignees.length ? draft.assignees : [agentId],
      });

      if (response.status === 'rejected') {
        setError(`Kernel rejected this dispatch: ${response.reason ?? 'no reason given'}`);
        return;
      }

      session.addLocalTask({ ...draft, agent_id: agentId, status: 'working' });
      session.setLastRunId(response.run_id);
      session.linkRun(response.run_id, draft.task_id);
      setDraft(null);
      setSelectedId(draft.task_id);
      refresh();
      navigate(`/runs/${encodeURIComponent(response.run_id)}`);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page page--tight" style={{ flexDirection: 'row', gap: 32, alignItems: 'flex-start' }}>
      <section style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div className="row row--between">
          <h2 style={{ margin: 0, fontSize: 21, fontWeight: 700, color: 'var(--text-strong)' }}>Schedule a task</h2>
          <button type="button" className="btn" onClick={newTask}>
            + New task
          </button>
        </div>

        <div className="row" style={{ gap: 8 }}>
          {DAYS.map((label) => {
            const active = label === day;
            return (
              <button
                key={label}
                type="button"
                onClick={() => setDay(label)}
                style={{
                  padding: '6px 16px',
                  borderRadius: 'var(--r-sm)',
                  border: active ? 'none' : '1px solid var(--border)',
                  background: active ? 'var(--accent-soft)' : 'var(--surface)',
                  color: active ? 'var(--accent-dark)' : 'var(--text-muted)',
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  cursor: 'pointer',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>

        <div className="card" style={{ padding: '16px 20px' }}>
          <div style={{ position: 'relative', height: HOURS.length * SLOT_HEIGHT }}>
            {HOURS.map((hour, index) => (
              <div
                key={hour}
                style={{
                  position: 'absolute',
                  top: index * SLOT_HEIGHT,
                  left: 0,
                  width: '100%',
                  height: SLOT_HEIGHT,
                  borderTop: '1px solid var(--border-soft)',
                  borderBottom: index === HOURS.length - 1 ? '1px solid var(--border-soft)' : undefined,
                }}
              >
                <span
                  className="mono"
                  style={{ fontSize: 11, color: 'var(--text-faint)', paddingTop: 2, display: 'inline-block', width: 64 }}
                >
                  {hourLabel(hour)}
                </span>
              </div>
            ))}

            {dayTasks.map((task) => {
              const top = slotTop(task.created_at);
              if (top === null) return null;
              const isSelected = selected?.task_id === task.task_id && !draft;
              const done = task.status === 'done';
              const killed = task.status === 'killed';
              return (
                <button
                  key={task.task_id}
                  type="button"
                  onClick={() => {
                    setDraft(null);
                    setSelectedId(task.task_id);
                  }}
                  style={{
                    position: 'absolute',
                    top,
                    left: 76,
                    right: 20,
                    height: isSelected ? 44 : 36,
                    background: isSelected ? 'var(--surface)' : killed ? 'var(--danger-soft)' : 'var(--accent-soft)',
                    border: isSelected
                      ? '1.5px solid var(--accent)'
                      : killed
                        ? '1px solid var(--danger-border)'
                        : 'none',
                    borderRadius: 'var(--r-sm)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '0 14px',
                    cursor: 'pointer',
                    textAlign: 'left',
                    font: 'inherit',
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: isSelected ? 20 : 18,
                      height: isSelected ? 20 : 18,
                      borderRadius: '50%',
                      background: 'var(--surface)',
                      border: `2px solid ${killed ? 'var(--danger)' : isSelected ? 'var(--accent)' : 'var(--accent-mid)'}`,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 9,
                      fontWeight: 700,
                      color: killed ? 'var(--danger)' : 'var(--accent)',
                      flexShrink: 0,
                    }}
                  >
                    {initial(task.agent_id)}
                  </span>
                  <span
                    style={{
                      fontSize: isSelected ? 13 : 12,
                      fontWeight: isSelected ? 600 : 400,
                      color: 'var(--text-strong)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {task.title}
                  </span>
                  <span style={{ marginLeft: 'auto', fontSize: 11, color: statusColor(task.status), fontWeight: 600 }}>
                    {done ? '✓ Done' : killed ? '✗ Killed' : isSelected ? 'Selected' : capitalize(task.status)}
                  </span>
                </button>
              );
            })}

            {dayTasks.length === 0 && !draft && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'grid',
                  placeItems: 'center',
                  color: 'var(--text-faint)',
                  fontSize: 13,
                  pointerEvents: 'none',
                }}
              >
                Nothing scheduled for {day}.
              </div>
            )}
          </div>
        </div>

        <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 18 }}>
          {selected ? (
            <>
              <div>
                {draft ? (
                  <input
                    className="input"
                    style={{ fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 700 }}
                    value={draft.title}
                    placeholder="Refund order #4482 — duplicate charge"
                    onChange={(event) => patchDraft({ title: event.target.value })}
                    aria-label="Task title"
                  />
                ) : (
                  <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-strong)' }}>{selected.title}</div>
                )}
                <div className="mono" style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                  {dayOf(selected.created_at)} · {shortTime(selected.created_at)} ·{' '}
                  {relativeTime(selected.created_at)}
                </div>
              </div>

              <div className="field">
                <Label>Assignees</Label>
                <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
                  {(draft ? draft.assignees : selected.assignees).map((assignee) => (
                    <span
                      key={assignee}
                      className="row"
                      style={{
                        gap: 8,
                        background: 'var(--surface-muted)',
                        border: '1px solid var(--border)',
                        borderRadius: 'var(--r-pill)',
                        padding: '5px 12px 5px 5px',
                      }}
                    >
                      <AgentBubble name={agentDisplayName(tables.agents, assignee)} tone="accent" size={26} />
                      <span style={{ fontSize: 13 }}>{agentDisplayName(tables.agents, assignee)}</span>
                      {draft && (
                        <button
                          type="button"
                          className="linkish"
                          style={{ textDecoration: 'none', color: 'var(--text-faint)' }}
                          aria-label={`Remove ${assignee}`}
                          onClick={() =>
                            patchDraft({ assignees: draft.assignees.filter((item) => item !== assignee) })
                          }
                        >
                          ✕
                        </button>
                      )}
                    </span>
                  ))}
                  {draft && (
                    <AddAssignee
                      options={agentOptions.map((agent) => ({
                        id: agent.id,
                        assignable: agent.direct_assignable,
                        via: agent.entry_only_via,
                      }))}
                      chosen={draft.assignees}
                      onAdd={(id) => patchDraft({ assignees: [...draft.assignees, id] })}
                    />
                  )}
                  {!draft && selected.assignees.length === 0 && <span className="muted">No assignees recorded.</span>}
                </div>
              </div>

              <div className="field">
                <Label>Expectation criteria</Label>
                {draft ? (
                  <textarea
                    className="textarea"
                    style={{ fontFamily: 'var(--font-sans)', fontSize: 13, minHeight: 80 }}
                    value={draft.criteria.join('\n')}
                    placeholder={'Refund amount ≤ $100, per contract\nRespond within 2 minutes'}
                    onChange={(event) =>
                      patchDraft({ criteria: event.target.value.split('\n').filter((line) => line.trim()) })
                    }
                    aria-label="Expectation criteria, one per line"
                  />
                ) : selected.criteria.length ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {selected.criteria.map((criterion) => (
                      <Criterion key={criterion}>{criterion}</Criterion>
                    ))}
                  </div>
                ) : (
                  <span className="muted">No criteria recorded.</span>
                )}
              </div>

              <div className="row" style={{ gap: 24 }}>
                <div className="row" style={{ gap: 8 }}>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Priority</span>
                  {draft ? (
                    <select
                      value={draft.priority}
                      onChange={(event) => patchDraft({ priority: event.target.value })}
                      style={{ borderRadius: 'var(--r-pill)', border: '1px solid var(--border)', padding: '3px 10px' }}
                      aria-label="Priority"
                    >
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                    </select>
                  ) : (
                    <Badge tone={priorityTone(selected.priority)}>{capitalize(selected.priority)}</Badge>
                  )}
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Source</span>
                  {draft ? (
                    <input
                      className="input"
                      style={{ height: 28, width: 200, fontSize: 12 }}
                      value={draft.source ?? ''}
                      placeholder="order #4482"
                      onChange={(event) => patchDraft({ source: event.target.value || null })}
                      aria-label="Source"
                    />
                  ) : (
                    <span className="mono" style={{ fontSize: 12 }}>
                      {selected.source ?? '—'}
                    </span>
                  )}
                </div>
              </div>

              {error && <Banner tone="danger">{error}</Banner>}

              <div className="row row--end" style={{ gap: 12, marginTop: 'auto' }}>
                {draft ? (
                  <>
                    <button type="button" className="btn btn--ghost" onClick={() => setDraft(null)}>
                      Cancel
                    </button>
                    <button type="button" className="btn" onClick={schedule} disabled={busy || isDemo}>
                      {busy ? 'Dispatching…' : 'Schedule task'}
                    </button>
                  </>
                ) : (
                  <span className="muted">
                    Scheduling dispatches to the kernel at once — there is no deferred queue behind this screen yet.
                  </span>
                )}
              </div>
            </>
          ) : (
            <div className="empty">Select a task, or start a new one.</div>
          )}
        </div>
      </section>

      <aside style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <h3 className="label" style={{ fontSize: 13 }}>
          Team
        </h3>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-faint)', lineHeight: 1.5 }}>
          Add an agent as an assignee. Pod counts come straight from the live <code>agents</code> table.
        </p>

        {pools.map((pool) => (
          <AgentCard
            key={pool.agent_id}
            name={pool.name}
            tone={poolTone(pool.status, pool.replicas)}
            statusLabel={poolStatusLabel(pool.status, pool.replicas)}
            replicas={pool.replicas}
            detail={
              pool.replicas > 1
                ? `${pool.replicas} instances running · ${pool.working} working`
                : (manifest?.agents.find((agent) => agent.id === pool.agent_id)?.purpose ??
                  `updated ${relativeTime(pool.updated_at)}`)
            }
            onClick={draft ? () => addAssignee(draft, pool.agent_id, patchDraft) : undefined}
          />
        ))}

        {pools.length === 0 && (
          <div className="card">
            <div className="empty" style={{ padding: 16 }}>
              No instances in the <code>agents</code> table for <code>{config.siteId}</code> yet. The kernel
              writes a row per instance on every scaler tick.
            </div>
          </div>
        )}
      </aside>
    </main>
  );
}

function AddAssignee({
  options,
  chosen,
  onAdd,
}: {
  options: Array<{ id: string; assignable: boolean; via: string[] }>;
  chosen: string[];
  onAdd: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const available = options.filter((option) => !chosen.includes(option.id));

  if (!open) {
    return (
      <button type="button" className="btn btn--dashed" onClick={() => setOpen(true)}>
        + Add assignee
      </button>
    );
  }

  return (
    <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      {available.map((option) => (
        <button
          key={option.id}
          type="button"
          className="btn btn--dashed"
          disabled={!option.assignable}
          title={
            option.assignable
              ? undefined
              : `Not directly assignable — entry only via ${option.via.join(', ') || 'another agent'}`
          }
          onClick={() => {
            onAdd(option.id);
            setOpen(false);
          }}
        >
          {option.assignable ? titleize(option.id) : `🔒 ${titleize(option.id)}`}
        </button>
      ))}
      {available.length === 0 && <span className="muted">Every agent is already assigned.</span>}
      <button type="button" className="linkish" onClick={() => setOpen(false)}>
        cancel
      </button>
    </div>
  );
}

function addAssignee(draft: TaskRow, agentId: string, patch: (changes: Partial<TaskRow>) => void) {
  if (draft.assignees.includes(agentId)) return;
  patch({ assignees: [...draft.assignees, agentId] });
}

/** Live rows win; local commitments fill the gap until `tasks` has a writer. */
function mergeTasks(live: TaskRow[], local: TaskRow[]): TaskRow[] {
  const byId = new Map<string, TaskRow>();
  for (const task of local) byId.set(task.task_id, task);
  for (const task of live) byId.set(task.task_id, task);
  return [...byId.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
}

function dayOf(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return DAYS[2];
  const index = date.getDay() - 1;
  return DAYS[Math.min(Math.max(index, 0), 4)];
}

/** Pixel offset for a task's hour, or null when it falls outside 9am–2pm. */
function slotTop(iso: string): number | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const hour = date.getHours() + date.getMinutes() / 60;
  if (hour < HOURS[0] || hour >= HOURS[HOURS.length - 1] + 1) return null;
  return (hour - HOURS[0]) * SLOT_HEIGHT + 6;
}

function hourLabel(hour: number): string {
  if (hour === 12) return '12 PM';
  return hour > 12 ? `${hour - 12} PM` : `${hour} AM`;
}

function statusColor(status: string): string {
  if (status === 'done') return 'var(--ok)';
  if (status === 'killed') return 'var(--danger)';
  return 'var(--accent)';
}

function capitalize(value: string): string {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}
