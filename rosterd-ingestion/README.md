# rosterd — ingestion service (Person 2)

Turns a LangGraph repo plus a `constraints.yaml` into the **agent manifest** every other rosterd service reads. Person 1's kernel enforces it, Person 3's frontend renders it. This is the contract the rest of the team builds against.

```
frontend ──> ingestion-service ──> (manifest stored, read by kernel + frontend)
```

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/run.sh                 # serves on :8000, /docs for the API explorer
./scripts/smoke.sh               # end-to-end check against the bundled demo repo
.venv/bin/python -m pytest       # 84 tests
```

## What it does

```
POST /ingest
  ├─ clone the repo             shallow, hooks off, timeout, size cap
  ├─ locate the compiled graph  langgraph.json, then conventions
  ├─ call get_graph()           in a subprocess — nodes, edges, tools, docstrings
  ├─ AST-scan the repo          tools per node, which get_graph() cannot give you
  ├─ parse constraints.yaml     node names validated against the discovered graph
  ├─ merge                      one AgentManifestEntry per agent
  └─ store                      content-addressed, immutable
```

Against the bundled `demo-agent/` fixture that produces:

| `id` | `node` | `tools` | `direct_assignable` | `constraints` |
| --- | --- | --- | --- | --- |
| `triage` | `triage_node` | `classify_request` | yes | — |
| `refund` | `refund_node` | `issue_refund` | yes | `max_refund_usd: 100` |
| `escalation` | `escalation_node` | `create_ticket` | no, `entry_only_via: [refund_node, triage_node]` | `requires_prior_node: refund_node` |

Which is exactly the Contracts screen in the product mockup.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/ingest` | Repo URL + constraints YAML in, manifest out |
| `GET` | `/manifest/{manifest_id}` | Re-fetch a manifest without re-ingesting |
| `GET` | `/manifest/{manifest_id}/provenance` | Commit, hashes, version, warnings |
| `GET` | `/manifests` | Everything ingested so far |
| `GET` | `/healthz` | Liveness and effective settings |

Full request/response shapes, error codes, and notes for Person 1 and Person 3 are in [docs/API.md](docs/API.md).

## Versioning, in one paragraph

`manifest_id` is a hash of (repo URL, commit sha, constraints hash, schema version). Re-ingesting identical inputs returns the **same id** and creates nothing new. Any change produces a **new id**, and the old manifest keeps resolving forever — so the kernel can pin a `manifest_id` for the life of a run and know the rules cannot move underneath it. The reasoning, the alternatives, and what it requires of the kernel and frontend are in [ADR-001](docs/ADR-001-manifest-versioning.md).

## Constraints file

```yaml
version: 1

constraints:
  refund_node:                              # must match a LangGraph node name
    purpose: Issues refunds, $100 cap       # optional; defaults to the docstring
    direct_assignable: true                 # defaults to false
    max_refund_usd: 100                     # passed through to the kernel

  escalation_node:
    direct_assignable: false
    entry_only_via: [refund_node, triage_node]
    requires_prior_node: refund_node
```

`id`, `purpose`, `tools`, `direct_assignable`, and `entry_only_via` shape the manifest entry. **Every other key is passed through untouched** as a runtime constraint for the kernel to interpret, so an agent can declare a constraint shape nobody has typed yet.

Two rules govern the merge:

- **Constraints win.** Anything written in the file overrides what was inferred from the repo. Inference is a convenience; the file is the contract.
- **Fail closed.** An agent with no block is not directly assignable, and a node name that does not exist in the graph is a hard `422` — not a warning. Accepting a typo'd `refund_nod` would mean the kernel finds no constraints for it and runs an uncapped refund agent, failing open on precisely the thing rosterd exists to prevent.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROSTERD_DATA_DIR` | `./data` | Where manifests are stored |
| `ROSTERD_DISCOVERY_MODE` | `import` | `import` runs `get_graph()`; `static` never executes repo code |
| `ROSTERD_GRAPH_SPEC` | — | Override graph location, e.g. `src/graph.py:workflow` |
| `ROSTERD_ALLOWED_HOSTS` | — | Comma-separated clone allowlist. Empty means any host |
| `ROSTERD_CLONE_TIMEOUT_SEC` | `60` | Ceiling on `git clone` |
| `ROSTERD_IMPORT_TIMEOUT_SEC` | `60` | Ceiling on importing and introspecting the graph |
| `ROSTERD_MAX_REPO_MB` | `100` | Reject clones larger than this |
| `ROSTERD_LOCAL_REPO_ROOT` | — | **Dev only.** Resolves `http://localhost/<name>` to `<root>/<name>` |

