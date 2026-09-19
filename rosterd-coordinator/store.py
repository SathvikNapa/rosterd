"""Bounded in-memory log of EventLogEntry, backing the `GET /events` debug
endpoint (same "additive convenience" spirit as rosterd-ingestion's
`/manifests` and rosterd-kernel's `/manifest` debug routes -- adds no field
to any contracted response). The frontend's real activity feed subscribes
to SpacetimeDB's `events` table directly, per the brief; this is purely for
poking at the coordinator without a SpacetimeDB module standing behind it.
"""
from __future__ import annotations

import threading
from collections import deque

from coordinator import EventLogEntry


class EventStore:
    def __init__(self, capacity: int = 500) -> None:
        self._lock = threading.Lock()
        self._events: deque[EventLogEntry] = deque(maxlen=capacity)

    def append(self, entry: EventLogEntry) -> None:
        with self._lock:
            self._events.append(entry)

    def list_all(self, *, site_id: str | None = None, limit: int = 100) -> list[EventLogEntry]:
        with self._lock:
            events = list(self._events)
        if site_id is not None:
            events = [e for e in events if e.site_id == site_id]
        return list(reversed(events))[:limit]
