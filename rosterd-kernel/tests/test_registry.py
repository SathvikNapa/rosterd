"""InstanceRegistry: pool bookkeeping and queue counters."""
from __future__ import annotations

from datetime import datetime, timezone

from kernel import AgentInstance, InstanceStatus
from registry import InstanceRegistry


def make_instance(agent_id="fulfillment", instance_id="i1", status=InstanceStatus.idle) -> AgentInstance:
    return AgentInstance(
        instance_id=instance_id,
        agent_id=agent_id,
        container_name=f"c-{instance_id}",
        status=status,
        started_at=datetime.now(timezone.utc),
    )


def test_pick_idle_claims_the_instance_atomically():
    registry = InstanceRegistry()
    registry.add(make_instance(instance_id="i1"))

    picked = registry.pick_idle("fulfillment")
    assert picked is not None
    assert picked.status == InstanceStatus.working
    # Nothing left idle to pick a second time.
    assert registry.pick_idle("fulfillment") is None


def test_current_and_working_counts():
    registry = InstanceRegistry()
    registry.add(make_instance(instance_id="i1", status=InstanceStatus.idle))
    registry.add(make_instance(instance_id="i2", status=InstanceStatus.working))
    assert registry.current_replicas("fulfillment") == 2
    assert registry.working_count("fulfillment") == 1
    assert len(registry.idle_instances("fulfillment")) == 1


def test_remove_drops_the_instance_from_the_pool():
    registry = InstanceRegistry()
    registry.add(make_instance(instance_id="i1"))
    removed = registry.remove("fulfillment", "i1")
    assert removed is not None
    assert registry.current_replicas("fulfillment") == 0
    assert registry.remove("fulfillment", "i1") is None


def test_queue_counters():
    registry = InstanceRegistry()
    assert registry.queued_count("fulfillment") == 0
    registry.enter_queue("fulfillment")
    registry.enter_queue("fulfillment")
    assert registry.queued_count("fulfillment") == 2
    registry.leave_queue("fulfillment")
    assert registry.queued_count("fulfillment") == 1
