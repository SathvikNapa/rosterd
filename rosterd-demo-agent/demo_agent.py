"""Demo agent service (Person 4's e-commerce LangGraph system). Only
ever answers, never calls out. Wrapped and invoked by its site's
kernel."""

from enum import Enum

from pydantic import BaseModel

from shared import GraphSpec


class EntryNode(str, Enum):
    order_intake = "order_intake"
    fulfillment = "fulfillment"
    refund_exception = "refund_exception"


class InvokeInput(BaseModel):
    text: str
    context: dict | None = None


class InvokeRequest(BaseModel):
    entry_node: EntryNode
    input: InvokeInput


class ToolCall(BaseModel):
    tool: str
    args: dict  # keep this intact and exact, the kernel evaluates rules against it
    result: str | None = None


class InvokeResponse(BaseModel):
    output: str
    tool_calls: list[ToolCall] = []
    next_node: str | None = None


class GraphResponse(GraphSpec):
    """Same shape as GraphSpec. Returned by GET /graph, mirrors what
    ingestion extracts statically at setup time."""
