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
"""
from __future__ import annotations

import asyncio
import logging
import threading

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("rosterd.coordinator.live_ws")

#: Every table rosterd-postgres/schema.sql's trigger fires pg_notify on.
CHANNELS = ("agents", "agent_metrics", "sites", "events", "tasks", "manifests")


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
        # builds it with row_to_json(NEW)::text) -- splicing it in directly
        # avoids a pointless parse-then-reserialize round trip.
        message = f'{{"table": "{channel}", "row": {payload}}}'
        dead: set[WebSocket] = set()
        for client in self._clients:
            try:
                await client.send_text(message)
            except Exception:  # noqa: BLE001 - one dead client must not break the rest
                dead.add(client)
        self._clients -= dead

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
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
