"""Runtime configuration for the coordinator service.

Same philosophy as rosterd-ingestion/config.py and rosterd-kernel/config.py:
everything environment-driven, `get_settings()` deliberately not cached so
tests can flip ROSTERD_COORDINATOR_* between cases.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_json_dict(name: str) -> dict[str, str]:
    raw = os.environ.get(name)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: _env_int("PORT", 8300))

    #: {site_id: kernel_base_url}. How the coordinator knows who to fan a
    #: pushed policy out to -- there's no service discovery in this repo,
    #: so it's config, same as the kernel's own ROSTERD_KERNEL_AGENT_IMAGES.
    site_kernels: dict[str, str] = field(default_factory=lambda: _env_json_dict("ROSTERD_COORDINATOR_SITE_KERNELS"))

    #: A site with no event in this long is reported `offline` by GET
    #: /sites, and (via the background sweeper) gets its SpacetimeDB `sites`
    #: row updated to match even with nobody polling.
    site_offline_after_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_COORDINATOR_OFFLINE_AFTER_SEC", 30.0)
    )
    site_offline_sweep_interval_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_COORDINATOR_OFFLINE_SWEEP_SEC", 5.0)
    )
    #: How many of a site's most recent events feed its health score
    #: (fraction that were NOT a violation/kill).
    site_score_window: int = field(default_factory=lambda: _env_int("ROSTERD_COORDINATOR_SCORE_WINDOW", 20))

    # ---- shared-failure-pattern detection --------------------------------
    #: A violation on the same rule seen at this many distinct sites within
    #: the lookback window counts as a "shared failure pattern".
    pattern_site_threshold: int = field(default_factory=lambda: _env_int("ROSTERD_COORDINATOR_PATTERN_THRESHOLD", 2))
    pattern_window_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_COORDINATOR_PATTERN_WINDOW_SEC", 300.0)
    )
    #: Don't re-push a tightened policy for the same rule more than once
    #: within this many seconds, even if more matching violations keep
    #: arriving -- avoids flooding every kernel's /policy on a hot loop.
    pattern_push_cooldown_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_COORDINATOR_PATTERN_COOLDOWN_SEC", 60.0)
    )
    #: How hard an auto-detected pattern tightens a numeric lte/gte bound.
    #: lte: new = old * factor (shrinks the ceiling). gte: new = old / factor
    #: (raises the floor). 0.5 halves an lte cap / doubles a gte floor.
    pattern_tighten_factor: float = field(
        default_factory=lambda: _env_float("ROSTERD_COORDINATOR_TIGHTEN_FACTOR", 0.5)
    )

    push_timeout_sec: float = field(default_factory=lambda: _env_float("ROSTERD_COORDINATOR_PUSH_TIMEOUT_SEC", 5.0))

    # ---- SpacetimeDB -----------------------------------------------------------
    #: Both unset (the default) means "log every row instead of writing it" --
    #: see spacetime.py. Set both once Joy's module is up.
    spacetimedb_url: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_COORDINATOR_SPACETIMEDB_URL") or None
    )
    spacetimedb_module: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_COORDINATOR_SPACETIMEDB_MODULE") or None
    )
    spacetimedb_auth_token: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_COORDINATOR_SPACETIMEDB_TOKEN") or None
    )

    # ---- OpenTelemetry -----------------------------------------------------------
    otel_enabled: bool = field(default_factory=lambda: _env_bool("ROSTERD_COORDINATOR_OTEL_ENABLED", True))
    otel_endpoint: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_COORDINATOR_OTEL_ENDPOINT", "http://localhost:4318")
    )
    otel_service_name: str = field(default="rosterd-coordinator")


def get_settings() -> Settings:
    return Settings()
