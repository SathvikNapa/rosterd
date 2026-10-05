"""Turning a cloned repo into a graph structure plus per-node facts.

Two passes, deliberately:

* **Runtime** (`ROSTERD_DISCOVERY_MODE=import`, the default) runs the repo's own
  `get_graph()` in a subprocess. Authoritative for structure — it sees whatever
  the graph actually compiled to, including dynamically built nodes.
* **Static** (AST) never imports anything. It recovers the tools a node body
  reaches for, which `get_graph()` does not expose, and can stand alone as the
  whole of discovery when running untrusted code is not acceptable.

The static pass always runs (its `RepoScan` feeds tool attribution either
way), so when the runtime pass fails for a reason that isn't a missing
Python dependency sandbox.py can install its way around -- a real public
repo's own required runtime config, e.g. an API key or a config.yaml it
refuses to start without, confirmed against a real one, bytedance/deer-flow
-- `discover()` falls back to the static result already computed rather
than failing outright. Lower fidelity (no dynamically built nodes, tools
inferred from source instead of a real `get_graph()` call), but a usable
manifest instead of nothing, with a warning saying exactly why it's the
static result and not the runtime one. Precedence and the reasoning behind
it are in docs/DISCOVERY.md.
"""
from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from domain import astscan
from domain.errors import GraphLoadError, GraphNotFoundError

# NOTE: pragmatic hexagonal exception -- ensure_installed() is a single
# concrete function with no alternate implementation anywhere (same spirit
# as a governed agent's own LLM-brain or tool-invocation adapter -- no
# formal interface, just one concrete implementation), so this stays a
# direct adapter import rather than an invented port.
from adapters.subprocess_sandbox import sandbox
from config import Settings
from rosterd_contracts import GraphEdge, GraphSpec

logger = logging.getLogger("rosterd.ingestion.discovery")

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

#: CrewAI's own equivalents of the two lists above.
_CREWAI_CANDIDATE_FILES = ["crew.py", "main.py", "app.py", "src/crew.py", "src/main.py"]
_CREWAI_CANDIDATE_ATTRS = ["crew"]

# Both worker scripts live in adapters/subprocess_sandbox/, a sibling
# directory to this module's own application/ -- not Path(__file__).parent,
# since this file moved there as part of the hexagonal restructuring and the
# workers didn't move with it (they're adapters, this is application).
_WORKER_DIR = Path(__file__).resolve().parent.parent / "adapters" / "subprocess_sandbox"
_WORKER = _WORKER_DIR / "_introspect_worker.py"
_WORKER_CREWAI = _WORKER_DIR / "crewai_introspect_worker.py"


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
    #: An absolute file path (importable via spec_from_file_location), OR a
    #: dotted module path (e.g. "deerflow.agents") when `is_dotted` -- see
    #: `_parse_spec`'s docstring for why a spec can be either.
    module_ref: str
    attr: str
    how: str
    #: What the worker subprocess chdir's into and puts on sys.path[0] --
    #: the repo root by default, but the directory containing langgraph.json
    #: when found there (see _find_langgraph_json): a real monorepo's
    #: package-relative imports (`from app.foo import bar`) resolve against
    #: THAT directory, not the outer clone root one or more levels above it.
    project_root: Path
    is_dotted: bool = False


