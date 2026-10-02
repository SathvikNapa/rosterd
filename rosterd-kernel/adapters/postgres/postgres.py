"""Shared-live-state row shapes the kernel writes, and the writer that gets
them there.

Replaces the earlier SpacetimeDB-backed writer (same role, same two calls
-- `write_agent` / `write_agent_metrics` -- scaler.py and killer.py don't
know or care which one they're talking to). The real store now is plain
Postgres (rosterd-postgres/schema.sql, tables `agents` + `agent_metrics`
among others) instead of a bespoke module: an ordinary
`INSERT ... ON CONFLICT DO UPDATE`/`INSERT`, not an RPC call. A trigger in
that schema fires `pg_notify` on every insert/update, which is how
rosterd-coordinator's live_ws.py gets these writes to the frontend in real
time without this service knowing a browser is listening.

* `LoggingStateWriter` (the default) just logs each row. This still runs
  whenever `ROSTERD_KERNEL_POSTGRES_DSN` isn't set (e.g. a bare
  `docker compose up` without the `postgres` service), so the scaler and
  kill switch keep working without a live Postgres.
* `PostgresStateWriter` writes directly via psycopg, autocommitting each
  write (every call here is already its own unit of work, and a held-open
  transaction across scaler ticks would serialize them for no reason).

Writes are best-effort: a Postgres outage must not stop the kernel from
dispatching or scaling, only from being *visible* while it does -- same
contract the SpacetimeDB-backed writer had.
"""
from __future__ import annotations

import logging

from domain.ports import AgentMetricsRow, AgentRow, StateWriter

logger = logging.getLogger("rosterd.kernel.postgres")


class LoggingStateWriter:
    def write_agent(self, row: AgentRow) -> None:
        logger.info("agents <- %s", row.model_dump_json())

    def write_agent_metrics(self, row: AgentMetricsRow) -> None:
        logger.info("agent_metrics <- %s", row.model_dump_json())


class PostgresStateWriter:
    """One short-lived connection per write -- see module docstring for why
    that's fine here (no multi-write transaction ever needed). Reuses a
    single connection across calls instead of reconnecting every time would
    be a reasonable later optimization; not worth the added failure modes
    (a dead connection needing reconnect logic) for a demo-scale write rate.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        import psycopg  # lazy: no package needed when Postgres isn't configured

        return psycopg.connect(self._dsn, autocommit=True, connect_timeout=5)

    def write_agent(self, row: AgentRow) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agents (instance_id, site_id, agent_id, name, status, updated_at)
                    VALUES (%(instance_id)s, %(site_id)s, %(agent_id)s, %(name)s, %(status)s, %(updated_at)s)
                    ON CONFLICT (instance_id) DO UPDATE SET
                        site_id = EXCLUDED.site_id,
                        agent_id = EXCLUDED.agent_id,
                        name = EXCLUDED.name,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at
                    """,
                    row.model_dump(mode="json"),
                )
        except Exception:  # noqa: BLE001 - best-effort, see module docstring
            logger.warning("Postgres write to agents failed (continuing)", exc_info=True)

    def write_agent_metrics(self, row: AgentMetricsRow) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_metrics (
                        site_id, agent_id, timestamp, in_flight_count, queued_count,
                        target_concurrency, current_replicas, desired_replicas,
                        min_replicas, max_replicas
                    ) VALUES (
                        %(site_id)s, %(agent_id)s, %(timestamp)s, %(in_flight_count)s, %(queued_count)s,
                        %(target_concurrency)s, %(current_replicas)s, %(desired_replicas)s,
                        %(min_replicas)s, %(max_replicas)s
                    )
                    """,
                    row.model_dump(mode="json"),
                )
        except Exception:  # noqa: BLE001 - best-effort, see module docstring
            logger.warning("Postgres write to agent_metrics failed (continuing)", exc_info=True)


def build_state_writer(settings) -> StateWriter:
    if settings.postgres_dsn:
        return PostgresStateWriter(settings.postgres_dsn)
    return LoggingStateWriter()
