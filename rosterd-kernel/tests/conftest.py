"""Shared fixtures.

Every test builds its own isolated Container (simulated docker backend --
no real container, but every dispatch still forwards to a real HTTP demo
agent double via fake_demo_agent below -- plus a StaticManifestSource
seeded in-process, no live ingestion/coordinator/SpacetimeDB needed) rather
than sharing one module-level app, since the kernel is stateful across
requests -- see app.py's module docstring.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app import build_container, create_app
from config import Settings
from manifest import (
    AgentManifestEntry,
    ConstraintRule,
    ConstraintSource,
    Confidence,
    ManifestDocument,
    ScalingPolicy,
)
from manifest_source import StaticManifestSource


def make_settings(**overrides) -> Settings:
    base = dict(
        site_id="test-site",
        docker_mode="simulated",
        otel_enabled=False,  # no collector in tests; also skips SDK setup entirely
        dispatch_timeout_sec=2.0,
        dispatch_queue_wait_sec=0.5,
        dispatch_queue_poll_interval_sec=0.05,
        max_tool_calls_per_run=5,
        max_run_seconds=60.0,
        scaler_interval_sec=3600.0,  # background loop effectively never ticks mid-test
        manifest_poll_interval_sec=3600.0,
        coordinator_url="http://localhost:9999",  # closed port: fails fast, swallowed
    )
    base.update(overrides)
    return Settings(**base)


FULFILLMENT = AgentManifestEntry(
    id="fulfillment",
    node="fulfillment_node",
    purpose="Reserves inventory",
    tools=["reserve_inventory"],
    direct_assignable=True,
    entry_only_via=[],
    constraints=[
        ConstraintRule(
            field="tool_calls[*].args.qty",
            op="lte",
            value=50,
            source=ConstraintSource.schema,
            confidence=Confidence.high,
        )
    ],
    scaling=ScalingPolicy(min_replicas=0, max_replicas=2, target_concurrency=1, scale_down_after_idle_seconds=1),
)

REFUND = AgentManifestEntry(
    id="refund",
    node="refund_exception_node",
    purpose="Issues refunds",
    tools=["issue_refund"],
    direct_assignable=False,
    entry_only_via=["fulfillment_node"],
    constraints=[
        ConstraintRule(
            field="tool_calls[*].args.amount",
            op="lte",
            value=100,
            source=ConstraintSource.schema,
            confidence=Confidence.high,
        )
    ],
    scaling=ScalingPolicy(min_replicas=0, max_replicas=1, target_concurrency=1, scale_down_after_idle_seconds=1),
)


def make_manifest(*entries: AgentManifestEntry) -> ManifestDocument:
    return ManifestDocument(manifest_id="mf_test", agents=list(entries or (FULFILLMENT, REFUND)))


class FakeHttpxResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


class _HttpxStub:
    """Forwards everything to the real httpx module except `post`, so
    `except httpx.TimeoutException` / `httpx.HTTPError` in the modules under
    test keep working unmodified."""

    def __init__(self, post):
        self.post = post

    def __getattr__(self, name):
        return getattr(httpx, name)


@pytest.fixture
def fake_demo_agent(monkeypatch):
    """Default handler: always succeeds with no tool calls. Tests override
    via `fake_demo_agent.handler = fn`."""

    state = {"handler": lambda payload: FakeHttpxResponse(200, {"output": "ok", "tool_calls": []})}

    def _post(url, json=None, headers=None, timeout=None):  # noqa: A002
        return state["handler"](json)

    import demo_agent_client

    monkeypatch.setattr(demo_agent_client, "httpx", _HttpxStub(_post))

    class Handle:
        def set(self, fn):
            state["handler"] = fn

    return Handle()


@pytest.fixture
def container(fake_demo_agent):
    settings = make_settings()
    source = StaticManifestSource(make_manifest())
    return build_container(settings, manifest_source=source)


@pytest.fixture
def client(container):
    app = create_app(container.settings, container=container)
    with TestClient(app) as test_client:
        yield test_client
