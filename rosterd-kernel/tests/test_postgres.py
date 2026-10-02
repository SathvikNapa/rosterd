"""Regression tests for PostgresStateWriter.

A fake psycopg connection/cursor stands in for the real one (same spirit
as the old test_spacetime.py's fake httpx transport) -- these pin the SQL
shape and the best-effort-on-failure contract without a live Postgres.
rosterd-postgres/schema.sql itself was verified separately against a real
`postgres:16-alpine` container (upsert + the NOTIFY trigger both confirmed
live) -- that's the thing an in-process fake can't stand in for.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from adapters.postgres.postgres import PostgresStateWriter
from domain.ports import AgentMetricsRow, AgentRow


class _FakeCursor:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def execute(self, sql: str, params: dict) -> None:
        self._calls.append((sql, params))


class _FakeConnection:
    def __init__(self, calls: list, *, raise_on_connect: Exception | None = None) -> None:
        self._calls = calls
        self._raise_on_connect = raise_on_connect

    def __enter__(self):
        if self._raise_on_connect:
            raise self._raise_on_connect
        return self

    def __exit__(self, *exc) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._calls)


@pytest.fixture()
def recorded_calls(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    fake_module = SimpleNamespace(connect=lambda dsn, **kw: _FakeConnection(calls))
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_module)
    return calls


def test_write_agent_upserts_by_instance_id(recorded_calls: list) -> None:
    writer = PostgresStateWriter("postgresql://rosterd:rosterd@localhost/rosterd")
    row = AgentRow(
        site_id="site-a",
        agent_id="fulfillment",
        instance_id="inst-1",
        name="site-a-fulfillment-1",
        status="idle",
        updated_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_agent(row)

    assert len(recorded_calls) == 1
    sql, params = recorded_calls[0]
    assert "INSERT INTO agents" in sql
    assert "ON CONFLICT (instance_id) DO UPDATE" in sql
    assert params["instance_id"] == "inst-1"
    assert params["status"] == "idle"


def test_write_agent_metrics_is_a_plain_insert(recorded_calls: list) -> None:
    writer = PostgresStateWriter("postgresql://rosterd:rosterd@localhost/rosterd")
    row = AgentMetricsRow(
        site_id="site-a",
        agent_id="fulfillment",
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

    assert len(recorded_calls) == 1
    sql, params = recorded_calls[0]
    assert "INSERT INTO agent_metrics" in sql
    assert "ON CONFLICT" not in sql  # append-only, no PK to conflict on
    assert params["in_flight_count"] == 2


def test_a_postgres_outage_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writes are best-effort -- see the module docstring."""

    def raising_connect(dsn, **kw):
        return _FakeConnection([], raise_on_connect=ConnectionRefusedError("connection refused"))

    fake_module = SimpleNamespace(connect=raising_connect)
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_module)

    writer = PostgresStateWriter("postgresql://rosterd:rosterd@localhost/rosterd")
    row = AgentRow(
        site_id="site-a",
        agent_id="fulfillment",
        instance_id="inst-1",
        name="site-a-fulfillment-1",
        status="idle",
        updated_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_agent(row)  # must not raise
