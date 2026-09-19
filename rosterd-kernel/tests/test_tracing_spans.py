"""The brief names three custom spans explicitly: "add custom spans for
`dispatch` (root span for the whole call chain), `constraint_check`, and
`scale_decision`". `constraint_check` was missing entirely until this
file's addition caught it (found by reading dispatch.py's span calls
side-by-side with the brief, not by any test failing) -- this asserts the
exact span names a dispatch and a scaler tick actually request, so a
regression here fails loudly instead of just silently under-instrumenting
again.
"""
from __future__ import annotations

from conftest import FakeHttpxResponse


def test_a_successful_dispatch_opens_dispatch_and_constraint_check_spans(client, container, fake_demo_agent):
    fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, {"output": "ok", "tool_calls": []}))

    span_names = []
    real_span = container.telemetry.span

    def spy_span(name, **attrs):
        span_names.append(name)
        return real_span(name, **attrs)

    container.telemetry.span = spy_span

    response = client.post(
        "/dispatch",
        json={
            "agent_id": "fulfillment",
            "task": {"id": "t1", "title": "test", "description": "reserve inventory"},
            "assignees": ["me"],
        },
    )
    assert response.status_code == 200
    assert "dispatch" in span_names
    assert "constraint_check" in span_names
    assert "invoke_agent" in span_names


def test_a_scaler_tick_opens_a_scale_decision_span(container):
    span_names = []
    real_span = container.telemetry.span

    def spy_span(name, **attrs):
        span_names.append(name)
        return real_span(name, **attrs)

    container.telemetry.span = spy_span
    container.manifest_subscription.poll_once()
    container.scaler.tick()

    assert "scale_decision" in span_names
