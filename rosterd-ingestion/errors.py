"""Typed ingestion failures.

Every one carries an HTTP status and a machine-readable `code`, so the frontend
can branch on the failure without string-matching a message.
"""
from __future__ import annotations

from typing import Any


class IngestError(Exception):
    """Base class for every expected (non-bug) ingestion failure."""

    status_code: int = 400
    code: str = "ingest_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class RepoFetchError(IngestError):
    """Clone failed: bad URL, private repo, network, timeout, or size cap."""

    status_code = 422
    code = "repo_fetch_failed"


class RepoNotAllowedError(IngestError):
    """The repo host is not on ROSTERD_ALLOWED_HOSTS."""

    status_code = 403
    code = "repo_not_allowed"


class GraphNotFoundError(IngestError):
    """No compiled LangGraph graph could be located in the repo."""

    status_code = 422
    code = "graph_not_found"


class GraphLoadError(IngestError):
    """The graph was located but importing or compiling it raised."""

    status_code = 422
    code = "graph_load_failed"


class ConstraintsParseError(IngestError):
    """constraints.yaml is not valid YAML, or is not shaped as expected."""

    status_code = 422
    code = "constraints_invalid"


class ConstraintsValidationError(IngestError):
    """constraints.yaml references node names that the graph does not contain."""

    status_code = 422
    code = "constraints_unknown_nodes"


class ManifestNotFoundError(IngestError):
    """GET /manifest/{id} for an id we have never issued."""

    status_code = 404
    code = "manifest_not_found"
