"""rosterd coordinator service (Person 3, coordinator half).

Endpoints (exactly the contract in the brief):

    POST /events          kernel posts a run or scale event, may get a policy_update back
    GET  /sites            per-site status and score
    POST /policy/push      push a policy update to every known kernel

Plus small additive debug endpoints, same spirit as the other two
services' own `/healthz` and debug routes -- none add a field to a
contracted response.

    GET  /events            recent event log (the frontend subscribes to SpacetimeDB instead)
    GET  /healthz            liveness + effective settings

`create_app()` is a factory (not a bare module-level app) for the same
reason as the kernel's: this service is stateful across requests (site
health, the pattern detector's sighting windows, the background sweeper),
so tests build their own isolated Container instead of sharing one process
-wide app and flipping env vars.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from broadcaster import PolicyBroadcaster
from config import Settings, get_settings
from coordinator import EventLogEntry, EventRequest, EventResponse, PolicyPushRequest, PolicyUpdate, SiteSummary
from patterns import PatternDetector
from pydantic import BaseModel
from sites import SiteRegistry
from spacetime import SpacetimeWriter, build_spacetime_writer
from store import EventStore
from sweeper import OfflineSweeper
from tracing import Telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("rosterd.coordinator.app")


class PolicyPushResponse(BaseModel):
    """Not part of the brief's coordinator.py (POST /policy/push has a
    request shape but no specified response), so this lives here rather
    than in coordinator.py -- it adds a field to nothing contracted."""

    pushed: dict[str, bool]


@dataclass
class Container:
    settings: Settings
    telemetry: Telemetry
    site_registry: SiteRegistry
    pattern_detector: PatternDetector
    broadcaster: PolicyBroadcaster
    event_store: EventStore
    spacetime_writer: SpacetimeWriter
    sweeper: OfflineSweeper


def build_container(settings: Settings, *, spacetime_writer: SpacetimeWriter | None = None) -> Container:
    telemetry = Telemetry(settings)
    site_registry = SiteRegistry(settings)
    pattern_detector = PatternDetector(settings)
    broadcaster = PolicyBroadcaster(settings, telemetry)
    event_store = EventStore()
    spacetime_writer = spacetime_writer or build_spacetime_writer(settings)
    sweeper = OfflineSweeper(
        registry=site_registry, spacetime_writer=spacetime_writer, interval_sec=settings.site_offline_sweep_interval_sec
    )
    return Container(
        settings=settings,
        telemetry=telemetry,
        site_registry=site_registry,
        pattern_detector=pattern_detector,
        broadcaster=broadcaster,
        event_store=event_store,
        spacetime_writer=spacetime_writer,
        sweeper=sweeper,
    )


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        container.sweeper.start()
        logger.info("coordinator started: known kernels=%s", list(settings.site_kernels))
        yield
        container.sweeper.stop()

    fastapi_app = FastAPI(
        title="rosterd coordinator service",
        version="1.0.0",
        description="Federation layer: receives run/scale events from every site's kernel, "
        "tracks site health, and pushes a policy update to every kernel when a shared "
        "failure pattern is detected across sites.",
        lifespan=lifespan,
    )
    fastapi_app.state.container = container

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Must happen at app-construction time, not inside lifespan startup --
    # see rosterd-kernel/app.py's comment at the same call for why
    # (Starlette builds its middleware stack on the first ASGI call, which
    # is the lifespan startup event itself).
    container.telemetry.instrument_app(fastapi_app)

    # ----------------------------------------------------------- contract

    @fastapi_app.post("/events")
    def post_events(event: EventRequest, request: Request) -> EventResponse:
        with container.telemetry.span_from_headers(
            "handle_event", dict(request.headers), **{"rosterd.site_id": event.site_id, "rosterd.agent_id": event.agent_id}
        ) as span:
            summary = container.site_registry.record_event(event)
            container.spacetime_writer.write_site(summary)

            entry = EventLogEntry(
                site_id=event.site_id,
                run_id=event.run_id,
                status=event.status,
                violation=event.violation,
                trace_id=event.trace_id,
                timestamp=event.timestamp,
            )
            container.event_store.append(entry)
            container.spacetime_writer.write_event(entry)

            policy_update = None
            if event.violation is not None:
                match = container.pattern_detector.record_violation(event.violation.rule, event.site_id, event.timestamp)
                if match is not None:
                    span.set_attribute("rosterd.pattern_detected", True)
                    span.set_attribute("rosterd.pattern_distinct_sites", match.distinct_sites)
                    policy_update = PolicyUpdate(rule=match.rule_key, value=match.value)
                    logger.info("%s -> pushing to every other known kernel", match.reason)
                    container.broadcaster.push(match.rule_key, match.value, match.reason, exclude_site=event.site_id)

            return EventResponse(received=True, policy_update=policy_update)

    @fastapi_app.get("/sites")
    def get_sites() -> list[SiteSummary]:
        return container.site_registry.list_sites()

    @fastapi_app.post("/policy/push")
    def post_policy_push(request: PolicyPushRequest) -> PolicyPushResponse:
        pushed = container.broadcaster.push(request.rule, request.value, request.reason)
        return PolicyPushResponse(pushed=pushed)

    # ------------------------------------------------------------- debug

    @fastapi_app.get("/events")
    def get_events_debug(site_id: str | None = None, limit: int = 100) -> list[EventLogEntry]:
        return container.event_store.list_all(site_id=site_id, limit=limit)

    @fastapi_app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "known_kernels": list(settings.site_kernels),
            "sites_seen": len(container.site_registry.list_sites()),
            "server_time": datetime.now(timezone.utc).isoformat(),
        }

    return fastapi_app


app = create_app()
