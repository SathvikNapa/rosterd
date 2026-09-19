"""Typed kernel failures.

Mirrors rosterd-ingestion/errors.py's shape deliberately: every expected
(non-bug) failure carries an HTTP status and a machine-readable `code`, so
the frontend can branch on the failure without string-matching a message.
"""
from __future__ import annotations

from typing import Any


class KernelError(Exception):
    """Base class for every expected (non-bug) kernel failure."""

    status_code: int = 400
    code: str = "kernel_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ManifestNotReadyError(KernelError):
    """No confirmed manifest has been loaded yet for this site."""

    status_code = 503
    code = "manifest_not_ready"


class AgentNotFoundError(KernelError):
    """`agent_id` does not appear in the currently confirmed manifest."""

    status_code = 404
    code = "agent_not_found"


class RunNotFoundError(KernelError):
    """GET/POST against a run_id the kernel has never issued."""

    status_code = 404
    code = "run_not_found"
