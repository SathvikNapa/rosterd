"""Client for Shruti's demo-agent `/invoke` and `/resume`.

The request/response shapes mirror rosterd-shruti-demo-agent.md's
`demo_agent.py` as closely as possible, with one deliberate relaxation:
`entry_node` is typed `str` here instead of her `EntryNode` enum. Her repo
doesn't exist yet in this tree, and hard-coding a guess at her node names
(`order_intake` / `fulfillment` / `refund_exception`) would silently break
the moment her graph lands with real names. The kernel already knows the
right value at the call site -- it's `AgentManifestEntry.node`, straight
from the confirmed manifest -- so there's nothing an enum buys here except
a second place to keep in sync.

Keep the tool-call args intact and exact end to end: constraints.py reads
`tool_calls[].args` straight off the parsed InvokeResponse below.

`resume()` closes the human-in-the-loop gap: demo-agent's real `/invoke`
pauses a run with `output` shaped
`"PENDING_HUMAN_APPROVAL [thread_id=...]: <reason>"` (see her `main.py`
`_to_response`) whenever a node's `interrupt()` fires, and until now nothing
in the kernel ever called her real `/resume` endpoint back -- a paused run
just sat there marked `done`, indistinguishable from actually finishing
(see dispatch.py's docstring for the fuller story, and run_store.py's
`pause()`). `PENDING_HUMAN_APPROVAL_RE` is the one place that string gets
parsed; both `dispatch.py` (the initial pause) and `reviewer.py` (deciding
it later) import it from here rather than each hand-rolling the regex.
"""
from __future__ import annotations

import logging
import re

import httpx
from pydantic import BaseModel

logger = logging.getLogger("rosterd.kernel.demo_agent_client")

#: Matches demo-agent main.py's `_to_response`:
#: f"PENDING_HUMAN_APPROVAL [thread_id={thread_id}]: {info.get('reason')}"
PENDING_HUMAN_APPROVAL_RE = re.compile(r"^PENDING_HUMAN_APPROVAL \[thread_id=([^\]]+)\]:\s*(.*)$")


class InvokeInput(BaseModel):
    text: str
    context: dict | None = None


class InvokeRequest(BaseModel):
    entry_node: str
    input: InvokeInput


class ToolCall(BaseModel):
    tool: str
    args: dict  # keep this intact and exact, the kernel evaluates rules against it
    result: str | None = None


class InvokeResponse(BaseModel):
    output: str
    tool_calls: list[ToolCall] = []
    next_node: str | None = None


class DemoAgentTimeout(Exception):
    """The hard dispatch timeout elapsed waiting on /invoke."""


class DemoAgentError(Exception):
    """/invoke was unreachable, errored, or returned something unparsable.

    Also raised when the kernel severs the connection itself via a
    container-level kill (POST /runs/{run_id}/kill or a killed idle
    instance) -- from the caller's perspective that looks identical to any
    other broken connection, and dispatch.py disambiguates by checking
    run_store.is_kill_requested() before treating it as a real failure.
    """


class DemoAgentClient:
    """Thin, stateless wrapper -- one instance is shared across the process.
    `httpx.post` is called as a module-level function (not through a shared
    `httpx.Client`) specifically so tests can monkeypatch
    `demo_agent_client.httpx.post` without needing a live demo agent."""

    def __init__(self, settings, telemetry) -> None:
        self._settings = settings
        self._telemetry = telemetry

    def invoke(
        self,
        base_url: str,
        entry_node: str,
        text: str,
        context: dict | None,
        *,
        timeout: float,
    ) -> InvokeResponse:
        payload = InvokeRequest(entry_node=entry_node, input=InvokeInput(text=text, context=context))
        with self._telemetry.span("invoke_agent", **{"rosterd.entry_node": entry_node}):
            headers = self._telemetry.inject_headers({"content-type": "application/json"})
            try:
                response = httpx.post(
                    f"{base_url.rstrip('/')}/invoke",
                    json=payload.model_dump(mode="json"),
                    headers=headers,
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                raise DemoAgentTimeout(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise DemoAgentError(str(exc)) from exc

            if response.status_code >= 400:
                raise DemoAgentError(f"demo agent returned {response.status_code}: {response.text[:500]}")
            try:
                return InvokeResponse.model_validate(response.json())
            except Exception as exc:  # noqa: BLE001 - any parse failure is a DemoAgentError
                raise DemoAgentError(f"unparsable /invoke response: {exc}") from exc

    def resume(
        self,
        base_url: str,
        thread_id: str,
        approved: bool,
        *,
        timeout: float,
    ) -> InvokeResponse:
        """Continues a paused run past its interrupt(). Demo-agent's own
        refund_node.py decides what `approved: False` means (it denies the
        refund and returns cleanly, no tool call) -- this always calls
        through rather than short-circuiting on denial, so that denial path
        runs for real instead of being guessed at here."""
        with self._telemetry.span("resume_agent", **{"rosterd.thread_id": thread_id, "rosterd.approved": approved}):
            headers = self._telemetry.inject_headers({"content-type": "application/json"})
            try:
                response = httpx.post(
                    f"{base_url.rstrip('/')}/resume",
                    json={"thread_id": thread_id, "approved": approved},
                    headers=headers,
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                raise DemoAgentTimeout(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise DemoAgentError(str(exc)) from exc

            if response.status_code >= 400:
                raise DemoAgentError(f"demo agent /resume returned {response.status_code}: {response.text[:500]}")
            try:
                return InvokeResponse.model_validate(response.json())
            except Exception as exc:  # noqa: BLE001 - any parse failure is a DemoAgentError
                raise DemoAgentError(f"unparsable /resume response: {exc}") from exc
