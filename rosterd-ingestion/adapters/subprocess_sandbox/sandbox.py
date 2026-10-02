"""Sandboxed dependency installation for a cloned repo, so a graph that only
resolves once the repo's own dependencies are installed (a `uv` workspace
package, anything needing `pip install -e .`) can still be introspected --
`discovery.py`'s fast path (exec a file directly against the ingestion
service's own interpreter) only ever worked for a self-contained repo with
no install step of its own.

SECURITY NOTE, read before extending or reusing this: installing a repo's
declared dependencies means running THAT REPO'S (and every one of its
transitive dependencies') setup/build-time code with this process's own
OS-level privileges, with real network access to fetch packages. A venv
isolates installed PACKAGE STATE -- one ingest's dependencies can't collide
with another's or with the ingestion service's own -- it does NOT isolate
against malicious code execution, network exfiltration, or resource
exhaustion during that install. That gap is accepted deliberately for now,
not overlooked: real isolation (a throwaway, network-restricted container
per ingest) is real, separate follow-up work, tracked as such rather than
half-built here and quietly treated as done. Ship this, be honest about
what it isn't, harden it later.

Never raises: every failure here (no installable project found, `uv`/`pip`
missing, the install itself failing, a timeout) means "nothing changes,
keep using whatever interpreter discovery.py already had" -- a sandboxed
install is a best-effort upgrade over the fast path, never a new way for
ingestion to fail outright.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from config import Settings

logger = logging.getLogger("rosterd.ingestion.sandbox")

#: Presence of any of these in a directory means "this looks like an
#: installable Python project".
_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def _find_project_dir(start: Path, stop: Path) -> Path | None:
    """Walk upward from `start` (a langgraph.json's own directory, or the
    repo root) to `stop` (the repo root) looking for a project marker.
    Upward, not downward: a `uv`/pip workspace's own pyproject.toml is
    commonly one level *above* the specific package a graph was found in
    (confirmed against a real repo: bytedance/deer-flow's
    backend/pyproject.toml sits above backend/packages/harness/deerflow/,
    where the actual graph factory lives)."""
    current = start.resolve()
    stop = stop.resolve()
    while True:
        if any((current / marker).is_file() for marker in _PROJECT_MARKERS):
            return current
        if current == stop or current == current.parent:
            break
        current = current.parent
    return None


def ensure_installed(start: Path, repo: Path, settings: Settings) -> str | None:
    """Best-effort: install a target repo's own dependencies into a fresh,
    throwaway venv, and return that venv's python executable -- or None
    (nothing installable found, or the install failed/timed out), in which
    case the caller keeps using whatever it already had."""
    if not settings.sandbox_install_enabled:
        return None

    project_dir = _find_project_dir(start, repo)
    if project_dir is None:
        logger.info("no pyproject.toml/setup.py/setup.cfg found between %s and %s; nothing to install", start, repo)
        return None

    venv_dir = Path(tempfile.mkdtemp(prefix="rosterd-sandbox-"))
    try:
        if (project_dir / "uv.lock").is_file() and shutil.which("uv"):
            python = _install_with_uv(project_dir, venv_dir, settings)
        else:
            python = _install_with_pip(project_dir, venv_dir, settings)
    except subprocess.TimeoutExpired:
        logger.warning("sandboxed install of %s exceeded the timeout", project_dir)
        python = None
    except Exception:  # noqa: BLE001 - best-effort, see module docstring
        logger.warning("sandboxed install of %s failed", project_dir, exc_info=True)
        python = None

    if python is None:
        shutil.rmtree(venv_dir, ignore_errors=True)
    return python


def _install_with_uv(project_dir: Path, venv_dir: Path, settings: Settings) -> str | None:
    """`uv.lock` present: prefer `uv sync` over `uv pip install -e .`.

    `uv sync` is the one that understands a `uv` workspace correctly --
    `[tool.uv.workspace]` members each get installed as their own editable
    package. `uv pip install -e .` (tried first, before this was found to
    be the wrong command) treats the current directory as a single flat
    package instead, which fails outright against a real workspace layout:
    confirmed against a real repo, bytedance/deer-flow's backend/ has
    app/, samples/, packages/, extension_test_fixtures/ side by side, and
    setuptools' own package-discovery safety check correctly refuses to
    guess which one is "the" package rather than silently picking wrong.

    `UV_PROJECT_ENVIRONMENT` redirects where `uv sync` creates the venv --
    otherwise it defaults to `.venv` inside the cloned repo itself, which
    would still work (that checkout is throwaway) but scatters state
    outside the one place (`venv_dir`) the caller knows to clean up.
    """
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv_dir)}
    result = subprocess.run(
        ["uv", "sync"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=settings.sandbox_install_timeout_sec,
        env=env,
    )
    if result.returncode != 0:
        logger.warning("uv sync failed in %s:\n%s", project_dir, (result.stdout + result.stderr)[-2000:])
        return None
    python = venv_dir / "bin" / "python"
    return str(python) if python.is_file() else None


def _install_with_pip(project_dir: Path, venv_dir: Path, settings: Settings) -> str | None:
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    python = venv_dir / "bin" / "python"
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "-e", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=settings.sandbox_install_timeout_sec,
    )
    if result.returncode != 0:
        logger.warning("pip install -e %s failed:\n%s", project_dir, result.stderr[-2000:])
        return None
    return str(python) if python.is_file() else None
