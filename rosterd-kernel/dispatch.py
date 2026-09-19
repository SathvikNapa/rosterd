"""The POST /dispatch pipeline: validate against the confirmed manifest,
obtain an instance (idle pick, spin-up, or a bounded queue wait at cap),
invoke the demo agent with a hard timeout, evaluate constraints, enforce
budget, kill on any breach, and report the outcome to the coordinator.

Dispatch is synchronous end to end within the POST /dispatch request. That
reading of the contract: kernel.py's DispatchResponse carries only
`status: accepted | rejected` (no "queued"/"running"), and its endpoint
table says /dispatch itself should "run it" -- so `accepted` means the
kernel took the task and ran it to completion (or to a kill) before
returning, not that it queued something for later. GET /runs/{run_id} still
exists for polling/detail (trace_id, violation, output) and is what the
Task Run & Violation screen actually renders; POST /dispatch is the
synchronous trigger for it.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from budget import BudgetTracker
from constraints import evaluate_all
from coordinator_client import CoordinatorClient, EventRequest
from demo_agent_client import DemoAgentClient, DemoAgentError, DemoAgentTimeout
from docker_backend import DockerBackend
from errors import AgentNotFoundError, ManifestNotReadyError
from kernel import DispatchRequest, DispatchResponse, DispatchStatus, InstanceStatus
from killer import kill as kill_instance
from manifest import ManifestIndex
from policy import PolicyStore
from registry import InstanceRegistry
from run_store import RunStore
from shared import RunStatus, Violation
from spacetime import AgentRow, SpacetimeWriter
from tracing import mark_violation

logger = logging.getLogger("rosterd.kernel.dispatch")


class Dispatcher:
    def __init__(
        self,
        *,
        settings,
        manifest_index: ManifestIndex,
        registry: InstanceRegistry,
        docker_backend: DockerBackend,
        demo_agent_client: DemoAgentClient,
        coordinator_client: CoordinatorClient,
        budget_tracker: BudgetTracker,
        run_store: RunStore,
        telemetry,
        policy_store: PolicyStore,
        spacetime_writer: SpacetimeWriter,
    ) -> None:
        self._settings = settings
        self._manifest_index = manifest_index
        self._registry = registry
        self._docker_backend = docker_backend
        self._demo_agent_client = demo_agent_client
        self._coordinator_client = coordinator_client
        self._budget_tracker = budget_tracker
        self._run_store = run_store
        self._telemetry = telemetry
        self._policy_store = policy_store
        self._spacetime_writer = spacetime_writer

    # ------------------------------------------------------------- public

    def dispatch(self, request: DispatchRequest) -> DispatchResponse:
        entry = self._manifest_index.get(request.agent_id)
        if entry is None:
            if not self._manifest_index.is_loaded():
                raise ManifestNotReadyError("no confirmed manifest is loaded yet for this site")
            raise AgentNotFoundError(f"unknown agent_id {request.agent_id!r}", agent_id=request.agent_id)

        run_id = f"run_{uuid.uuid4().hex[:16]}"

        with self._telemetry.span("dispatch", **{"rosterd.agent_id": request.agent_id, "rosterd.run_id": run_id}) as span:
            if not entry.direct_assignable:
                reason = (
                    f"{request.agent_id} is not directly assignable "
                    f"(entry only via: {', '.join(entry.entry_only_via) or 'none'})"
                )
                span.set_attribute("rosterd.rejected", True)
                return DispatchResponse(run_id=run_id, status=DispatchStatus.rejected, reason=reason)

            instance = self._obtain_instance(request.agent_id, entry.scaling.max_replicas)
            if instance is None:
                span.set_attribute("rosterd.rejected", True)
                return DispatchResponse(
                    run_id=run_id,
                    status=DispatchStatus.rejected,
                    reason=(
                        f"pool for {request.agent_id} is at max_replicas "
                        f"({entry.scaling.max_replicas}) and no instance freed up within "
                        f"{self._settings.dispatch_queue_wait_sec}s"
                    ),
                )

            self._run_store.create(run_id, request.agent_id)
            self._run_store.set_current_instance(run_id, instance.instance_id)
            self._budget_tracker.start(run_id)

            self._run_dispatch(run_id, request, entry, instance, span)

        return DispatchResponse(run_id=run_id, status=DispatchStatus.accepted)

    # ------------------------------------------------------------ instances

    def _obtain_instance(self, agent_id: str, max_replicas: int):
        instance = self._registry.pick_idle(agent_id)
        if instance is not None:
            return instance

        if self._registry.current_replicas(agent_id) < max_replicas:
            instance = self._start_instance(agent_id)
            if instance is not None:
                self._registry.set_status(agent_id, instance.instance_id, InstanceStatus.working)
                return instance

        # At cap: wait a bounded amount of time for something to free up
        # rather than rejecting outright. Counted as "queued" the whole
        # time, so the scaler's load figure (and the Monitor sparkline)
        # reflects real demand during the wait, not just in-flight work.
        self._registry.enter_queue(agent_id)
        try:
            deadline = time.monotonic() + self._settings.dispatch_queue_wait_sec
            while time.monotonic() < deadline:
                instance = self._registry.pick_idle(agent_id)
                if instance is not None:
                    return instance
                time.sleep(self._settings.dispatch_queue_poll_interval_sec)
            return None
        finally:
            self._registry.leave_queue(agent_id)

    def _start_instance(self, agent_id: str):
        try:
            instance = self._docker_backend.start_instance(agent_id)
        except Exception:
            logger.exception("failed to start instance for %s during dispatch", agent_id)
            return None
        self._registry.add(instance)
        self._spacetime_writer.write_agent(
            AgentRow(
                site_id=self._settings.site_id,
                agent_id=agent_id,
                instance_id=instance.instance_id,
                name=instance.container_name,
                status="idle",
                updated_at=datetime.now(timezone.utc),
            )
        )
        return instance

    # -------------------------------------------------------------- run

    def _run_dispatch(self, run_id, request: DispatchRequest, entry, instance, span) -> None:
        text = request.task.description or request.task.title
        context = {
            "task_id": request.task.id,
            "title": request.task.title,
            "priority": request.task.priority.value,
            "source": request.task.source,
            "expectation_criteria": request.task.expectation_criteria,
            "assignees": request.assignees,
        }
        base_url = self._docker_backend.invoke_base_url(instance)

        try:
            response = self._demo_agent_client.invoke(
                base_url, entry.node, text, context, timeout=self._settings.dispatch_timeout_sec
            )
        except DemoAgentTimeout:
            self._finish_killed(
                run_id,
                request.agent_id,
                instance,
                Violation(
                    rule="dispatch.timeout",
                    expected=f"<= {self._settings.dispatch_timeout_sec}s",
                    actual="timed out",
                ),
                span,
            )
            return
        except DemoAgentError as exc:
            if self._run_store.is_kill_requested(run_id):
                self._finish_killed(run_id, request.agent_id, instance, None, span, reason="manual_kill")
            else:
                self._finish_killed(
                    run_id,
                    request.agent_id,
                    instance,
                    Violation(rule="dispatch.invoke_error", expected="a valid /invoke response", actual=str(exc)),
                    span,
                )
            return

        self._budget_tracker.record_tool_calls(run_id, len(response.tool_calls))
        self._budget_tracker.finish(run_id)

        violation = self._budget_tracker.check(run_id)
        if violation is None:
            violation = evaluate_all(entry.constraints, response, policy=self._policy_store)

        if violation is not None:
            self._finish_killed(run_id, request.agent_id, instance, violation, span)
            return

        self._registry.set_status(request.agent_id, instance.instance_id, InstanceStatus.idle)
        trace_id = self._telemetry.current_trace_id()
        self._run_store.finish(run_id, status=RunStatus.done, output=response.output, trace_id=trace_id)
        self._coordinator_client.post_event(
            EventRequest(
                site_id=self._settings.site_id,
                run_id=run_id,
                agent_id=request.agent_id,
                status=RunStatus.done,
                trace_id=trace_id,
                timestamp=datetime.now(timezone.utc),
            )
        )

    def _finish_killed(self, run_id, agent_id, instance, violation: Violation | None, span, *, reason: str | None = None) -> None:
        kill_instance(
            instance,
            reason=violation.rule if violation else (reason or "manual_kill"),
            registry=self._registry,
            docker_backend=self._docker_backend,
            spacetime_writer=self._spacetime_writer,
            settings=self._settings,
        )
        if violation is not None:
            mark_violation(span, violation)
        trace_id = self._telemetry.current_trace_id()
        self._run_store.finish(run_id, status=RunStatus.killed, violation=violation, trace_id=trace_id)
        self._coordinator_client.post_event(
            EventRequest(
                site_id=self._settings.site_id,
                run_id=run_id,
                agent_id=agent_id,
                status=RunStatus.killed,
                violation=violation,
                trace_id=trace_id,
                timestamp=datetime.now(timezone.utc),
            )
        )
