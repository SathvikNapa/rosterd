# How discovery works

Turning a repo into a list of agents is two passes over the same code, because neither one alone is sufficient.

## The two passes

**Runtime (`import`, the default).** Locates the compiled graph, imports it in a subprocess, and calls `get_graph()`. Authoritative for structure: it reports whatever the graph actually compiled to, including nodes added in a loop or behind a conditional that no static reading would find.

**Static (AST).** Parses every `.py` file without importing anything. This is the only way to get **tools**, because `get_graph()` does not expose them for an ordinary function node — a node that calls `issue_refund.invoke(...)` inside its body looks, at runtime, exactly like a node that calls nothing.

Both run on every ingest in `import` mode and the results are merged. Setting `ROSTERD_DISCOVERY_MODE=static` runs only the second, which never executes target code.

## Locating the graph — LangGraph, or plain LangChain

What's actually being located is anything with a `get_graph()` method — and that's a **LangChain Core `Runnable` method**, not a LangGraph-specific one. Every LCEL chain (`prompt | llm | parser`, built with no LangGraph import at all) has one too, since `CompiledGraph` is itself just a `Runnable`. So a repo doesn't need LangGraph to be discoverable; it needs *something* that exposes `get_graph()`.

In order:

1. `ROSTERD_GRAPH_SPEC`, e.g. `src/graph.py:workflow` — the operator override.
2. `langgraph.json`, the manifest the LangGraph CLI itself uses. Its `graphs` map points at `./agent.py:graph`. LangGraph-specific — a plain LangChain repo won't have one, so this tier is simply skipped for it.
3. A top-level assignment in a conventional module (`agent.py`, `graph.py`, `main.py`, `app.py`, `workflow.py`, and `src/`/`app/` variants), then anywhere else in the repo, matching either `X = <something>.compile(...)` (LangGraph) or `X = a | b | c` (plain LangChain LCEL — a Runnable never calls `.compile()` at all).
4. A conventional attribute name (`graph`, `app`, `workflow`, `agent`, `compiled_graph`, `chain`) assigned in a conventional module.

Steps 2–4 read the file with `ast.parse`, so locating the graph never runs anything — including step 3's LCEL/`.compile()` checks, which recognize the AST shape syntactically and can't verify the operands are real Runnables without importing them. That verification is what "now import it" actually does.

