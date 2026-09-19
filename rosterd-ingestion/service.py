"""The ingest pipeline, end to end.

    clone -> discover graph -> parse constraints -> validate -> merge -> store

Kept separate from app.py so the pipeline can be unit-tested and called from a
script without standing up HTTP.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import constraints_loader
import discovery
import manifest as manifest_builder
from config import Settings
from repo import fetch_repo
from store import ManifestStore, StoredManifest, compute_manifest_id, sha256_text

logger = logging.getLogger("rosterd.ingestion")

#: Where to look for constraints in the repo when the request body omits them.
REPO_CONSTRAINTS_PATHS = ["constraints.yaml", "constraints.yml", ".rosterd/constraints.yaml"]


@dataclass
class IngestOutcome:
    record: StoredManifest
    reused: bool


def _resolve_constraints(repo_path: Path, supplied: str) -> tuple[str, str]:
    """Pick the constraints text to use, and say where it came from.

    The request body wins. Falling back to the repo's own constraints.yaml keeps
    the mockup's "loaded from the repo at ingest time" flow working without
    forcing the frontend to fetch and re-post the file first.
    """
    if supplied and supplied.strip():
        return supplied, "request"
    for rel in REPO_CONSTRAINTS_PATHS:
        candidate = repo_path / rel
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), f"repo:{rel}"
    return "", "none"


def ingest(
    repo_url: str,
    constraints_yaml: str,
    settings: Settings,
    store: ManifestStore,
) -> IngestOutcome:
    """Clone, discover, validate, merge, persist. Idempotent by content."""
    with fetch_repo(repo_url, settings) as fetched:
        logger.info("cloned %s at %s", repo_url, fetched.commit_sha[:12])

        constraints_text, constraints_source = _resolve_constraints(
            fetched.path, constraints_yaml
        )
        constraints_sha = sha256_text(constraints_text)

        # Content address is known before any expensive work beyond the clone,
        # so an unchanged re-ingest short-circuits here.
        manifest_id = compute_manifest_id(repo_url, fetched.commit_sha, constraints_sha)
        if store.exists(manifest_id):
            logger.info("manifest %s already exists; returning it unchanged", manifest_id)
            return IngestOutcome(record=store.get(manifest_id), reused=True)

        discovered = discovery.discover(fetched.path, settings)
        logger.info(
            "discovered %d agent(s) via %s", len(discovered.agent_nodes), discovered.graph_attr
        )

        parsed = constraints_loader.parse_constraints(constraints_text)
        wiring_warnings = constraints_loader.validate_against_graph(
            parsed, discovered.agent_nodes, discovered.graph.edges
        )

        built = manifest_builder.build_manifest(discovered, parsed)
        warnings = built.warnings + wiring_warnings
        if constraints_source == "none":
            warnings.append(
                "No constraints supplied and none found in the repo. Every agent is "
                "unconstrained and not directly assignable."
            )

        record = store.save(
            manifest_id=manifest_id,
            agents=built.agents,
            graph=built.graph,
            repo_url=repo_url,
            commit_sha=fetched.commit_sha,
            constraints_sha=constraints_sha,
            discovery_mode=discovered.mode,
            graph_located_via=discovered.graph_attr,
            warnings=warnings,
        )
        logger.info("stored manifest %s (v%d)", record.manifest_id, record.provenance.version)
        return IngestOutcome(record=record, reused=False)
