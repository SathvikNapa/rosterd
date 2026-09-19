"""The kill switch: container-level kill via the docker backend, reused by
both dispatch.py (violation / timeout / budget breach) and scaler.py (idle
scale-down) so there is exactly one place a container actually dies and
exactly one place the `agents` row gets marked killed.

"Restart-for-next-dispatch" is implemented lazily, not proactively: `kill`
removes the instance from the registry, and the very next thing that needs
capacity for this agent_id -- the next POST /dispatch, or the next scaler
tick if the pool has fallen under min_replicas -- spins a fresh instance up
through the normal pool-growth path. There's no separate "replace this
exact instance" codepath to keep in sync with that one.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from docker_backend import DockerBackend
from kernel import AgentInstance
from registry import InstanceRegistry
from spacetime import AgentRow, SpacetimeWriter

logger = logging.getLogger("rosterd.kernel.killer")


def kill(
    instance: AgentInstance,
    *,
    reason: str,
    registry: InstanceRegistry,
    docker_backend: DockerBackend,
    spacetime_writer: SpacetimeWriter,
    settings,
) -> None:
    registry.remove(instance.agent_id, instance.instance_id)
    try:
        docker_backend.kill_instance(instance)
    except Exception:  # noqa: BLE001 - the row still needs to be marked killed either way
        logger.exception("docker kill failed for instance=%s reason=%s", instance.instance_id, reason)

    spacetime_writer.write_agent(
        AgentRow(
            site_id=settings.site_id,
            agent_id=instance.agent_id,
            instance_id=instance.instance_id,
            name=instance.container_name,
            status="killed",
            updated_at=datetime.now(timezone.utc),
        )
    )
    logger.info("killed instance=%s agent=%s reason=%s", instance.instance_id, instance.agent_id, reason)
