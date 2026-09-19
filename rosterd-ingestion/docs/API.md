# Ingestion API

Base URL in dev: `http://127.0.0.1:8000`. Interactive docs at `/docs`.

Two endpoints are the contract from the brief and will not change without telling Person 1 and Person 3 first. The other three are additive conveniences — they add no fields to the contracted responses, so ignoring them is always safe.

| Method | Path | Contract? | Purpose |
| --- | --- | --- | --- |
| `POST` | `/ingest` | yes | Repo URL + constraints YAML in, **draft** manifest out |
| `POST` | `/manifest/{manifest_id}/confirm` | yes | Approve a draft, optionally with edits, making it live |
| `POST` | `/ask/parse` | yes | Plain text in, a proposed task out |
| `GET` | `/manifest/{manifest_id}` | kept | Re-fetch a manifest without re-ingesting |
| `GET` | `/manifest/{manifest_id}/provenance` | additive | Commit, hashes, version, warnings |
| `GET` | `/manifests` | additive | Everything ingested so far |
| `GET` | `/healthz` | additive | Liveness and effective settings |

## `POST /ingest`

Clones the repo, discovers its agents, merges `constraints.yaml`, and stores the result.

**Request**

```json
{
  "repo_url": "https://github.com/your-org/support-agents",
  "constraints_yaml": "version: 1\nconstraints:\n  refund_node:\n    max_refund_usd: 100\n"
}
```

`repo_url` must be `http` or `https` — that is `HttpUrl` in the shared schema. `constraints_yaml` may be an empty string, in which case ingestion falls back to `constraints.yaml`, `constraints.yml`, or `.rosterd/constraints.yaml` in the repo itself. If neither exists the ingest still succeeds, but every agent comes back unconstrained and not directly assignable, and a warning says so.

**Response `200`**

```json
{
  "manifest_id": "mf_e3daf0f97ce20032",
  "agents": [
    {
      "id": "refund",
      "node": "refund_node",
      "purpose": "Issues refunds, $100 cap",
      "tools": ["issue_refund"],
      "direct_assignable": true,
      "entry_only_via": [],
      "constraints": {"max_refund_usd": 100.0, "requires_prior_node": null}
    }
  ],
  "graph": {
    "nodes": ["__start__", "triage_node", "refund_node", "escalation_node", "__end__"],
    "edges": [
      {"source": "__start__", "target": "triage_node", "condition": null},
      {"source": "triage_node", "target": "refund_node", "condition": "route_after_triage"}
    ]
  }
}
```

**This call is idempotent.** Same repo, same commit, same constraints → same `manifest_id`, every time. See [ADR-001](ADR-001-manifest-versioning.md).

**The result is a draft.** `status` is `"draft"`, and a draft governs nothing — the kernel must ignore it and `/ask/parse` rejects it. Confirm it first.

## `POST /manifest/{manifest_id}/confirm`

Approve a draft manifest, optionally replacing its agent list with whatever the Review screen edited.

```json
{"agents": []}
```

Send an empty list to confirm exactly what discovery produced. Send a full `AgentManifestEntry` list to confirm with edits.

**Response `200`**

```json
{"manifest_id": "mf_236bb258dafc3f76", "status": "confirmed"}
```

**The returned `manifest_id` is a new one.** Confirming derives a new immutable manifest rather than mutating the draft, so a kernel pinned to a manifest never sees it change, and the draft stays readable beside the confirmed version for comparison. **Use the returned id for everything afterwards** — Contracts, Roster, and `/ask/parse`. See [ADR-002](ADR-002-confirm-gate.md).

Idempotent: confirming the same draft with the same agents returns the same confirmed id. Confirming with *different* edits produces a different id, so two reviewers cannot silently overwrite each other.

## `POST /ask/parse`

Turn a plain-language request into a proposed task for a human to accept.

```json
{"manifest_id": "mf_236bb258dafc3f76", "text": "Customer was charged twice on invoice 4482, issue a refund urgently. Must respond within 2 minutes."}
```

**Response `200`**

```json
{
  "agent_id": "refund",
  "task": {
    "title": "Customer was charged twice on invoice 4482, issue a refund urgently",
    "description": "Customer was charged twice on invoice 4482, issue a refund urgently. Must respond within 2 minutes.",
    "priority": "high",
    "expectation_criteria": [
      "Refund amount \u2264 $100, per contract",
      "Must respond within 2 minutes"
    ]
  },
  "confidence": "high"
}
```

Three things worth knowing:

**The manifest must be confirmed.** A draft returns `409 manifest_not_confirmed`. Assigning work against rules nobody approved would route around the confirm gate.

