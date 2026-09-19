# demo-agent — support graph fixture

A three-agent LangGraph customer-support system: **triage → refund → escalation**. It mirrors the system in the rosterd product mockup, so the Contracts and Roster screens have real data behind them.

This is a stand-in for Person 4's repo (the dependency called out in the ingestion brief). Ingestion tests against it, so it should stay importable with **no API key and no network** — the node bodies are deliberately dumb stand-ins.

## Layout

| File | Purpose |
| --- | --- |
| `agent.py` | Builds and compiles the graph. Exposes the compiled graph as `graph`. |
| `tools.py` | The three `@tool` callables: `classify_request`, `issue_refund`, `create_ticket`. |
| `langgraph.json` | Standard LangGraph manifest. Points at `./agent.py:graph`. |
| `constraints.yaml` | The rosterd constraints ingestion merges into the manifest. |

## What ingestion should find

Three agents, six edges, one tool bound per agent:

| Node | Tool | `direct_assignable` | Constraints |
| --- | --- | --- | --- |
| `triage_node` | `classify_request` | yes | — |
| `refund_node` | `issue_refund` | yes | `max_refund_usd: 100` |
| `escalation_node` | `create_ticket` | no | `entry_only_via: [refund_node, triage_node]`, `requires_prior_node: refund_node` |

## Running it directly

```bash
python -c "from agent import graph; print(graph.invoke({'request': 'refund me', 'invoice_id': '4482', 'refund_amount_usd': 40.0}))"
```

## Swapping in the real repo

Nothing in ingestion is hardcoded to this fixture. Point `repo_url` at Person 4's repo once it exists; discovery reads `langgraph.json` first and falls back to conventional layouts (`agent.py`, `graph.py`, `main.py`, `src/…`).
