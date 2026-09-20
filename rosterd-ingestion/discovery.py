"""Turning a cloned repo into a graph structure plus per-node facts.

Two passes, deliberately:

* **Runtime** (`ROSTERD_DISCOVERY_MODE=import`, the default) runs the repo's own
  `get_graph()` in a subprocess. Authoritative for structure — it sees whatever
  the graph actually compiled to, including dynamically built nodes.
* **Static** (AST) never imports anything. It recovers the tools a node body
  reaches for, which `get_graph()` does not expose, and can stand alone as the
  whole of discovery when running untrusted code is not acceptable.

The two are merged: structure from runtime when available, tools from the union
of both. Precedence and the reasoning behind it are in docs/DISCOVERY.md.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import astscan
from config import Settings
from errors import GraphLoadError, GraphNotFoundError
from shared import GraphEdge, GraphSpec

#: LangGraph's synthetic entry/exit nodes. Part of the graph, never agents.
SENTINELS = {"__start__", "__end__"}

#: Module filenames commonly holding a compiled graph, in search order.
_CANDIDATE_FILES = [
    "agent.py", "graph.py", "main.py", "app.py", "workflow.py",
    "src/agent.py", "src/graph.py", "src/main.py", "src/workflow.py",
    "app/agent.py", "app/graph.py", "agents/graph.py",
]

#: Attribute names commonly holding a compiled graph, in search order.
_CANDIDATE_ATTRS = ["graph", "app", "workflow", "agent", "compiled_graph", "chain"]

_WORKER = Path(__file__).parent / "_introspect_worker.py"


@dataclass
class DiscoveredNode:
    """One node of the target graph, as discovered."""

    name: str
    purpose: str = ""
    tools: list[str] = field(default_factory=list)
    kind: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    #: True if this node's function body calls something named `interrupt`
    #: (see astscan.calls_interrupt). manifest.py reads this to infer
    #: direct_assignable when constraints.yaml doesn't say so explicitly.
    has_interrupt: bool = False


@dataclass
class DiscoveryResult:
    """What ingestion learned about the repo before constraints are applied."""

    graph: GraphSpec
    nodes: dict[str, DiscoveredNode]
    agent_nodes: list[str]
    graph_attr: str
    mode: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphLocation:
    module_file: Path
    attr: str
    how: str
    #: What the worker subprocess chdir's into and puts on sys.path[0] --
    #: the repo root by default, but the directory containing langgraph.json
    #: when found there (see _find_langgraph_json): a real monorepo's
    #: package-relative imports (`from app.foo import bar`) resolve against
    #: THAT directory, not the outer clone root one or more levels above it.
    project_root: Path = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.project_root is None:
            object.__setattr__(self, "project_root", self.module_file.parent)


def _parse_spec(spec: str, project_root: Path, *, how: str | None = None) -> GraphLocation | None:
    """Turn a 'path/to/mod.py:attr' or 'pkg.mod:attr' spec into a location,
    resolved relative to `project_root` -- the repo root for a top-level
    spec, or a nested langgraph.json's own directory (see locate_graph)."""
    if ":" not in spec:
        return None
    raw_path, attr = spec.rsplit(":", 1)
    raw_path = raw_path.strip().lstrip("./")
    candidate = project_root / raw_path
    if not candidate.suffix:
        candidate = project_root / (raw_path.replace(".", "/") + ".py")
    if candidate.is_file():
        return GraphLocation(candidate, attr.strip(), how or spec, project_root=project_root)
    return None


def _find_langgraph_json(repo: Path, *, max_depth: int = 3) -> Path | None:
    """Search for langgraph.json up to `max_depth` below the repo root, not
    just at the root itself.

    Real monorepos commonly nest the actual deployable project under a
    subdirectory (backend/, server/, apps/api/) alongside a frontend/ or
    docs/ that share the outer repo -- confirmed against a real one
    (bytedance/deer-flow): its langgraph.json lives at backend/langgraph.json,
    never at the root, so a root-only check silently found nothing and fell
    through to a much less reliable whole-repo compile()-assignment scan
    instead, which is what actually produced "No module named 'app'" (it
    matched an unrelated file with imports that only resolve from a
    different directory than the one the worker put on sys.path).

    Shallowest, first-found match wins -- lexicographic within a depth so
    the result is deterministic, and vendored/build directories are
    excluded the same way astscan already skips them.
    """
    root_candidate = repo / "langgraph.json"
    if root_candidate.is_file():
        return root_candidate
    for depth in range(1, max_depth + 1):
        pattern = "/".join(["*"] * depth) + "/langgraph.json"
        found = sorted(
            p
            for p in repo.glob(pattern)
            if not any(part in astscan._SKIP_DIRS for part in p.relative_to(repo).parts)
        )
        if found:
            return found[0]
    return None


