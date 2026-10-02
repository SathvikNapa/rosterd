/**
 * One live view of the six Postgres tables, for every screen.
 *
 * Source, in preference order:
 *   1. the coordinator's live-relay WebSocket   (./ws.ts)
 *   2. coordinator REST (`/sites`, `/events`)    — fallback for when the
 *      relay itself can't connect; covers `sites` and `events` only,
 *      which is all the coordinator knows about over plain REST.
 *
 * The transport in use is surfaced in the nav bar, so "why is Roster empty"
 * is answerable without opening devtools.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { config, isDemo } from '../config';
import { getEvents, getSites } from '../api/coordinator';
import type { EventRow, LiveTables, SiteRow } from '../types';
import { demoTables } from './demo';
import { EMPTY_TABLES } from './rows';
import { connectLive } from './ws';

export type LiveTransport = 'demo' | 'websocket' | 'coordinator-rest' | 'connecting' | 'disconnected';

interface LiveContextValue {
  tables: LiveTables;
  transport: LiveTransport;
  error: string | null;
  /** Force an immediate re-read (used after a mutation such as dispatch). */
  refresh: () => void;
}

const LiveContext = createContext<LiveContextValue | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [tables, setTables] = useState<LiveTables>(isDemo ? demoTables : EMPTY_TABLES);
  const [transport, setTransport] = useState<LiveTransport>(isDemo ? 'demo' : 'connecting');
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // Latest tables without re-subscribing on every tick.
  const tablesRef = useRef(tables);
  tablesRef.current = tables;
  const transportRef = useRef(transport);
  transportRef.current = transport;

  /**
   * Re-read now. On the live WebSocket rows are already streaming, so this
   * is a no-op rather than a reconnect — bumping the nonce would tear the
   * connection down and rebuild it after every dispatch.
   */
  const refresh = useCallback(() => {
    if (transportRef.current === 'websocket' || transportRef.current === 'demo') return;
    setNonce((value) => value + 1);
  }, []);

  useEffect(() => {
    if (isDemo) return;

    let cancelled = false;
    let timer: number | undefined;
    let handle: { disconnect: () => void } | null = null;
    const controller = new AbortController();

    const applyTables = (next: LiveTables) => {
      if (cancelled) return;
      setTables(next);
      setError(null);
    };

    /** Fallback tier, run on an interval. */
    const poll = async () => {
      try {
        const [sites, events] = await Promise.all([
          getSites(controller.signal),
          getEvents({ limit: 100 }, controller.signal),
        ]);
        if (cancelled) return;
        setTables({
          ...tablesRef.current,
          sites: sites.map(toSiteRowFromSummary),
          events: events.map(toEventRowFromLog),
        });
        setTransport('coordinator-rest');
        setError(
          'The live connection is unreachable — showing the coordinator REST fallback ' +
            '(sites and events only; agents, metrics, tasks and manifests are blank).',
        );
      } catch (restError) {
        // Both tiers are down: say so rather than showing "Polling" over
        // data that is never going to arrive.
        if (cancelled) return;
        setTransport('disconnected');
        setError(describe(restError));
      }
    };

    const startPolling = () => {
      void poll();
      timer = window.setInterval(() => void poll(), config.pollIntervalMs);
    };

    const start = () => {
      handle = connectLive(
        (next) => {
          applyTables(next);
          if (!cancelled) setTransport('websocket');
        },
        (wsError) => {
          if (cancelled) return;
          handle?.disconnect();
          handle = null;
          setError(`Live connection failed (${describe(wsError)}); polling instead.`);
          if (timer === undefined) startPolling();
        },
      );
    };

    start();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) window.clearInterval(timer);
      handle?.disconnect();
    };
  }, [nonce]);

  const value = useMemo<LiveContextValue>(
    () => ({ tables, transport, error, refresh }),
    [tables, transport, error, refresh],
  );

  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveContextValue {
  const value = useContext(LiveContext);
  if (!value) throw new Error('useLive must be used inside <LiveProvider>');
  return value;
}

/** Convenience: one table, already typed. */
export function useLiveTable<K extends keyof LiveTables>(table: K): LiveTables[K] {
  return useLive().tables[table];
}

function toSiteRowFromSummary(summary: {
  site_id: string;
  status: SiteRow['status'];
  last_event?: string | null;
  score: number;
}): SiteRow {
  return {
    site_id: summary.site_id,
    status: summary.status,
    last_event: summary.last_event ?? null,
    score: summary.score,
  };
}

function toEventRowFromLog(
  entry: {
    site_id: string;
    run_id?: string | null;
    status: EventRow['status'];
    violation?: EventRow['violation'];
    trace_id?: string | null;
    timestamp: string;
  },
  index: number,
): EventRow {
  return {
    // The REST log carries no row id; index is stable within one response and
    // is only used as a React key and for ordering.
    id: index + 1,
    site_id: entry.site_id,
    run_id: entry.run_id ?? null,
    status: entry.status,
    violation: entry.violation ?? null,
    trace_id: entry.trace_id ?? null,
    timestamp: entry.timestamp,
  };
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
