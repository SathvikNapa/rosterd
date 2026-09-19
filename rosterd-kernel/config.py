"""Runtime configuration for the kernel service.

Everything is environment-driven, same philosophy as
rosterd-ingestion/config.py: a teammate, a test, or CI can run this
identically without editing code. `get_settings()` is deliberately not
cached (tests flip ROSTERD_KERNEL_* between cases).
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


def _env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


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
    #: Which site this kernel instance runs. One kernel per site.
    site_id: str = field(default_factory=lambda: os.environ.get("ROSTERD_SITE_ID", "site-a"))

    #: Port this service listens on (scripts/run.sh reads this too).
    port: int = field(default_factory=lambda: _env_int("PORT", 8100))

    # ---- manifest subscription -------------------------------------------------
    #: Ingestion service base URL, polled for the confirmed manifest. Stands
    #: in for a real SpacetimeDB `manifests` subscription -- see
    #: manifest_source.py's docstring for why.
    ingestion_url: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_INGESTION_URL", "http://localhost:8000")
    )
    #: Which manifest this site's kernel governs. Unset means "no manifest
    #: yet" -- dispatch rejects everything with manifest_not_ready until an
    #: operator sets this (or wires the real SpacetimeDB subscription).
    manifest_id: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_MANIFEST_ID") or None
    )
    manifest_poll_interval_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_KERNEL_MANIFEST_POLL_SEC", 5.0)
    )

    # ---- docker / instance pool -------------------------------------------------
    #: "fake" (default) simulates instances without a Docker daemon, so the
    #: kernel runs and is testable before anyone's image is built. "real"
    #: uses the Docker SDK. See docker_backend.py.
    docker_mode: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_DOCKER_MODE", "fake").strip().lower()
    )
    #: fake mode only: every simulated instance's /invoke target.
    fake_agent_url: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_FAKE_AGENT_URL", "http://localhost:9000")
    )
    #: real mode only: this site's isolated Docker network (per the compose
    #: file, the kernel is the only thing on it that can also reach outside).
    docker_network: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_DOCKER_NETWORK")
        or f"rosterd-{os.environ.get('ROSTERD_SITE_ID', 'site-a')}"
    )
    #: real mode only: {agent_id: image}. Falls back to default_agent_image.
    agent_image_map: dict[str, str] = field(default_factory=lambda: _env_json_dict("ROSTERD_KERNEL_AGENT_IMAGES"))
    default_agent_image: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_DEFAULT_AGENT_IMAGE", "rosterd/demo-agent:latest")
    )
    #: real mode only: the port Shruti's /invoke listens on inside its container.
    demo_agent_port: int = field(default_factory=lambda: _env_int("ROSTERD_KERNEL_DEMO_AGENT_PORT", 8000))

    # ---- dispatch ----------------------------------------------------------
    dispatch_timeout_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_KERNEL_DISPATCH_TIMEOUT_SEC", 20.0)
    )
    #: How long a dispatch at a full pool waits for an instance to free up
    #: before it's rejected, instead of queuing forever.
    dispatch_queue_wait_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_KERNEL_QUEUE_WAIT_SEC", 5.0)
    )
    dispatch_queue_poll_interval_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_KERNEL_QUEUE_POLL_SEC", 0.25)
    )

    # ---- budget --------------------------------------------------------------
    max_tool_calls_per_run: int = field(default_factory=lambda: _env_int("ROSTERD_KERNEL_MAX_TOOL_CALLS", 20))
    max_run_seconds: float = field(default_factory=lambda: _env_float("ROSTERD_KERNEL_MAX_RUN_SECONDS", 120.0))

    # ---- scaling -------------------------------------------------------------
    scaler_interval_sec: float = field(default_factory=lambda: _env_float("ROSTERD_KERNEL_SCALER_INTERVAL_SEC", 5.0))
    #: Demo-friendly override: when set, replaces every agent's
    #: scale_down_after_idle_seconds so scale-down is watchable live
    #: (15-30s) instead of a realistic production cooldown. Also settable
    #: per-run via SimulateLoadRequest.scale_down_after_idle_seconds_override.
    demo_idle_seconds: int | None = field(
        default_factory=lambda: _env_optional_int("ROSTERD_KERNEL_DEMO_IDLE_SECONDS")
    )

    # ---- coordinator -----------------------------------------------------------
    coordinator_url: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_COORDINATOR_URL", "http://localhost:8300")
    )
    coordinator_timeout_sec: float = field(
        default_factory=lambda: _env_float("ROSTERD_KERNEL_COORDINATOR_TIMEOUT_SEC", 5.0)
    )

    # ---- SpacetimeDB -----------------------------------------------------------
    #: Both unset (the default) means "log every row instead of writing it" --
    #: see spacetime.py. Set both once Joy's module is up.
    spacetimedb_url: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_SPACETIMEDB_URL") or None
    )
    spacetimedb_module: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_SPACETIMEDB_MODULE") or None
    )
    spacetimedb_auth_token: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_SPACETIMEDB_TOKEN") or None
    )

    # ---- OpenTelemetry -----------------------------------------------------------
    otel_enabled: bool = field(default_factory=lambda: _env_bool("ROSTERD_KERNEL_OTEL_ENABLED", True))
    otel_endpoint: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_KERNEL_OTEL_ENDPOINT", "http://localhost:4318")
    )

    @property
    def otel_service_name(self) -> str:
        return f"rosterd-kernel-{self.site_id}"


def get_settings() -> Settings:
    return Settings()
