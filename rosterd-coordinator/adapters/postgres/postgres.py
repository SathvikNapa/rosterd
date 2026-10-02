"""Shared-live-state rows the coordinator writes, and the writer that gets
them there. Same pattern as rosterd-kernel/postgres.py -- see its
docstring for the full rationale.

The real store is rosterd-postgres/schema.sql (database `rosterd`),
tables `sites` and `events` specifically -- plain `INSERT ... ON CONFLICT
DO UPDATE` / `INSERT` via psycopg, not an RPC call. `TaskRow` and the
schema's `tasks` table are kept for reference because the original brief
lists `tasks` among the tables the coordinator owns, but nothing in the
coordinator's own contract (`POST /events`, `GET /sites`, `POST
/policy/push`) ever produces one -- see the README's "Notes for the team".
No writer method exists for it because nothing in this service has a
TaskRow to write yet.

`EventLogEntry.violation` is the one field that isn't a flat SQL type --
it's `rosterd_contracts.Violation | None`, stored in the `events` table's
`violation JSONB` column. `psycopg.types.json.Jsonb` is what adapts a
Python dict to a real jsonb parameter rather than a plain string.
"""
from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel

from domain.coordinator import EventLogEntry, SiteSummary
from domain.ports import StateWriter

logger = logging.getLogger("rosterd.coordinator.postgres")


class TaskRow(BaseModel):
    task_id: str
    site_id: str
    agent_id: str
    title: str
    status: str
    assignees: list[str]
    criteria: list[str]
    priority: str
    source: str | None = None
    created_at: datetime


class LoggingStateWriter:
    def write_site(self, row: SiteSummary) -> None:
        logger.info("sites <- %s", row.model_dump_json())

    def write_event(self, row: EventLogEntry) -> None:
        logger.info("events <- %s", row.model_dump_json())


class PostgresStateWriter:
    """One short-lived connection per write -- see rosterd-kernel/postgres.py's
    docstring for why that's fine at this write rate."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        import psycopg  # lazy: no package needed when Postgres isn't configured

        return psycopg.connect(self._dsn, autocommit=True, connect_timeout=5)

    def write_site(self, row: SiteSummary) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sites (site_id, status, last_event, score)
                    VALUES (%(site_id)s, %(status)s, %(last_event)s, %(score)s)
                    ON CONFLICT (site_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        last_event = EXCLUDED.last_event,
                        score = EXCLUDED.score
                    """,
                    row.model_dump(mode="json"),
                )
        except Exception:  # noqa: BLE001 - best-effort, see rosterd-kernel/postgres.py
            logger.warning("Postgres write to sites failed (continuing)", exc_info=True)

    def write_event(self, row: EventLogEntry) -> None:
        from psycopg.types.json import Jsonb

        try:
            data = row.model_dump(mode="json")
            data["violation"] = Jsonb(data["violation"]) if data["violation"] is not None else None
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events (site_id, run_id, status, violation, trace_id, timestamp)
                    VALUES (%(site_id)s, %(run_id)s, %(status)s, %(violation)s, %(trace_id)s, %(timestamp)s)
                    """,
                    data,
                )
        except Exception:  # noqa: BLE001 - best-effort, see rosterd-kernel/postgres.py
            logger.warning("Postgres write to events failed (continuing)", exc_info=True)


def build_state_writer(settings) -> StateWriter:
    if settings.postgres_dsn:
        return PostgresStateWriter(settings.postgres_dsn)
    return LoggingStateWriter()
