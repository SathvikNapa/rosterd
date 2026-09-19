"""Manifest persistence and versioning.

Manifests are **immutable and content-addressed**. `manifest_id` is a hash of
(repo_url, commit_sha, constraints_sha256, schema_version), so:

* re-ingesting identical inputs is idempotent and returns the same id;
* any change to the repo or the constraints file produces a *new* id, and the
  old manifest keeps resolving for anything already pinned to it.

That second property is the point. The kernel pins a manifest_id for the life of
a run, so a re-ingest can never change the rules underneath a task that is
already executing. Full reasoning in docs/ADR-001-manifest-versioning.md.

Storage is one JSON file per manifest plus a small index — no database to stand
up, and a manifest stays readable with `cat` when something looks wrong.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from config import MANIFEST_SCHEMA_VERSION, Settings
from errors import ManifestNotFoundError
from ingestion import AgentManifestEntry
from shared import GraphSpec


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalise_repo_url(repo_url: str) -> str:
    """Collapse trivial URL differences so one repo means one lineage."""
    cleaned = repo_url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    return cleaned.lower()


def lineage_id_for(repo_url: str) -> str:
    """Stable id for 'this repo', across every version of its manifest."""
    return "ln_" + sha256_text(_normalise_repo_url(repo_url))[:16]


def compute_manifest_id(repo_url: str, commit_sha: str, constraints_sha: str) -> str:
    """Content address for one exact (repo, commit, constraints, schema) tuple."""
    payload = json.dumps(
        {
            "repo": _normalise_repo_url(repo_url),
            "commit": commit_sha,
            "constraints": constraints_sha,
            "schema": MANIFEST_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "mf_" + sha256_text(payload)[:16]


class Provenance(BaseModel):
    """Where a manifest came from and how it relates to its siblings."""

    lineage_id: str
    version: int
    created_at: datetime
    repo_url: str
    commit_sha: str
    constraints_sha256: str
    schema_version: int = MANIFEST_SCHEMA_VERSION
    discovery_mode: str = "import"
    graph_located_via: str = ""
    supersedes: str | None = None
    superseded_by: str | None = None
    warnings: list[str] = Field(default_factory=list)


class StoredManifest(BaseModel):
    """The full on-disk record. A superset of the wire response."""

    manifest_id: str
    agents: list[AgentManifestEntry]
    graph: GraphSpec
    provenance: Provenance


class ManifestStore:
    """File-backed manifest storage."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.manifest_dir = settings.manifest_dir
        self.index_path = settings.index_path
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- index

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        """Write via temp file + rename so a crash cannot leave a partial file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, default=str)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------ retrieval

    def exists(self, manifest_id: str) -> bool:
        return self._path_for(manifest_id).is_file()

    def _path_for(self, manifest_id: str) -> Path:
        # Never let a caller-supplied id escape the manifest directory.
        safe = "".join(c for c in manifest_id if c.isalnum() or c in "_-")
        return self.manifest_dir / f"{safe}.json"

    def get(self, manifest_id: str) -> StoredManifest:
        path = self._path_for(manifest_id)
        if not path.is_file():
            raise ManifestNotFoundError(
                f"No manifest with id {manifest_id!r}. Run POST /ingest first.",
                manifest_id=manifest_id,
            )
        return StoredManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def lineage(self, lineage_id: str) -> list[dict[str, Any]]:
        """Every manifest for one repo, oldest version first."""
        entries = [
            {"manifest_id": mid, **meta}
            for mid, meta in self._read_index().items()
            if meta.get("lineage_id") == lineage_id
        ]
        return sorted(entries, key=lambda e: e.get("version", 0))

    def list_all(self) -> list[dict[str, Any]]:
        entries = [{"manifest_id": mid, **meta} for mid, meta in self._read_index().items()]
        return sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)

    # ------------------------------------------------------------ persisting

    def save(
        self,
        *,
        manifest_id: str,
        agents: list[AgentManifestEntry],
        graph: GraphSpec,
        repo_url: str,
        commit_sha: str,
        constraints_sha: str,
        discovery_mode: str,
        graph_located_via: str,
        warnings: list[str],
    ) -> StoredManifest:
        """Persist a manifest, assigning it a version within its repo lineage.

        Idempotent: if this exact content was already ingested, the existing
        record is returned untouched rather than re-versioned.
        """
        if self.exists(manifest_id):
            return self.get(manifest_id)

        lineage = lineage_id_for(repo_url)
        siblings = self.lineage(lineage)
        version = (max((s.get("version", 0) for s in siblings), default=0)) + 1
        predecessor = siblings[-1]["manifest_id"] if siblings else None

        record = StoredManifest(
            manifest_id=manifest_id,
            agents=agents,
            graph=graph,
            provenance=Provenance(
                lineage_id=lineage,
                version=version,
                created_at=datetime.now(timezone.utc),
                repo_url=repo_url,
                commit_sha=commit_sha,
                constraints_sha256=constraints_sha,
                discovery_mode=discovery_mode,
                graph_located_via=graph_located_via,
                supersedes=predecessor,
                warnings=warnings,
            ),
        )

        self._write_json_atomic(
            self._path_for(manifest_id), record.model_dump(mode="json")
        )

        index = self._read_index()
        index[manifest_id] = {
            "lineage_id": lineage,
            "version": version,
            "created_at": record.provenance.created_at.isoformat(),
            "repo_url": repo_url,
            "commit_sha": commit_sha,
            "constraints_sha256": constraints_sha,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "agent_count": len(agents),
            "superseded_by": None,
        }
        # The previous version stays readable, but is marked as no longer latest.
        if predecessor and predecessor in index:
            index[predecessor]["superseded_by"] = manifest_id
        self._write_json_atomic(self.index_path, index)

        if predecessor and self.exists(predecessor):
            previous = self.get(predecessor)
            previous.provenance.superseded_by = manifest_id
            self._write_json_atomic(
                self._path_for(predecessor), previous.model_dump(mode="json")
            )

        return record