def _compiled_assignments(path: Path) -> list[str]:
    """Top-level names in a file assigned from a `.compile(...)` call."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == "compile":
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def locate_graph(repo: Path, settings: Settings) -> GraphLocation:
    """Find the compiled graph, cheapest and most explicit signal first."""
    # 1. Operator override.
    if settings.graph_spec_override:
        found = _parse_spec(settings.graph_spec_override, repo)
        if found:
            return found
        raise GraphNotFoundError(
            f"ROSTERD_GRAPH_SPEC={settings.graph_spec_override!r} does not resolve in this repo."
        )

    # 2. langgraph.json — the convention the LangGraph CLI itself uses.
    # Searched below the root too, not just at it (see _find_langgraph_json).
    manifest = _find_langgraph_json(repo)
    if manifest is not None:
        project_root = manifest.parent
        rel = manifest.relative_to(repo)
        try:
            declared = json.loads(manifest.read_text(encoding="utf-8")).get("graphs", {})
        except json.JSONDecodeError as exc:
            raise GraphNotFoundError(f"{rel} is not valid JSON: {exc}") from exc
        for name, spec in declared.items():
            found = _parse_spec(str(spec), project_root, how=f"{rel}:{name}")
            if found:
                return found

    # 3. A top-level `X = something.compile()` in a conventional module.
    searched = [repo / rel for rel in _CANDIDATE_FILES]
    searched += [p for p in astscan.iter_python_files(repo) if p not in searched]
    for path in searched:
        if not path.is_file():
            continue
        for name in _compiled_assignments(path):
            return GraphLocation(path, name, f"compile() assignment in {path.name}")

    # 4. A conventional name in a conventional file.
    for rel in _CANDIDATE_FILES:
        path = repo / rel
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue
        assigned = {
            t.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
        }
        for attr in _CANDIDATE_ATTRS:
            if attr in assigned:
                return GraphLocation(path, attr, f"conventional name in {rel}")

    raise GraphNotFoundError(
        "No compiled LangGraph graph found. Add a langgraph.json, or set "
        "ROSTERD_GRAPH_SPEC to 'path/to/module.py:attr'.",
        looked_for=[str(p) for p in _CANDIDATE_FILES],
    )


def _run_worker(location: GraphLocation, settings: Settings) -> dict:
    """Import the graph in a child process and get its structure back.

    The worker chdir's into and sys.path-inserts `location.project_root`,
    not necessarily the outer repo root -- a nested langgraph.json's
    package-relative imports need to resolve against ITS directory (see
    GraphLocation.project_root / _find_langgraph_json)."""
    try:
        completed = subprocess.run(
            [sys.executable, str(_WORKER), str(location.project_root), str(location.module_file), location.attr],
            capture_output=True,
            text=True,
            timeout=settings.import_timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise GraphLoadError(
            f"Importing the graph exceeded {settings.import_timeout_sec}s. "
            "A module-level network call or an input() is the usual cause.",
        ) from exc

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise GraphLoadError(
            "Graph introspection produced no output.",
            stderr=(completed.stderr or "")[-1500:],
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise GraphLoadError(
            "Graph introspection returned malformed output.",
            stdout=stdout[-1500:],
        ) from exc

    if not payload.get("ok"):
        raise GraphLoadError(
            f"Importing the graph failed: {payload.get('error', 'unknown error')}",
            traceback=payload.get("traceback", "")[-2000:],
        )
    return payload


def _normalise(name: str) -> str:
    """AST sees START/END; the compiled graph reports __start__/__end__."""
    return {"START": "__start__", "END": "__end__"}.get(name, name)


def _condition_map(scan: astscan.RepoScan) -> dict[tuple[str, str], str]:
    """(source, target) -> routing function name, recovered statically."""
    mapping: dict[tuple[str, str], str] = {}
    for source, target, condition in scan.edges:
        if condition:
            mapping[(_normalise(source), _normalise(target))] = condition
    return mapping


def _static_tools(scan: astscan.RepoScan, node_name: str) -> list[str]:
    """Tools a node reaches, from its function body or an inline ToolNode."""
    if node_name in scan.node_inline_tools:
        return sorted(scan.node_inline_tools[node_name])
    func_name = scan.node_to_function.get(node_name, node_name)
    func = scan.functions.get(func_name)
    if func is None:
        return []
    return astscan.tools_used_by_function(func, scan.tool_names, scan)


def _static_has_interrupt(scan: astscan.RepoScan, node_name: str) -> bool:
    """Does this node's function body call `interrupt(...)`? Same node-to-
    function resolution as `_static_tools`; an inline ToolNode has no
    function body of its own to check, so it's never interrupt-gated."""
    func_name = scan.node_to_function.get(node_name, node_name)
    func = scan.functions.get(func_name)
    if func is None:
        return False
    return astscan.calls_interrupt(func)


