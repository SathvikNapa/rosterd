"""Static tool attribution — the half of discovery that get_graph() cannot do."""
from __future__ import annotations

from pathlib import Path

from astscan import scan_repo, tools_for_source, tools_used_by_function


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
