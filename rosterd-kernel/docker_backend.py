"""Container-level control of demo-agent instances.

Both backends below dispatch to a genuinely real, running demo-agent --
real HTTP calls, real graph execution, real LLM calls when a provider key
is configured. Neither backend is a mock of *the agent*. What differs
between them is only the layer underneath the agent: whether an "instance"
in the pool corresponds to an actual Docker container or to an in-memory
bookkeeping record pointed at one already-running process.

`SimulatedDockerBackend` is the default (`ROSTERD_KERNEL_DOCKER_MODE=simulated`):
every "instance" is an in-memory record, and every instance's /invoke
target is the same configured URL (`ROSTERD_KERNEL_SIMULATED_AGENT_URL`).
That's enough to run the whole kernel -- dispatch, constraints, budget, the
scaler, the demo endpoints -- against one locally-running demo-agent
process (or a test double) with no Docker daemon and no image built yet,
which matters this early in a hackathon build; it simulates *scaling*
(replica count going up and down), not the agent doing the work.
`RealDockerBackend` is the real thing via the `docker` SDK, for when
Shruti's image and the site network both exist -- there, "instance" means
an actual container.

Kill is always `container.kill()` (SIGKILL), never a graceful stop -- the
brief is explicit that this is container-level, not a cooperative timeout,
because an agent that ignored its own guardrails can't be trusted to honor
a graceful shutdown signal either.
"""
from __future__ import annotations

import itertools
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

from kernel import AgentInstance, InstanceStatus

logger = logging.getLogger("rosterd.kernel.docker_backend")


class DockerBackend(Protocol):
    def start_instance(self, agent_id: str) -> AgentInstance: ...

    def kill_instance(self, instance: AgentInstance) -> None: ...

    def invoke_base_url(self, instance: AgentInstance) -> str: ...


class SimulatedDockerBackend:
    """Simulates the instance *pool* -- no real container is ever started or
    killed. Every simulated instance still forwards real invocations to a
    real demo-agent process at `simulated_agent_url`; nothing about the
    agent's own execution is simulated."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._seq = itertools.count(1)

    def start_instance(self, agent_id: str) -> AgentInstance:
        n = next(self._seq)
        instance_id = f"sim-{agent_id}-{n}"
        return AgentInstance(
            instance_id=instance_id,
            agent_id=agent_id,
            # No "rosterd-"/"sim-" prefix here on purpose, unlike instance_id
            # above -- this is the display name a person actually reads (the
            # agents table's container_name column), and "fake"/"sim" baked
            # into it read like the AGENT was fake, not just the pool
            # bookkeeping underneath it (see docker_backend.py's own module
            # docstring on that distinction). site_id/agent_id/n is already
            # unique among simulated instances without a mode marker; a real
            # Docker container name still gets one below, where it's a
            # genuine host-level namespace, not just a display string.
            container_name=f"{self._settings.site_id}-{agent_id}-{n}",
            status=InstanceStatus.idle,
            started_at=datetime.now(timezone.utc),
        )

    def kill_instance(self, instance: AgentInstance) -> None:
        logger.info("simulated-kill instance=%s (no real container to stop)", instance.instance_id)

    def invoke_base_url(self, instance: AgentInstance) -> str:
        return self._settings.simulated_agent_url


class RealDockerBackend:
    def __init__(self, settings) -> None:
        import docker  # imported lazily: only needed in real mode

        self._docker = docker
        self._client = docker.from_env()
        self._settings = settings

    def _image_for(self, agent_id: str) -> str:
        return self._settings.agent_image_map.get(agent_id, self._settings.default_agent_image)

    def start_instance(self, agent_id: str) -> AgentInstance:
        image = self._image_for(agent_id)
        name = f"rosterd-{self._settings.site_id}-{agent_id}-{uuid.uuid4().hex[:8]}"
        container = self._client.containers.run(
            image,
            name=name,
            network=self._settings.docker_network,
            detach=True,
            environment={"ROSTERD_SITE_ID": self._settings.site_id, "ROSTERD_AGENT_ID": agent_id},
            labels={"rosterd.site_id": self._settings.site_id, "rosterd.agent_id": agent_id},
        )
        return AgentInstance(
            instance_id=container.id,
            agent_id=agent_id,
            container_name=name,
            status=InstanceStatus.idle,
            started_at=datetime.now(timezone.utc),
        )

    def kill_instance(self, instance: AgentInstance) -> None:
        try:
            container = self._client.containers.get(instance.instance_id)
        except self._docker.errors.NotFound:
            return
        try:
            container.kill()
        except self._docker.errors.APIError:
            logger.warning("kill on already-stopped container %s", instance.instance_id, exc_info=True)
        container.remove(force=True)

    def invoke_base_url(self, instance: AgentInstance) -> str:
        return f"http://{instance.container_name}:{self._settings.demo_agent_port}"


def build_docker_backend(settings) -> DockerBackend:
    if settings.docker_mode == "real":
        return RealDockerBackend(settings)
    return SimulatedDockerBackend(settings)
