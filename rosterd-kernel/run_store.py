"""In-memory run tracking.

Holds exactly the wire-contract RunResponse per run_id (kernel.py's shape,
untouched -- no extra fields bolted on, same discipline ingestion's README
calls out for its own contracted responses) plus a small internal sidecar
(`_RunMeta`) for the bookkeeping GET /runs/{run_id} and POST
/runs/{run_id}/kill need but the wire shape doesn't carry: which instance
is currently serving the run, and whether someone has asked for a manual
kill.

`finish()` never downgrades a run that's already `killed` back to `done` --
see `force_kill()`'s docstring for why that matters.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from kernel import RunResponse
from shared import RunStatus, Violation


@dataclass
class _RunMeta:
    agent_id: str
    current_instance_id: str | None = None
    kill_requested: bool = False
    #: Set on pause() -- the LangGraph checkpoint id demo-agent's /resume
    #: needs to continue this exact run. None for any run that never paused.
    thread_id: str | None = None
    #: The task text as dispatched, kept so a reviewer (human or agent)
    #: deciding a paused run later has the same context the original
    #: dispatch did -- the wire-contract RunResponse itself carries no task
    #: fields (see this module's docstring), so this is the only copy.
    task_text: str = ""
    #: Set on pause() -- demo-agent's own interrupt() reason, e.g. "refund
    #: on flagged/high-value order needs human approval". Surfaced to
    #: whoever (human or reviewer agent) is about to decide.
    pause_reason: str = ""


class RunStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._responses: dict[str, RunResponse] = {}
        self._meta: dict[str, _RunMeta] = {}

    def create(self, run_id: str, agent_id: str, *, task_text: str = "") -> RunResponse:
        with self._lock:
            response = RunResponse(
                run_id=run_id,
                agent_id=agent_id,
                status=RunStatus.working,
                started_at=datetime.now(timezone.utc),
            )
            self._responses[run_id] = response
            self._meta[run_id] = _RunMeta(agent_id=agent_id, task_text=task_text)
            return response

    def set_current_instance(self, run_id: str, instance_id: str | None) -> None:
        with self._lock:
            meta = self._meta.get(run_id)
            if meta is not None:
                meta.current_instance_id = instance_id

    def instance_for(self, run_id: str) -> tuple[str, str | None] | None:
        """(agent_id, current_instance_id) for the kill endpoint, or None if
        this run_id was never issued."""
        with self._lock:
            meta = self._meta.get(run_id)
            if meta is None:
                return None
            return meta.agent_id, meta.current_instance_id

    def request_kill(self, run_id: str) -> bool:
        with self._lock:
            meta = self._meta.get(run_id)
            if meta is None:
                return False
            meta.kill_requested = True
            return True

    def is_kill_requested(self, run_id: str) -> bool:
        with self._lock:
            meta = self._meta.get(run_id)
            return bool(meta and meta.kill_requested)

    def finish(
        self,
        run_id: str,
        *,
        status: RunStatus,
        output: str | None = None,
        violation: Violation | None = None,
        trace_id: str | None = None,
    ) -> RunResponse | None:
        with self._lock:
            response = self._responses.get(run_id)
            if response is None:
                return None
            if response.status == RunStatus.killed:
                # killed is sticky: a manual force_kill that races the
                # normal completion path must not be clobbered by a late
                # "done" write landing after it. See force_kill().
                return response
            response.status = status
            response.ended_at = datetime.now(timezone.utc)
            response.output = output
            response.violation = violation
            response.trace_id = trace_id or response.trace_id
            return response

    def pause(self, run_id: str, *, thread_id: str, reason: str, output: str, trace_id: str | None = None) -> RunResponse | None:
        """A run interrupted for human/reviewer approval -- not finished,
        not killed, resumable via `thread_id`. Same "killed is sticky" guard
        as finish(): a manual kill racing the pause must win."""
        with self._lock:
            response = self._responses.get(run_id)
            if response is None:
                return None
            if response.status == RunStatus.killed:
                return response
            response.status = RunStatus.paused
            response.output = output
            response.trace_id = trace_id or response.trace_id
            meta = self._meta.get(run_id)
            if meta is not None:
                meta.thread_id = thread_id
                meta.pause_reason = reason
            return response

    def pause_context(self, run_id: str) -> tuple[str, str, str] | None:
        """(thread_id, task_text, pause_reason) for resuming a paused run,
        or None if this run never paused (or was never issued)."""
        with self._lock:
            meta = self._meta.get(run_id)
            if meta is None or meta.thread_id is None:
                return None
            return meta.thread_id, meta.task_text, meta.pause_reason

    def paused_run_ids(self) -> list[str]:
        """Every run currently awaiting approval -- what the reviewer loop
        polls."""
        with self._lock:
            return [run_id for run_id, r in self._responses.items() if r.status == RunStatus.paused]

    def force_kill(self, run_id: str, *, reason: str) -> RunResponse | None:
        """Used by POST /runs/{run_id}/kill: unconditionally marks the run
        killed, even if it had already finished as `done` by the time the
        kill request arrived (a human asked for a kill; GET /runs/{run_id}
        should reflect that afterward, not a state that raced it)."""
        with self._lock:
            response = self._responses.get(run_id)
            if response is None:
                return None
            response.status = RunStatus.killed
            if response.ended_at is None:
                response.ended_at = datetime.now(timezone.utc)
            if response.violation is None:
                response.violation = Violation(rule="manual_kill", expected="run to completion", actual=reason)
            return response

    def get(self, run_id: str) -> RunResponse | None:
        with self._lock:
            return self._responses.get(run_id)
