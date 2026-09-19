"""Kernel service -- one instance per site. Dispatches tasks, enforces
constraints from the confirmed manifest, scales agent pools, exports
scaling metrics and traces, and can kill a run or an idle instance.

Verbatim from the team brief (rosterd-sathvik-kernel.md). This is the wire
contract everyone else's client code is written against -- app.py imports
these types directly rather than redefining request/response shapes inline.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from shared import Priority, RunStatus, Violation


class DispatchStatus(str, Enum):
    accepted = "accepted"
    rejected = "rejected"


class TaskSpec(BaseModel):
    id: str
    title: str
    description: str
    priority: Priority = Priority.medium
    source: str | None = None
    expectation_criteria: list[str] = []


class DispatchRequest(BaseModel):
    agent_id: str
    task: TaskSpec
    assignees: list[str]


class DispatchResponse(BaseModel):
    run_id: str
    status: DispatchStatus
    reason: str | None = None


class RunResponse(BaseModel):
    run_id: str
    agent_id: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime | None = None
    violation: Violation | None = None
    output: str | None = None
    trace_id: str | None = None  # links this run to its Jaeger trace


class KillResponse(BaseModel):
    run_id: str
    status: Literal["killed"] = "killed"
    reason: str


class PolicyUpdateRequest(BaseModel):
    rule: str
    value: float | str | bool
    reason: str


class PolicyUpdateResponse(BaseModel):
    applied: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    budget_remaining: float


class InstanceStatus(str, Enum):
    idle = "idle"
    working = "working"


class AgentInstance(BaseModel):
    instance_id: str
    agent_id: str
    container_name: str
    status: InstanceStatus
    started_at: datetime


class InstancesResponse(BaseModel):
    agent_id: str
    instances: list[AgentInstance]


class ScaleRequest(BaseModel):
    target_replicas: int


class ScaleResponse(BaseModel):
    agent_id: str
    replicas: int


class SimulateLoadRequest(BaseModel):
    count: int = 10
    rate_per_second: float = 3.0
    scale_down_after_idle_seconds_override: int | None = None


class SimulateLoadResponse(BaseModel):
    agent_id: str
    dispatched: int