def _parse_spec(spec: str, project_root: Path, *, how: str | None = None) -> GraphLocation | None:
    """Turn a langgraph.json graph spec into a location, resolved relative
    to `project_root` -- the repo root for a top-level spec, or a nested
    langgraph.json's own directory (see locate_graph).

    A spec is one of two real, both-documented LangGraph Server shapes:
    * `'./path/to/mod.py:attr'` -- a file path, resolved and returned
      directly if it exists.
    * `'pkg.module:attr'` -- a dotted import into an *installed* package,
      which this function cannot verify by itself (nothing here imports
      anything) -- confirmed necessary against a real repo,
      bytedance/deer-flow's `"deerflow.agents:make_lead_agent"`, where
      `deerflow` only exists once its own workspace package
      (backend/packages/harness/) is actually installed. Returned as a
      best-effort `is_dotted=True` location instead of `None`; the caller
      (discover()'s sandboxed-install retry) is what actually gets a
      chance to make it resolve, by installing the project first and
      retrying with that venv's interpreter.
    """
    if ":" not in spec:
        return None
    raw_path, attr = spec.rsplit(":", 1)
    raw_path = raw_path.strip()
    attr = attr.strip()

    file_like = raw_path.lstrip("./")
    candidate = project_root / file_like
    if not candidate.suffix:
        candidate = project_root / (file_like.replace(".", "/") + ".py")
    if candidate.is_file():
        return GraphLocation(str(candidate), attr, how or spec, project_root=project_root)

    # Didn't resolve to a literal file (checked against the ORIGINAL
    # raw_path, not the reassigned `candidate` above, which always ends in
    # .py by this point and would make this check vacuous). A bare name
    # with no slash and no leading dot reads as a dotted import into an
    # installed package, not a file path that simply doesn't exist yet --
    # e.g. "deerflow.agents", not "./missing.py".
    if "/" not in raw_path and not raw_path.startswith("."):
        return GraphLocation(raw_path, attr, how or spec, project_root=project_root, is_dotted=True)
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


#: Receivers whose `.compile(...)` call is never a LangGraph builder --
#: `re.compile(...)`/`regex.compile(...)` assigned to a module-level name
#: (`PATTERN = re.compile(r"...")`) is an extremely common real-world
#: pattern that matches the exact same "`X.compile(...)`, assigned to a
#: top-level name" AST shape this tier looks for, found live scanning a
#: real repo for this check's own false positives (the same class of bug
#: `_lcel_assignments`' PEP 604 type-union rejection already fixed for the
#: `|` tier -- see `_looks_like_a_type_expression`). This can't cover an
#: aliased `import re as rx`, same honest limit the rest of this tier
#: already has without importing anything.
_NEVER_A_GRAPH_COMPILE_RECEIVERS = {"re", "regex"}


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
                receiver = func.value
                if isinstance(receiver, ast.Name) and receiver.id in _NEVER_A_GRAPH_COMPILE_RECEIVERS:
                    continue
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


#: Builtin type names that show up on the `|` side of a PEP 604 type
#: union (`X | None`, `str | SomeType`) but never as a real LCEL pipe
#: operand -- see _looks_like_a_type_expression's docstring.
_BUILTIN_TYPE_NAMES = {
    "str", "int", "float", "bool", "bytes", "complex", "list", "dict",
    "tuple", "set", "frozenset", "bytearray", "object", "type",
}


