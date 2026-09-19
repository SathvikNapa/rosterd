"""Per-site health state: `GET /sites` reads straight from here, not from
SpacetimeDB -- the brief is explicit that `GET /sites` is "mostly for
debugging, frontend subscribes directly" to the `sites` table instead.
This registry is the coordinator's own source of truth; SpacetimeDB writes
(via spacetime.py) are a side-channel for that direct subscription, kept in
sync but never read back.

`status` per site:
  - `healthy`   the most recent event was a normal completion / scale event
  - `violation` the most recent event carried a Violation, or was a kill
  - `offline`   no event in `site_offline_after_sec` -- computed lazily on
                every read (`list_sites`), not stored, so it's never stale
                regardless of how long nothing calls in

`score` is the fraction of a site's last `site_score_window` events that
were NOT a violation/kill -- 1.0 for a site with a clean recent history,
trending toward 0.0 under a sustained bad streak.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from coordinator import EventRequest, SiteSummary
from shared import RunStatus, SiteStatus


def _is_trouble(event: EventRequest) -> bool:
    """Does this event count against the site's health score / flip its
    status to `violation`? A kill (with or without a Violation payload --
    a manual kill has none) or an explicit Violation both count; a normal
    `done`, `working`, `scaled_up`, or `scaled_down` does not."""
    return event.violation is not None or event.status == RunStatus.killed


@dataclass
class _SiteState:
    site_id: str
    last_event: datetime
    last_status: SiteStatus
    recent_ok: deque = field(default_factory=lambda: deque(maxlen=1))  # resized on first real use


class SiteRegistry:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._sites: dict[str, _SiteState] = {}

    def record_event(self, event: EventRequest) -> SiteSummary:
        with self._lock:
            state = self._sites.get(event.site_id)
            if state is None:
                state = _SiteState(
                    site_id=event.site_id,
                    last_event=event.timestamp,
                    last_status=SiteStatus.healthy,
                    recent_ok=deque(maxlen=max(1, self._settings.site_score_window)),
                )
                self._sites[event.site_id] = state

            state.last_event = max(state.last_event, event.timestamp)
            state.last_status = SiteStatus.violation if _is_trouble(event) else SiteStatus.healthy
            state.recent_ok.append(not _is_trouble(event))
            return self._summary(state)

    def _summary(self, state: _SiteState) -> SiteSummary:
        now = datetime.now(timezone.utc)
        status = state.last_status
        if (now - state.last_event).total_seconds() > self._settings.site_offline_after_sec:
            status = SiteStatus.offline
        score = (sum(state.recent_ok) / len(state.recent_ok)) if state.recent_ok else 1.0
        return SiteSummary(site_id=state.site_id, status=status, last_event=state.last_event, score=score)

    def list_sites(self) -> list[SiteSummary]:
        with self._lock:
            return sorted((self._summary(s) for s in self._sites.values()), key=lambda s: s.site_id)

    def get(self, site_id: str) -> SiteSummary | None:
        with self._lock:
            state = self._sites.get(site_id)
            return self._summary(state) if state is not None else None

    def list_offline(self) -> list[SiteSummary]:
        """Every site whose *computed* status is `offline` right now. Used
        by the background sweeper to keep the SpacetimeDB `sites` rows for
        silent sites current even though nothing calls `record_event` for
        them -- a site going dark is exactly the case no incoming request
        would otherwise ever refresh."""
        with self._lock:
            return [summary for s in self._sites.values() if (summary := self._summary(s)).status == SiteStatus.offline]
