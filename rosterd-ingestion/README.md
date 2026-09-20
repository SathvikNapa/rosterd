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

## Probing a real repo

`scripts/probe.py` clones a public LangGraph repo and prints the wiring rosterd discovers, without going through HTTP or storing a manifest. It is how the discovery rules were validated against code nobody on this team wrote.

```bash
.venv/bin/python scripts/probe.py https://github.com/langchain-ai/react-agent
.venv/bin/python scripts/probe.py --suite     # five LangChain templates
```

It tries `import` mode and falls back to `static` when the target repo's dependencies still can't be resolved even after a sandboxed install attempt — always reports which mode produced the answer, so a result is never mistaken for something it is not.

Verified against five of LangChain's own public templates, every one matching the graph definition in its source exactly. Before `sandbox.py` existed, four of these only resolved via the less-accurate `static` AST fallback, since none of their own dependencies were installed here — that's the normal case for someone else's repo. All five now resolve via authoritative runtime `import` mode instead:

| Repo | Agents | Edges | Mode |
| --- | --- | --- | --- |
| `react-agent` | 2 | 4 | import (was static) |
| `memory-agent` | 2 | 4 | import (was static) |
| `retrieval-agent-template` | 1 | 2 | import (was static, 4 agents/4 edges) |
| `data-enrichment` | 3 | 8 | import (was static) |
| `new-langgraph-project` | 1 | 2 | import |

`retrieval-agent-template`'s agent count changed, not just its mode: its `langgraph.json` declares two separate graphs (`indexer` and a retrieval agent), and `locate_graph` resolves the first one declared — `indexer`, one node. The old static pass scanned the whole repo's AST regardless of which graph declaration it belonged to, so it merged nodes from both into one inflated, less accurate answer. This is the runtime-precedence tradeoff `discovery.py`'s own module docstring already documents ("structure from runtime when available") — surfaced here for real, not just filed in the docstring — not a regression from this work.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/ingest` | Repo URL + constraints YAML in, **draft** manifest out |
| `POST` | `/manifest/{manifest_id}/confirm` | Approve a draft, optionally with edits, making it live |
| `POST` | `/ask/parse` | Plain text in, a proposed task out |
| `GET` | `/manifest/{manifest_id}` | Re-fetch a manifest without re-ingesting |
| `GET` | `/manifest/{manifest_id}/provenance` | Commit, hashes, version, warnings |
| `GET` | `/manifests` | Everything ingested so far |
| `GET` | `/healthz` | Liveness and effective settings |

Full request/response shapes, error codes, and notes for Person 1 and Person 3 are in [docs/API.md](docs/API.md).

## The confirm gate

Ingestion produces a **draft**. A draft governs nothing: the kernel ignores it and `/ask/parse` refuses it. `POST /manifest/{id}/confirm` takes the possibly-edited agent list from the Review screen and derives a **new, immutable, confirmed** manifest — it does not mutate the draft.

That matters twice over. A kernel pinned to a manifest never sees it change underneath a running task, and both sides of the trust boundary stay readable, so you can always diff what discovery *inferred* against what a human *approved*. The returned `manifest_id` is the new one; use it for everything afterwards. Reasoning in [ADR-002](docs/ADR-002-confirm-gate.md).

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
| `ROSTERD_SANDBOX_INSTALL_ENABLED` | `true` | Fall back to a throwaway venv + real `uv sync`/`pip install -e` when the fast import fails on a missing dependency (see `sandbox.py`) |
| `ROSTERD_SANDBOX_INSTALL_TIMEOUT_SEC` | `120` | Ceiling on that install step — separate from `ROSTERD_IMPORT_TIMEOUT_SEC`, since a real dependency install is much slower than importing an already-installed module |

`ROSTERD_LOCAL_REPO_ROOT` exists because `IngestRequest.repo_url` is an `HttpUrl` by contract, so a local fixture has to arrive dressed as an http URL. It is off unless set, and paths are containment-checked against the root.

## Security note — please read before demoing

**`import` mode executes code from the cloned repo.** That is not incidental; the task requires calling `get_graph()`, and you cannot compile a LangGraph graph without running the module that builds it. A malicious repo URL is therefore arbitrary code execution as whoever runs this service.

What is in place: the import runs in a **subprocess** with a wall-clock timeout, so a hang or a crash cannot take the API down; clones are shallow with **git hooks disabled** and credential prompts off; there is a size cap and an optional host allowlist; and locating the graph is done by AST, so nothing runs until the graph has actually been found.

What is **not** in place: no container, no seccomp, no user separation, no network egress control for the child. Isolation is not a sandbox.

For a demo where the repo is your own, `import` mode is the right default and gives the most accurate results. If you ever point this at a URL a judge or a stranger typed, run `ROSTERD_DISCOVERY_MODE=static` — it never imports anything, and a test asserts that (`test_does_not_execute_repo_code` writes a canary file from module scope and checks it was never created). On the bundled demo repo, static mode produces the same agents, tools, and edges as import mode.

This matters beyond hygiene: rosterd's pitch is that constraints belong outside the model, enforced at the boundary. An ingestion service that runs untrusted code without saying so undercuts that argument. Better to state the limit plainly.

### Sandboxed dependency installation (`sandbox.py`)