def discover(repo: Path, settings: Settings) -> DiscoveryResult:
    """Discover the graph, its nodes, and each node's tools."""
    repo = repo.resolve()
    scan = astscan.scan_repo(repo)
    warnings: list[str] = []
    if scan.unparsed:
        warnings.append(f"{len(scan.unparsed)} file(s) failed to parse and were skipped.")

    if settings.discovery_mode == "static":
        return _discover_static(repo, scan, warnings)

    location = locate_graph(repo, settings)
    payload = _run_worker(location, settings)
    conditions = _condition_map(scan)

    nodes: dict[str, DiscoveredNode] = {}
    for name, info in payload["nodes"].items():
        node = DiscoveredNode(name=name, kind=info.get("kind", "unknown"))
        if warning := info.get("warning"):
            node.warnings.append(warning)

        # Only trust a docstring that came from the repo. Otherwise we surface
        # LangGraph's own internal docstrings as if they were the agent's purpose.
        source = info.get("source") or {}
        source_file = source.get("file")
        in_repo = bool(source_file) and Path(source_file).resolve().is_relative_to(repo)
        if in_repo:
            node.purpose = info.get("purpose", "")

        runtime_tools = list(info.get("tools") or [])
        static_tools = _static_tools(scan, name)
        node.tools = sorted(dict.fromkeys(runtime_tools + static_tools))
        node.has_interrupt = _static_has_interrupt(scan, name)
        nodes[name] = node

    edges: list[GraphEdge] = []
    for edge in payload["edges"]:
        source, target = edge["source"], edge["target"]
        condition = edge.get("data") or conditions.get((source, target))
        if condition is None and edge.get("conditional"):
            condition = "conditional"
        edges.append(GraphEdge(source=source, target=target, condition=condition))

    node_names = list(payload["nodes"].keys())
    agent_nodes = [n for n in node_names if n not in SENTINELS]
    if not agent_nodes:
        raise GraphLoadError("The graph compiled but contains no agent nodes.")

    return DiscoveryResult(
        graph=GraphSpec(nodes=node_names, edges=edges),
        nodes=nodes,
        agent_nodes=agent_nodes,
        graph_attr=location.how,
        mode="import",
        warnings=warnings,
    )


def _discover_static(repo: Path, scan: astscan.RepoScan, warnings: list[str]) -> DiscoveryResult:
    """Whole-repo AST discovery, with nothing imported."""
    if not scan.declared_nodes:
        raise GraphNotFoundError(
            "Static discovery found no add_node(...) calls. Use "
            "ROSTERD_DISCOVERY_MODE=import for graphs built dynamically."
        )

    agent_nodes = list(scan.declared_nodes)
    node_names = ["__start__", *agent_nodes, "__end__"]

    nodes: dict[str, DiscoveredNode] = {}
    for name in node_names:
        node = DiscoveredNode(name=name, kind="static")
        if name not in SENTINELS:
            func = scan.functions.get(scan.node_to_function.get(name, name))
            if func is not None:
                node.purpose = (ast.get_docstring(func) or "").strip().split("\n")[0]
            node.tools = _static_tools(scan, name)
            node.has_interrupt = _static_has_interrupt(scan, name)
        nodes[name] = node

    seen: set[tuple[str, str]] = set()
    edges: list[GraphEdge] = []
    for source, target, condition in scan.edges:
        pair = (_normalise(source), _normalise(target))
        if pair in seen:
            continue
        seen.add(pair)
        edges.append(GraphEdge(source=pair[0], target=pair[1], condition=condition))

    warnings.append(
        "Static discovery: edges and tools are inferred from source, not from a "
        "compiled graph. Dynamically constructed nodes will be missing."
    )
    return DiscoveryResult(
        graph=GraphSpec(nodes=node_names, edges=edges),
        nodes=nodes,
        agent_nodes=agent_nodes,
        graph_attr="static AST scan",
        mode="static",
        warnings=warnings,
    )
