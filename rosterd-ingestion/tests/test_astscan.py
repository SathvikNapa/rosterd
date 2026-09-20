"""Static tool attribution — the half of discovery that get_graph() cannot do."""
from __future__ import annotations

import ast
from pathlib import Path

from astscan import calls_interrupt, scan_repo, tools_for_source, tools_used_by_function


def _func(source: str) -> ast.FunctionDef:
    """First function def in `source`, for calls_interrupt tests."""
    tree = ast.parse(source)
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


def write(tmp_path: Path, **files: str) -> Path:
    for name, content in files.items():
        (tmp_path / name.replace("__", "/")).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name.replace("__", "/")).write_text(content, encoding="utf-8")
    return tmp_path


def test_finds_tool_decorated_functions(tmp_path):
    root = write(
        tmp_path,
        **{"tools.py": (
            "from langchain_core.tools import tool\n"
            "@tool\ndef issue_refund(x): ...\n"
            "@tool(description='d')\ndef create_ticket(x): ...\n"
            "def not_a_tool(x): ...\n"
        )},
    )
    assert scan_repo(root).tool_names == {"issue_refund", "create_ticket"}


def test_finds_tools_built_from_factories(tmp_path):
    root = write(
        tmp_path,
        **{"tools.py": (
            "from langchain_core.tools import StructuredTool, Tool\n"
            "lookup = StructuredTool.from_function(func=print)\n"
            "legacy = Tool(name='legacy', func=print, description='d')\n"
        )},
    )
    assert scan_repo(root).tool_names == {"lookup", "legacy"}


def test_attributes_a_tool_to_the_node_that_invokes_it(tmp_path):
    root = write(
        tmp_path,
        **{"agent.py": (
            "from langchain_core.tools import tool\n"
            "@tool\ndef issue_refund(x): ...\n"
            "@tool\ndef create_ticket(x): ...\n"
            "def refund_node(state):\n    return issue_refund.invoke({'x': 1})\n"
            "b.add_node('refund_node', refund_node)\n"
        )},
    )
    scan = scan_repo(root)
    assert tools_used_by_function(scan.functions["refund_node"], scan.tool_names) == ["issue_refund"]


def test_finds_tools_passed_to_bind_tools(tmp_path):
    """The common LLM pattern: tools are bound, never called by name."""
    root = write(
        tmp_path,
        **{"agent.py": (
            "from langchain_core.tools import tool\n"
            "@tool\ndef issue_refund(x): ...\n"
            "@tool\ndef create_ticket(x): ...\n"
            "def planner_node(state):\n"
            "    model = llm.bind_tools([issue_refund, create_ticket])\n"
            "    return model.invoke(state)\n"
        )},
    )
    scan = scan_repo(root)
    assert tools_used_by_function(scan.functions["planner_node"], scan.tool_names) == [
        "create_ticket", "issue_refund",
    ]


def test_finds_module_qualified_tool_references(tmp_path):
    root = write(
        tmp_path,
        **{
            "tools.py": "from langchain_core.tools import tool\n@tool\ndef issue_refund(x): ...\n",
            "agent.py": "import tools\ndef refund_node(s):\n    return tools.issue_refund.invoke({})\n",
        },
    )
    scan = scan_repo(root)
    assert tools_used_by_function(scan.functions["refund_node"], scan.tool_names) == ["issue_refund"]


def test_recovers_inline_toolnode_tools(tmp_path):
    root = write(
        tmp_path,
        **{"agent.py": (
            "from langchain_core.tools import tool\n"
            "@tool\ndef issue_refund(x): ...\n"
            "b.add_node('tools', ToolNode([issue_refund]))\n"
        )},
    )
    assert scan_repo(root).node_inline_tools == {"tools": ["issue_refund"]}


def test_recovers_conditional_edges_with_their_router(tmp_path):
    root = write(
        tmp_path,
        **{"agent.py": (
            "b.add_edge(START, 'triage_node')\n"
            "b.add_conditional_edges('triage_node', route, "
            "{'refund_node': 'refund_node', 'escalation_node': 'escalation_node'})\n"
        )},
    )
    edges = scan_repo(root).edges
    assert ("START", "triage_node", None) in edges
    assert ("triage_node", "refund_node", "route") in edges


def test_vendored_directories_are_skipped(tmp_path):
    root = write(
        tmp_path,
        **{
            "agent.py": "from langchain_core.tools import tool\n@tool\ndef mine(x): ...\n",
            ".venv__lib__vendored.py": "from langchain_core.tools import tool\n@tool\ndef theirs(x): ...\n",
        },
    )
    assert scan_repo(root).tool_names == {"mine"}


def test_a_file_that_does_not_parse_is_recorded_not_fatal(tmp_path):
    root = write(
        tmp_path,
        **{
            "good.py": "from langchain_core.tools import tool\n@tool\ndef mine(x): ...\n",
            "bad.py": "def broken(:\n",
        },
    )
    scan = scan_repo(root)
    assert scan.tool_names == {"mine"}
    assert scan.unparsed == ["bad.py"]


def test_tools_for_source_handles_unparseable_input():
    assert tools_for_source("def broken(:", {"x"}) == []


