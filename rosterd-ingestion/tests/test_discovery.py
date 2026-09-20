"""Task 1: cloning, locating the graph, and extracting nodes/edges/tools."""
from __future__ import annotations

import sys

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


class TestDottedModuleSpecs:
    """langgraph.json can declare a dotted import into an installed package
    (`"deerflow.agents:make_lead_agent"`), not just a file path -- confirmed
    necessary against a real repo, bytedance/deer-flow. `_parse_spec` itself
    never imports anything (nothing here has deerflow installed); it just
    has to recognize the shape and hand back a best-effort dotted location
    for discover()'s sandboxed-install retry to actually resolve."""

    def test_a_bare_dotted_name_with_no_slash_is_recognized_as_a_dotted_import(self, tmp_path):
        location = discovery._parse_spec("deerflow.agents:make_lead_agent", tmp_path)
        assert location is not None
        assert location.is_dotted is True
        assert location.module_ref == "deerflow.agents"
        assert location.attr == "make_lead_agent"

    def test_a_relative_file_path_is_not_treated_as_dotted_even_if_missing(self, tmp_path):
        """`./missing.py:graph` has a slash after stripping `./` -- sorry,
        has no slash once the leading `./` is stripped, but it DOES start
        with `.`, which is what actually rules out the dotted branch. Either
        way it must not silently become a dotted import guess."""
        location = discovery._parse_spec("./missing.py:graph", tmp_path)
        assert location is None

    def test_a_nested_relative_path_with_a_slash_is_not_treated_as_dotted(self, tmp_path):
        location = discovery._parse_spec("some/where/missing.py:graph", tmp_path)
        assert location is None

    def test_a_real_file_on_disk_still_wins_over_the_dotted_fallback(self, tmp_path, make_repo):
        _, path = make_repo("dotted-vs-file", {"deerflow.py": "graph = None\n"})
        # "deerflow:graph" has no slash and no leading dot -- it *could* read
        # as dotted, but a literal deerflow.py exists, so the file path must
        # win (checked before the dotted fallback in _parse_spec).
        location = discovery._parse_spec("deerflow:graph", path)
        assert location is not None
        assert location.is_dotted is False
        assert location.module_ref == str(path / "deerflow.py")


class TestSandboxedInstallRetry:
    """discover()'s fallback to sandbox.ensure_installed() on what looks
    like a missing-dependency failure -- verified live against a real repo
    (bytedance/deer-flow) end to end; these pin the decision logic with a
    fake sandbox so the test suite doesn't need network access or a real
    venv build."""

    def test_looks_like_missing_dependency_matches_modulenotfounderror(self):
        from errors import GraphLoadError

        error = GraphLoadError("boom", traceback="...\nModuleNotFoundError: No module named 'deerflow'\n")
        assert discovery._looks_like_missing_dependency(error) is True

    def test_looks_like_missing_dependency_matches_importerror_without_cannot_import_name(self):
        from errors import GraphLoadError

        error = GraphLoadError("boom", traceback="ImportError: cannot load shared library")
        assert discovery._looks_like_missing_dependency(error) is True

    def test_looks_like_missing_dependency_excludes_cannot_import_name(self):
        """A real bug in the target graph (a typo'd import from an already-
        installed module) must NOT trigger a pointless sandboxed install --
        that failure has nothing to do with a missing dependency."""
        from errors import GraphLoadError

        error = GraphLoadError("boom", traceback="ImportError: cannot import name 'Foo' from 'bar'")
        assert discovery._looks_like_missing_dependency(error) is False

    def test_looks_like_missing_dependency_excludes_unrelated_errors(self):
        from errors import GraphLoadError

        error = GraphLoadError("boom", traceback="RuntimeError: boom at import time")
        assert discovery._looks_like_missing_dependency(error) is False

    def test_a_missing_dependency_triggers_a_sandboxed_retry_that_succeeds(
        self, settings, make_repo, monkeypatch
    ):
        _, path = make_repo(
            "needs-install",
            {
                "agent.py": SIMPLE_AGENT,
                "langgraph.json": '{"graphs": {"g": "./agent.py:graph"}}',
            },
        )

        import sandbox

        calls = {"ensure_installed": 0, "run_worker_pythons": []}
        real_run_worker = discovery._run_worker

        def fake_run_worker(location, settings_, *, python_executable=None):
            calls["run_worker_pythons"].append(python_executable)
            if python_executable is None:
                from errors import GraphLoadError

                raise GraphLoadError(
                    "Importing the graph failed: ModuleNotFoundError: No module named 'nope'",
                    traceback="ModuleNotFoundError: No module named 'nope'",
                )
            return real_run_worker(location, settings_, python_executable=None)

        def fake_ensure_installed(start, repo, settings_):
            calls["ensure_installed"] += 1
            return sys.executable  # "sandboxed" interpreter = this interpreter, for the test

        monkeypatch.setattr(discovery, "_run_worker", fake_run_worker)
        monkeypatch.setattr(sandbox, "ensure_installed", fake_ensure_installed)

        result = discovery.discover(path, settings)

        assert calls["ensure_installed"] == 1
        assert result.agent_nodes == ["triage_node", "refund_node"]
        assert any("sandboxed venv" in w for w in result.warnings)

    def test_when_sandbox_finds_nothing_to_install_the_original_error_propagates(
        self, settings, make_repo, monkeypatch
    ):
        _, path = make_repo(
            "needs-install-but-nothing-found",
            {
                "agent.py": SIMPLE_AGENT,
                "langgraph.json": '{"graphs": {"g": "./agent.py:graph"}}',
            },
        )

        import sandbox

        def fake_run_worker(location, settings_, *, python_executable=None):
            from errors import GraphLoadError

            raise GraphLoadError(
                "Importing the graph failed: ModuleNotFoundError: No module named 'nope'",
                traceback="ModuleNotFoundError: No module named 'nope'",
            )

        monkeypatch.setattr(discovery, "_run_worker", fake_run_worker)
        monkeypatch.setattr(sandbox, "ensure_installed", lambda start, repo, settings_: None)

        with pytest.raises(GraphLoadError, match="No module named 'nope'"):
            discovery.discover(path, settings)

    def test_a_non_dependency_failure_never_triggers_a_sandboxed_install_attempt(
        self, settings, make_repo, monkeypatch
    ):
        _, path = make_repo(
            "broken-for-real",
            {
                "agent.py": "raise RuntimeError('boom at import time')\n",
                "langgraph.json": '{"graphs": {"g": "./agent.py:graph"}}',
            },
        )

        import sandbox

        calls = {"ensure_installed": 0}
        monkeypatch.setattr(
            sandbox, "ensure_installed", lambda *a, **k: calls.__setitem__("ensure_installed", calls["ensure_installed"] + 1)
        )

        with pytest.raises(GraphLoadError, match="boom at import time"):
            discovery.discover(path, settings)
        assert calls["ensure_installed"] == 0


