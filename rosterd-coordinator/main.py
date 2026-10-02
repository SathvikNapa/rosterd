"""Thin entrypoint -- the real FastAPI app lives in adapters/http_in/app.py.

Kept at the service root (rather than, say, adapters/http_in/app.py being
the uvicorn target directly) so `uvicorn main:app`, `pytest.ini`'s
`pythonpath = .`, and every doc/script that says "run main:app" all keep
meaning the same thing no matter how the internals move around underneath.
"""
from adapters.http_in.app import Container, app, build_container, create_app

__all__ = ["app", "build_container", "create_app", "Container"]
