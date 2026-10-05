"""adapters/filesystem/repo.py: fetch_repo()'s host allowlist, local-fixture
containment check, and post-clone size cap -- none of these had a single
test before this file, confirmed by grep. Written after a real failure
report against bytedance/deer-flow (~118MB, over the original 100MB
default) showed the size cap itself had never been exercised, only its
number quoted in docs.

All of these use the `make_repo`/`env` fixtures already in conftest.py,
which build and clone a REAL local git repo -- no mocking `git` itself,
since the clone's actual shallow/no-tags/hooks-off behavior is exactly
what a mock would paper over.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from adapters.filesystem.repo import fetch_repo
from domain.errors import RepoFetchError, RepoNotAllowedError


def test_a_small_repo_clones_and_cleans_up(settings, make_repo):
    url, _ = make_repo("tiny", {"agent.py": "graph = None\n"})

    with fetch_repo(url, settings) as fetched:
        workdir = fetched.path.parent
        assert fetched.is_local is True
        assert len(fetched.commit_sha) == 40  # a real git SHA, not a placeholder
        assert (fetched.path / "agent.py").is_file()

    # The working tree is scratch space -- gone the moment the context exits,
    # per fetch_repo's own docstring ("Anything ingestion needs to keep must
    # be copied into the manifest before this context exits").
    assert not workdir.exists()


def test_a_repo_under_the_size_cap_succeeds(settings, make_repo, monkeypatch):
    monkeypatch.setenv("ROSTERD_MAX_REPO_MB", "1")  # 1MB -- comfortably above a few KB
    from config import get_settings

    url, _ = make_repo("small", {"agent.py": "x = 1\n" * 100})

    with fetch_repo(url, get_settings()) as fetched:
        assert (fetched.path / "agent.py").is_file()


def test_a_repo_over_the_size_cap_is_rejected(settings, make_repo, monkeypatch, tmp_path):
    """The check that actually fired against bytedance/deer-flow -- pinned
    here with a tiny cap and a real oversized file instead, so the test
    doesn't need a genuinely huge fixture to exercise the same code path."""
    monkeypatch.setenv("ROSTERD_MAX_REPO_MB", "1")
    from config import get_settings

    big_content = "x" * (2 * 1024 * 1024)  # 2MB of content, over the 1MB cap
    url, _ = make_repo("big", {"big_file.txt": big_content, "agent.py": "x = 1\n"})

    with pytest.raises(RepoFetchError, match="MB, over the 1 MB limit"):
        with fetch_repo(url, get_settings()):
            pass  # pragma: no cover - should never be reached


def test_default_cap_is_raised_past_a_real_monorepos_shallow_clone_size():
    """bytedance/deer-flow -- a legitimate Python/LangGraph backend bundled
    with an unrelated web frontend, docs, and test fixtures -- shallow-clones
    to ~118MB on a completely normal layout, not an abuse case. The original
    100MB default rejected it outright; see config.py's own comment on
    max_repo_mb for the full story. Pinned here so a future "round the
    number back down" edit has to deliberately break this, not drift back
    silently."""
    from config import Settings

    assert Settings().max_repo_mb >= 300


def test_the_git_tree_is_excluded_from_the_size_count(settings, make_repo, monkeypatch):
    """`.git` itself (history, objects) is explicitly excluded by
    _tree_size_mb -- only the checked-out WORKING TREE counts, since that's
    what discovery actually walks/imports afterward. A repo whose history
    alone would blow the cap, but whose single shallow-cloned commit does
    not, must still succeed."""
    monkeypatch.setenv("ROSTERD_MAX_REPO_MB", "1")
    from config import get_settings

    url, _ = make_repo("shallow-only", {"agent.py": "x = 1\n"})

    with fetch_repo(url, get_settings()) as fetched:
        # The real .git directory is genuinely present (it's a real clone)
        # but must not have been counted against the 1MB cap above.
        assert (fetched.path / ".git").is_dir()


def test_a_disallowed_host_is_rejected_before_any_clone_is_attempted(settings, monkeypatch):
    monkeypatch.setenv("ROSTERD_ALLOWED_HOSTS", "github.com")
    monkeypatch.delenv("ROSTERD_LOCAL_REPO_ROOT", raising=False)
    from config import get_settings

    with pytest.raises(RepoNotAllowedError, match="evil.example.com"):
        with fetch_repo("https://evil.example.com/repo", get_settings()):
            pass  # pragma: no cover - should never be reached


def test_a_local_fixture_path_cannot_escape_the_configured_root(settings, tmp_path):
    """A crafted repo_url like http://localhost/../../etc must not resolve
    outside ROSTERD_LOCAL_REPO_ROOT -- the containment check in
    _resolve_source, exercised here for the first time."""
    with pytest.raises(RepoNotAllowedError, match="escapes"):
        with fetch_repo("http://localhost/../outside", settings):
            pass  # pragma: no cover - should never be reached


def test_a_missing_local_fixture_is_a_clean_repo_fetch_error(settings):
    with pytest.raises(RepoFetchError, match="not found"):
        with fetch_repo("http://localhost/does-not-exist", settings):
            pass  # pragma: no cover - should never be reached
