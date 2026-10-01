"""Relays Postgres LISTEN/NOTIFY to connected browser clients over a plain
WebSocket.

A browser can't open a raw Postgres connection, so something has to sit in
between. This lives in the coordinator rather than as its own service
because the coordinator already is the one process every site's kernel
and the frontend both already talk to -- adding a 6th deployable unit
just to bridge Postgres to a websocket would be a new moving part for no
real benefit, and rosterd-postgres/schema.sql's trigger already does all
the real work (`pg_notify` on every insert/update).

One background thread holds a single `LISTEN` connection (not one per
browser tab -- that would mean one Postgres connection per open tab for no
reason) and blocks on `conn.notifies()`, same "background thread with
start()/stop()" shape as application/sweeper.py and rosterd-kernel's
scaler.py. Each notification is handed across the sync/async boundary via
`asyncio.run_coroutine_threadsafe` to the event loop captured when the
relay starts, then fanned out to every connected websocket.

Best-effort in both directions, same contract as adapters/postgres/postgres.py:
a Postgres outage means no live updates, not a crashed coordinator (the
listen loop retries every 2s rather than giving up); a browser that's gone
away just gets dropped from the broadcast set instead of blocking everyone
else.

NOTIFY only ever fires on a *change* -- a client that connects after
`agents` already has rows would otherwise see nothing until the next
write. So `handle()` sends one `{"type": "snapshot", ...}` message per
table immediately on connect (a plain `SELECT *`, off the request/response
path, no different from the dashboards every other service already
exposes), then switches to `{"type": "live", ...}` messages as NOTIFYs
arrive. The frontend applies a snapshot as a full replace and a live
message as an upsert into the table it names.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date, datetime

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("rosterd.coordinator.live_ws")

#: Every table rosterd-postgres/schema.sql's trigger fires pg_notify on --
#: also the exact set snapshotted on connect, in this order.
CHANNELS = ("agents", "agent_metrics", "sites", "events", "tasks", "manifests")


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON-serializable: {value!r}")


class LiveRelay:
    def __init__(self, dsn: str | None) -> None:
        self._dsn = dsn
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once from the FastAPI lifespan startup, with the actual
        running event loop -- captured so the background thread below can
        safely hand a notification across to it."""
        self._loop = loop
        if not self._dsn:
            logger.info("no Postgres DSN configured -- live_ws relay stays idle")
            return
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="live-ws-relay")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _listen_loop(self) -> None:
        import psycopg  # lazy: no package needed when Postgres isn't configured

        while not self._stop.is_set():
            try:
                with psycopg.connect(self._dsn, autocommit=True) as conn:
                    with conn.cursor() as cur:
                        for channel in CHANNELS:
                            cur.execute(f"LISTEN {channel}")
                    logger.info("live_ws listening on Postgres channels: %s", ", ".join(CHANNELS))
                    for notify in conn.notifies():
                        if self._stop.is_set():
                            return
                        self._broadcast_threadsafe(notify.channel, notify.payload)
            except Exception:  # noqa: BLE001 - best-effort, see module docstring
                logger.warning("live_ws Postgres LISTEN connection dropped, retrying in 2s", exc_info=True)
                self._stop.wait(2.0)

    def _broadcast_threadsafe(self, channel: str, payload: str) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(channel, payload), self._loop)

    async def _broadcast(self, channel: str, payload: str) -> None:
        # `payload` is already a valid JSON object (schema.sql's trigger
        # builds it with row_to_json(NEW)::text) -- splicing it into a
        # literal `row` field avoids a pointless parse-then-reserialize
        # round trip on the hot path.
        message = f'{{"type": "live", "table": "{channel}", "row": {payload}}}'
        dead: set[WebSocket] = set()
        for client in self._clients:
            try:
                await client.send_text(message)
            except Exception:  # noqa: BLE001 - one dead client must not break the rest
                dead.add(client)
        self._clients -= dead

    def _fetch_snapshot(self) -> dict[str, list[dict]]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, autocommit=True, row_factory=dict_row) as conn:
            snapshot: dict[str, list[dict]] = {}
            for table in CHANNELS:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {table}")
                    snapshot[table] = cur.fetchall()
            return snapshot

    async def _send_snapshot(self, websocket: WebSocket) -> None:
        if not self._dsn:
            return
        try:
            snapshot = await asyncio.to_thread(self._fetch_snapshot)
        except Exception:  # noqa: BLE001 - best-effort, see module docstring
            logger.warning("live_ws snapshot fetch failed (continuing with live updates only)", exc_info=True)
            return
        for table, rows in snapshot.items():
            await websocket.send_text(json.dumps({"type": "snapshot", "table": table, "rows": rows}, default=_json_default))

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await self._send_snapshot(websocket)
        self._clients.add(websocket)
        try:
            while True:
                # Clients don't send anything meaningful back; this just
                # blocks until they disconnect so we notice and clean up.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(websocket)
