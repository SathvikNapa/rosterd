"""sandbox.py: project-dir discovery and the never-raises contract.

Real `uv sync` / `pip install -e` runs are slow and network-dependent --
verified live against a real repo (bytedance/deer-flow) instead of here (see
sandbox.py's module docstring and rosterd-ingestion's commit history for that
verification). These tests cover the parts that are fast, deterministic, and
actually worth pinning: finding the right project root to install, and the
"never raises, never breaks the fast path" contract when there's nothing to
install or installing is turned off.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sandbox


class TestFindProjectDir:
    def test_finds_pyproject_at_the_start_directory_itself(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        assert sandbox._find_project_dir(tmp_path, tmp_path) == tmp_path

    def test_walks_upward_to_find_a_workspace_root(self, tmp_path):
        """A `uv`/pip workspace's pyproject.toml commonly sits *above* the
        specific package a graph was found in -- confirmed against a real
        repo (bytedance/deer-flow): backend/pyproject.toml sits above
        backend/packages/harness/deerflow/, where the graph factory lives."""
        repo = tmp_path
        (repo / "backend").mkdir()
        (repo / "backend" / "pyproject.toml").write_text("[project]\nname='backend'\n")
        deep = repo / "backend" / "packages" / "harness" / "deerflow"
        deep.mkdir(parents=True)

        assert sandbox._find_project_dir(deep, repo) == repo / "backend"

    def test_stops_at_the_repo_root_without_overshooting(self, tmp_path):
        """No project marker anywhere between start and the repo root (the
        repo root's *parent*, e.g. /tmp itself, is never considered)."""
        repo = tmp_path / "repo"
        deep = repo / "a" / "b"
        deep.mkdir(parents=True)
        assert sandbox._find_project_dir(deep, repo) is None

    def test_recognizes_setup_py_and_setup_cfg_too(self, tmp_path):
        (tmp_path / "setup.py").write_text("")
        assert sandbox._find_project_dir(tmp_path, tmp_path) == tmp_path

        other = tmp_path.parent / "other-fixture-root"
        other.mkdir()
        (other / "setup.cfg").write_text("")
        assert sandbox._find_project_dir(other, other) == other


class TestEnsureInstalled:
    def test_disabled_returns_none_without_touching_the_filesystem(self, tmp_path):
        settings = _settings(sandbox_install_enabled=False)
        assert sandbox.ensure_installed(tmp_path, tmp_path, settings) is None

    def test_no_installable_project_found_returns_none(self, tmp_path):
        """A repo with no pyproject.toml/setup.py anywhere: nothing to
        install, and this must not raise -- the caller falls back to
        whatever interpreter it already had (see discovery._discover_import)."""
        settings = _settings(sandbox_install_enabled=True)
        assert sandbox.ensure_installed(tmp_path, tmp_path, settings) is None

    def test_a_failing_install_returns_none_not_an_exception(self, tmp_path, monkeypatch):
        """Even once a project dir IS found, a failed install (bad command,
        network error, whatever) must degrade to None, per the module's
        documented never-raises contract -- never a new way for ingestion to
        fail outright."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        monkeypatch.setattr(sandbox, "_install_with_pip", lambda *a, **k: None)
        monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)  # no uv on PATH
        settings = _settings(sandbox_install_enabled=True)

        assert sandbox.ensure_installed(tmp_path, tmp_path, settings) is None

    def test_an_installer_that_raises_is_swallowed_too(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

        def _boom(*a, **k):
            raise RuntimeError("pip exploded")

        monkeypatch.setattr(sandbox, "_install_with_pip", _boom)
        monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
        settings = _settings(sandbox_install_enabled=True)

        assert sandbox.ensure_installed(tmp_path, tmp_path, settings) is None

    def test_uv_lock_present_and_uv_on_path_prefers_uv_sync(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "uv.lock").write_text("")
        monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/uv")

        calls = []
        monkeypatch.setattr(sandbox, "_install_with_uv", lambda *a, **k: calls.append("uv") or "/fake/python")
        monkeypatch.setattr(sandbox, "_install_with_pip", lambda *a, **k: calls.append("pip") or "/fake/python")
        settings = _settings(sandbox_install_enabled=True)

        result = sandbox.ensure_installed(tmp_path, tmp_path, settings)
        assert result == "/fake/python"
        assert calls == ["uv"]

    def test_no_uv_lock_falls_back_to_pip_even_with_uv_on_path(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/uv")

        calls = []
        monkeypatch.setattr(sandbox, "_install_with_uv", lambda *a, **k: calls.append("uv") or "/fake/python")
        monkeypatch.setattr(sandbox, "_install_with_pip", lambda *a, **k: calls.append("pip") or "/fake/python")
        settings = _settings(sandbox_install_enabled=True)

        sandbox.ensure_installed(tmp_path, tmp_path, settings)
        assert calls == ["pip"]


def _settings(*, sandbox_install_enabled: bool):
    from config import Settings

    return Settings(sandbox_install_enabled=sandbox_install_enabled)