def _flatten_bitor_operands(node: ast.expr) -> list[ast.expr]:
    """`a | b | c` parses as a left-associative BinOp(BinOp(a, b), c) --
    flatten it to the three leaf operands so each one can be checked."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flatten_bitor_operands(node.left) + _flatten_bitor_operands(node.right)
    return [node]


def _looks_like_a_type_expression(operand: ast.expr) -> bool:
    """A real LCEL pipe operand is a Runnable -- a call (`ChatOpenAI()`,
    `PromptTemplate.from_template(...)`) or a reference to one. A PEP 604
    type union (`X | Y`, e.g. a type alias like
    `LangChainToolResult = str | LangChainContentBlock | list[...]`) uses
    the IDENTICAL `|` AST shape `_lcel_assignments` looks for -- there is
    no way to tell them apart without importing, which this tier
    deliberately never does (see its module docstring). These three
    shapes are strong enough signals to reject on sight -- found live
    against a real repo, mcp-use/mcp-use, whose langchain_adapter.py has
    exactly that type alias at module level, which `locate_graph` matched
    and returned FIRST, pre-empting any real graph the repo might
    otherwise have: a subscripted generic (`list[X]`, `dict[str, int]`),
    a bare reference to a builtin type name, or `None` -- none of these
    are ever valid LCEL pipe operands."""
    if isinstance(operand, ast.Subscript):
        return True
    if isinstance(operand, ast.Constant) and operand.value is None:
        return True
    return isinstance(operand, ast.Name) and operand.id in _BUILTIN_TYPE_NAMES


def _lcel_assignments(path: Path) -> list[str]:
    """Top-level names assigned from an LCEL `|`-composed expression
    (`chain = prompt | llm | parser`) -- the other half of "a compiled
    thing worth introspecting" this tier looks for. A plain LangChain
    Runnable never calls `.compile()` at all (that's a LangGraph-specific
    step), so `_compiled_assignments` alone misses every pure-LCEL repo
    that doesn't also happen to use one of the conventional names tier 4
    checks. `ast.BitOr` is `|`'s AST node regardless of what the operands
    actually are, so a PEP 604 type union (`X | None`) matches the exact
    same shape -- `_looks_like_a_type_expression` rejects the clearest
    cases of that on sight. What's left still can't be verified as a real
    Runnable without importing it, same honest limit `_compiled_assignments`
    already has for `.compile()` on an arbitrary object. `_run_worker`'s
    own `hasattr(obj, "get_graph")` check is still what actually confirms
    it, same as it already does for every other tier here.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.BitOr):
            operands = _flatten_bitor_operands(node.value)
            if any(_looks_like_a_type_expression(operand) for operand in operands):
                continue
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def locate_graph(repo: Path, settings: Settings) -> GraphLocation:
    """The single best-guess location -- the first candidate
    `locate_graph_candidates` finds. Prefer that function directly when a
    failed import should try the next guess rather than give up; this
    wrapper exists for callers (and tests) that only ever want one."""
    return locate_graph_candidates(repo, settings)[0]


def locate_graph_candidates(repo: Path, settings: Settings) -> list[GraphLocation]:
    """Find the compiled graph, cheapest and most explicit signal first.

    Tiers 1 (`ROSTERD_GRAPH_SPEC`) and 2 (`langgraph.json`) are explicit,
    authoritative signals -- each returns AT MOST one candidate, and an
    operator/repo that declared one explicitly gets exactly that one, not
    a fallback search, if it fails to import. Tiers 3 and 4 are heuristic
    guessing across the whole repo and commonly turn up more than one
    match (a module-level constant that merely LOOKS like a graph --
    `re.compile(...)`, a PEP 604 type union -- can sort before the real
    one in file-scan order; see `_NEVER_A_GRAPH_COMPILE_RECEIVERS` /
    `_looks_like_a_type_expression`), so every tier-3/4 match is returned,
    in priority order, for `_discover_via_import` to try in turn rather
    than committing to the first guess.
    """
    # 1. Operator override.
    if settings.graph_spec_override:
        found = _parse_spec(settings.graph_spec_override, repo)
        if found:
            return [found]
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
                return [found]

    candidates: list[GraphLocation] = []

    # 3. A top-level `X = something.compile()` (LangGraph) or `X = a | b | c`
    # (plain LangChain LCEL -- never calls .compile() at all) in any module.
    searched = [repo / rel for rel in _CANDIDATE_FILES]
    searched += [p for p in astscan.iter_python_files(repo) if p not in searched]
    for path in searched:
        if not path.is_file():
            continue
        for name in _compiled_assignments(path):
            candidates.append(
                GraphLocation(str(path), name, f"compile() assignment in {path.name}", project_root=path.parent)
            )
        for name in _lcel_assignments(path):
            candidates.append(
                GraphLocation(str(path), name, f"LCEL ('|') assignment in {path.name}", project_root=path.parent)
            )

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
                candidates.append(
                    GraphLocation(str(path), attr, f"conventional name in {rel}", project_root=path.parent)
                )

    if candidates:
        return candidates

    raise GraphNotFoundError(
        "No LangGraph graph or LangChain Runnable found. Add a langgraph.json, or set "
        "ROSTERD_GRAPH_SPEC to 'path/to/module.py:attr'.",
        looked_for=[str(p) for p in _CANDIDATE_FILES],
    )


