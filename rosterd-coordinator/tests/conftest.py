"""Shared fixtures.

Every test builds its own isolated Container (a settings tuned for fast,
deterministic tests, and a spy StateWriter recording every write) --
same reasoning as rosterd-kernel/tests/conftest.py: this service is
stateful across requests, so nothing is shared module-level.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config import Settings
from main import build_container, create_app


def make_settings(**overrides) -> Settings:
    base = dict(
        otel_enabled=False,  # no collector in tests
        site_offline_after_sec=1.0,
        site_offline_sweep_interval_sec=3600.0,  # background sweeper effectively never ticks mid-test
        site_score_window=5,
        pattern_site_threshold=2,
        pattern_window_sec=300.0,
        pattern_push_cooldown_sec=60.0,
        pattern_tighten_factor=0.5,
        push_timeout_sec=1.0,
    )
    base.update(overrides)
    return Settings(**base)


class SpyStateWriter:
    def __init__(self) -> None:
        self.sites: list = []
        self.events: list = []

    def write_site(self, row) -> None:
        self.sites.append(row)

    def write_event(self, row) -> None:
        self.events.append(row)


@pytest.fixture
def spy_state_writer():
    return SpyStateWriter()


@pytest.fixture
def container(spy_state_writer):
    settings = make_settings()
    return build_container(settings, state_writer=spy_state_writer)


@pytest.fixture
def client(container):
    app = create_app(container.settings, container=container)
    with TestClient(app) as test_client:
        yield test_client
