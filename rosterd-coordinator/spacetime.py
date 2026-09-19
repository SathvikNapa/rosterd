"""SpacetimeDB rows the coordinator writes, and the writer that gets them
there. Same pattern as rosterd-kernel/spacetime.py -- see its docstring for
the full rationale.

The real module now exists at rosterd-spacetimedb/ (database name
`rosterd`) and has been published and verified locally, including its
`sites` and `events` tables and the `update_site_score` / `record_event`
reducers this writer calls. `TaskRow` and the module's `tasks` table are
kept for reference because Joy's brief lists `tasks` among the tables she
owns, but nothing in the coordinator's own contract (`POST /events`, `GET
/sites`, `POST /policy/push`) ever produces one -- see the README's "Notes
for the team". No writer method exists for it because nothing in this
service has a TaskRow to write yet (the module's `record_task` reducer is
ready whenever that changes).

`SiteSummary.last_event` and `EventLogEntry.run_id` / `.violation` /
`.trace_id` are all `X | None` -- SpacetimeDB's Option type is a tagged sum
type on the wire, not a bare nullable value: a *present* value must be sent
as `{"some": value}`; a *missing* one is sent as bare JSON `null` (confirmed
accepted as shorthand for the "none" variant via a live round trip against
a real `spacetime start` server -- `{"none": []}` also works but `null` is
simpler and Pydantic already produces it for free). `_wrap_option` below
does that translation; `AgentRow` / `AgentMetricsRow` in the kernel have no
optional fields, so the kernel's writer doesn't need this at all.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from coordinator import EventLogEntry, SiteSummary

logger = logging.getLogger("rosterd.coordinator.spacetime")


def _wrap_option(value: Any) -> Any:
    """`None` -> `None` (bare JSON null, accepted as SpacetimeDB's "none"
    variant); anything else -> `{"some": value}`, the wire shape its Option
    sum type requires for a present value."""
    if value is None:
        return None
    return {"some": value}


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
        # SpacetimeDB's HTTP reducer-call API takes a JSON *array* of
        # positional arguments, not a bare object -- confirmed via a live
        # round trip against a real `spacetime start` server. Every reducer
        # in rosterd's module (see rosterd-spacetimedb/) takes exactly one
        # structured `row` parameter, so the array always has one element.
        url = (
            f"{self._settings.spacetimedb_url.rstrip('/')}/v1/database/"
            f"{self._settings.spacetimedb_module}/call/{reducer}"
        )
        headers = {"content-type": "application/json"}
        if self._settings.spacetimedb_auth_token:
            headers["authorization"] = f"Bearer {self._settings.spacetimedb_auth_token}"
        try:
            response = httpx.post(url, json=[args], headers=headers, timeout=5.0)
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - best-effort, see module docstring
            logger.warning("SpacetimeDB reducer call %s failed (continuing)", reducer, exc_info=True)

    def write_site(self, row: SiteSummary) -> None:
        data = row.model_dump(mode="json")
        data["last_event"] = _wrap_option(data["last_event"])
        self._call("update_site_score", data)

    def write_event(self, row: EventLogEntry) -> None:
        data = row.model_dump(mode="json")
        data["run_id"] = _wrap_option(data["run_id"])
        data["violation"] = _wrap_option(data["violation"])
        data["trace_id"] = _wrap_option(data["trace_id"])
        self._call("record_event", data)


def build_spacetime_writer(settings) -> SpacetimeWriter:
    if settings.spacetimedb_url and settings.spacetimedb_module:
        return HttpReducerSpacetimeWriter(settings)
    return LoggingSpacetimeWriter()
