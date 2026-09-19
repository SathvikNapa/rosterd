"""rosterd kernel service (Person 1).

Endpoints (exactly the contract in the brief):

    POST /dispatch                          accept/reject + run a task
    GET  /runs/{run_id}                      check a run's status
    POST /runs/{run_id}/kill                 force-terminate a run
    POST /policy                             apply a coordinator policy update
    GET  /health                             status + remaining budget
    GET  /agents/{agent_id}/instances        pool size + per-instance status
    POST /agents/{agent_id}/scale            manual override (debug/emergency)
    POST /agents/{agent_id}/simulate-load    fire synthetic load

Plus small additive debug endpoints, same spirit as rosterd-ingestion's own
`/healthz` and `/manifests`: they add no fields to a contracted response.

    GET  /manifest                           the manifest currently governing this site
    GET  /policy                             current policy overrides
    GET  /healthz                            liveness + effective settings

`create_app()` is a factory rather than a single module-level app because,
unlike ingestion, the kernel is genuinely stateful across requests (the
instance registry, the manifest index, the scaler thread) -- tests build
their own isolated Container via `create_app(settings, ...)` instead of
flipping env vars against one shared app.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from budget import BudgetTracker
from config import Settings, get_settings
from coordinator_client import CoordinatorClient
from demo_agent_client import DemoAgentClient
from dispatch import Dispatcher
from docker_backend import DockerBackend, build_docker_backend
from errors import AgentNotFoundError, KernelError, RunNotFoundError
from kernel import (
    DispatchRequest,
    HealthResponse,
    InstancesResponse,
    KillResponse,
    PolicyUpdateRequest,
    PolicyUpdateResponse,
    ScaleRequest,
    ScaleResponse,
    SimulateLoadRequest,
)
from killer import kill as kill_instance
from manifest import ManifestIndex
from manifest_source import IngestionPollManifestSource, ManifestSource, ManifestSubscription, StaticManifestSource
from policy import PolicyStore
from registry import InstanceRegistry
from run_store import RunStore
from scaler import ScalerLoop
from simulate import simulate_load
from spacetime import SpacetimeWriter, build_spacetime_writer
from tracing import Telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("rosterd.kernel.app")


@dataclass
class Container:
    settings: Settings
    telemetry: Telemetry
    manifest_index: ManifestIndex
    manifest_subscription: ManifestSubscription
    registry: InstanceRegistry
    docker_backend: DockerBackend
    spacetime_writer: SpacetimeWriter
    budget_tracker: BudgetTracker
    policy_store: PolicyStore
    run_store: RunStore
    demo_agent_client: DemoAgentClient
    coordinator_client: CoordinatorClient
    dispatcher: Dispatcher
    scaler: ScalerLoop


def build_container(
    settings: Settings,
    *,
    manifest_source: ManifestSource | None = None,
    docker_backend: DockerBackend | None = None,
    spacetime_writer: SpacetimeWriter | None = None,
) -> Container:
    """The composition root. Every collaborator below is overridable purely
    for tests -- production always calls this with only `settings`."""
    telemetry = Telemetry(settings)
    manifest_index = ManifestIndex()
    registry = InstanceRegistry()
    docker_backend = docker_backend or build_docker_backend(settings)
    spacetime_writer = spacetime_writer or build_spacetime_writer(settings)
    budget_tracker = BudgetTracker(settings)
    policy_store = PolicyStore()
    run_store = RunStore()
    demo_agent_client = DemoAgentClient(settings, telemetry)
    coordinator_client = CoordinatorClient(settings, telemetry)

    dispatcher = Dispatcher(
        settings=settings,
        manifest_index=manifest_index,
        registry=registry,
        docker_backend=docker_backend,
        demo_agent_client=demo_agent_client,
        coordinator_client=coordinator_client,
        budget_tracker=budget_tracker,
        run_store=run_store,
        telemetry=telemetry,
        policy_store=policy_store,
        spacetime_writer=spacetime_writer,
    )

    source = manifest_source or (
        IngestionPollManifestSource(settings) if settings.manifest_id else StaticManifestSource(None)
    )
    manifest_subscription = ManifestSubscription(source, manifest_index, settings.manifest_poll_interval_sec)

    scaler = ScalerLoop(
        manifest_index=manifest_index,
        registry=registry,
        docker_backend=docker_backend,
        spacetime_writer=spacetime_writer,
        coordinator_client=coordinator_client,
        budget_tracker=budget_tracker,
        telemetry=telemetry,
        settings=settings,
    )

    return Container(
        settings=settings,
        telemetry=telemetry,
        manifest_index=manifest_index,
        manifest_subscription=manifest_subscription,
        registry=registry,
        docker_backend=docker_backend,
        spacetime_writer=spacetime_writer,
        budget_tracker=budget_tracker,
        policy_store=policy_store,
        run_store=run_store,
        demo_agent_client=demo_agent_client,
        coordinator_client=coordinator_client,
        dispatcher=dispatcher,
        scaler=scaler,
    )


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        container.manifest_subscription.start()
        container.scaler.start()
        container.telemetry.instrument_app(_app)
        logger.info("kernel started: site=%s docker_mode=%s", settings.site_id, settings.docker_mode)
        yield
        container.scaler.stop()
        container.manifest_subscription.stop()

    fastapi_app = FastAPI(
        title=f"rosterd kernel service ({settings.site_id})",
        version="1.0.0",
        description="Dispatches tasks to this site's demo agent pool, enforces the confirmed "
        "manifest's constraints, autoscales, and exports traces + metrics.",
        lifespan=lifespan,
    )
    fastapi_app.state.container = container

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @fastapi_app.exception_handler(KernelError)
    async def handle_kernel_error(_: Request, exc: KernelError) -> JSONResponse:
        logging.getLogger("rosterd.kernel").warning("%s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    # ----------------------------------------------------------- contract

    @fastapi_app.post("/dispatch")
    def post_dispatch(request: DispatchRequest):
        return container.dispatcher.dispatch(request)

    @fastapi_app.get("/runs/{run_id}")
    def get_run(run_id: str):
        response = container.run_store.get(run_id)
        if response is None:
            raise RunNotFoundError(f"no run with id {run_id!r}", run_id=run_id)
        return response

    @fastapi_app.post("/runs/{run_id}/kill")
    def post_kill_run(run_id: str) -> KillResponse:
        located = container.run_store.instance_for(run_id)
        if located is None:
            raise RunNotFoundError(f"no run with id {run_id!r}", run_id=run_id)
        agent_id, instance_id = located
        container.run_store.request_kill(run_id)

        instance = container.registry.find(agent_id, instance_id) if instance_id else None
        if instance is not None:
            kill_instance(
                instance,
                reason="manual_kill",
                registry=container.registry,
                docker_backend=container.docker_backend,
                spacetime_writer=container.spacetime_writer,
                settings=settings,
            )
        container.run_store.force_kill(run_id, reason="manual kill requested via POST /runs/{run_id}/kill")
        return KillResponse(run_id=run_id, reason="manual kill requested")

    @fastapi_app.post("/policy")
    def post_policy(request: PolicyUpdateRequest) -> PolicyUpdateResponse:
        container.policy_store.apply(request)
        return PolicyUpdateResponse(applied=True)

    @fastapi_app.get("/health")
    def get_health() -> HealthResponse:
        remaining = container.budget_tracker.remaining_budget()
        status = "ok" if container.manifest_index.is_loaded() and remaining > 0.1 else "degraded"
        return HealthResponse(status=status, budget_remaining=remaining)

    @fastapi_app.get("/agents/{agent_id}/instances")
    def get_instances(agent_id: str) -> InstancesResponse:
        if container.manifest_index.is_loaded() and container.manifest_index.get(agent_id) is None:
            raise AgentNotFoundError(f"unknown agent_id {agent_id!r}", agent_id=agent_id)
        return InstancesResponse(agent_id=agent_id, instances=container.registry.pool(agent_id))

    @fastapi_app.post("/agents/{agent_id}/scale")
    def post_scale(agent_id: str, request: ScaleRequest) -> ScaleResponse:
        current = container.registry.current_replicas(agent_id)
        target = max(0, request.target_replicas)
        if target > current:
            for _ in range(target - current):
                try:
                    instance = container.docker_backend.start_instance(agent_id)
                except Exception:
                    logger.exception("manual scale-up failed for %s", agent_id)
                    break
                container.registry.add(instance)
        elif target < current:
            for instance in container.registry.idle_instances(agent_id)[: current - target]:
                kill_instance(
                    instance,
                    reason="manual_scale",
                    registry=container.registry,
                    docker_backend=container.docker_backend,
                    spacetime_writer=container.spacetime_writer,
                    settings=settings,
                )
        return ScaleResponse(agent_id=agent_id, replicas=container.registry.current_replicas(agent_id))

    @fastapi_app.post("/agents/{agent_id}/simulate-load")
    def post_simulate_load(agent_id: str, request: SimulateLoadRequest):
        return simulate_load(container.dispatcher, agent_id, request, settings)

    # ------------------------------------------------------------- debug

    @fastapi_app.get("/manifest")
    def get_manifest_debug() -> dict:
        return {
            "manifest_id": container.manifest_index.manifest_id,
            "loaded": container.manifest_index.is_loaded(),
            "agents": [entry.model_dump(mode="json") for entry in container.manifest_index.all_entries()],
        }

    @fastapi_app.get("/policy")
    def get_policy_debug() -> dict:
        return container.policy_store.all()

    @fastapi_app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "site_id": settings.site_id,
            "docker_mode": settings.docker_mode,
            "manifest_loaded": container.manifest_index.is_loaded(),
        }

    return fastapi_app


app = create_app()
