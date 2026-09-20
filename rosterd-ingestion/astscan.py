"""Static (AST) analysis of a target repo.

Used for two jobs:

1. Finding every tool the repo defines, so a bare name in a node body can be
   recognised as a tool rather than an ordinary function call.
2. Attributing tools to the node function that uses them.

Nothing here imports the target repo, so it is safe to run on untrusted code and
it is the whole implementation of ROSTERD_DISCOVERY_MODE=static.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

#: Directories never worth scanning.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", ".tox", "build", "dist", ".eggs", "site-packages",
}

#: Decorators that mark a function as a LangChain tool.
_TOOL_DECORATORS = {"tool", "langchain_tool", "structured_tool"}

#: Factories whose result is a tool object.
_TOOL_FACTORIES = {"StructuredTool", "Tool"}

#: Calls that attach a list of tools to a model.
_BIND_CALLS = {"bind_tools"}


def iter_python_files(root: Path) -> list[Path]:
    """Every .py file in the repo, skipping vendored and cache directories."""
    found: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found


def _decorator_name(node: ast.expr) -> str | None:
    """Reduce a decorator expression to its bare name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


@dataclass
class RepoScan:
    """Everything the static pass learned about a repo."""

    #: Names of tools the repo defines, e.g. {"issue_refund", "create_ticket"}.
    tool_names: set[str] = field(default_factory=set)

    #: Fully-qualified-ish function name -> AST node, for body analysis.
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)

    #: Node name -> function name, from add_node("x", fn) calls.
    node_to_function: dict[str, str] = field(default_factory=dict)

    #: Node name -> tools, from add_node("x", ToolNode([...])) calls.
    node_inline_tools: dict[str, list[str]] = field(default_factory=dict)

    #: Module-level list literals, e.g. TOOLS = [search, scrape]. Real repos
    #: almost always pass a variable to ToolNode/bind_tools rather than a
    #: literal, so these have to be resolved to recover the tools at all.
    list_vars: dict[str, list[str]] = field(default_factory=dict)

    #: Edges recovered statically: (source, target, condition).
    edges: list[tuple[str, str, str | None]] = field(default_factory=list)

    #: Node names seen in add_node calls, in declaration order.
    declared_nodes: list[str] = field(default_factory=list)

    #: Files that failed to parse (syntax errors in the target repo).
    unparsed: list[str] = field(default_factory=list)


def scan_repo(root: Path) -> RepoScan:
    """Walk every Python file and collect tools, node wiring, and functions.

    Two passes. Definitions (functions, tools, list variables) are collected
    across the whole repo first, because wiring in one file routinely refers to
    a tool list or router defined in another. Resolving those on a single pass
    would depend on file order.
    """
    scan = RepoScan()
    trees: list[ast.AST] = []
    for path in iter_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (SyntaxError, ValueError):
            scan.unparsed.append(str(path.relative_to(root)))
            continue
        trees.append(tree)
        _collect_definitions(tree, scan)

    for tree in trees:
        _collect_wiring(tree, scan)
    return scan


def _collect_definitions(tree: ast.AST, scan: RepoScan) -> None:
    """Pass one: functions, decorated tools, tool factories, and list literals."""
    for node in ast.walk(tree):
        # def foo(...) decorated with @tool
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan.functions[node.name] = node
            for dec in node.decorator_list:
                if _decorator_name(dec) in _TOOL_DECORATORS:
                    scan.tool_names.add(node.name)

        # my_tool = StructuredTool.from_function(...) / Tool(...)
        # TOOLS = [search, scrape]  /  TOOLS: list[Callable] = [search]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                [t for t in node.targets if isinstance(t, ast.Name)]
                if isinstance(node, ast.Assign)
                else ([node.target] if isinstance(node.target, ast.Name) else [])
            )
            value = node.value

            if isinstance(value, ast.Call):
                owner = value.func
                base = None
                if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
                    base = owner.value.id
                elif isinstance(owner, ast.Name):
                    base = owner.id
                if base in _TOOL_FACTORIES:
                    for target in targets:
                        scan.tool_names.add(target.id)

            elif isinstance(value, (ast.List, ast.Tuple)):
                names = [n for n in (_call_name(e) for e in value.elts) if n]
                if names:
                    for target in targets:
                        scan.list_vars[target.id] = names


def _collect_wiring(tree: ast.AST, scan: RepoScan) -> None:
    """Pass two: add_node / add_edge / add_conditional_edges."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _scan_call(node, scan)


def _literal_str(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _name_list(
    node: ast.expr, scan: RepoScan | None = None, *, allow_strings: bool = False
) -> list[str]:
    """Extract bare names from a list/tuple literal, or a variable holding one.

    `ToolNode(TOOLS)` is far more common in real repos than `ToolNode([a, b])`,
    so a bare Name is resolved against the module-level lists collected in pass
    one before giving up.

    `allow_strings` is for conditional-edge path maps, which mix quoted node
    names with bare sentinels: `["store_memory", END]`. Tool lists leave it off,
    since a string in a tool list is not a tool.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        names = []
        for element in node.elts:
            found = _call_name(element)
            if found is None and allow_strings:
                found = _literal_str(element)
            if found:
                names.append(found)
        return names
    if scan is not None:
        name = _call_name(node)
        if name and name in scan.list_vars:
            return list(scan.list_vars[name])
    return []


