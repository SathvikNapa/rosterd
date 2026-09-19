/**
 * Loads the manifest every downstream screen reads.
 *
 * Source of truth is ingestion's `GET /manifest/{id}` — the `manifests` table
 * has no writer yet (rosterd-param-frontend.md gap 3), so subscribing to it
 * would show nothing. If a row DOES appear (someone wires ingestion to
 * `store_manifest`), it is preferred, since that is the live path.
 *
 * Kernel's `GET /manifest` is fetched alongside it for per-constraint
 * source/confidence and the scaling policy — neither exists in ingestion's
 * response. A kernel that is down or governing a different manifest just
 * means no badges, not a failed screen.
 */
import { useCallback, useEffect, useState } from 'react';
import { getManifest } from './api/ingestion';
import { getKernelManifest } from './api/kernel';
import { isDemo } from './config';
import { useLive } from './live/LiveProvider';
import { DEMO_DRAFT_MANIFEST_ID, demoDraftManifest, demoKernelEntries, demoManifest } from './live/demo';
import { useSession } from './session';
import type { KernelManifestEntry, ManifestResponse } from './types';

interface UseManifestResult {
  manifest: ManifestResponse | null;
  kernelEntries: KernelManifestEntry[];
  kernelManifestId: string | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useManifest(manifestId: string | null): UseManifestResult {
  const session = useSession();
  const { tables } = useLive();
  const [manifest, setManifest] = useState<ManifestResponse | null>(
    isDemo ? demoManifest : (session.manifest ?? null),
  );
  const [kernelEntries, setKernelEntries] = useState<KernelManifestEntry[]>(isDemo ? demoKernelEntries : []);
  const [kernelManifestId, setKernelManifestId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  // Prefer a live `manifests` row if anything ever writes one.
  const liveRow = manifestId ? tables.manifests.find((row) => row.manifest_id === manifestId) : undefined;

  useEffect(() => {
    if (isDemo) {
      setManifest(manifestId === DEMO_DRAFT_MANIFEST_ID ? demoDraftManifest : demoManifest);
      return;
    }
    if (!manifestId) return;
    const controller = new AbortController();

    if (liveRow) {
      try {
        setManifest({
          manifest_id: liveRow.manifest_id,
          status: liveRow.status,
          agents: JSON.parse(liveRow.agents_json),
          graph: JSON.parse(liveRow.graph_json),
        });
        setError(null);
      } catch (cause) {
        setError(`manifests row ${liveRow.manifest_id} has unparseable JSON: ${String(cause)}`);
      }
    } else {
      setLoading(true);
      getManifest(manifestId, controller.signal)
        .then((response) => {
          setManifest(response);
          session.setManifest(response);
          setError(null);
        })
        .catch((cause) => {
          if (controller.signal.aborted) return;
          setError(cause instanceof Error ? cause.message : String(cause));
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }

    return () => controller.abort();
    // `session` is intentionally excluded: setManifest would re-trigger it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifestId, liveRow?.manifest_id, liveRow?.version, nonce]);

  useEffect(() => {
    if (isDemo) return;
    const controller = new AbortController();
    getKernelManifest(controller.signal)
      .then((response) => {
        setKernelEntries(response.agents ?? []);
        setKernelManifestId(response.manifest_id);
      })
      .catch(() => {
        // Kernel down, or no manifest pinned — badges degrade, screen doesn't.
        setKernelEntries([]);
        setKernelManifestId(null);
      });
    return () => controller.abort();
  }, [manifestId, nonce]);

  return { manifest, kernelEntries, kernelManifestId, loading, error, reload };
}
