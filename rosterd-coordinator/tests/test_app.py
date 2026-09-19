"""End-to-end HTTP contract tests against a real FastAPI app."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from conftest import SpySpacetimeWriter


def event(site_id="site-a", status="done", violation=None, run_id="r1"):
    payload = {
        "site_id": site_id,
        "run_id": run_id,
        "agent_id": "fulfillment",
        "status": status,
        # A live timestamp, not a fixed one: the registry computes `offline`
        # from (now - last_event), so a stale hardcoded value would make a
        # freshly-posted "done" event register as offline.
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if violation is not None:
        payload["violation"] = violation
    return payload


class TestEventsHealthyPath:
    def test_a_normal_completion_is_received_with_no_policy_update(self, client):
        response = client.post("/events", json=event(status="done"))
        assert response.status_code == 200
        body = response.json()
        assert body["received"] is True
        assert body["policy_update"] is None

    def test_the_site_shows_up_healthy_afterward(self, client):
        client.post("/events", json=event(status="done"))
        sites = client.get("/sites").json()
        assert len(sites) == 1
        assert sites[0]["site_id"] == "site-a"
        assert sites[0]["status"] == "healthy"
        assert sites[0]["score"] == 1.0

    def test_writes_go_through_to_spacetime(self, client, container):
        client.post("/events", json=event(status="done"))
        writer: SpySpacetimeWriter = container.spacetime_writer
        assert len(writer.sites) == 1
        assert len(writer.events) == 1


class TestSharedFailurePattern:
    def test_a_single_sites_violation_does_not_trigger_a_policy_push(self, client):
        response = client.post(
            "/events",
            json=event(
                site_id="site-a",
                status="killed",
                violation={"rule": "tool_calls[*].args.amount lte 100", "expected": "lte 100", "actual": "5000"},
            ),
        )
        assert response.json()["policy_update"] is None

    def test_a_second_distinct_site_seeing_the_same_violation_triggers_a_push(self, client, monkeypatch):
        pushed = []
        monkeypatch.setattr(
            httpx, "post", lambda url, **kw: pushed.append(url) or _ok_response()
        )

        violation = {"rule": "tool_calls[*].args.amount lte 100", "expected": "lte 100", "actual": "5000"}
        client.post("/events", json=event(site_id="site-a", status="killed", violation=violation, run_id="r1"))
        response = client.post("/events", json=event(site_id="site-b", status="killed", violation=violation, run_id="r2"))

        body = response.json()
        assert body["policy_update"] == {"rule": "tool_calls[*].args.amount lte", "value": 50.0}

    def test_the_broadcast_excludes_the_reporting_site(self, client, monkeypatch, container):
        object.__setattr__(
            container.settings,
            "site_kernels",
            {"site-a": "http://k-a:8100", "site-b": "http://k-b:8100", "site-c": "http://k-c:8100"},
        )
        called_urls = []
        monkeypatch.setattr(httpx, "post", lambda url, **kw: called_urls.append(url) or _ok_response())

        violation = {"rule": "amount lte 100", "expected": "lte 100", "actual": "5000"}
        client.post("/events", json=event(site_id="site-a", status="killed", violation=violation, run_id="r1"))
        client.post("/events", json=event(site_id="site-b", status="killed", violation=violation, run_id="r2"))

        # site-b is the reporting (second) site and must not receive its own echo back.
        assert "http://k-b:8100/policy" not in called_urls
        assert "http://k-a:8100/policy" in called_urls
        assert "http://k-c:8100/policy" in called_urls


class TestPolicyPushEndpoint:
    def test_pushes_to_every_configured_kernel(self, client, monkeypatch, container):
        object.__setattr__(container.settings, "site_kernels", {"site-a": "http://k-a:8100"})
        monkeypatch.setattr(httpx, "post", lambda url, **kw: _ok_response())
        response = client.post("/policy/push", json={"rule": "amount lte", "value": 25, "reason": "manual"})
        assert response.status_code == 200
        assert response.json() == {"pushed": {"site-a": True}}

    def test_no_configured_kernels_returns_an_empty_result(self, client):
        response = client.post("/policy/push", json={"rule": "amount lte", "value": 25, "reason": "manual"})
        assert response.json() == {"pushed": {}}


class TestDebugEndpoints:
    def test_events_debug_returns_newest_first(self, client):
        client.post("/events", json=event(run_id="r1"))
        client.post("/events", json=event(run_id="r2"))
        events = client.get("/events").json()
        assert [e["run_id"] for e in events] == ["r2", "r1"]

    def test_events_debug_filters_by_site(self, client):
        client.post("/events", json=event(site_id="site-a", run_id="r1"))
        client.post("/events", json=event(site_id="site-b", run_id="r2"))
        events = client.get("/events", params={"site_id": "site-b"}).json()
        assert [e["run_id"] for e in events] == ["r2"]

    def test_healthz(self, client):
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["sites_seen"] == 0


def _ok_response():
    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

    return _R()
