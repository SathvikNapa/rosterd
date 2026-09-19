"""Regression tests for HttpReducerSpacetimeWriter's wire format.

Confirmed empirically against a real `spacetime start` server while
building rosterd-spacetimedb/: the HTTP reducer-call API rejects a bare
JSON object (`json=args`) and requires a JSON *array* of positional
arguments. These tests pin that down with a mocked transport so a
regression back to the bare-object shape fails loudly instead of only
showing up against a live server.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from spacetime import AgentMetricsRow, AgentRow, HttpReducerSpacetimeWriter


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        spacetimedb_url="http://spacetimedb:3000",
        spacetimedb_module="rosterd",
        spacetimedb_auth_token=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _RecordingTransport(httpx.BaseTransport):
    """Captures every request instead of hitting the network."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={})


@pytest.fixture()
def transport(monkeypatch: pytest.MonkeyPatch) -> _RecordingTransport:
    recorder = _RecordingTransport()

    def fake_post(url, *, json, headers, timeout):
        request = httpx.Request("POST", url, json=json, headers=headers)
        return recorder.handle_request(request)

    monkeypatch.setattr("spacetime.httpx.post", fake_post)
    return recorder


def test_write_agent_sends_a_json_array_with_one_element(transport: _RecordingTransport) -> None:
    writer = HttpReducerSpacetimeWriter(_settings())
    row = AgentRow(
        site_id="site-a",
        agent_id="refund-bot",
        instance_id="inst-1",
        name="refund-bot-1",
        status="idle",
        updated_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_agent(row)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.path.endswith("/call/update_agent_status")
    payload = json.loads(request.content)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["instance_id"] == "inst-1"
    assert payload[0]["site_id"] == "site-a"


def test_write_agent_metrics_sends_a_json_array_with_one_element(transport: _RecordingTransport) -> None:
    writer = HttpReducerSpacetimeWriter(_settings())
    row = AgentMetricsRow(
        site_id="site-a",
        agent_id="refund-bot",
        timestamp=datetime(2026, 9, 19, tzinfo=timezone.utc),
        in_flight_count=2,
        queued_count=1,
        target_concurrency=3,
        current_replicas=2,
        desired_replicas=3,
        min_replicas=1,
        max_replicas=5,
    )

    writer.write_agent_metrics(row)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.path.endswith("/call/record_agent_metrics")
    payload = json.loads(request.content)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["in_flight_count"] == 2


def test_a_spacetimedb_outage_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writes are best-effort -- see the module docstring."""

    def raising_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("spacetime.httpx.post", raising_post)
    writer = HttpReducerSpacetimeWriter(_settings())
    row = AgentRow(
        site_id="site-a",
        agent_id="refund-bot",
        instance_id="inst-1",
        name="refund-bot-1",
        status="idle",
        updated_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_agent(row)  # must not raise
