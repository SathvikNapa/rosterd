"""Task 1: cloning, locating the graph, and extracting nodes/edges/tools."""
from __future__ import annotations

import pytest
from conftest import DEMO_AGENT, SIMPLE_AGENT, TOOLNODE_AGENT

import discovery
from errors import GraphLoadError, GraphNotFoundError


def test_discovers_nodes_edges_and_tools_from_the_demo_repo(settings):
    result = discovery.discover(DEMO_AGENT, settings)

    assert result.agent_nodes == ["triage_node", "refund_node", "escalation_node"]
    # Sentinels stay in the graph so entry and exit points are visible...
    assert "__start__" in result.graph.nodes and "__end__" in result.graph.nodes
    # ...but are never agents.
    assert not set(result.agent_nodes) & discovery.SENTINELS

    assert result.nodes["refund_node"].tools == ["issue_refund"]
    assert result.nodes["triage_node"].tools == ["classify_request"]
    assert result.nodes["escalation_node"].tools == ["create_ticket"]
    assert result.nodes["refund_node"].purpose == "Issues refunds, $100 cap."


def test_conditional_edges_carry_their_routing_function_name(settings):
    result = discovery.discover(DEMO_AGENT, settings)
    conditional = {
        (e.source, e.target): e.condition for e in result.graph.edges if e.condition
    }
    assert conditional[("triage_node", "refund_node")] == "route_after_triage"
    assert conditional[("refund_node", "escalation_node")] == "route_after_refund"


def test_sentinel_nodes_do_not_leak_langgraph_internal_docstrings(settings):
    """__start__ wraps a LangGraph lambda; its docstring is not a purpose."""
    result = discovery.discover(DEMO_AGENT, settings)
    assert result.nodes["__start__"].purpose == ""


def test_langgraph_json_is_preferred_for_locating_the_graph(settings, make_repo):
    _, path = make_repo(
        "declared",
        {
            "agent.py": SIMPLE_AGENT,
            "langgraph.json": '{"graphs": {"support": "./agent.py:graph"}}',
        },
    )
    result = discovery.discover(path, settings)
    assert result.graph_attr == "langgraph.json:support"


def test_langgraph_json_nested_in_a_subdirectory_is_found_and_its_imports_resolve(settings, make_repo):
    """Found against a real repo (bytedance/deer-flow): langgraph.json lives
    at backend/langgraph.json, never at the root -- a root-only check
    silently found nothing and fell through to a much less reliable
    whole-repo compile()-assignment scan, which is what actually produced
    "No module named 'app'" for a file whose package-relative imports only
    resolve from backend/, not the outer clone root. This monorepo-shaped
    fixture mirrors that: a nested langgraph.json, a package-relative
    import (`from agentapp.tools import issue_refund`) that only works if
    backend/ -- not the repo root -- ends up on sys.path."""
    _, path = make_repo(
        "nested-monorepo",
        {
            "backend/langgraph.json": '{"graphs": {"main": "./agentapp/graph.py:graph"}}',
            "backend/agentapp/__init__.py": "",
            "backend/agentapp/tools.py": (
                "from langchain_core.tools import tool\n"
                "@tool\ndef issue_refund(order_id: str, amount: float) -> str:\n"
                "    '''Issue a refund.'''\n"
                "    return 'ok'\n"
            ),
            "backend/agentapp/graph.py": (
                "from typing import TypedDict\n"
                "from langgraph.graph import END, START, StateGraph\n"
                "from agentapp.tools import issue_refund\n"
                "\n"
                "class S(TypedDict, total=False):\n"
                "    text: str\n"
                "\n"
                "def refund_node(state: S) -> S:\n"
                "    issue_refund.invoke({'order_id': 'x', 'amount': 1.0})\n"
                "    return state\n"
                "\n"
                "builder = StateGraph(S)\n"
                "builder.add_node('refund_node', refund_node)\n"
                "builder.add_edge(START, 'refund_node')\n"
                "builder.add_edge('refund_node', END)\n"
                "graph = builder.compile()\n"
            ),
        },
    )
    result = discovery.discover(path, settings)
    assert result.graph_attr == "backend/langgraph.json:main"
    assert result.agent_nodes == ["refund_node"]
    assert result.nodes["refund_node"].tools == ["issue_refund"]


