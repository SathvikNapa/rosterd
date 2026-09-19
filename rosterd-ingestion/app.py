"""rosterd ingestion service (Person 2).

Endpoints
    POST /ingest                        repo URL + constraints YAML in, manifest out
    GET  /manifest/{manifest_id}        re-fetch a manifest without re-ingesting

Both are exactly the contract in the brief. The three below are additive
conveniences for debugging and for the Contracts screen's "Verified" badge; they
add no fields to the two contracted responses.

    GET  /manifest/{manifest_id}/provenance   commit, hashes, version, warnings
    GET  /manifests                           everything ingested so far
    GET  /healthz                             liveness + effective settings
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import MANIFEST_SCHEMA_VERSION, get_settings
from errors import IngestError
from ingestion import IngestRequest, IngestResponse, ManifestResponse
from service import ingest
from store import ManifestStore, Provenance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="rosterd ingestion service",
    version="1.0.0",
    description="Turns a LangGraph repo plus constraints.yaml into the agent manifest "
    "the kernel enforces and the frontend renders.",
)

# The frontend (Person 3) runs on a different port in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _store() -> ManifestStore:
    return ManifestStore(get_settings())


@app.exception_handler(IngestError)
async def handle_ingest_error(_: Request, exc: IngestError) -> JSONResponse:
    """Every expected failure becomes {code, message, details}."""
    logging.getLogger("rosterd.ingestion").warning("%s: %s", exc.code, exc.message)
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.post("/ingest", response_model=IngestResponse)
def post_ingest(request: IngestRequest) -> IngestResponse:
    """Clone the repo, discover its agents, merge constraints, return a manifest.

    Idempotent by content: the same repo at the same commit with the same
    constraints always yields the same `manifest_id` and does not create a new
    version. See docs/ADR-001-manifest-versioning.md.
    """
    settings = get_settings()
    outcome = ingest(
        repo_url=str(request.repo_url),
        constraints_yaml=request.constraints_yaml,
        settings=settings,
        store=ManifestStore(settings),
    )
    record = outcome.record
    return IngestResponse(
        manifest_id=record.manifest_id, agents=record.agents, graph=record.graph
    )


@app.get("/manifest/{manifest_id}", response_model=ManifestResponse)
def get_manifest(manifest_id: str) -> ManifestResponse:
    """Return a previously generated manifest. Never re-ingests, never clones."""
    record = _store().get(manifest_id)
    return ManifestResponse(
        manifest_id=record.manifest_id, agents=record.agents, graph=record.graph
    )


@app.get("/manifest/{manifest_id}/provenance", response_model=Provenance)
def get_provenance(manifest_id: str) -> Provenance:
    """Commit, constraints hash, version, lineage, and ingest-time warnings.

    Additive to the contracted shape. The Contracts screen reads
    `constraints_sha256` for its "Verified" badge.
    """
    return _store().get(manifest_id).provenance


@app.get("/manifests")
def list_manifests() -> dict:
    """Every manifest ingested, newest first. Debugging aid."""
    return {"manifests": _store().list_all()}


@app.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "discovery_mode": settings.discovery_mode,
        "data_dir": str(settings.data_dir),
        "local_repo_root": str(settings.local_repo_root) if settings.local_repo_root else None,
    }
