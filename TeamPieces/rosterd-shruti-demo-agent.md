# rosterd — Person 4: Demo agentic system (e-commerce)

## High-level idea (context)

rosterd takes an existing LangGraph agent system and makes it safe to
run, schedulable in plain language, and federatable, without a manual
config file. Discovery reads the repo directly, a human confirms what
was inferred, the kernel enforces the confirmed contract at runtime.

## Your role

You build the order-fulfillment LangGraph system the whole platform
is demonstrated against. This matters more than it did before: there
is no `constraints.yaml` anymore, ingestion infers everything from
your code. Your tool schemas, your guard clauses, and your
`interrupt()` call ARE the contract. Write them for real, or the
"no manual config" pitch has nothing to point at.

```
kernel-service ──> demo-agent-service (your build, same site only)
```

Your service is the only one that doesn't call anyone else. It just
answers.

## Tasks

- Build the 3-agent LangGraph system:
  - **Order Intake** — classifies an incoming order (standard,
    high-value, fraud-flagged)
  - **Fulfillment** — reserves inventory, tool
    `reserve_inventory(sku: str, qty: int)`, give `qty` a real
    Pydantic constraint (e.g. `Field(le=stock_on_hand)` or a fixed
    sane cap if you're not modeling live stock), this is the agent
    whose pool autoscales under a flash-sale burst
  - **Refund/Exception** — issues refunds, tool
    `issue_refund(order_id: str, amount: float = Field(le=100))`,
    call `interrupt()` before approving anything the schema doesn't
    already bound, this single call is what makes ingestion correctly
    infer `direct_assignable: false` for this node
- `POST /invoke` — accept `{entry_node, input}`, run that node (and
  downstream routing if applicable), return output plus any tool
  calls made, with the tool call args intact (this is what the
  kernel's constraint evaluator reads)
- `GET /graph` — expose the same graph structure ingestion extracts
  statically, so the two can be checked against each other
- Seed the misdirection scenario: a conversation where a fake
  "manager override" message tries to get Refund/Exception to approve
  an out-of-policy amount. It should trip the **schema-inferred**
  `amount <= 100` rule specifically, not a hardcoded kernel check,
  that's the point being demonstrated
- Dockerfile for this service, isolated on its own site network per
  the compose file (only the kernel can reach you)
- Add OTel auto-instrumentation to `/invoke`, propagate the incoming
  `traceparent` header so your span nests correctly under the
  kernel's dispatch trace, this is what makes the misdirection demo's
  Jaeger trace show the full path, not just the kernel's half of it

## Dependencies

- None to start, build in parallel with everyone else, but get your
  tool schemas and the `interrupt()` call in early even as rough
  stubs, Person 2 needs something real to test inference against, not
  just a graph shape
- Your `/invoke` response is what Person 1's kernel checks against
  the inferred constraints, keep the tool-call args shape exact and
  don't strip them out of the response

## API contract

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/invoke` | Run one node with the given input |
| GET | `/graph` | Return this system's graph structure |

### Shared types (`shared.py`, import this, don't redefine it)

```python
"""Shared types used across ingestion, kernel, demo-agent, and coordinator schemas."""

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
```

### Your schema (`demo_agent.py`)

```python
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
```

### Reference: the tool schemas ingestion infers from

These aren't part of the API contract above, they live in your own
LangChain tool definitions, shown here so it's clear what Person 2's
inference is actually reading:

```python
from pydantic import BaseModel, Field


class ReserveInventoryArgs(BaseModel):
    sku: str
    qty: int = Field(le=50)  # or Field(le=stock_on_hand) if you model live stock


class IssueRefundArgs(BaseModel):
    order_id: str
    amount: float = Field(le=100)
```

And in the Refund/Exception node body, a call to LangGraph's
`interrupt()` before any refund outside what the schema already
bounds is approved, this is the signal that flips
`direct_assignable` to `false` for this node.

### Who calls you

- Person 1's kernel calls `POST /invoke` on every dispatched task,
  and checks your response's `tool_calls[].args` against the
  inferred constraints before marking the run done or killing it
