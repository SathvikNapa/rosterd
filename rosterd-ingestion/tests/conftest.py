"""Shared fixtures.

Every test builds its own throwaway git repo and its own data dir, so tests
never touch the checked-in demo-agent fixture or each other's manifests.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_AGENT = PROJECT_ROOT / "demo-agent"


def git_init_commit(path: Path) -> str:
    """Make `path` a git repo with one commit, and return the commit sha."""
    env_args = ["-c", "user.name=test", "-c", "user.email=test@local"]
    subprocess.run(["git", "init", "--quiet", str(path)], check=True, capture_output=True)
    subprocess.run(["git", *env_args, "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", *env_args, "commit", "--quiet", "-m", "fixture"],
        cwd=path, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def make_repo(tmp_path: Path):
    """Build a git repo from a {relative_path: contents} mapping.

    Returns (repo_url, repo_path). The URL is the http://localhost/<name> form
    that ROSTERD_LOCAL_REPO_ROOT resolves to an on-disk fixture.
    """
    def _make(name: str, files: dict[str, str]) -> tuple[str, Path]:
        root = tmp_path / "repos" / name
        root.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        git_init_commit(root)
        return f"http://localhost/{name}", root

    return _make


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the service at a throwaway data dir and the fixture repo root."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ROSTERD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ROSTERD_LOCAL_REPO_ROOT", str(tmp_path / "repos"))
    monkeypatch.delenv("ROSTERD_DISCOVERY_MODE", raising=False)
    monkeypatch.delenv("ROSTERD_GRAPH_SPEC", raising=False)
    monkeypatch.delenv("ROSTERD_ALLOWED_HOSTS", raising=False)
    (tmp_path / "repos").mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    import app as app_module

    return TestClient(app_module.app)


@pytest.fixture
def settings(env):
    from config import get_settings

    return get_settings()


# --------------------------------------------------------------- fixture code

SIMPLE_AGENT = '''
"""A two-agent graph."""
from typing import TypedDict

from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph


class S(TypedDict, total=False):
    request: str


@tool
def classify_request(text: str) -> str:
    """Classify a request."""
    return "refund"


@tool
def issue_refund(invoice_id: str, amount_usd: float) -> str:
    """Issue a refund."""
    return "ok"


def triage_node(state: S) -> S:
    """Classifies incoming requests."""
    classify_request.invoke({"text": state.get("request", "")})
    return state


def refund_node(state: S) -> S:
    """Issues refunds, $100 cap."""
    issue_refund.invoke({"invoice_id": "1", "amount_usd": 1.0})
    return state


builder = StateGraph(S)
builder.add_node("triage_node", triage_node)
builder.add_node("refund_node", refund_node)
builder.add_edge(START, "triage_node")
builder.add_edge("triage_node", "refund_node")
builder.add_edge("refund_node", END)
graph = builder.compile()
'''

TOOLNODE_AGENT = '''
"""A graph using a prebuilt ToolNode."""
from typing import TypedDict

from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode


class S(TypedDict, total=False):
    request: str


@tool
def issue_refund(invoice_id: str, amount_usd: float) -> str:
    """Issue a refund."""
    return "ok"


@tool
def create_ticket(subject: str) -> str:
    """Open a ticket."""
    return "ok"


def planner_node(state: S) -> S:
    """Plans the next step."""
    return state


builder = StateGraph(S)
builder.add_node("planner_node", planner_node)
builder.add_node("tools", ToolNode([issue_refund, create_ticket]))
builder.add_edge(START, "planner_node")
builder.add_edge("planner_node", "tools")
builder.add_edge("tools", END)
graph = builder.compile()
'''

SIMPLE_CONSTRAINTS = """
version: 1
constraints:
  triage_node:
    direct_assignable: true
  refund_node:
    direct_assignable: true
    max_refund_usd: 100
"""
