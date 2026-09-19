# demo-agent (Person 4) -- e-commerce LangGraph system for rosterd

Answers only, never calls out. Endpoints:

| Method | Path | Notes |
|---|---|---|
| POST | /invoke | contract endpoint |
| GET | /graph | contract endpoint |
| GET | /health | extra: Docker healthcheck, shows mode |
| POST | /resume | extra: approve/deny a run paused by interrupt() |

## Files
- `demo_agent.py`   API schema, exactly as in the contract (don't edit)
- `shared.py`       team's shared types, kept verbatim per the team convention (every service carries its own copy)
- `tools.py`        tool schemas: `Field(le=50)`, `Field(le=100)`  <- ingestion reads these
- `refund_node.py`  Refund/Exception node with `interrupt()`      <- ingestion reads this
- `graph.py`        the 3-node graph, incl. a module-level `graph = build_graph()` <- ingestion reads this
- `langgraph.json`  points ingestion at `graph.py:graph` (the convention its discovery checks first)
- `constraints.yaml`  reference copy of the constraints text to paste into ingestion's `POST /ingest` (not auto-read from the repo -- see the file's own header comment, and the gap it flags)
- `brain.py`        scripted vs LLM decision-making
- `toolrun.py`      records tool calls exactly as attempted
- `main.py`         FastAPI app
- `scenarios.py`, `demo_client.py`  seeded demos + a tiny CLI

## Integration status (as verified against `rosterd-ingestion`/`rosterd-kernel` on `main`)
- Ingestion's default discovery (`ROSTERD_DISCOVERY_MODE=import`) locates the
  graph via `langgraph.json` first; without it, this repo had no top-level
  compiled graph for any of ingestion's fallback conventions to find
  (`build_graph()` only lived inside a function). Fixed by adding
  `langgraph.json` + the module-level `graph` in `graph.py`.
- Tool attribution is AST/name-based and works as-is: `attempt_tool(reserve_inventory, ...)`
  and `attempt_tool(issue_refund, ...)` are recognised because `astscan.py`
  matches any bare reference to a known tool name inside a node's function
  body, not only a direct `.invoke(...)` call.
- **Not yet closed, not fixable from this folder:** ingestion still requires
  a hand-written `constraints.yaml` (no code path infers rules from
  `Field(le=...)` or `interrupt()` yet -- see `rosterd-ingestion/docs/ADR-002-confirm-gate.md`),
  and even a correct `constraints.yaml` won't reach the kernel's evaluator
  correctly today: `rosterd-kernel/manifest.py` documents that ingestion's
  committed `AgentConstraints` (a flat blob) and the kernel's expected
  `list[ConstraintRule]` (dot-path `field`/`op`/`value`) have not converged.
  See `constraints.yaml`'s header comment for the concrete field this
  affects (`amount` / `max_refund_usd`). This needs Param and/or Sathvik,
  not a change here.

## Modes
- `AGENT_MODE=scripted` (default): deterministic, no network. Use for the stage demo.
- `AGENT_MODE=llm` + `ANTHROPIC_API_KEY=...`: a real model decides (optional `DEMO_LLM_MODEL`).
  Any LLM failure falls back to scripted for that call, so a run never crashes.
- Per request override: put `"mode": "llm"` in `input.context`.

## Run locally
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    python -m pytest -q
    uvicorn main:app --port 8000
    python demo_client.py misdirection          # scripted
    python demo_client.py misdirection --llm    # unscripted (needs key)

## Docker
    docker build -t demo-agent .
    docker run --rm -p 8000:8000 demo-agent
