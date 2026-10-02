"""Thin entrypoint -- see adapters/http_in/app.py for the real FastAPI app.
Kept at the root so `uvicorn main:app` (Dockerfile, scripts, langgraph.json's
own dev-server convention) and `tests/test_service.py`'s `from main import
app` keep working unchanged."""

from adapters.http_in.app import app

__all__ = ["app"]
