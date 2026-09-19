"""The instance registry: `instances: dict[str, list[AgentInstance]]`, per
the design, plus the queue counters dispatch and the scaler share so
"load" means the same thing (in-flight + queued) everywhere it's read.

One registry per process, thread-safe -- FastAPI gives each sync request
its own thread, and the scaler runs on a background thread too.
"""
from __future__ import annotations

import threading
from collections import Counter

from kernel import AgentInstance, InstanceStatus


class InstanceRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.instances: dict[str, list[AgentInstance]] = {}
        self._queued: Counter[str] = Counter()

    # ------------------------------------------------------------- pool

    def pool(self, agent_id: str) -> list[AgentInstance]:
        with self._lock:
            return list(self.instances.get(agent_id, []))

    def add(self, instance: AgentInstance) -> None:
        with self._lock:
            self.instances.setdefault(instance.agent_id, []).append(instance)

    def remove(self, agent_id: str, instance_id: str) -> AgentInstance | None:
        with self._lock:
            pool = self.instances.get(agent_id, [])
            for i, instance in enumerate(pool):
                if instance.instance_id == instance_id:
                    return pool.pop(i)
            return None

    def find(self, agent_id: str, instance_id: str) -> AgentInstance | None:
        with self._lock:
            for instance in self.instances.get(agent_id, []):
                if instance.instance_id == instance_id:
                    return instance
            return None

    def set_status(self, agent_id: str, instance_id: str, status: InstanceStatus) -> None:
        with self._lock:
            instance = self.find(agent_id, instance_id)
            if instance is not None:
                instance.status = status

    def pick_idle(self, agent_id: str) -> AgentInstance | None:
        """Claims the instance (flips it to working) atomically with the
        search, so two concurrent dispatches can never both pick the same
        idle instance."""
        with self._lock:
            for instance in self.instances.get(agent_id, []):
                if instance.status == InstanceStatus.idle:
                    instance.status = InstanceStatus.working
                    return instance
            return None

    def idle_instances(self, agent_id: str) -> list[AgentInstance]:
        with self._lock:
            return [i for i in self.instances.get(agent_id, []) if i.status == InstanceStatus.idle]

    def current_replicas(self, agent_id: str) -> int:
        with self._lock:
            return len(self.instances.get(agent_id, []))

    def working_count(self, agent_id: str) -> int:
        with self._lock:
            return sum(1 for i in self.instances.get(agent_id, []) if i.status == InstanceStatus.working)

    # ------------------------------------------------------------- queue

    def enter_queue(self, agent_id: str) -> None:
        with self._lock:
            self._queued[agent_id] += 1

    def leave_queue(self, agent_id: str) -> None:
        with self._lock:
            if self._queued[agent_id] > 0:
                self._queued[agent_id] -= 1

    def queued_count(self, agent_id: str) -> int:
        with self._lock:
            return self._queued.get(agent_id, 0)
