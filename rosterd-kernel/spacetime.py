"""SpacetimeDB row shapes the kernel writes, and the writer that gets them
there.

`AgentRow` / `AgentMetricsRow` are verbatim from the brief (Joy owns the
table schemas; the kernel is the primary writer for both). The writer
itself is a small abstraction over how the row actually gets to
SpacetimeDB, because no generated SpacetimeDB client exists in this repo:

* `LoggingSpacetimeWriter` (the default) just logs each row. This is what
  runs until Joy's module and reducers (`update_agent_status`,
  `record_agent_metrics`) exist, so the scaler and kill switch can be built
  and tested today without blocking on that.
* `HttpReducerSpacetimeWriter` calls SpacetimeDB's HTTP reducer-call API
  directly (`POST /v1/database/{module}/call/{reducer}`), the stable REST
  surface every generated client sits on top of. Swap in real generated
  bindings later without touching scaler.py or killer.py -- both only ever
  call `write_agent` / `write_agent_metrics`.

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
        url = f"{self._settings.spacetimedb_url.rstrip('/')}/v1/database/{self._settings.spacetimedb_module}/call/{reducer}"
        headers = {"content-type": "application/json"}
        if self._settings.spacetimedb_auth_token:
            headers["authorization"] = f"Bearer {self._settings.spacetimedb_auth_token}"
        try:
            response = httpx.post(url, json=args, headers=headers, timeout=5.0)
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
