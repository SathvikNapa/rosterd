/**
 * Cross-screen state that is NOT in any table yet.
 *
 * Two gaps from rosterd-param-frontend.md force this to live in the client:
 *  - `manifests` has no writer, so Review/Contracts drive off ingestion's
 *    `GET /manifest/{id}` and the id has to be carried between screens.
 *    Confirming returns a NEW id (ingestion ADR-002), so both are kept.
 *  - `tasks` has no writer, so a task scheduled in this UI is held here and
 *    merged with any live `tasks` rows that do show up.
 *
 * Persisted to localStorage so a reload mid-demo doesn't lose the manifest.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { isDemo } from './config';
import { DEMO_DRAFT_MANIFEST_ID, DEMO_MANIFEST_ID, demoManifest, demoRunLinks } from './live/demo';
import type { ManifestResponse, TaskRow } from './types';

const STORAGE_KEY = 'rosterd.session.v1';

interface PersistedSession {
  repoUrl: string;
  draftManifestId: string | null;
  confirmedManifestId: string | null;
  localTasks: TaskRow[];
  lastRunId: string | null;
  /**
   * run_id -> task_id. Neither `runs` (kernel, REST only) nor `tasks`
   * (SpacetimeDB) carries the other's id, so the dispatch that creates both
   * is the only place the link can be recorded.
   */
  runLinks: Record<string, string>;
}

const EMPTY: PersistedSession = {
  repoUrl: '',
  draftManifestId: null,
  confirmedManifestId: null,
  localTasks: [],
  lastRunId: null,
  runLinks: {},
};

const DEMO: PersistedSession = {
  repoUrl: 'https://github.com/acme/orders-agents',
  draftManifestId: DEMO_DRAFT_MANIFEST_ID,
  confirmedManifestId: DEMO_MANIFEST_ID,
  localTasks: [],
  lastRunId: 'run-4482',
  runLinks: demoRunLinks,
};

interface SessionValue extends PersistedSession {
  /** The manifest the live screens should read: confirmed if there is one. */
  activeManifestId: string | null;
  /** Cached manifest body, so Review/Ask/Contracts don't each re-fetch. */
  manifest: ManifestResponse | null;
  setManifest: (manifest: ManifestResponse | null) => void;
  setRepoUrl: (url: string) => void;
  setDraftManifestId: (id: string | null) => void;
  setConfirmedManifestId: (id: string | null) => void;
  addLocalTask: (task: TaskRow) => void;
  removeLocalTask: (taskId: string) => void;
  setLastRunId: (runId: string | null) => void;
  /** Records which task a dispatch produced, so a run can name its task. */
  linkRun: (runId: string, taskId: string) => void;
  reset: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

function load(): PersistedSession {
  if (isDemo) return DEMO;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    return { ...EMPTY, ...(JSON.parse(raw) as Partial<PersistedSession>) };
  } catch {
    return EMPTY;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PersistedSession>(load);
  const [manifest, setManifest] = useState<ManifestResponse | null>(isDemo ? demoManifest : null);

  useEffect(() => {
    if (isDemo) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      /* private mode / quota — the session just won't survive a reload */
    }
  }, [state]);

  const patch = useCallback((changes: Partial<PersistedSession>) => {
    setState((previous) => ({ ...previous, ...changes }));
  }, []);

  const value = useMemo<SessionValue>(
    () => ({
      ...state,
      activeManifestId: state.confirmedManifestId ?? state.draftManifestId,
      manifest,
      setManifest,
      setRepoUrl: (repoUrl) => patch({ repoUrl }),
      setDraftManifestId: (draftManifestId) => patch({ draftManifestId }),
      setConfirmedManifestId: (confirmedManifestId) => patch({ confirmedManifestId }),
      addLocalTask: (task) =>
        setState((previous) => ({
          ...previous,
          localTasks: [...previous.localTasks.filter((item) => item.task_id !== task.task_id), task],
        })),
      removeLocalTask: (taskId) =>
        setState((previous) => ({
          ...previous,
          localTasks: previous.localTasks.filter((item) => item.task_id !== taskId),
        })),
      setLastRunId: (lastRunId) => patch({ lastRunId }),
      linkRun: (runId, taskId) =>
        setState((previous) => ({ ...previous, runLinks: { ...previous.runLinks, [runId]: taskId } })),
      reset: () => {
        setState(EMPTY);
        setManifest(null);
      },
    }),
    [state, manifest, patch],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession must be used inside <SessionProvider>');
  return value;
}
