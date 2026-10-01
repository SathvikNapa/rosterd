"""Regression tests for PostgresStateWriter.

Same pattern as rosterd-kernel/tests/test_postgres.py -- a fake psycopg
connection/cursor stands in for the real one. This file additionally pins
the one thing the coordinator's writer needs that the kernel's doesn't:
`EventLogEntry.violation` goes into a `violation JSONB` column via
`psycopg.types.json.Jsonb`, not a flat column. rosterd-postgres/schema.sql
itself was verified separately against a real postgres:16-alpine
container -- that's the thing an in-process fake can't stand in for.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from adapters.postgres.postgres import PostgresStateWriter
from domain.coordinator import EventLogEntry, SiteSummary


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


def _install_fake_psycopg(monkeypatch: pytest.MonkeyPatch, calls: list, *, raise_on_connect=None) -> None:
    """`PostgresStateWriter` does `import psycopg` and, in write_event,
    `from psycopg.types.json import Jsonb` -- both lazy, both resolved
    against sys.modules, so all three module names need an entry for the
    `from a.b.c import d` form to resolve without touching the real
    package."""
    fake_psycopg = SimpleNamespace(connect=lambda dsn, **kw: _FakeConnection(calls, raise_on_connect=raise_on_connect))
    fake_json_module = SimpleNamespace(Jsonb=lambda value: {"__jsonb__": value})
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.types", SimpleNamespace(json=fake_json_module))
    monkeypatch.setitem(sys.modules, "psycopg.types.json", fake_json_module)


@pytest.fixture()
def recorded_calls(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    _install_fake_psycopg(monkeypatch, calls)
    return calls


def test_write_site_upserts_by_site_id(recorded_calls: list) -> None:
    writer = PostgresStateWriter("postgresql://rosterd:rosterd@localhost/rosterd")
    row = SiteSummary(
        site_id="site-a",
        status="healthy",
        last_event=datetime(2026, 9, 19, tzinfo=timezone.utc),
        score=0.95,
    )

    writer.write_site(row)

    assert len(recorded_calls) == 1
    sql, params = recorded_calls[0]
    assert "INSERT INTO sites" in sql
    assert "ON CONFLICT (site_id) DO UPDATE" in sql
    assert params["site_id"] == "site-a"
    assert params["score"] == 0.95


def test_write_event_wraps_violation_as_jsonb(recorded_calls: list) -> None:
    writer = PostgresStateWriter("postgresql://rosterd:rosterd@localhost/rosterd")
    row = EventLogEntry(
        site_id="site-a",
        run_id="run-1",
        status="killed",
        violation={"rule": "tool_calls[*].args.amount_usd lte 100", "expected": "<=100", "actual": "5000"},
        trace_id="trace-1",
        timestamp=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_event(row)

    assert len(recorded_calls) == 1
    sql, params = recorded_calls[0]
    assert "INSERT INTO events" in sql
    assert "ON CONFLICT" not in sql  # append-only
    assert params["run_id"] == "run-1"
    assert params["violation"] == {"__jsonb__": {"rule": "tool_calls[*].args.amount_usd lte 100",
                                                  "expected": "<=100", "actual": "5000"}}


def test_write_event_leaves_violation_as_bare_none_when_absent(recorded_calls: list) -> None:
    writer = PostgresStateWriter("postgresql://rosterd:rosterd@localhost/rosterd")
    row = EventLogEntry(
        site_id="site-a", run_id=None, status="scaled_up", violation=None, trace_id=None,
        timestamp=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    writer.write_event(row)

    _, params = recorded_calls[0]
    assert params["violation"] is None
    assert params["run_id"] is None


def test_a_postgres_outage_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writes are best-effort -- see rosterd-kernel/postgres.py's docstring."""
    _install_fake_psycopg(monkeypatch, [], raise_on_connect=ConnectionRefusedError("connection refused"))

    writer = PostgresStateWriter("postgresql://rosterd:rosterd@localhost/rosterd")
    row = SiteSummary(site_id="site-a", status="healthy", last_event=None, score=1.0)

    writer.write_site(row)  # must not raise
