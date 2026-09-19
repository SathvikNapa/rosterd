"""Fetching the target repo.

Clone is deliberately narrow: shallow, no tags, no submodules, hooks disabled,
credential prompts disabled, wall-clock timeout, and a post-clone size cap.
A repo we clone is untrusted input; see the security note in README.md.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from config import Settings
from errors import RepoFetchError, RepoNotAllowedError

#: Hosts that resolve to an on-disk fixture when local_repo_root is configured.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "local"}


@dataclass(frozen=True)
class FetchedRepo:
    """A checked-out working tree plus the commit it is pinned to."""

    path: Path
    commit_sha: str
    source: str
    is_local: bool


def _resolve_source(repo_url: str, settings: Settings) -> tuple[str, bool]:
    """Map the request's repo_url onto something `git clone` accepts.

    Returns (clone_source, is_local). Remote URLs pass through untouched.
    """
    parsed = urlparse(repo_url)
    host = (parsed.hostname or "").lower()

    if settings.local_repo_root is not None and host in _LOCAL_HOSTS:
        root = settings.local_repo_root
        candidate = (root / parsed.path.lstrip("/")).resolve()
        # Containment check: a crafted path must not escape the fixture root.
        if not candidate.is_relative_to(root):
            raise RepoNotAllowedError(
                "Local repo path escapes ROSTERD_LOCAL_REPO_ROOT.",
                repo_url=repo_url,
            )
        if not candidate.exists():
            raise RepoFetchError(
                f"Local repo fixture not found: {candidate}", repo_url=repo_url
            )
        return str(candidate), True

    if settings.allowed_hosts and host not in {h.lower() for h in settings.allowed_hosts}:
        raise RepoNotAllowedError(
            f"Host {host!r} is not on ROSTERD_ALLOWED_HOSTS.",
            repo_url=repo_url,
            allowed_hosts=settings.allowed_hosts,
        )

    return repo_url, False


def _tree_size_mb(path: Path) -> float:
    total = 0
    for root, dirs, files in os.walk(path):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(root) / name).stat().st_size
    return total / (1024 * 1024)


def _run_git(args: list[str], *, timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        # Never block on a credential prompt for a private repo.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GCM_INTERACTIVE": "never",
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@contextlib.contextmanager
def fetch_repo(repo_url: str, settings: Settings) -> Iterator[FetchedRepo]:
    """Clone `repo_url` into a temp dir, yield it, then always delete it.

    The working tree is scratch space. Anything ingestion needs to keep must be
    copied into the manifest before this context exits.
    """
    source, is_local = _resolve_source(repo_url, settings)
    workdir = Path(tempfile.mkdtemp(prefix="rosterd-ingest-"))
    checkout = workdir / "repo"

    try:
        clone_args = [
            "-c", "core.hooksPath=/dev/null",   # never run a repo's git hooks
            "clone",
            "--depth", "1",
            "--no-tags",
            "--quiet",
        ]
        if not is_local:
            # Local fixtures are plain directories, not necessarily bare repos.
            clone_args += ["--no-single-branch"]
        clone_args += [source, str(checkout)]

        try:
            result = _run_git(clone_args, timeout=settings.clone_timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise RepoFetchError(
                f"Clone exceeded {settings.clone_timeout_sec}s.", repo_url=repo_url
            ) from exc
        except FileNotFoundError as exc:  # pragma: no cover - git missing
            raise RepoFetchError("`git` is not installed or not on PATH.") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip().splitlines()
            raise RepoFetchError(
                "git clone failed.",
                repo_url=repo_url,
                git_error=stderr[-1] if stderr else f"exit {result.returncode}",
            )

        size_mb = _tree_size_mb(checkout)
        if size_mb > settings.max_repo_mb:
            raise RepoFetchError(
                f"Repo is {size_mb:.1f} MB, over the {settings.max_repo_mb} MB limit.",
                repo_url=repo_url,
            )

        rev = _run_git(["rev-parse", "HEAD"], timeout=15, cwd=checkout)
        commit_sha = rev.stdout.strip() if rev.returncode == 0 else "unknown"

        yield FetchedRepo(
            path=checkout, commit_sha=commit_sha, source=source, is_local=is_local
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