**Tiers 1-2 stop at the first hit — tiers 3-4 don't.** An explicit `ROSTERD_GRAPH_SPEC` or `langgraph.json` entry is authoritative: if it fails to import, that's a real error about the repo's own stated entry point, not something to route around. Tiers 3-4 are heuristic guessing across the whole repo, and commonly turn up more than one match — a module-level constant that merely *looks* like a graph (`PATTERN = re.compile(...)`, a PEP 604 type alias like `Result = str | SomeType | list[SomeType]`) can sort before the real one in file-scan order, confirmed live against two real repos: `mcp-use/mcp-use` had a type-union alias match first, and a synthetic `re.compile(...)` constant would match the exact same `.compile()` AST shape the real tier is looking for. The clearest cases of both (a subscripted generic, a bare builtin type name, `None`, or a `re`/`regex` receiver) are rejected on sight; what's left is tried as a CANDIDATE, not committed to — `locate_graph_candidates()` returns every tier-3/4 match in order, and a failed import (or an import that succeeds but isn't actually a Runnable, which the rejections above can't always catch — see `_looks_like_a_type_expression`'s docstring for the honest limit) moves on to the next one, up to `ROSTERD_MAX_GRAPH_CANDIDATES` (default 5) before giving up on the import pass entirely.

The loader also accepts an uncompiled `StateGraph` builder or a zero-argument factory that returns one, and compiles or calls it — a repo that exports `build_graph` rather than `graph` still works.

**Node names, either way.** A LangGraph node is already keyed by whatever name `add_node("x", fn)` gave it. A plain LCEL chain's nodes are not — `get_graph()` auto-generates an opaque hex id for each step, since nothing in LCEL ever names them. Discovery prefers each node's own `.name` (confirmed empirically: equals the LangGraph key exactly when there is one, so this is a no-op there; for LCEL it's a wrapped function's own name, or the component's class name) and drops the two auto-generated input/output schema nodes LCEL adds at the edges of the whole chain — `_introspect_worker.py`'s `_rename_map` is where this lives.

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

## CrewAI

A CrewAI `Crew` has no `get_graph()` and shares no base class with a LangChain `Runnable` at all, so it's a genuinely separate path, checked **before** the LangChain one: `is_crewai_repo()` looks for `import crewai`/`from crewai import ...` anywhere in the repo (no import, no `Crew(...)` needed yet — cheap and first). If found, `locate_crew()` finds the `Crew(...)` assignment (same tiered approach as `locate_graph()`, minus a langgraph.json-equivalent tier — CrewAI has no CLI manifest convention) and `crewai_introspect_worker.py` imports it in its own subprocess.

The mapping onto the same `{nodes, edges}` shape the LangChain path produces, settled explicitly before writing any of it, not assumed:

- **A `Task` is the node, not the `Agent`.** Only a Task has a position in the workflow (via `Process.sequential`'s ordering, or an explicit `context=[...]` dependency) — the same role a LangGraph node plays. A node's name is `task.name` if set, else the executing agent's own `role` (deduplicated with a numeric suffix if the same agent runs more than one task).
- **Edges** come from an explicit `context=[...]` dependency where set; `Process.sequential` additionally gets an implicit edge from the task immediately before it when no explicit context is given — CrewAI's own real runtime behavior for that process, not a convention invented here. `Process.hierarchical`'s manager agent decides routing at runtime, which isn't statically knowable, so no implicit edges are synthesized for it.
- **Tools** come from `task.tools`, which CrewAI itself already populates from the executing agent's own tools at Task construction (confirmed empirically — nothing here re-reads `agent.tools` separately).
- **There is no `interrupt()`-equivalent in CrewAI**, full stop. Every CrewAI-discovered node is `direct_assignable` with no gate, regardless of what `constraints.yaml` says, and the manifest carries a warning saying so. This was a real design decision, not a default: the alternative (inventing a "needs approval" naming convention CrewAI itself has no concept of) was considered and rejected as more likely to mislead than help.

CrewAI repos get the same sandboxed-install retry as LangChain ones (`sandbox.py`) when their own dependencies aren't importable yet, and the same subprocess isolation `crewai_introspect_worker.py`'s own docstring describes — see the README's "Security note", which applies to both workers equally.

## Known limits

Structure is read from a graph compiled at import time. A graph assembled per-request, or one whose nodes depend on runtime configuration, is discovered as it looked at import.

Static mode cannot see dynamically added nodes at all — it reports what `add_node(...)` calls appear in the source, and warns that its results are inferred. It's also LangGraph-only, by nature of what's reliably AST-discoverable: `add_node()`/`add_edge()`/`add_conditional_edges()` are declarative builder calls, greppable on sight, where LCEL's `|` is just operator overloading on arbitrary expressions — recognizing *that* an assignment is an LCEL chain is cheap (see locating-the-graph tier 3 above), but recovering its *tools and structure* without ever running it is not, so `ROSTERD_DISCOVERY_MODE=static` on a plain-LangChain repo with no LangGraph in it at all will find nothing and say so, honestly, rather than guess. `import` mode (the default) has no such limit — it runs the real chain's own `get_graph()`.

Tool attribution is name-based. A tool reached through an alias, a registry lookup, or a variable that is never spelled out will be missed; declare it in `constraints.yaml`.

The target repo's own dependencies must be importable by the service's interpreter -- `sandbox.py`'s sandboxed install tries this automatically on what looks like a missing-dependency failure. When the runtime import fails for any other reason (a required `config.yaml`, an API key the repo won't start without -- confirmed against a real one, bytedance/deer-flow), `discover()` falls back to the static scan instead of failing the ingest outright, with a warning naming exactly what the import failure was. The ingest only fails with `graph_load_failed` when static finds nothing either (no `add_node(...)` calls anywhere) -- in that case the *import* failure is what's reported, not a generic "nothing found."
