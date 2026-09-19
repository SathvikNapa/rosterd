"""POST /agents/{agent_id}/simulate-load: fire N synthetic dispatches at a
configurable rate and let the scaler loop react on its own. This is what
the Federation dashboard's "Simulate flash sale" button calls, not
POST /scale -- the whole point is that the pool count climbing is a real
consequence of real (synthetic) load, not a number someone set by hand.

Each dispatch runs on its own thread so `count` requests genuinely overlap
in flight instead of running one at a time -- firing them sequentially
through Dispatcher.dispatch() (which blocks for the whole invoke call)
would never build up enough concurrent load to pressure the scaler, which
would defeat the entire demo.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

from dispatch import Dispatcher
from kernel import DispatchRequest, SimulateLoadRequest, SimulateLoadResponse, TaskSpec

logger = logging.getLogger("rosterd.kernel.simulate")


def simulate_load(dispatcher: Dispatcher, agent_id: str, request: SimulateLoadRequest, settings) -> SimulateLoadResponse:
    if request.scale_down_after_idle_seconds_override is not None:
        # Per-run override of the demo cooldown, so a specific simulate-load
        # call can force a fast scale-down back even if the env-level
        # override wasn't set. Settings is a frozen dataclass everywhere
        # else in the kernel; this is the one place it's mutated in place,
        # deliberately, since it's a live, global "how fast should the demo
        # look" knob rather than per-request config.
        object.__setattr__(settings, "demo_idle_seconds", request.scale_down_after_idle_seconds_override)

    interval = 1.0 / request.rate_per_second if request.rate_per_second > 0 else 0.0

    def _dispatch_one(i: int) -> None:
        task = TaskSpec(
            id=f"sim-{uuid.uuid4().hex[:8]}",
            title="Simulated load",
            description=f"Synthetic dispatch {i + 1}/{request.count} for {agent_id} (simulate-load)",
        )
        try:
            dispatcher.dispatch(DispatchRequest(agent_id=agent_id, task=task, assignees=["simulator"]))
        except Exception:
            logger.exception("simulated dispatch %d/%d for %s failed", i + 1, request.count, agent_id)

    def _fire_all() -> None:
        for i in range(request.count):
            threading.Thread(target=_dispatch_one, args=(i,), daemon=True, name=f"simulate-{agent_id}-{i}").start()
            if interval:
                time.sleep(interval)

    threading.Thread(target=_fire_all, daemon=True, name=f"simulate-load-{agent_id}").start()
    return SimulateLoadResponse(agent_id=agent_id, dispatched=request.count)