**Only `direct_assignable` agents are proposable.** An agent the contract says is reachable only via another never comes back as a direct assignee, however well the text describes it. Asking the demo repo to "escalate this to a human" does *not* return `escalation` — it returns the best assignable agent with `low` confidence.

**`expectation_criteria` merges the contract with the request.** Limits the kernel will actually enforce (`max_refund_usd`, `requires_prior_node`, `entry_only_via`) appear alongside any explicit requirement in the text, so the person approving the card sees what will be enforced rather than having to remember it.

**The parser is deterministic, not an LLM.** It scores agents on tool-name, id, node, and purpose overlap. That keeps it testable, offline, and impossible to fail live during a demo. `score_agents` in `ask.py` is the seam if you want to swap in a model call.

## `GET /manifest/{manifest_id}`

Returns the identical document `POST /ingest` returned for that id. Never clones, never re-runs discovery, works with the network down. A superseded manifest still resolves and still returns its original contents — that is what makes it safe for the kernel to pin one for the life of a run.

`404` with `code: "manifest_not_found"` for an id that was never issued.

## `GET /manifest/{manifest_id}/provenance`

Everything about where a manifest came from. Additive, so it is not part of the contracted shape.

```json
{
  "lineage_id": "ln_e71dec77c2999d95",
  "version": 1,
  "created_at": "2026-09-19T15:52:54.278Z",
  "repo_url": "https://github.com/your-org/support-agents",
  "commit_sha": "9ce3989af4abbda639bd0b0f98ff945c91812c7f",
  "constraints_sha256": "3c299da2401c…",
  "schema_version": 1,
  "discovery_mode": "import",
  "graph_located_via": "langgraph.json:support",
  "supersedes": null,
  "superseded_by": null,
  "warnings": []
}
```

**Person 3:** `constraints_sha256` is what the Contracts screen's "Verified" badge should display, and `commit_sha` is what ties the screen back to a specific state of the repo. `warnings` is worth surfacing somewhere — it is where "this agent has no tools" and "nothing can route work to this agent" end up.

## Errors

Every expected failure returns the same envelope, so you can branch on `code` instead of matching message text.

```json
{"code": "constraints_unknown_nodes", "message": "constraints.yaml references node(s) not in the graph: refund_nod.", "details": {}}
```

| `code` | HTTP | Means |
| --- | --- | --- |
| `repo_fetch_failed` | 422 | Clone failed: bad URL, private repo, timeout, or over the size cap |
| `repo_not_allowed` | 403 | Host is not on `ROSTERD_ALLOWED_HOSTS` |
| `graph_not_found` | 422 | No compiled LangGraph graph found in the repo |
| `graph_load_failed` | 422 | The graph was found but importing it raised or timed out |
| `constraints_invalid` | 422 | `constraints.yaml` is not valid YAML, or is shaped wrong |
| `constraints_unknown_nodes` | 422 | It names nodes the graph does not contain |
| `manifest_not_found` | 404 | No manifest with that id |
| `manifest_not_confirmed` | 409 | `/ask/parse` was given a draft manifest |
| `no_assignable_agent` | 422 | No agent in the manifest is `direct_assignable` |

`constraints_unknown_nodes` carries the useful part in `details`:

```json
{
  "unknown_nodes": [{"name": "refund_nod", "where": "constraints", "did_you_mean": "refund_node"}],
  "known_nodes": ["triage_node", "refund_node", "escalation_node"]
}
```

A typo'd node name is a **hard error**, not a warning. Accepting it would mean the kernel looks up constraints for a node that does not exist, finds none, and runs an uncapped refund agent — failing open on exactly the thing rosterd is for.

## Reading the manifest

**`graph.nodes` includes `__start__` and `__end__`.** They are LangGraph's synthetic entry and exit sentinels, kept so edges resolve and entry points are visible when drawing the graph. **They are never agents** — `agents` contains only real nodes. Filter them out if you are rendering bubbles.

**`agents[].id` vs `agents[].node`.** `node` is the exact LangGraph node name and is what the kernel matches on. `id` is a short human-facing handle derived by stripping a `_node`/`_agent` suffix (`refund_node` → `refund`), which is what the Roster screen should put on a bubble. Ids are unique within a manifest; `constraints.<node>.id` overrides the derived one.

**`direct_assignable` defaults to `false`.** An agent with no block in `constraints.yaml` is not directly assignable. Silence does not grant capability.

**`constraints` accepts extra keys.** `max_refund_usd` and `requires_prior_node` are declared in the shared schema; anything else a node declares is passed through untouched for the kernel to interpret. Add a field to `AgentConstraints` in `ingestion.py` when a constraint becomes common enough to be typed.