The fast path above (exec the target module against this service's own interpreter) only ever worked for a self-contained repo. A real repo with its own `pyproject.toml`/`uv.lock` — the common case once you point this at an arbitrary public GitHub repo instead of the bundled demo — fails with `No module named '<its own package>'` until its dependencies are actually installed somewhere.

When the fast import fails with what looks like a missing dependency (`ModuleNotFoundError`, or an `ImportError` that isn't a broken import statement), discovery now retries once: it walks upward from the graph's own directory looking for a `pyproject.toml`/`setup.py`/`setup.cfg`, creates a **fresh, throwaway venv per ingest**, installs that project's own dependencies into it (`uv sync` when a `uv.lock` is present — not `uv pip install -e .`, which fails outright on a real `uv` workspace with multiple top-level packages — otherwise a plain `python -m venv` + `pip install -e .`), and re-imports the graph with *that* interpreter. It also resolves the two real `langgraph.json` graph-spec shapes: a file path, and a dotted import into an installed package (`"pkg.module:attr"`) — the second only ever resolves once the sandboxed install has actually happened.

**Read the security tradeoff before relying on this against an untrusted URL** (full detail in `sandbox.py`'s module docstring): a venv isolates installed *package state* — one ingest's dependencies can't collide with another's — it does **not** isolate against malicious setup/build-time code execution, network exfiltration, or resource exhaustion during the install. Real isolation would be a throwaway, network-restricted container per ingest; that's real follow-up work, not something to half-build and call done. This is a deliberate, faster-to-ship middle ground, same posture as `import` mode itself: state the limit plainly rather than imply more safety than exists.

It never turns a working fast-path ingest into a new way to fail: if there's nothing installable found, `uv`/`pip` is missing, or the install itself fails or times out, discovery just surfaces the *original* fast-path error, unchanged.

**Verified live** against a real, non-trivial public repo (`bytedance/deer-flow`, a `uv`-workspace LangGraph project neither the demo repo nor any unit test fixture resembles) through three real, successive blockers, each confirmed by an actual failure and fixed in turn: a missing dependency (fixed by the sandboxed `uv sync` install), a dotted-module graph spec that only resolves once that install has happened (fixed by the dotted-import fallback), and a graph factory requiring a `RunnableConfig` argument rather than being zero-arg callable (fixed by an `obj({})` retry — `RunnableConfig` is an unvalidated `TypedDict`, so an empty dict is a reasonable stand-in purely for introspection). Ingestion got past all three. It then hit `deer-flow`'s own `config.yaml` requirement — a real runtime settings file the repo's own docs say to copy from `config.example.yaml` and fill in — which is a **genuine, application-specific requirement**, not an ingestion gap: no generic tool can synthesize another project's runtime configuration (API keys, model settings) on its behalf. That is the honest edge of what sandboxed installation can close.

For a clean, unambiguous success — not just "got further before hitting a different wall" — see the `scripts/probe.py --suite` table above: `react-agent`, `memory-agent`, `retrieval-agent-template`, and `data-enrichment` each have their own installable `pyproject.toml` and no runtime config file of their own to trip on, and all four went from the less-accurate `static` fallback to authoritative `import`-mode discovery once this shipped.

## Layout

| File | Purpose |
| --- | --- |
| `shared.py` | Types shared across the team. **Do not redefine these elsewhere.** |
| `ingestion.py` | This service's schema, exactly as specified in the brief |
| `app.py` | FastAPI routes and error handling |
| `service.py` | The ingest pipeline, end to end |
| `repo.py` | Cloning, with the safety controls |
| `discovery.py` | Locating the graph and merging the runtime and static passes |
| `sandbox.py` | Fresh-venv dependency install + retry, when the fast import fails on a missing dependency |
| `_introspect_worker.py` | Subprocess that imports the repo and calls `get_graph()` |
| `astscan.py` | AST analysis: tool definitions, tool attribution, node wiring |
| `constraints_loader.py` | Parsing and validating `constraints.yaml` |
| `manifest.py` | Merging graph + constraints into `AgentManifestEntry` |
| `store.py` | Persistence, content addressing, lineage |
| `demo-agent/` | Stand-in for Person 4's repo — a working triage/refund/escalation graph |
| `docs/` | API contract, versioning ADR, discovery internals |

## Known limitations

- **Single process.** The store's index is read-modify-written without a lock. Fine for one uvicorn worker; multi-worker needs a lock or a real database.
- **Target repo dependencies** are handled automatically now — see "Sandboxed dependency installation" above — but only up to what a generic tool can reasonably do. A repo with its own required runtime config file (API keys, settings it cannot run without — confirmed against `bytedance/deer-flow`'s `config.yaml`) still fails, honestly, on that app-specific requirement rather than a missing-package one.
- **Tool attribution is name-based.** A tool reached through an alias or a registry lookup will be missed — declare it under `constraints.<node>.tools`.
- **No retention policy.** Manifests accumulate, a few KB each. Deliberately no delete endpoint, since deleting one would break a kernel pinned to it.

## Notes for the team

Two things in the brief I did **not** change unilaterally, since it says to flag manifest-shape changes to Person 1 and Person 3 first:

1. **`shared.py` and `ingestion.py` are verbatim from the brief.** Every field, default, and docstring matches. Nothing was added to `IngestResponse` or `ManifestResponse`.
2. **Provenance is a separate endpoint, not extra fields.** The Contracts screen needs a constraints hash for its "Verified" badge and the kernel benefits from `commit_sha`, but both would have meant adding fields to the contracted response. They live on `GET /manifest/{id}/provenance` instead. **If Person 1 or Person 3 would rather have them inline, that is a one-line change and worth doing — but it is your call, not mine.**

One question worth settling early: the kernel needs to match on `node`, not `id`. `node` is the exact LangGraph node name; `id` is the short display handle (`refund_node` → `refund`). If Person 1 is matching on `id`, a repo with a node literally named `refund` would collide. Matching on `node` avoids it entirely.