def _imports_crewai(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "crewai" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "crewai":
            return True
    return False


def _crewai_assignments(path: Path) -> list[str]:
    """Top-level names assigned from a `Crew(...)` call -- the CrewAI
    equivalent of `_compiled_assignments`/`_lcel_assignments` above, same
    shape, different target class name."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if func_name == "Crew":
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def is_crewai_repo(repo: Path) -> bool:
    """Cheap, import-free signal: does anything in this repo import
    `crewai` at all? Checked before ever trying the LangChain/LangGraph
    path in `discover()`, since the two are mutually exclusive frameworks
    with no shared base to duck-type against (a `crewai.Crew` has no
    `get_graph()`, unlike every LangChain Runnable) -- there's no "try one,
    fall back to the other" seam the way `_compiled_assignments` vs.
    `_lcel_assignments` is just two patterns checked in the same pass."""
    return any(_imports_crewai(path) for path in astscan.iter_python_files(repo))


def locate_crew(repo: Path, settings: Settings) -> GraphLocation:
    """Find the Crew object, cheapest and most explicit signal first --
    same philosophy as locate_graph, with no langgraph.json-equivalent
    tier (CrewAI has no CLI manifest convention)."""
    if settings.graph_spec_override:
        found = _parse_spec(settings.graph_spec_override, repo)
        if found:
            return found
        raise GraphNotFoundError(
            f"ROSTERD_GRAPH_SPEC={settings.graph_spec_override!r} does not resolve in this repo."
        )

    searched = [repo / rel for rel in _CREWAI_CANDIDATE_FILES]
    searched += [p for p in astscan.iter_python_files(repo) if p not in searched]
    for path in searched:
        if not path.is_file():
            continue
        for name in _crewai_assignments(path):
            return GraphLocation(str(path), name, f"Crew(...) assignment in {path.name}", project_root=path.parent)

    for rel in _CREWAI_CANDIDATE_FILES:
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
        for attr in _CREWAI_CANDIDATE_ATTRS:
            if attr in assigned:
                return GraphLocation(str(path), attr, f"conventional name in {rel}", project_root=path.parent)

    raise GraphNotFoundError(
        "This repo imports crewai, but no Crew(...) assignment was found. Set "
        "ROSTERD_GRAPH_SPEC to 'path/to/module.py:attr'.",
        looked_for=[str(p) for p in _CREWAI_CANDIDATE_FILES],
    )


def _run_worker(
    location: GraphLocation,
    settings: Settings,
    *,
    python_executable: str | None = None,
    worker: Path = _WORKER,
) -> dict:
    """Import the graph (or CrewAI crew, via `worker=_WORKER_CREWAI`) in a
    child process and get its structure back.

    The worker chdir's into and sys.path-inserts `location.project_root`,
    not necessarily the outer repo root -- a nested langgraph.json's
    package-relative imports need to resolve against ITS directory (see
    GraphLocation.project_root / _find_langgraph_json).

    `python_executable` defaults to the ingestion service's own interpreter
    (fast, no install step -- works for a self-contained repo). `discover()`
    passes a sandboxed venv's interpreter instead on a retry after
    sandbox.ensure_installed() -- see its module docstring.

    A `dotted:` prefix on the module-ref argv tells the worker to
    `importlib.import_module()` it instead of loading it as a file --
    see GraphLocation.is_dotted / _parse_spec's docstring for why a
    langgraph.json spec can be either. Both workers share this exact argv
    protocol and JSON response shape -- see crewai_introspect_worker.py's
    own docstring for why that's a deliberate design choice, not an
    accident of copying this function."""
    module_ref = f"dotted:{location.module_ref}" if location.is_dotted else location.module_ref
    try:
        completed = subprocess.run(
            [
                python_executable or sys.executable,
                str(worker),
                str(location.project_root),
                module_ref,
                location.attr,
            ],
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


def _looks_like_missing_dependency(error: GraphLoadError) -> bool:
    """Heuristic: does this failure look like it would be fixed by actually
    installing the repo's own dependencies, rather than e.g. a genuine bug
    in the target graph? Checked against the traceback, not just the
    top-level message -- the failure often surfaces several frames deep, in
    a file the real target transitively imports, not the target itself
    (confirmed against a real repo: bytedance/deer-flow's traceback bottoms
    out in an unrelated notification-channel module, not its own graph
    factory)."""
    text = str(error.message) + str(error.details.get("traceback", ""))
    return "ModuleNotFoundError" in text or ("ImportError" in text and "cannot import name" not in text)


def _discover_import(
    repo: Path, location: GraphLocation, settings: Settings, warnings: list[str], *, worker: Path = _WORKER
) -> dict:
    """The fast path (the ingestion service's own interpreter, no install
    step), with one fallback: a sandboxed install-and-retry when that fails
    on what looks like a missing dependency -- a repo that needs
    `pip install -e .` / `uv sync` before its own graph module is even
    importable. See sandbox.py's module docstring for what this does and
    does not isolate against. `worker` selects which introspection script
    runs (LangChain/LangGraph by default, `_WORKER_CREWAI` for a CrewAI
    repo) -- a CrewAI repo needs its own dependencies installed before
    `import crewai` even resolves just as often as a LangChain one does,
    so this fallback isn't LangChain-specific either."""
    try:
        return _run_worker(location, settings, worker=worker)
    except GraphLoadError as exc:
        original = exc
        if not _looks_like_missing_dependency(exc):
            raise

    logger.info("fast import failed on a missing dependency; trying a sandboxed install")
    python = sandbox.ensure_installed(location.project_root, repo, settings)
    if python is None:
        raise original

    warnings.append(
        "Discovery installed this repo's own dependencies into a sandboxed venv before its "
        "graph module was importable -- see rosterd-ingestion/sandbox.py for what that "
        "does and does not isolate against."
    )
    return _run_worker(location, settings, python_executable=python, worker=worker)


def _discover_crewai(repo: Path, settings: Settings, warnings: list[str]) -> DiscoveryResult:
    """CrewAI has no interrupt()-equivalent and no static AST pass of its
    own (a Task is a declarative object with a description string, not a
    function body to scan for tool calls) -- every node from here is
    direct_assignable with no gate, by explicit design decision, not by
    omission. See crewai_introspect_worker.py's own docstring for the
    full mapping this builds from (a Task is the node; Process.sequential
    gets implicit ordering edges; Process.hierarchical gets none, its
    manager agent's routing isn't statically knowable)."""
    location = locate_crew(repo, settings)
    # _run_worker (called inside _discover_import) already raises GraphLoadError
    # on a failed import -- same as the LangChain path, nothing extra needed here.
    payload = _discover_import(repo, location, settings, warnings, worker=_WORKER_CREWAI)

    nodes: dict[str, DiscoveredNode] = {}
    for name, info in payload["nodes"].items():
        node = DiscoveredNode(name=name, kind=info.get("kind", "unknown"), purpose=info.get("purpose", ""))
        node.tools = list(info.get("tools") or [])
        node.has_interrupt = False  # no interrupt()-equivalent in CrewAI -- see module docstring above
        nodes[name] = node

    edges = [GraphEdge(source=e["source"], target=e["target"], condition=None) for e in payload["edges"]]
    node_names = list(payload["nodes"].keys())
    if not node_names:
        raise GraphLoadError("The crew compiled but contains no tasks.")

    warnings.append(
        "CrewAI has no interrupt()-equivalent: every task here is direct_assignable with no "
        "approval gate, regardless of what constraints.yaml says -- an honest capability gap, "
        f"not a simulated one. Process: {payload.get('process', 'unknown')}."
    )
    if payload.get("process") == "hierarchical":
        warnings.append(
            "Process.hierarchical: a manager agent decides task routing at runtime, which isn't "
            "statically knowable -- only explicit task context=[...] dependencies are reflected "
            "as edges here, same spirit as LangGraph's own 'a graph assembled per-request' limit."
        )

    return DiscoveryResult(
        graph=GraphSpec(nodes=node_names, edges=edges),
        nodes=nodes,
        agent_nodes=node_names,  # every CrewAI task is directly assignable -- no entry_only_via gate exists
        graph_attr=location.how,
        mode="crewai",
        warnings=warnings,
    )


def discover(repo: Path, settings: Settings) -> DiscoveryResult:
    """Discover the graph, its nodes, and each node's tools."""
    repo = repo.resolve()

    # CrewAI and LangChain/LangGraph are mutually exclusive frameworks with
    # no shared base to duck-type against (a crewai.Crew has no
    # get_graph()) -- checked first, cheaply, with nothing imported.
    if is_crewai_repo(repo):
        return _discover_crewai(repo, settings, [])

    scan = astscan.scan_repo(repo)
    warnings: list[str] = []
    if scan.unparsed:
        warnings.append(f"{len(scan.unparsed)} file(s) failed to parse and were skipped.")

    if settings.discovery_mode == "static":
        return _discover_static(repo, scan, warnings)

    try:
        return _discover_via_import(repo, scan, settings, warnings)
    except (GraphLoadError, GraphNotFoundError) as exc:
        # The runtime pass is authoritative when it works, but a real
        # public repo commonly can't be imported here at all for a reason
        # no amount of sandboxed-install retrying fixes -- its own
        # required runtime config (an API key, a config.yaml it refuses
        # to start without), not a missing Python package. Rather than
        # fail the whole ingest, fall back to the static result from the
        # `scan` already computed above: lower fidelity, but a real, usable
        # manifest instead of nothing. See the module docstring.
        logger.info("import-mode discovery failed (%s); falling back to static AST scan", exc)
        warnings.append(
            f"Runtime import failed ({exc}) -- falling back to static source "
            "analysis. Structure and tools are inferred from source, not a "
            "compiled graph; dynamically constructed nodes will be missing."
        )
        try:
            return _discover_static(repo, scan, warnings)
        except GraphNotFoundError:
            # Static found nothing either -- the import failure is the
            # real, actionable error here (a genuine bug, a hang, a
            # missing dependency with nothing to install), not "no
            # add_node() calls found", which would otherwise mask it.
            # Surface it alone, not chained onto the static failure.
            raise exc from None


def _discover_via_import(
    repo: Path, scan: astscan.RepoScan, settings: Settings, warnings: list[str]
) -> DiscoveryResult:
    """The runtime pass: import the real graph in a subprocess and read its
    structure back. Tries every candidate `locate_graph_candidates` found
    (up to `settings.max_graph_candidates`), in priority order, moving on
    to the next on ANY failure -- a heuristic tier-3/4 match that turns
    out not to be a real graph (an import error, or "compiled but
    contains no agent nodes") doesn't mean the repo has no graph, only
    that this particular guess was wrong. Raises the LAST candidate's
    error if every one tried fails -- discover() decides whether to fall
    back to the static scan."""
    candidates = locate_graph_candidates(repo, settings)
    # At least the first candidate always gets a real attempt, regardless
    # of a misconfigured (<=0) ROSTERD_MAX_GRAPH_CANDIDATES.
    tried = candidates[: max(1, settings.max_graph_candidates)]
    last_error: GraphLoadError | GraphNotFoundError | None = None
    for index, location in enumerate(tried):
        try:
            return _discover_one_candidate(repo, location, scan, settings, warnings)
        except (GraphLoadError, GraphNotFoundError) as exc:
            last_error = exc
            if index + 1 < len(tried):
                logger.info("candidate %r (%s) failed (%s); trying the next match", location.attr, location.how, exc)
                warnings.append(f"{location.how} did not resolve to a real graph ({exc}); tried the next match.")
    assert last_error is not None  # locate_graph_candidates never returns an empty, non-raising list
    raise last_error


def _discover_one_candidate(
    repo: Path,
    location: GraphLocation,
    scan: astscan.RepoScan,
    settings: Settings,
    warnings: list[str],
) -> DiscoveryResult:
    """Import exactly ONE located candidate and build the DiscoveryResult
    from it. Split out of `_discover_via_import` so trying the next
    candidate on failure is a plain loop there, not a nested try/except."""
    payload = _discover_import(repo, location, settings, warnings)
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