`ROSTERD_LOCAL_REPO_ROOT` exists because `IngestRequest.repo_url` is an `HttpUrl` by contract, so a local fixture has to arrive dressed as an http URL. It is off unless set, and paths are containment-checked against the root.

## Security note — please read before demoing

**`import` mode executes code from the cloned repo.** That is not incidental; the task requires calling `get_graph()`, and you cannot compile a LangGraph graph without running the module that builds it. A malicious repo URL is therefore arbitrary code execution as whoever runs this service.

What is in place: the import runs in a **subprocess** with a wall-clock timeout, so a hang or a crash cannot take the API down; clones are shallow with **git hooks disabled** and credential prompts off; there is a size cap and an optional host allowlist; and locating the graph is done by AST, so nothing runs until the graph has actually been found.

What is **not** in place: no container, no seccomp, no user separation, no network egress control for the child. Isolation is not a sandbox.

For a demo where the repo is your own, `import` mode is the right default and gives the most accurate results. If you ever point this at a URL a judge or a stranger typed, run `ROSTERD_DISCOVERY_MODE=static` — it never imports anything, and a test asserts that (`test_does_not_execute_repo_code` writes a canary file from module scope and checks it was never created). On the bundled demo repo, static mode produces the same agents, tools, and edges as import mode.

This matters beyond hygiene: rosterd's pitch is that constraints belong outside the model, enforced at the boundary. An ingestion service that runs untrusted code without saying so undercuts that argument. Better to state the limit plainly.

## Layout

| File | Purpose |
| --- | --- |
| `shared.py` | Types shared across the team. **Do not redefine these elsewhere.** |
| `ingestion.py` | This service's schema, exactly as specified in the brief |
| `app.py` | FastAPI routes and error handling |
| `service.py` | The ingest pipeline, end to end |
| `repo.py` | Cloning, with the safety controls |
| `discovery.py` | Locating the graph and merging the runtime and static passes |
| `_introspect_worker.py` | Subprocess that imports the repo and calls `get_graph()` |
| `astscan.py` | AST analysis: tool definitions, tool attribution, node wiring |
| `constraints_loader.py` | Parsing and validating `constraints.yaml` |
| `manifest.py` | Merging graph + constraints into `AgentManifestEntry` |
| `store.py` | Persistence, content addressing, lineage |
| `demo-agent/` | Stand-in for Person 4's repo — a working triage/refund/escalation graph |
| `docs/` | API contract, versioning ADR, discovery internals |

## Known limitations

- **Single process.** The store's index is read-modify-written without a lock. Fine for one uvicorn worker; multi-worker needs a lock or a real database.
- **Target repo dependencies must be importable** by this service's interpreter in `import` mode. Install them into the same venv or the ingest fails with `graph_load_failed` naming the missing module.
- **Tool attribution is name-based.** A tool reached through an alias or a registry lookup will be missed — declare it under `constraints.<node>.tools`.
- **No retention policy.** Manifests accumulate, a few KB each. Deliberately no delete endpoint, since deleting one would break a kernel pinned to it.

## Notes for the team

Two things in the brief I did **not** change unilaterally, since it says to flag manifest-shape changes to Person 1 and Person 3 first:

1. **`shared.py` and `ingestion.py` are verbatim from the brief.** Every field, default, and docstring matches. Nothing was added to `IngestResponse` or `ManifestResponse`.
2. **Provenance is a separate endpoint, not extra fields.** The Contracts screen needs a constraints hash for its "Verified" badge and the kernel benefits from `commit_sha`, but both would have meant adding fields to the contracted response. They live on `GET /manifest/{id}/provenance` instead. **If Person 1 or Person 3 would rather have them inline, that is a one-line change and worth doing — but it is your call, not mine.**

One question worth settling early: the kernel needs to match on `node`, not `id`. `node` is the exact LangGraph node name; `id` is the short display handle (`refund_node` → `refund`). If Person 1 is matching on `id`, a repo with a node literally named `refund` would collide. Matching on `node` avoids it entirely.
