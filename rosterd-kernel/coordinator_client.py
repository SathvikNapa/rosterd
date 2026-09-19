"""Client for Joy's coordinator `POST /events`.

Shapes mirror rosterd-joy-coordinator-frontend.md's `coordinator.py`
exactly. Posting is best-effort: a coordinator outage must never take the
kernel down mid-dispatch, so every failure here is logged and swallowed,
never raised. `dispatch.py` and `scaler.py` both call `post_event` fire
-and-forget style.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

import httpx
from pydantic import BaseModel

from shared import RunStatus, Violation

logger = logging.getLogger("rosterd.kernel.coordinator_client")


class EventRequest(BaseModel):
    """Posted by a kernel after every run or scale event."""

    site_id: str
    run_id: str | None = None  # None for a pool scale event
    agent_id: str
    status: RunStatus | Literal["scaled_up", "scaled_down"]
    violation: Violation | None = None
    pool_size: int | None = None
    trace_id: str | None = None  # links this event to its Jaeger trace
    timestamp: datetime


class PolicyUpdate(BaseModel):
    rule: str
    value: float | str | bool


class EventResponse(BaseModel):
    received: Literal[True] = True
    policy_update: PolicyUpdate | None = None


class CoordinatorClient:
    def __init__(self, settings, telemetry) -> None:
        self._settings = settings
        self._telemetry = telemetry

    def post_event(self, event: EventRequest) -> PolicyUpdate | None:
        with self._telemetry.span("post_event", **{"rosterd.agent_id": event.agent_id, "rosterd.status": str(event.status)}):
            headers = self._telemetry.inject_headers({"content-type": "application/json"})
            try:
                response = httpx.post(
                    f"{self._settings.coordinator_url.rstrip('/')}/events",
                    json=event.model_dump(mode="json"),
                    headers=headers,
                    timeout=self._settings.coordinator_timeout_sec,
                )
                response.raise_for_status()
                parsed = EventResponse.model_validate(response.json())
                return parsed.policy_update
            except Exception:  # noqa: BLE001 - best-effort by design, see module docstring
                logger.warning("POST %s/events failed (continuing without it)", self._settings.coordinator_url, exc_info=True)
                return None