def _literal_targets(func: ast.FunctionDef | ast.AsyncFunctionDef | None) -> list[str]:
    """Routing targets from a `-> Literal["tools", "__end__"]` return annotation.

    LangGraph lets `add_conditional_edges` omit the path map and infer targets
    from the router's return type. That is the idiomatic style in LangChain's
    own templates, so without this the conditional edges of a typical repo are
    invisible.
    """
    if func is None or func.returns is None:
        return []
    annotation = func.returns
    if not isinstance(annotation, ast.Subscript):
        return []
    if _call_name(annotation.value) != "Literal":
        return []
    target = annotation.slice
    elements = target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
    return [e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def _scan_call(node: ast.Call, scan: RepoScan) -> None:
    name = _call_name(node.func)

    if name == "add_node" and node.args:
        node_name = _literal_str(node.args[0])
        target = node.args[1] if len(node.args) > 1 else None

        # add_node(my_func) — node name defaults to the function name.
        if node_name is None and len(node.args) == 1:
            node_name = _call_name(node.args[0])
            target = node.args[0]

        if node_name:
            if node_name not in scan.declared_nodes:
                scan.declared_nodes.append(node_name)
            if target is not None:
                if isinstance(target, ast.Call) and _call_name(target.func) == "ToolNode":
                    tools = _name_list(target.args[0], scan) if target.args else []
                    scan.node_inline_tools[node_name] = tools
                    # A plain callable in a ToolNode list is a tool even without
                    # an @tool decorator, which is how LangChain templates do it.
                    scan.tool_names.update(tools)
                else:
                    func_name = _call_name(target)
                    if func_name:
                        scan.node_to_function[node_name] = func_name

    elif name == "add_edge" and len(node.args) >= 2:
        source = _literal_str(node.args[0]) or _call_name(node.args[0])
        target = _literal_str(node.args[1]) or _call_name(node.args[1])
        if source and target:
            scan.edges.append((source, target, None))

    elif name == "add_conditional_edges" and node.args:
        source = _literal_str(node.args[0]) or _call_name(node.args[0])
        condition = _call_name(node.args[1]) if len(node.args) > 1 else None
        targets: list[str] = []
        if len(node.args) > 2:
            mapping = node.args[2]
            if isinstance(mapping, ast.Dict):
                for value in mapping.values:
                    found = _literal_str(value) or _call_name(value)
                    if found:
                        targets.append(found)
            else:
                targets.extend(_name_list(mapping, scan, allow_strings=True))
        if not targets and condition:
            # No path map: recover the targets from the router's return type.
            targets.extend(_literal_targets(scan.functions.get(condition)))
        if source:
            for target in targets:
                scan.edges.append((source, target, condition))


def tools_used_by_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    tool_names: set[str],
    scan: RepoScan | None = None,
) -> list[str]:
    """Which known tools does this function body reference?

    Catches three shapes:
      * a bare reference, `issue_refund.invoke({...})` or `issue_refund(...)`
      * a module-qualified one, `tools.issue_refund(...)`
      * a bulk bind, `llm.bind_tools([issue_refund, create_ticket])`
    """
    found: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id in tool_names:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in tool_names:
            found.add(node.attr)
        elif isinstance(node, ast.Call) and _call_name(node.func) in _BIND_CALLS:
            if node.args:
                found.update(_name_list(node.args[0], scan))
    return sorted(found)


def tools_for_source(source: str, tool_names: set[str], scan: RepoScan | None = None) -> list[str]:
    """Same as `tools_used_by_function`, but starting from source text."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return tools_used_by_function(node, tool_names, scan)
    return []


def calls_interrupt(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Does this function body call something literally named `interrupt`?

    LangGraph's human-in-the-loop primitive is `langgraph.types.interrupt`,
    almost always imported bare (`from langgraph.types import interrupt`) and
    called directly -- `interrupt({...})` -- occasionally qualified
    (`types.interrupt(...)`). Matched the same way `tools_used_by_function`
    matches a tool reference: by bare name or by attribute name, not by
    resolving the import. A false positive would need an unrelated function
    named exactly `interrupt` in the node's call graph, which is exactly the
    kind of edge case a human reviewing Review's discovered contract before
    confirming it is there to catch -- this is a starting point for that
    review, not a silent authority (see manifest.py's own "constraints win"
    rule: any explicit `direct_assignable` in constraints.yaml overrides
    this outright).
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and _call_name(node.func) == "interrupt":
            return True
    return False
