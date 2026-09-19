"""Shared types used across ingestion, kernel, demo-agent, and coordinator schemas.

Verbatim from the team brief. Do not redefine these elsewhere -- every
service in the repo carries its own copy of this exact file so each can be
built and deployed independently, but the *shape* has to stay identical or
status enums drift apart between services.
"""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RunStatus(str, Enum):
    working = "working"
    done = "done"
    killed = "killed"


class SiteStatus(str, Enum):
    healthy = "healthy"
    violation = "violation"
    offline = "offline"


class Violation(BaseModel):
    rule: str
    expected: str
    actual: str


class GraphEdge(BaseModel):
    source: str
    target: str
    condition: str | None = None


class GraphSpec(BaseModel):
    nodes: list[str]
    edges: list[GraphEdge]
