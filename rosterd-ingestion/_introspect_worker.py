"""Subprocess worker: import a target repo's graph and dump its structure.

Run as a child process, never in-process, because importing a cloned repo means
executing code we did not write. Isolating it buys three things: a hard
wall-clock timeout, no `sys.modules` pollution in the service, and a crash in
the target repo that cannot take the API down.

Protocol: argv is [repo_path, module_file, attr]; a JSON document goes to
stdout. `{"ok": true, ...}` on success, `{"ok": false, "error": ...}` on
failure. Diagnostics go to stderr so they never corrupt the payload.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
import traceback
from typing import Any


def _first_docline(obj: Any) -> str:
    doc = inspect.getdoc(obj) or ""
    for line in doc.strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def _tool_names_from_binding(obj: Any) -> list[str]:
    """Pull tool names off a model that had .bind_tools() applied."""
    names: list[str] = []
    kwargs = getattr(obj, "kwargs", None)
    if isinstance(kwargs, dict):
        for entry in kwargs.get("tools", []) or []:
            if isinstance(entry, dict):
                # OpenAI-style {"type": "function", "function": {"name": ...}}
                fn = entry.get("function")
                if isinstance(fn, dict) and fn.get("name"):
                    names.append(str(fn["name"]))
                elif entry.get("name"):
                    names.append(str(entry["name"]))
            elif hasattr(entry, "name"):
                names.append(str(entry.name))
    return names


def _unwrap(obj: Any, depth: int = 0) -> list[Any]:
    """Flatten a runnable into the pieces worth inspecting for tools."""
    if obj is None or depth > 4:
        return []
    parts = [obj]
    for attr in ("bound", "runnable", "func", "last", "first"):
        inner = getattr(obj, attr, None)
        if inner is not None and inner is not obj and not isinstance(inner, (str, bytes)):
            parts.extend(_unwrap(inner, depth + 1))
    steps = getattr(obj, "steps", None)
    if isinstance(steps, (list, tuple)):
        for step in steps:
            parts.extend(_unwrap(step, depth + 1))
    return parts


def _describe_node(node: Any) -> dict[str, Any]:
    """Everything we can learn about one node without running it."""
    data = getattr(node, "data", None)
    info: dict[str, Any] = {"kind": type(data).__name__, "purpose": "", "tools": [], "source": None}
    if data is None:
        return info

    tools: list[str] = []
    for part in _unwrap(data):
        # A ToolNode names its tools directly — the strongest signal there is.
        by_name = getattr(part, "tools_by_name", None)
        if isinstance(by_name, dict):
            tools.extend(str(k) for k in by_name)
        tools.extend(_tool_names_from_binding(part))

    info["tools"] = sorted(dict.fromkeys(tools))

    # The original function, for its docstring and source location.
    func = getattr(data, "func", None) or getattr(data, "afunc", None)
    target = func if callable(func) else data
    info["purpose"] = _first_docline(target) or _first_docline(data)
    if isinstance(by_name := getattr(data, "tools_by_name", None), dict):
        info["kind"] = "ToolNode"
        info["purpose"] = info["purpose"] or "Executes bound tools"
    try:
        info["source"] = {
            "file": inspect.getsourcefile(target),
            "qualname": getattr(target, "__qualname__", None),
        }
    except (TypeError, OSError):
        pass
    return info


def _load_module(module_ref: str) -> Any:
    """`module_ref` is either a `dotted:pkg.module` reference -- a real
    Python import, only resolvable once the target repo's own dependencies
    are actually installed (see sandbox.py) -- or a plain file path, loaded
    directly regardless of whether it sits on any package's import path."""
    if module_ref.startswith("dotted:"):
        return importlib.import_module(module_ref[len("dotted:") :])
    spec = importlib.util.spec_from_file_location("_rosterd_target", module_ref)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load a module spec from {module_ref}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_rosterd_target"] = module
    spec.loader.exec_module(module)
    return module


def _load_attr(module_ref: str, attr: str) -> Any:
    module = _load_module(module_ref)

    obj = getattr(module, attr, None)
    if obj is None:
        raise AttributeError(f"{module_ref} has no attribute {attr!r}")

    # A repo may export the builder rather than the compiled graph, or a
    # factory that returns one. Accept all three.
    if not hasattr(obj, "get_graph"):
        if callable(obj):
            try:
                obj = obj()
            except TypeError:
                # A real LangGraph Server deployment commonly exports a
                # factory taking a RunnableConfig, resolved by the platform
                # at request time -- not a zero-arg callable (confirmed
                # against a real repo: bytedance/deer-flow's
                # make_lead_agent(config)). RunnableConfig is a TypedDict,
                # not validated at runtime, so an empty dict is a
                # reasonable stand-in purely for introspection -- this
                # won't always work (a factory that reads specific keys
                # eagerly still fails), but costs nothing to try before
                # giving up.
                obj = obj({})
        if hasattr(obj, "compile") and not hasattr(obj, "get_graph"):
            obj = obj.compile()
    if not hasattr(obj, "get_graph"):
        raise TypeError(f"{attr!r} is a {type(obj).__name__}, which has no get_graph()")
    return obj


def main() -> int:
    repo_path, module_file, attr = sys.argv[1], sys.argv[2], sys.argv[3]

    os.chdir(repo_path)
    sys.path.insert(0, repo_path)
    # Keep the target repo from importing the ingestion service by accident.
    sys.path = [p for p in sys.path if p not in ("", os.path.dirname(os.path.abspath(__file__)))]

    try:
        compiled = _load_attr(module_file, attr)
        drawable = compiled.get_graph()

        nodes = {}
        for name, node in drawable.nodes.items():
            try:
                nodes[name] = _describe_node(node)
            except Exception as exc:  # one bad node must not sink the ingest
                nodes[name] = {"kind": "unknown", "purpose": "", "tools": [],
                               "source": None, "warning": f"{type(exc).__name__}: {exc}"}

        edges = [
            {
                "source": edge.source,
                "target": edge.target,
                "conditional": bool(getattr(edge, "conditional", False)),
                "data": getattr(edge, "data", None) if isinstance(getattr(edge, "data", None), str) else None,
            }
            for edge in drawable.edges
        ]

        json.dump({"ok": True, "nodes": nodes, "edges": edges, "attr": attr}, sys.stdout)
        return 0
    except BaseException as exc:
        json.dump(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-4000:],
            },
            sys.stdout,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
