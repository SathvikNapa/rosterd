"""SiteRegistry: status/score derivation and lazy offline detection."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from config import Settings
from coordinator import EventRequest
from shared import SiteStatus
from sites import SiteRegistry


def event(site_id="site-a", status="done", violation=None, **overrides):
    payload = dict(
        site_id=site_id,
        agent_id="fulfillment",
        status=status,
        violation=violation,
        timestamp=datetime.now(timezone.utc),
    )
    payload.update(overrides)
    return EventRequest(**payload)


def make_registry(**overrides) -> SiteRegistry:
    defaults = dict(site_offline_after_sec=1.0, site_score_window=5)
    defaults.update(overrides)
    return SiteRegistry(Settings(**defaults))


class TestStatus:
    def test_a_normal_completion_is_healthy(self):
        registry = make_registry()
        summary = registry.record_event(event(status="done"))
        assert summary.status == SiteStatus.healthy

    def test_a_kill_is_violation_even_without_a_violation_payload(self):
        """A manual kill has no Violation, but it's still trouble."""
        registry = make_registry()
        summary = registry.record_event(event(status="killed"))
        assert summary.status == SiteStatus.violation

    def test_scale_events_are_healthy(self):
        registry = make_registry()
        summary = registry.record_event(event(status="scaled_up", pool_size=2))
        assert summary.status == SiteStatus.healthy

    def test_a_site_with_no_recent_event_is_offline_on_read(self):
        registry = make_registry(site_offline_after_sec=0.05)
        registry.record_event(event())
        time.sleep(0.1)
        assert registry.get("site-a").status == SiteStatus.offline

    def test_unknown_site_is_none(self):
        registry = make_registry()
        assert registry.get("nope") is None


class TestScore:
    def test_all_healthy_events_score_1(self):
        registry = make_registry()
        for _ in range(3):
            registry.record_event(event(status="done"))
        assert registry.get("site-a").score == 1.0

    def test_score_reflects_the_recent_window_not_the_full_history(self):
        registry = make_registry(site_score_window=2)
        registry.record_event(event(status="killed"))
        registry.record_event(event(status="done"))
        registry.record_event(event(status="done"))
        # Window=2, so only the last two ("done", "done") count.
        assert registry.get("site-a").score == 1.0


class TestListSites:
    def test_list_sites_is_sorted_and_covers_every_known_site(self):
        registry = make_registry()
        registry.record_event(event(site_id="site-b"))
        registry.record_event(event(site_id="site-a"))
        assert [s.site_id for s in registry.list_sites()] == ["site-a", "site-b"]

    def test_list_offline_only_returns_stale_sites(self):
        registry = make_registry(site_offline_after_sec=0.05)
        registry.record_event(event(site_id="site-a"))
        assert registry.list_offline() == []
        time.sleep(0.1)
        assert [s.site_id for s in registry.list_offline()] == ["site-a"]
