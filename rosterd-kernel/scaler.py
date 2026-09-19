"""The scaler loop.

`compute_desired_replicas` is the pure formula the Monitor screen spells
out live ("load: 8, target: 2, desired: ceil(8/2) = 4, clamped to max 4"),
kept separate from ScalerLoop so it's trivially unit-testable.

`ScalerLoop.tick()` runs once per agent_id in the current manifest, every
`scaler_interval_sec`, on a background daemon thread started from app.py's
lifespan. Every tick, for every agent, it writes a full AgentMetricsRow
regardless of whether anything changed -- that's the brief's explicit
requirement, and it's what the Monitor screen's sparkline needs a
continuous line, not just change events. An AgentRow write only happens
when the pool itself actually changes (a new instance up, or one killed).
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone

from coordinator_client import CoordinatorClient, EventRequest
from killer import kill as kill_instance
from manifest import ManifestIndex, ScalingPolicy
from registry import InstanceRegistry
from spacetime import AgentMetricsRow, AgentRow, SpacetimeWriter

logger = logging.getLogger("rosterd.kernel.scaler")


def compute_desired_replicas(load: int, target_concurrency: int, min_replicas: int, max_replicas: int) -> int:
    """desired_replicas = clamp(ceil(load / target_concurrency), min, max)."""
    concurrency = target_concurrency if target_concurrency > 0 else 1
    desired = math.ceil(load / concurrency) if load > 0 else 0
    return max(min_replicas, min(desired, max_replicas))


class ScalerLoop:
    def __init__(
        self,
        *,
        manifest_index: ManifestIndex,
        registry: InstanceRegistry,
        docker_backend,
        spacetime_writer: SpacetimeWriter,
        coordinator_client: CoordinatorClient,
        budget_tracker,
        telemetry,
        settings,
    ) -> None:
        self._manifest_index = manifest_index
        self._registry = registry
        self._docker_backend = docker_backend
        self._spacetime_writer = spacetime_writer
        self._coordinator_client = coordinator_client
        self._budget_tracker = budget_tracker
        self._telemetry = telemetry
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _effective_idle_seconds(self, policy: ScalingPolicy) -> int:
        # Demo-friendly override: short enough to watch scale-down happen
        # live instead of waiting through a realistic production cooldown.
        if self._settings.demo_idle_seconds is not None:
            return self._settings.demo_idle_seconds
        return policy.scale_down_after_idle_seconds

    def tick(self) -> None:
        for agent_id in self._manifest_index.agent_ids():
            entry = self._manifest_index.get(agent_id)
            if entry is not None:
                self._tick_agent(agent_id, entry.scaling)
        self._telemetry.record_budget_remaining(self._budget_tracker.remaining_budget())

    def _start_instance(self, agent_id: str) -> bool:
        try:
            instance = self._docker_backend.start_instance(agent_id)
        except Exception:
            logger.exception("failed to start instance for %s", agent_id)
            return False
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
        return True

    def _tick_agent(self, agent_id: str, policy: ScalingPolicy) -> None:
        with self._telemetry.span("scale_decision", **{"rosterd.agent_id": agent_id}) as span:
            working = self._registry.working_count(agent_id)
            queued = self._registry.queued_count(agent_id)
            load = working + queued
            current = self._registry.current_replicas(agent_id)
            desired = compute_desired_replicas(load, policy.target_concurrency, policy.min_replicas, policy.max_replicas)

            span.set_attribute("rosterd.load", load)
            span.set_attribute("rosterd.current_replicas", current)
            span.set_attribute("rosterd.desired_replicas", desired)

            changed = False
            if desired > current:
                for _ in range(desired - current):
                    changed = self._start_instance(agent_id) or changed
            elif desired < current:
                idle_seconds = self._effective_idle_seconds(policy)
                now = datetime.now(timezone.utc)
                candidates = sorted(
                    (
                        i
                        for i in self._registry.idle_instances(agent_id)
                        if (now - i.started_at).total_seconds() >= idle_seconds
                    ),
                    key=lambda i: i.started_at,
                )
                # NOTE: AgentInstance (kernel.py) carries `started_at`, not a
                # separate "went idle at" timestamp, so idleness here is
                # approximated from instance age rather than true idle
                # duration. Fine for the demo's short (15-30s) cooldown; a
                # longer-running deployment would want a real idle-since
                # field on AgentInstance.
                for instance in candidates[: current - desired]:
                    kill_instance(
                        instance,
                        reason="scale_down_idle",
                        registry=self._registry,
                        docker_backend=self._docker_backend,
                        spacetime_writer=self._spacetime_writer,
                        settings=self._settings,
                    )
                    changed = True

            final_current = self._registry.current_replicas(agent_id)
            self._spacetime_writer.write_agent_metrics(
                AgentMetricsRow(
                    site_id=self._settings.site_id,
                    agent_id=agent_id,
                    timestamp=datetime.now(timezone.utc),
                    in_flight_count=working,
                    queued_count=queued,
                    target_concurrency=policy.target_concurrency,
                    current_replicas=final_current,
                    desired_replicas=desired,
                    min_replicas=policy.min_replicas,
                    max_replicas=policy.max_replicas,
                )
            )
            self._telemetry.record_scale_tick(agent_id, load, final_current, desired)

            if changed:
                self._coordinator_client.post_event(
                    EventRequest(
                        site_id=self._settings.site_id,
                        agent_id=agent_id,
                        status="scaled_up" if final_current > current else "scaled_down",
                        pool_size=final_current,
                        trace_id=self._telemetry.current_trace_id(),
                        timestamp=datetime.now(timezone.utc),
                    )
                )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("scaler tick failed")
            self._stop.wait(self._settings.scaler_interval_sec)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="scaler-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