def test_falls_back_to_a_compile_assignment_without_langgraph_json(settings, make_repo):
    _, path = make_repo("undeclared", {"agent.py": SIMPLE_AGENT})
    result = discovery.discover(path, settings)
    assert "compile() assignment" in result.graph_attr
    assert result.agent_nodes == ["triage_node", "refund_node"]


def test_graph_spec_override_wins(settings, make_repo, monkeypatch):
    _, path = make_repo("override", {"custom/place.py": SIMPLE_AGENT})
    monkeypatch.setenv("ROSTERD_GRAPH_SPEC", "custom/place.py:graph")
    from config import get_settings

    result = discovery.discover(path, get_settings())
    assert result.agent_nodes == ["triage_node", "refund_node"]


def test_toolnode_tools_are_read_from_the_compiled_graph(settings, make_repo):
    """A ToolNode names its tools at runtime; that beats any static guess."""
    _, path = make_repo("toolnode", {"agent.py": TOOLNODE_AGENT})
    result = discovery.discover(path, settings)
    assert result.nodes["tools"].kind == "ToolNode"
    assert result.nodes["tools"].tools == ["create_ticket", "issue_refund"]


def test_repo_without_a_graph_is_rejected(settings, make_repo):
    _, path = make_repo("empty", {"readme.md": "no python here"})
    with pytest.raises(GraphNotFoundError):
        discovery.discover(path, settings)


def test_a_graph_that_raises_on_import_is_reported_not_swallowed(settings, make_repo):
    _, path = make_repo(
        "broken",
        {
            "agent.py": "raise RuntimeError('boom at import time')\n",
            "langgraph.json": '{"graphs": {"g": "./agent.py:graph"}}',
        },
    )
    with pytest.raises(GraphLoadError, match="boom at import time"):
        discovery.discover(path, settings)


def test_import_timeout_is_enforced(settings, make_repo, monkeypatch):
    """A module-level hang must fail the ingest, not wedge the service."""
    _, path = make_repo(
        "hangs",
        {
            "agent.py": "import time\ntime.sleep(30)\ngraph = None\n",
            "langgraph.json": '{"graphs": {"g": "./agent.py:graph"}}',
        },
    )
    monkeypatch.setenv("ROSTERD_IMPORT_TIMEOUT_SEC", "2")
    from config import get_settings

    with pytest.raises(GraphLoadError, match="exceeded"):
        discovery.discover(path, get_settings())


class TestStaticMode:
    """ROSTERD_DISCOVERY_MODE=static never imports the target repo."""

    @pytest.fixture
    def static_settings(self, env, monkeypatch):
        monkeypatch.setenv("ROSTERD_DISCOVERY_MODE", "static")
        from config import get_settings

        return get_settings()

    def test_matches_import_mode_on_the_demo_repo(self, static_settings, settings):
        static = discovery.discover(DEMO_AGENT, static_settings)
        imported = discovery.discover(DEMO_AGENT, settings)

        assert static.mode == "static"
        assert static.agent_nodes == imported.agent_nodes
        for node in static.agent_nodes:
            assert static.nodes[node].tools == imported.nodes[node].tools
        assert {(e.source, e.target) for e in static.graph.edges} == {
            (e.source, e.target) for e in imported.graph.edges
        }

    def test_does_not_execute_repo_code(self, static_settings, make_repo, tmp_path):
        """The canary file proves nothing in the repo ran."""
        canary = tmp_path / "canary.txt"
        _, path = make_repo(
            "canary",
            {
                "agent.py": (
                    f"open({str(canary)!r}, 'w').write('executed')\n"
                    "from langgraph.graph import StateGraph, START, END\n"
                    "from typing import TypedDict\n"
                    "class S(TypedDict, total=False):\n    x: int\n"
                    "def a_node(s):\n    '''Does a thing.'''\n    return s\n"
                    "b = StateGraph(S)\n"
                    "b.add_node('a_node', a_node)\n"
                    "b.add_edge(START, 'a_node')\n"
                    "b.add_edge('a_node', END)\n"
                    "graph = b.compile()\n"
                ),
            },
        )
        result = discovery.discover(path, static_settings)
        assert result.agent_nodes == ["a_node"]
        assert not canary.exists(), "static mode must never execute the target repo"

    def test_warns_that_results_are_inferred(self, static_settings):
        result = discovery.discover(DEMO_AGENT, static_settings)
        assert any("inferred from source" in w for w in result.warnings)
