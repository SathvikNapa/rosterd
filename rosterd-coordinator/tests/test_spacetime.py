"""Regression tests for HttpReducerSpacetimeWriter's wire format.

Same pattern as rosterd-kernel/tests/test_spacetime.py -- see its docstring
for the array-wrapping rationale. This file additionally pins down the
Option sum-type translation (`_wrap_option`), the one thing the coordinator
needs that the kernel doesn't: `SiteSummary.last_event` and
`EventLogEntry.run_id` / `.violation` / `.trace_id` are all `X | None`, and
a *present* value must be sent as `{"some": value}` on the wire, not bare --
confirmed via a live round trip against a real `spacetime start` server.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from coordinator import EventLogEntry, SiteSummary
from spacetime import HttpReducerSpacetimeWriter, _wrap_option


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        spacetimedb_url="http://spacetimedb:3000",
        spacetimedb_module="rosterd",
        spacetimedb_auth_token=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _RecordingTransport:
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


def test_wrap_option_leaves_none_as_bare_null() -> None:
    assert _wrap_option(None) is None


def test_wrap_option_wraps_a_present_value_as_some() -> None:
    assert _wrap_option("run-1") == {"some": "run-1"}
    assert _wrap_option({"rule": "x", "expected": "1", "actual": "2"}) == {
        "some": {"rule": "x", "expected": "1", "actual": "2"}
    }


def test_write_site_sends_a_json_array_and_wraps_last_event(transport: _RecordingTransport) -> None:
    writer = HttpReducerSpacetimeWriter(_settings())
    row = SiteSummary(
        site_id="site-a",
        status="healthy",
        last_event=datetime(2026, 9, 19, tzinfo=timezone.utc),
        score=0.95,
    )

    writer.write_site(row)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.path.endswith("/call/update_site_score")
    payload = json.loads(request.content)
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["site_id"] == "site-a"
    assert payload[0]["last_event"] == {"some": "2026-09-19T00:00:00Z"}


def test_write_site_sends_bare_null_when_last_event_is_absent(transport: _RecordingTransport) -> None:
    writer = HttpReducerSpacetimeWriter(_settings())
    row = SiteSummary(site_id="site-b", status="offline", last_event=None, score=0.0)

    writer.write_site(row)

    payload = json.loads(transport.requests[0].content)
    assert payload[0]["last_event"] is None


def test_write_event_wraps_all_three_optional_fields(transport: _RecordingTransport) -> None:
    writer = HttpReducerSpacetimeWriter(_settings())
    row = EventLogEntry(
        site_id="site-a",
        run_id="run-1",
        status="killed",
        violation={"rule": "tool_calls[*].args.amount_usd lte 100", "expected": "<=100", "actual": "5000"},
        trace_id="trace-1",
        timestamp=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_event(row)

    payload = json.loads(transport.requests[0].content)
    assert transport.requests[0].url.path.endswith("/call/record_event")
    assert payload[0]["run_id"] == {"some": "run-1"}
    assert payload[0]["trace_id"] == {"some": "trace-1"}
    assert payload[0]["violation"] == {
        "some": {"rule": "tool_calls[*].args.amount_usd lte 100", "expected": "<=100", "actual": "5000"}
    }


def test_write_event_sends_bare_nulls_when_optionals_are_absent(transport: _RecordingTransport) -> None:
    writer = HttpReducerSpacetimeWriter(_settings())
    row = EventLogEntry(
        site_id="site-a",
        run_id=None,
        status="scaled_up",
        violation=None,
        trace_id=None,
        timestamp=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_event(row)

    payload = json.loads(transport.requests[0].content)
    assert payload[0]["run_id"] is None
    assert payload[0]["violation"] is None
    assert payload[0]["trace_id"] is None


def test_a_spacetimedb_outage_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("spacetime.httpx.post", raising_post)
    writer = HttpReducerSpacetimeWriter(_settings())
    row = SiteSummary(site_id="site-a", status="healthy", last_event=None, score=1.0)

    writer.write_site(row)  # must not raise
