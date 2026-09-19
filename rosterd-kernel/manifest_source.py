"""Where the confirmed manifest comes from.

The brief's task is "subscribe to this site's confirmed manifest in
SpacetimeDB". There is no generated SpacetimeDB subscription client in this
repo, and (see manifest.py's docstring) the ingestion service committed
today hasn't been updated to the finalized manifest shape or a
draft/confirmed status either. `IngestionPollManifestSource` is the
pragmatic stand-in: poll Param's `GET /manifest/{manifest_id}` on an
interval and validate the body against manifest.ManifestDocument. Nothing
downstream (dispatch, the scaler, the debug endpoint) talks to this module
directly -- they all read manifest.ManifestIndex, so swapping this for a
real subscription later is a one-file change.
"""
from __future__ import annotations

import logging
import threading
from typing import Protocol

import httpx

from manifest import ManifestDocument, ManifestIndex

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
        return ManifestDocument.model_validate(response.json())


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
