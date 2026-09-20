"""Where the confirmed manifest comes from.

The brief's task is "subscribe to this site's confirmed manifest in
SpacetimeDB". There is no generated SpacetimeDB subscription client in this
repo, so `IngestionPollManifestSource` is the pragmatic stand-in: poll
Param's `GET /manifest/{manifest_id}` on an interval and validate the body
against manifest.ManifestDocument. Nothing downstream (dispatch, the
scaler, the debug endpoint) talks to this module directly -- they all read
manifest.ManifestIndex, so swapping this for a real subscription later is a
one-file change.

Two things confirmed by actually running ingestion and pointing a kernel at
it (not just reading the code):

1. Ingestion *does* now emit `status: draft | confirmed` (as of its confirm
   -gate commit) -- `ManifestSubscription.poll_once()` enforces the brief's
   trust boundary ("a draft manifest governs nothing") by refusing to index
   anything that isn't `confirmed`, the same way it already refuses `None`.
   Confirmed via a repro: without this check, a schema-valid draft document
   was accepted by `ManifestIndex.update()` and its agents became live.

2. Ingestion's `AgentManifestEntry.constraints` is still a free-form
   `{max_refund_usd, requires_prior_node, ...}` object, not the finalized
   brief's `list[ConstraintRule]` (field/op/value/source/confidence) that
   `manifest.py` implements. A real `/ingest` -> confirm -> poll round trip
   against the bundled demo-agent fixture fails Pydantic validation on
   `constraints` for every agent. This is a cross-team contract gap, not
   something to silently paper over with a guessed field-path mapping here
   (see the kernel README's "Notes for the team") -- it surfaces as a
   logged warning and `manifest_not_ready`, not a crash, but it does mean
   today's real ingestion output can't be consumed until the shapes
   converge.
"""
from __future__ import annotations

import logging
import threading
from typing import Protocol

import httpx

from manifest import ManifestDocument, ManifestIndex, ManifestStatus, ScalingPolicy

logger = logging.getLogger("rosterd.kernel.manifest_source")


class ManifestSource(Protocol):
    def fetch(self) -> ManifestDocument | None: ...


class StaticManifestSource:
    """Wraps a manifest already in hand -- used by tests, and by a kernel
    started with no ROSTERD_KERNEL_MANIFEST_ID (fetch() returns None
    forever, so dispatch keeps reporting manifest_not_ready until one is
    supplied some other way)."""

    def __init__(self, document: ManifestDocument | None = None) -> None:
        self._document = document

    def fetch(self) -> ManifestDocument | None:
        return self._document

    def set(self, document: ManifestDocument | None) -> None:
        self._document = document


class IngestionPollManifestSource:
    def __init__(self, settings) -> None:
        self._settings = settings

    def fetch(self) -> ManifestDocument | None:
        if not self._settings.manifest_id:
            return None
        url = f"{self._settings.ingestion_url.rstrip('/')}/manifest/{self._settings.manifest_id}"
        response = httpx.get(url, timeout=5.0)
        response.raise_for_status()
        document = ManifestDocument.model_validate(response.json())
        return document.model_copy(update={"agents": [self._with_default_scaling(a) for a in document.agents]})

    def _with_default_scaling(self, agent):
        """Ingestion has no `scaling` field at all yet (see manifest.py's
        module docstring), so every entry it returns arrives with
        `ScalingPolicy()`'s hardcoded min=1/max=1 -- confirmed live: a real
        confirmed manifest kept every agent pinned at current_replicas=1
        regardless of load, since desired is clamped to max_replicas=1 no
        matter how deep the queue got. Stand in with the kernel's own
        configured default until ingestion can actually express a per-agent
        policy, rather than silently locking out scaling for anything
        that came from a real ingest."""
        return agent.model_copy(
            update={
                "scaling": ScalingPolicy(
                    min_replicas=self._settings.default_min_replicas,
                    max_replicas=self._settings.default_max_replicas,
                    target_concurrency=self._settings.default_target_concurrency,
                )
            }
        )


class ManifestSubscription:
    """Polls `source.fetch()` on a background daemon thread and writes
    whatever it gets into `index`. A failed poll (ingestion down, schema
    mismatch, network blip) is logged and skipped -- the index just keeps
    serving the last manifest it had, which is the right failure mode for a
    kernel mid-dispatch."""

    def __init__(self, source: ManifestSource, index: ManifestIndex, interval_sec: float) -> None:
        self._source = source
        self._index = index
        self._interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> bool:
        try:
            document = self._source.fetch()
        except Exception:  # noqa: BLE001 - see class docstring
            logger.warning("manifest poll failed", exc_info=True)
            return False
        if document is None:
            return False
        if document.status != ManifestStatus.confirmed:
            # The brief's trust boundary: "a draft manifest governs
            # nothing." Treated the same as no document at all -- the
            # index just keeps serving whatever it last had confirmed.
            logger.info(
                "manifest %s is %s, not confirmed -- not loading it",
                document.manifest_id, document.status.value,
            )
            return False
        try:
            self._index.update(document)
        except Exception:  # noqa: BLE001 - a malformed document must not kill the poller
            logger.warning("manifest document failed validation, keeping the previous one", exc_info=True)
            return False
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._interval_sec)

    def start(self) -> None:
        if self._thread is not None:
            return
        self.poll_once()  # don't make dispatch wait a full interval on cold start
        self._thread = threading.Thread(target=self._run, name="manifest-subscription", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