class TestPurposeDiscovery:
    """_introspect_worker.py's _describe_node/_first_docline -- found live
    against a real repo (rosterd-example) after a real bug report: /ask/parse
    routed a fraud-review request to a read-only catalog agent instead of
    order_intake, because catalog's discovered purpose had been truncated
    mid-sentence at a line break, landing on a comma right after the word
    "approval" -- which happened to match the user's text and outscored
    order_intake, whose own purpose had independently leaked a LangGraph
    internal wrapper's docstring instead of reporting empty. Both are real,
    distinct bugs in the same function; both are covered here."""

    def test_a_docstring_wrapped_across_lines_reports_the_full_sentence_not_the_first_line(
        self, settings, make_repo
    ):
        _, path = make_repo(
            "wrapped-docstring",
            {
                "agent.py": (
                    "from typing import TypedDict\n"
                    "from langgraph.graph import END, START, StateGraph\n"
                    "class S(TypedDict, total=False):\n    text: str\n"
                    "def catalog_node(state: S) -> S:\n"
                    "    '''Read-only stock lookup -- no interrupt() gate, no approval needed,\n"
                    "    same as fulfillment.'''\n"
                    "    return state\n"
                    "builder = StateGraph(S)\n"
                    "builder.add_node('catalog_node', catalog_node)\n"
                    "builder.add_edge(START, 'catalog_node')\n"
                    "builder.add_edge('catalog_node', END)\n"
                    "graph = builder.compile()\n"
                ),
                "langgraph.json": '{"graphs": {"g": "./agent.py:graph"}}',
            },
        )
        result = discovery.discover(path, settings)
        purpose = result.nodes["catalog_node"].purpose
        assert purpose == "Read-only stock lookup -- no interrupt() gate, no approval needed, same as fulfillment."
        # The specific old failure mode: truncated at the line break, landing
        # mid-sentence on a word that could accidentally match unrelated text.
        assert not purpose.endswith("needed,")

    def test_a_node_with_no_docstring_of_its_own_reports_an_empty_purpose(self, settings, make_repo):
        """Must NOT silently borrow LangGraph's own internal wrapper class
        docstring -- confirmed live against a real undocumented node
        (order_intake_node): the discovered purpose came back "A much
        simpler version of RunnableLambda that requires sync and async
        functions." before this fix, framework-internal text with nothing
        to do with the node itself."""
        _, path = make_repo(
            "undocumented-node",
            {
                "agent.py": (
                    "from typing import TypedDict\n"
                    "from langgraph.graph import END, START, StateGraph\n"
                    "class S(TypedDict, total=False):\n    text: str\n"
                    "def order_intake_node(state: S) -> S:\n"
                    "    return state\n"
                    "builder = StateGraph(S)\n"
                    "builder.add_node('order_intake_node', order_intake_node)\n"
                    "builder.add_edge(START, 'order_intake_node')\n"
                    "builder.add_edge('order_intake_node', END)\n"
                    "graph = builder.compile()\n"
                ),
                "langgraph.json": '{"graphs": {"g": "./agent.py:graph"}}',
            },
        )
        result = discovery.discover(path, settings)
        purpose = result.nodes["order_intake_node"].purpose
        assert purpose == ""
        assert "RunnableLambda" not in purpose


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
