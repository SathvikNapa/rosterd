"""Thin entrypoint -- see adapters/http_in/app.py for the real FastAPI app.
Kept at the root so `uvicorn main:app` (and any script expecting it there)
keeps working unchanged."""

from adapters.http_in.app import app

__all__ = ["app"]
