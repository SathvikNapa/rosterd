"""Unit tests for LiveRelay's message shape.

The real LISTEN/NOTIFY + snapshot mechanics were verified separately
against a real postgres:16-alpine container (see
rosterd-coordinator/README.md's "Verified") -- a real INSERT actually
arriving at a fake websocket client through the real
thread -> asyncio.run_coroutine_threadsafe -> broadcast path, and a
pre-existing row showing up in the snapshot sent on connect. These tests
pin the JSON message *shape* (`type`, `table`, `row`/`rows`) the frontend
depends on, without needing a live database for every test run.

Plain `asyncio.run()` rather than `@pytest.mark.asyncio` -- no async
pytest plugin is otherwise needed anywhere in this suite, and adding one
just for these four tests isn't worth a new dependency.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from adapters.http_in.live_ws import CHANNELS, LiveRelay


class _FakeWebSocket:
    def __init__(self) -> None:
        self.received: list[str] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, message: str) -> None:
        self.received.append(message)

    async def receive_text(self) -> str:
        # Simulates the client hanging up immediately after the snapshot.
        raise RuntimeError("client gone")


def test_connect_sends_one_snapshot_message_per_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = LiveRelay("postgresql://unused/unused")
    monkeypatch.setattr(
        relay,
        "_fetch_snapshot",
        lambda: {table: [{"id": 1}] if table == "agents" else [] for table in CHANNELS},
    )
    client = _FakeWebSocket()

    try:
        asyncio.run(relay.handle(client))
    except RuntimeError:
        pass

    snapshot_messages = [json.loads(m) for m in client.received]
    assert {m["table"] for m in snapshot_messages} == set(CHANNELS)
    assert all(m["type"] == "snapshot" for m in snapshot_messages)
    agents_message = next(m for m in snapshot_messages if m["table"] == "agents")
    assert agents_message["rows"] == [{"id": 1}]


def test_no_dsn_configured_sends_no_snapshot() -> None:
    relay = LiveRelay(None)
    client = _FakeWebSocket()

    try:
        asyncio.run(relay.handle(client))
    except RuntimeError:
        pass

    assert client.received == []


def test_broadcast_reaches_every_connected_client_and_drops_dead_ones() -> None:
    relay = LiveRelay(None)
    alive = _FakeWebSocket()
    dead = _FakeWebSocket()

    async def raise_on_send(_msg: str) -> None:
        raise ConnectionError("gone")

    dead.send_text = raise_on_send  # type: ignore[method-assign]
    relay._clients = {alive, dead}

    asyncio.run(relay._broadcast("agents", '{"instance_id": "inst-1", "status": "killed"}'))

    assert dead not in relay._clients
    assert alive in relay._clients
    message = json.loads(alive.received[0])
    assert message == {"type": "live", "table": "agents", "row": {"instance_id": "inst-1", "status": "killed"}}


def test_json_default_serializes_datetimes_isoformat() -> None:
    from adapters.http_in.live_ws import _json_default

    assert _json_default(datetime(2026, 9, 19, tzinfo=timezone.utc)) == "2026-09-19T00:00:00+00:00"
    with pytest.raises(TypeError):
        _json_default(object())
