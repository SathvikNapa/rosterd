"""Subprocess worker: import a target repo's CrewAI Crew and dump its
structure in the SAME {ok, nodes, edges} shape _introspect_worker.py
produces for a LangChain Runnable, so discovery.py's downstream processing
(building DiscoveredNode/GraphSpec, merging in static tool evidence) does
not need to know which framework actually produced it.

Run as a child process for the same three reasons _introspect_worker.py's
own docstring gives -- importing a cloned repo executes code we did not
write.

Protocol: argv is [repo_path, module_file, attr]; a JSON document goes to
stdout. {"ok": true, ...} on success, {"ok": false, "error": ...} on
failure.

Mapping CrewAI's own object model onto {nodes, edges} -- settled with the
user before writing any of this, not assumed:

* A Task is the node, not the Agent. An Agent has no position in the
  workflow by itself; only a Task does (via Process.sequential's implicit
  ordering, or an explicit `context=[...]` dependency) -- the same role a
  LangGraph node plays.
* Confirmed empirically against a real crew (crewai 1.15.23), not
  assumed: `task.tools` already includes the executing agent's own tools
  -- CrewAI copies them in at Task construction, so this never has to
  separately read `task.agent.tools`.
* A node's name is `task.name` if the author set one, else the executing
  agent's own `role`, deduplicated with a numeric suffix if the same
  agent runs more than one task -- CrewAI gives a Task no other
  human-meaningful identifier than that (its own `.id` is a bare UUID).
* `task.context` being a real list (not CrewAI's own NOT_SPECIFIED
  sentinel) means an explicit dependency: an edge from each task in it to
  this one. `Process.sequential` with no explicit context on a task
  additionally gets an implicit edge from the task immediately before it
  in `crew.tasks` -- CrewAI's own real runtime behavior for that process,
  confirmed against CrewAI's own docs, not a convention this invents.
  `Process.hierarchical`'s manager agent decides routing at runtime,
  which isn't statically knowable -- no implicit edges are synthesized
  for it, same spirit as LangGraph's own documented "a graph assembled
  per-request is discovered as it looked at import" limit.
* There is no interrupt()-equivalent in CrewAI at all. Every node from
  this worker reports no gate -- discovery.py never calls
  astscan.calls_interrupt for a CrewAI result, and the manifest this
  produces is always direct_assignable=true / entry_only_via=[] for
  every node, full stop. An honest capability gap, not a simulated gate
  (settled explicitly, not a default this drifted into).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
import traceback
from typing import Any


def _load_module(module_ref: str) -> Any:
    """Same dispatch as _introspect_worker.py's _load_module -- see its
    docstring; duplicated rather than imported so each worker stays a
    single, independently-readable script."""
    if module_ref.startswith("dotted:"):
        return importlib.import_module(module_ref[len("dotted:") :])
    spec = importlib.util.spec_from_file_location("_rosterd_crewai_target", module_ref)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load a module spec from {module_ref}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_rosterd_crewai_target"] = module
    spec.loader.exec_module(module)
    return module


def _load_crew(module_ref: str, attr: str) -> Any:
    module = _load_module(module_ref)
    obj = getattr(module, attr, None)
    if obj is None:
        raise AttributeError(f"{module_ref} has no attribute {attr!r}")
    if callable(obj) and not hasattr(obj, "tasks"):
        obj = obj()
    if not hasattr(obj, "tasks") or not hasattr(obj, "agents"):
        raise TypeError(f"{attr!r} is a {type(obj).__name__}, not a crewai.Crew (no .tasks/.agents)")
    return obj


def _task_name(task: Any, role_counts: dict[str, int]) -> str:
    name = getattr(task, "name", None)
    if name:
        return str(name)
    agent = getattr(task, "agent", None)
    role = agent.role if agent is not None else "unassigned"
    role_counts[role] = role_counts.get(role, 0) + 1
    return role if role_counts[role] == 1 else f"{role}_{role_counts[role]}"


def _first_sentence(text: str) -> str:
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return ""
    return re.split(r"(?<=[.!?])\s+", collapsed, maxsplit=1)[0].strip()


def main() -> int:
    repo_path, module_file, attr = sys.argv[1], sys.argv[2], sys.argv[3]

    os.chdir(repo_path)
    sys.path.insert(0, repo_path)
    sys.path = [p for p in sys.path if p not in ("", os.path.dirname(os.path.abspath(__file__)))]

    try:
        crew = _load_crew(module_file, attr)
        tasks = list(crew.tasks)

        role_counts: dict[str, int] = {}
        names = [_task_name(task, role_counts) for task in tasks]

        nodes: dict[str, Any] = {}
        for task, name in zip(tasks, names):
            agent = getattr(task, "agent", None)
            raw_tools = getattr(task, "tools", None) or []
            tools = sorted({str(getattr(tool, "name", tool)) for tool in raw_tools})
            nodes[name] = {
                "kind": "CrewAI Task",
                "purpose": _first_sentence(getattr(task, "description", "") or ""),
                "tools": tools,
                "source": None,
                "agent_role": agent.role if agent is not None else None,
            }

        process = str(getattr(crew, "process", "")).rsplit(".", 1)[-1].lower()

        edges: list[dict[str, Any]] = []
        for index, task in enumerate(tasks):
            context = getattr(task, "context", None)
            if isinstance(context, list):
                for dep in context:
                    if dep in tasks:
                        edges.append(
                            {"source": names[tasks.index(dep)], "target": names[index], "conditional": False, "data": None}
                        )
            elif process == "sequential" and index > 0:
                edges.append({"source": names[index - 1], "target": names[index], "conditional": False, "data": None})

        json.dump(
            {"ok": True, "nodes": nodes, "edges": edges, "attr": attr, "process": process},
            sys.stdout,
        )
        return 0
    except BaseException as exc:
        json.dump(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-4000:]},
            sys.stdout,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
