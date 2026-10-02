# demo-agent - e-commerce LangGraph system for rosterd

Answers only, never calls out. Endpoints:

| Method | Path | Notes |
|---|---|---|
| POST | /invoke | contract endpoint |
| GET | /graph | contract endpoint |
| GET | /health | extra: Docker healthcheck, shows mode |
| POST | /resume | extra: approve/deny a run paused by interrupt() |

## Layout

Hexagonal: `domain/` (pure logic + wire contracts) -> `application/` (the
graph, the refund node -- orchestration) -> `adapters/` (the LangChain/LLM
boundary, the tool-invocation boundary, the FastAPI boundary) -> `main.py`
(thin entrypoint). Same convention as `rosterd-kernel`/`rosterd-coordinator`.

- `domain/demo_agent.py`   API schema, exactly as in the contract (don't edit)
- shared types come from `rosterd-contracts` (see `../libs/rosterd-contracts/`), not a hand-copied `shared.py`
- `domain/tools.py`        tool schemas: `Field(gt=0, le=50)`, `Field(gt=0, le=100)`, `Field(gt=0, le=2000)`  <- ingestion reads these
- `application/refund_node.py`  Refund/Exception node with `interrupt()`      <- ingestion reads this
- `application/graph.py`   the 5-node graph (order_intake, fulfillment, refund_exception, catalog, payment), incl. a module-level `graph = build_graph()` <- ingestion reads this
- `langgraph.json`  points ingestion at `application/graph.py:graph` (the convention its discovery checks first)
- `constraints.yaml`  reference copy of the constraints text to paste into ingestion's `POST /ingest` (not auto-read from the repo -- see the file's own header comment, and the gap it flags)
- `adapters/llm/brain.py`        scripted vs LLM decision-making -- no formal Protocol today (just two interchangeable classes), so `application/graph.py` and `application/refund_node.py` import it directly (a documented pragmatic hexagonal exception)
- `adapters/tooling/toolrun.py`  records tool calls exactly as attempted -- same exception, same reason
- `adapters/http_in/app.py`      the real FastAPI app
- `main.py`         thin entrypoint re-exporting `app`
- `scenarios.py`, `demo_client.py`  seeded demos + a tiny CLI (dev tooling, stay at root)

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
  correctly today: `rosterd-kernel/domain/manifest.py` documents that ingestion's
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

Build context is the repo root (so the image can also see
`../libs/rosterd-contracts` -- see the Dockerfile's own header comment),
not this directory:

    cd .. && docker build -f rosterd-demo-agent/Dockerfile -t demo-agent . && cd rosterd-demo-agent
    docker run --rm -p 8000:8000 demo-agent