class TestRealWorldPatterns:
    """Patterns found in LangChain's own public templates.

    Each of these was discovered by probing a real repo (scripts/probe.py) and
    finding the wiring came back incomplete.
    """

    def test_toolnode_resolves_a_tool_list_held_in_a_variable(self, tmp_path):
        """`ToolNode(TOOLS)` — how langchain-ai/react-agent does it."""
        root = write(
            tmp_path,
            **{"agent.py": (
                "TOOLS = [search, scrape_website]\n"
                "b.add_node('tools', ToolNode(TOOLS))\n"
            )},
        )
        scan = scan_repo(root)
        assert scan.list_vars["TOOLS"] == ["search", "scrape_website"]
        assert scan.node_inline_tools["tools"] == ["search", "scrape_website"]

    def test_annotated_tool_list_is_resolved(self, tmp_path):
        """`TOOLS: List[Callable[..., Any]] = [search]` is an AnnAssign."""
        root = write(
            tmp_path,
            **{"agent.py": (
                "from typing import Any, Callable, List\n"
                "TOOLS: List[Callable[..., Any]] = [search]\n"
                "b.add_node('tools', ToolNode(TOOLS))\n"
            )},
        )
        assert scan_repo(root).node_inline_tools["tools"] == ["search"]

    def test_a_plain_callable_in_a_tool_list_counts_as_a_tool(self, tmp_path):
        """react-agent's `search` is a bare async def, not @tool-decorated."""
        root = write(
            tmp_path,
            **{"agent.py": (
                "async def search(q): ...\n"
                "TOOLS = [search]\n"
                "b.add_node('tools', ToolNode(TOOLS))\n"
            )},
        )
        assert "search" in scan_repo(root).tool_names

    def test_conditional_edges_recovered_from_a_literal_return_type(self, tmp_path):
        """add_conditional_edges may omit the path map entirely."""
        root = write(
            tmp_path,
            **{"agent.py": (
                "from typing import Literal\n"
                "def route_model_output(state) -> Literal['__end__', 'tools']:\n    ...\n"
                "b.add_conditional_edges('call_model', route_model_output)\n"
            )},
        )
        edges = scan_repo(root).edges
        assert ("call_model", "__end__", "route_model_output") in edges
        assert ("call_model", "tools", "route_model_output") in edges

    def test_single_target_literal_return_type(self, tmp_path):
        root = write(
            tmp_path,
            **{"agent.py": (
                "from typing import Literal\n"
                "def route(state) -> Literal['tools']:\n    ...\n"
                "b.add_conditional_edges('a', route)\n"
            )},
        )
        assert ("a", "tools", "route") in scan_repo(root).edges

    def test_path_map_list_mixing_strings_and_sentinels(self, tmp_path):
        """`add_conditional_edges(src, router, ["store_memory", END])`."""
        root = write(
            tmp_path,
            **{"agent.py": "b.add_conditional_edges('call_model', route, ['store_memory', END])\n"},
        )
        edges = scan_repo(root).edges
        assert ("call_model", "store_memory", "route") in edges
        assert ("call_model", "END", "route") in edges

    def test_an_explicit_path_map_still_wins_over_the_return_type(self, tmp_path):
        """The literal fallback must not override what the author wrote."""
        root = write(
            tmp_path,
            **{"agent.py": (
                "from typing import Literal\n"
                "def route(state) -> Literal['ignored']:\n    ...\n"
                "b.add_conditional_edges('a', route, {'x': 'real_target'})\n"
            )},
        )
        edges = scan_repo(root).edges
        assert ("a", "real_target", "route") in edges
        assert ("a", "ignored", "route") not in edges

    def test_a_string_in_a_tool_list_is_not_treated_as_a_tool(self, tmp_path):
        root = write(
            tmp_path,
            **{"agent.py": "b.add_node('tools', ToolNode(['not_a_tool_name']))\n"},
        )
        assert scan_repo(root).node_inline_tools["tools"] == []

    def test_bind_tools_resolves_a_variable(self, tmp_path):
        root = write(
            tmp_path,
            **{"agent.py": (
                "TOOLS = [search]\n"
                "def call_model(state):\n"
                "    return llm.bind_tools(TOOLS).invoke(state)\n"
            )},
        )
        scan = scan_repo(root)
        assert tools_used_by_function(scan.functions["call_model"], scan.tool_names, scan) == ["search"]


class TestCallsInterrupt:
    def test_bare_interrupt_call_is_detected(self):
        func = _func(
            "def refund_exception_node(state):\n"
            "    decision = interrupt({'reason': 'needs a human'})\n"
            "    return decision\n"
        )
        assert calls_interrupt(func) is True

    def test_qualified_interrupt_call_is_detected(self):
        func = _func(
            "def refund_exception_node(state):\n"
            "    decision = types.interrupt({'reason': 'needs a human'})\n"
            "    return decision\n"
        )
        assert calls_interrupt(func) is True

    def test_a_function_with_no_interrupt_call_is_not_flagged(self):
        func = _func(
            "def fulfillment_node(state):\n"
            "    return attempt_tool(reserve_inventory, {'sku': 'x', 'qty': 1})\n"
        )
        assert calls_interrupt(func) is False

    def test_an_unrelated_function_literally_named_interrupt_is_a_known_limitation(self):
        """Matched by bare call name, not a resolved import -- see calls_interrupt's
        own docstring. This test documents the tradeoff, it doesn't defend against it:
        a human still confirms the result on Review before it governs anything."""
        func = _func(
            "def some_node(state):\n"
            "    interrupt(state)  # a local helper, nothing to do with langgraph\n"
            "    return state\n"
        )
        assert calls_interrupt(func) is True
