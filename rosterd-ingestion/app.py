"""rosterd ingestion service (Person 2).

Endpoints
    POST /ingest                          repo URL + constraints YAML in, draft manifest out
    POST /manifest/{manifest_id}/confirm  approve a draft, optionally with edits
    POST /ask/parse                       plain text in, a proposed task out
    GET  /manifest/{manifest_id}          re-fetch a manifest without re-ingesting

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
from ask import parse_ask
from errors import ManifestNotConfirmedError, NoAssignableAgentError
from ingestion import (
    AskRequest,
    AskResponse,
    ConfirmRequest,
    ConfirmResponse,
    IngestRequest,
    IngestResponse,
    ManifestResponse,
    ManifestStatus,
)
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
        manifest_id=record.manifest_id,
        status=record.provenance.status,
        agents=record.agents,
        graph=record.graph,
    )


@app.get("/manifest/{manifest_id}", response_model=ManifestResponse)
def get_manifest(manifest_id: str) -> ManifestResponse:
    """Return a previously generated manifest. Never re-ingests, never clones."""
    record = _store().get(manifest_id)
    return ManifestResponse(
        manifest_id=record.manifest_id,
        status=record.provenance.status,
        agents=record.agents,
        graph=record.graph,
    )


@app.post("/manifest/{manifest_id}/confirm", response_model=ConfirmResponse)
def post_confirm(manifest_id: str, request: ConfirmRequest) -> ConfirmResponse:
    """Approve a draft manifest, optionally replacing its agent list.

    This is the trust boundary: discovery infers, a human confirms, and only
    then does anything live depend on it. Send an empty `agents` list to
    confirm exactly what was discovered.

    Returns a NEW manifest_id. Confirming derives an immutable confirmed
    manifest rather than mutating the draft, so a kernel pinned to a manifest
    never sees it change, and the draft stays readable next to the confirmed
    version for comparison. See docs/ADR-002-confirm-gate.md.
    """
    record = _store().confirm(manifest_id, request.agents or None)
    return ConfirmResponse(manifest_id=record.manifest_id)


@app.post("/ask/parse", response_model=AskResponse)
def post_ask_parse(request: AskRequest) -> AskResponse:
    """Turn a plain-language request into a proposed task.

    Only *confirmed* manifests can be asked against, and only agents the
    contract marks `direct_assignable` are proposable — an agent reachable
    only via another must not become a direct assignee just because someone
    described it well.

    The result is a proposal for a human to accept, not a dispatch.
    """
    record = _store().get(request.manifest_id)

    if record.provenance.status is not ManifestStatus.confirmed:
        raise ManifestNotConfirmedError(
            "This manifest is still a draft. Confirm it before asking against it.",
            manifest_id=request.manifest_id,
            status=record.provenance.status.value,
        )

    assignable = [a for a in record.agents if a.direct_assignable]
    if not assignable:
        raise NoAssignableAgentError(
            "No agent in this manifest is directly assignable.",
            manifest_id=request.manifest_id,
            agents=[a.id for a in record.agents],
        )

    agent_id, task, confidence = parse_ask(request.text, assignable)
    return AskResponse(agent_id=agent_id, task=task, confidence=confidence)


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
