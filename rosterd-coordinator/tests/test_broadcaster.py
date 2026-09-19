"""PolicyBroadcaster: fans a policy out to every known kernel, best-effort."""
from __future__ import annotations

import httpx

from broadcaster import PolicyBroadcaster
from config import Settings
from tracing import Telemetry


class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


def make_broadcaster(site_kernels):
    settings = Settings(otel_enabled=False, site_kernels=site_kernels, push_timeout_sec=1.0)
    return PolicyBroadcaster(settings, Telemetry(settings))


def test_pushes_to_every_known_kernel(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return _Response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    broadcaster = make_broadcaster({"site-a": "http://k-a:8100", "site-b": "http://k-b:8100"})
    result = broadcaster.push("amount lte", 50, "seen at 2 sites")

    assert result == {"site-a": True, "site-b": True}
    assert {url for url, _ in calls} == {"http://k-a:8100/policy", "http://k-b:8100/policy"}
    assert all(body == {"rule": "amount lte", "value": 50, "reason": "seen at 2 sites"} for _, body in calls)


def test_excludes_the_reporting_site(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(200))
    broadcaster = make_broadcaster({"site-a": "http://k-a:8100", "site-b": "http://k-b:8100"})
    result = broadcaster.push("amount lte", 50, "reason", exclude_site="site-a")
    assert result == {"site-b": True}


def test_one_unreachable_kernel_does_not_stop_the_others(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        if "k-a" in url:
            raise httpx.ConnectError("refused")
        return _Response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    broadcaster = make_broadcaster({"site-a": "http://k-a:8100", "site-b": "http://k-b:8100"})
    result = broadcaster.push("amount lte", 50, "reason")
    assert result == {"site-a": False, "site-b": True}


def test_no_known_kernels_is_a_harmless_no_op(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    broadcaster = make_broadcaster({})
    assert broadcaster.push("amount lte", 50, "reason") == {}
