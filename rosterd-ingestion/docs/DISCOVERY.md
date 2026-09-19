# How discovery works

Turning a repo into a list of agents is two passes over the same code, because neither one alone is sufficient.

## The two passes

**Runtime (`import`, the default).** Locates the compiled graph, imports it in a subprocess, and calls `get_graph()`. Authoritative for structure: it reports whatever the graph actually compiled to, including nodes added in a loop or behind a conditional that no static reading would find.

**Static (AST).** Parses every `.py` file without importing anything. This is the only way to get **tools**, because `get_graph()` does not expose them for an ordinary function node — a node that calls `issue_refund.invoke(...)` inside its body looks, at runtime, exactly like a node that calls nothing.

Both run on every ingest in `import` mode and the results are merged. Setting `ROSTERD_DISCOVERY_MODE=static` runs only the second, which never executes target code.

## Locating the compiled graph

In order, stopping at the first hit:

1. `ROSTERD_GRAPH_SPEC`, e.g. `src/graph.py:workflow` — the operator override.
2. `langgraph.json`, the manifest the LangGraph CLI itself uses. Its `graphs` map points at `./agent.py:graph`.
3. A top-level `X = <something>.compile(...)` assignment in a conventional module (`agent.py`, `graph.py`, `main.py`, `app.py`, `workflow.py`, and `src/`/`app/` variants), then anywhere else in the repo.
4. A conventional attribute name (`graph`, `app`, `workflow`, `agent`, `compiled_graph`, `chain`) assigned in a conventional module.

Steps 2–4 read the file with `ast.parse`, so locating the graph never runs anything. Only step "now import it" does.

The loader also accepts an uncompiled `StateGraph` builder or a zero-argument factory that returns one, and compiles or calls it — a repo that exports `build_graph` rather than `graph` still works.

## Why the import runs in a subprocess

Importing a cloned repo executes code we did not write. Isolating it in a child process buys three things: a hard wall-clock timeout on a module that hangs on a network call or an `input()`; no pollution of the service's `sys.modules` or `sys.path`; and a segfault or `sys.exit()` in the target that cannot take the API down. The child writes a single JSON document to stdout and diagnostics to stderr.

This is isolation, **not a sandbox**. See the security note in the README.

## Where tools come from

Four signals, strongest first:

1. **`constraints.yaml` declares `tools:`** — authoritative, replaces everything below. The escape hatch for a repo whose tools are constructed too dynamically to find.
2. **A `ToolNode`** — `tools_by_name` names its tools exactly, at runtime.
3. **A model with `.bind_tools()` applied** — tool names are read off the binding's `kwargs`.
4. **AST analysis of the node's function body** — names matched against the set of tools the repo defines.

Signals 2–4 are unioned, since each is evidence of a tool the node can reach. Only signal 1 replaces rather than adds.

For the AST pass, a "tool the repo defines" is a function decorated with `@tool`, or a name assigned from `StructuredTool.from_function(...)` or `Tool(...)`. Within a node body it then recognises a bare reference (`issue_refund.invoke({...})`), a module-qualified one (`tools.issue_refund(...)`), and a bulk bind (`llm.bind_tools([issue_refund, create_ticket])`).

When a node ends up with no tools at all, the manifest carries a warning suggesting an explicit `tools:` block, because "this agent has no tools" and "we could not find this agent's tools" look identical downstream and mean very different things.

## Where `purpose` comes from

`constraints.<node>.purpose`, then the node function's first docstring line, then a humanised node name (`refund_node` → `Refund`).

A docstring is only trusted when its source file is **inside the cloned repo**. Without that check, LangGraph's own internals leak in: `__start__` wraps an internal lambda, and its docstring ("A much simpler version of RunnableLambda…") would otherwise be presented as an agent's purpose.

## Known limits

Structure is read from a graph compiled at import time. A graph assembled per-request, or one whose nodes depend on runtime configuration, is discovered as it looked at import.

Static mode cannot see dynamically added nodes at all — it reports what `add_node(...)` calls appear in the source, and warns that its results are inferred.

Tool attribution is name-based. A tool reached through an alias, a registry lookup, or a variable that is never spelled out will be missed; declare it in `constraints.yaml`.

The target repo's own dependencies must be importable by the service's interpreter. In `import` mode, install them into the same virtualenv, or the ingest fails with `graph_load_failed` naming the missing module.
