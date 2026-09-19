"""Coordinator service -- thin aggregator. Receives events from every
kernel, never task content, and can push a policy update back.

Verbatim from the team brief (rosterd-joy-coordinator-frontend.md). This is
the wire contract the kernel's `coordinator_client.py` is written against
-- app.py imports these types directly rather than redefining shapes inline.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from shared import RunStatus, SiteStatus, Violation


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


class SiteSummary(BaseModel):
    site_id: str
    status: SiteStatus
    last_event: datetime | None = None
    score: float


class EventLogEntry(BaseModel):
    site_id: str
    run_id: str | None = None
    status: RunStatus | Literal["scaled_up", "scaled_down"]
    violation: Violation | None = None
    trace_id: str | None = None
    timestamp: datetime


class PolicyPushRequest(BaseModel):
    rule: str
    value: float | str | bool
    reason: str
