"""SpacetimeDB row shapes the kernel writes, and the writer that gets them
there.

`AgentRow` / `AgentMetricsRow` are verbatim from the brief. The real module
now exists at rosterd-spacetimedb/ (database name `rosterd`, tables
`agents` + `agent_metrics` among others) and has been published and
verified locally: both reducers this writer calls
(`update_agent_status`, `record_agent_metrics`) accept exactly the shape
`row.model_dump(mode="json")` produces. The writer itself is a small
abstraction over how the row actually gets to SpacetimeDB, because no
generated SpacetimeDB TypeScript-client bindings are vendored into this
Python repo:

* `LoggingSpacetimeWriter` (the default) just logs each row. This still
  runs whenever `ROSTERD_KERNEL_SPACETIMEDB_URL` / `_MODULE` aren't set
  (e.g. a bare `docker compose up` without the `spacetimedb` service), so
  the scaler and kill switch keep working without a live SpacetimeDB.
* `HttpReducerSpacetimeWriter` calls SpacetimeDB's HTTP reducer-call API
  directly (`POST /v1/database/{module}/call/{reducer}`), the stable REST
  surface every generated client sits on top of. The call body is a JSON
  *array* of positional arguments -- confirmed via a live round trip
  against a real `spacetime start` server, since the naive `json=args`
  (a bare object) is rejected by the real API. Both reducers here take one
  structured `row` argument, so the array always has exactly one element.
  Swap in real generated bindings later without touching scaler.py or
  killer.py -- both only ever call `write_agent` / `write_agent_metrics`.

Writes are best-effort: a SpacetimeDB outage must not stop the kernel from
dispatching or scaling, only from being *visible* while it does.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

import httpx
from pydantic import BaseModel

logger = logging.getLogger("rosterd.kernel.spacetime")


class AgentRow(BaseModel):
    """One row per running instance."""

    site_id: str
    agent_id: str
    instance_id: str
    name: str
    status: str  # "idle" | "working" | "killed"
    updated_at: datetime


class AgentMetricsRow(BaseModel):
    """Written every scaler tick, not just on change."""

    site_id: str
    agent_id: str
    timestamp: datetime
    in_flight_count: int
    queued_count: int
    target_concurrency: int
    current_replicas: int
    desired_replicas: int
    min_replicas: int
    max_replicas: int


class SpacetimeWriter(Protocol):
    def write_agent(self, row: AgentRow) -> None: ...

    def write_agent_metrics(self, row: AgentMetricsRow) -> None: ...


class LoggingSpacetimeWriter:
    def write_agent(self, row: AgentRow) -> None:
        logger.info("agents <- %s", row.model_dump_json())

    def write_agent_metrics(self, row: AgentMetricsRow) -> None:
        logger.info("agent_metrics <- %s", row.model_dump_json())


class HttpReducerSpacetimeWriter:
    def __init__(self, settings) -> None:
        self._settings = settings

    def _call(self, reducer: str, args: dict) -> None:
        # SpacetimeDB's HTTP reducer-call API takes a JSON *array* of
        # positional arguments, not a bare object -- confirmed via a live
        # round trip against a real `spacetime start` server. Every reducer
        # in rosterd's module (see rosterd-spacetimedb/) takes exactly one
        # structured `row` parameter, so the array always has one element:
        # the row object itself.
        url = f"{self._settings.spacetimedb_url.rstrip('/')}/v1/database/{self._settings.spacetimedb_module}/call/{reducer}"
        headers = {"content-type": "application/json"}
        if self._settings.spacetimedb_auth_token:
            headers["authorization"] = f"Bearer {self._settings.spacetimedb_auth_token}"
        try:
            response = httpx.post(url, json=[args], headers=headers, timeout=5.0)
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - best-effort, see module docstring
            logger.warning("SpacetimeDB reducer call %s failed (continuing)", reducer, exc_info=True)

    def write_agent(self, row: AgentRow) -> None:
        self._call("update_agent_status", row.model_dump(mode="json"))

    def write_agent_metrics(self, row: AgentMetricsRow) -> None:
        self._call("record_agent_metrics", row.model_dump(mode="json"))


def build_spacetime_writer(settings) -> SpacetimeWriter:
    if settings.spacetimedb_url and settings.spacetimedb_module:
        return HttpReducerSpacetimeWriter(settings)
    return LoggingSpacetimeWriter()
