"""Background thread that keeps the SpacetimeDB `sites` rows for silent
sites current. `sites.SiteRegistry` computes `offline` status lazily on
every read, which is correct for `GET /sites`, but nothing else would ever
notice a site going dark and refresh the row Joy's frontend is subscribed
to -- there is no event to react to. So, same shape as the kernel's own
ScalerLoop: a small daemon thread on a fixed interval.
"""
from __future__ import annotations

import logging
import threading

from sites import SiteRegistry
from spacetime import SpacetimeWriter

logger = logging.getLogger("rosterd.coordinator.sweeper")


class OfflineSweeper:
    def __init__(self, *, registry: SiteRegistry, spacetime_writer: SpacetimeWriter, interval_sec: float) -> None:
        self._registry = registry
        self._spacetime_writer = spacetime_writer
        self._interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        for summary in self._registry.list_offline():
            self._spacetime_writer.write_site(summary)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("offline sweep failed")
            self._stop.wait(self._interval_sec)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="offline-sweeper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
