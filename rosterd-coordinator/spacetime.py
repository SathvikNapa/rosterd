"""SpacetimeDB rows the coordinator writes, and the writer that gets them
there. Same pattern as rosterd-kernel/spacetime.py -- see its docstring for
the full rationale (no generated SpacetimeDB client exists in this repo, so
`LoggingSpacetimeWriter` is the default until Joy's module is up, and
`HttpReducerSpacetimeWriter` calls the stable HTTP reducer-call API in the
meantime).

The coordinator owns `sites` and `events` (`SiteSummary` / `EventLogEntry`,
both already defined in coordinator.py -- there's no separate "Row" shape
for them the way the kernel has AgentRow vs AgentInstance). `TaskRow` is
included here for reference because Joy's brief lists `tasks` among the
tables she owns, but nothing in the coordinator's own contract (`POST
/events`, `GET /sites`, `POST /policy/push`) ever produces one -- see the
README's "Notes for the team". No writer method exists for it because
nothing in this service has a TaskRow to write yet.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

import httpx
from pydantic import BaseModel

from coordinator import EventLogEntry, SiteSummary

logger = logging.getLogger("rosterd.coordinator.spacetime")


class TaskRow(BaseModel):
    task_id: str
    site_id: str
    agent_id: str
    title: str
    status: str
    assignees: list[str]
    criteria: list[str]
    priority: str
    source: str | None = None
    created_at: datetime


class SpacetimeWriter(Protocol):
    def write_site(self, row: SiteSummary) -> None: ...

    def write_event(self, row: EventLogEntry) -> None: ...


class LoggingSpacetimeWriter:
    def write_site(self, row: SiteSummary) -> None:
        logger.info("sites <- %s", row.model_dump_json())

    def write_event(self, row: EventLogEntry) -> None:
        logger.info("events <- %s", row.model_dump_json())


class HttpReducerSpacetimeWriter:
    def __init__(self, settings) -> None:
        self._settings = settings

    def _call(self, reducer: str, args: dict) -> None:
        url = (
            f"{self._settings.spacetimedb_url.rstrip('/')}/v1/database/"
            f"{self._settings.spacetimedb_module}/call/{reducer}"
        )
        headers = {"content-type": "application/json"}
        if self._settings.spacetimedb_auth_token:
            headers["authorization"] = f"Bearer {self._settings.spacetimedb_auth_token}"
        try:
            response = httpx.post(url, json=args, headers=headers, timeout=5.0)
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - best-effort, see module docstring
            logger.warning("SpacetimeDB reducer call %s failed (continuing)", reducer, exc_info=True)

    def write_site(self, row: SiteSummary) -> None:
        self._call("update_site_score", row.model_dump(mode="json"))

    def write_event(self, row: EventLogEntry) -> None:
        self._call("record_event", row.model_dump(mode="json"))


def build_spacetime_writer(settings) -> SpacetimeWriter:
    if settings.spacetimedb_url and settings.spacetimedb_module:
        return HttpReducerSpacetimeWriter(settings)
    return LoggingSpacetimeWriter()
