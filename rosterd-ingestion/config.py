"""Runtime configuration for the ingestion service.

Everything is environment-driven so the service can be run identically by a
teammate, in a test, or in CI without editing code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Schema version of the manifest document this build produces. Bump this when
# the *shape* of a stored manifest changes in a way that older readers cannot
# understand. It participates in the manifest_id hash (see docs/ADR-001).
MANIFEST_SCHEMA_VERSION = 1


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


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    #: Where manifests are persisted. One JSON file per manifest, plus an index.
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("ROSTERD_DATA_DIR", "data")
        ).expanduser().resolve()
    )

    #: "import" executes repo code to call get_graph(); "static" never imports.
    #: See the security note in README.md before changing this.
    discovery_mode: str = field(
        default_factory=lambda: os.environ.get("ROSTERD_DISCOVERY_MODE", "import").strip().lower()
    )

    #: Hard ceiling on `git clone`, in seconds.
    clone_timeout_sec: int = field(default_factory=lambda: _env_int("ROSTERD_CLONE_TIMEOUT_SEC", 60))

    #: Reject a clone whose working tree exceeds this size.
    max_repo_mb: int = field(default_factory=lambda: _env_int("ROSTERD_MAX_REPO_MB", 100))

    #: Wall-clock ceiling on importing + introspecting the graph.
    import_timeout_sec: int = field(default_factory=lambda: _env_int("ROSTERD_IMPORT_TIMEOUT_SEC", 60))

    #: If non-empty, only these hostnames may be cloned.
    allowed_hosts: list[str] = field(default_factory=lambda: _env_list("ROSTERD_ALLOWED_HOSTS"))

    #: DEV ONLY. When set, http://localhost/<name> resolves to <root>/<name>,
    #: which lets tests ingest a repo on disk without a network round trip.
    #: IngestRequest.repo_url is an HttpUrl by contract, so a local fixture has
    #: to arrive dressed as an http URL; this is the documented way to do that.
    local_repo_root: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["ROSTERD_LOCAL_REPO_ROOT"]).expanduser().resolve()
            if os.environ.get("ROSTERD_LOCAL_REPO_ROOT")
            else None
        )
    )

    #: Optional "module:attr" override when a repo declares no langgraph.json
    #: and does not match the conventional layouts.
    graph_spec_override: str | None = field(
        default_factory=lambda: os.environ.get("ROSTERD_GRAPH_SPEC") or None
    )

    @property
    def manifest_dir(self) -> Path:
        return self.data_dir / "manifests"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.json"


def get_settings() -> Settings:
    """Read settings fresh from the environment.

    Deliberately not cached: the tests flip ROSTERD_* between cases.
    """
    return Settings()
