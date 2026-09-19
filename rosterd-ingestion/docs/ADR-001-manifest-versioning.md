# ADR-001: Manifest versioning

**Status:** accepted · **Owner:** Person 2 (ingestion) · **Affects:** Person 1 (kernel), Person 3 (frontend)

## Context

Ingestion is a one-time step, but "one-time" is a lie in practice. Somebody will push a commit to the agent repo, or edit `constraints.yaml` to loosen a limit, and re-run `POST /ingest`. The question this ADR answers is what happens to the manifest that already exists — and, more importantly, what happens to the kernel that is enforcing it while a task is mid-run.

The failure we care about is specific. The kernel reads `max_refund_usd: 100` from the manifest and starts a refund task. Someone re-ingests with `max_refund_usd: 10000`. If the manifest the kernel is reading mutates underneath it, the constraint that was in force when the task was authorised is silently replaced mid-flight. That is a live privilege escalation, reachable by anyone who can push to the repo, and it defeats the thing rosterd exists to do.

A second, duller problem: the frontend polls. If every `POST /ingest` minted a brand-new id regardless of whether anything changed, a retry or a double-click would fork the roster and scatter tasks across manifests that are byte-identical.

## Decision

**Manifests are immutable and content-addressed.** A manifest is never edited after it is written.

`manifest_id` is a hash of the exact inputs that produced it:

```
manifest_id = "mf_" + sha256({
    repo:        <normalised repo_url>,
    commit:      <git commit sha of the clone>,
    constraints: <sha256 of the constraints text>,
    schema:      <MANIFEST_SCHEMA_VERSION>,
})[:16]
```

Three consequences follow directly, and they are the whole point:

1. **Re-ingesting unchanged inputs is idempotent.** Same repo, same commit, same constraints → same `manifest_id`, no new version, no new record. The service detects this immediately after the clone and returns the stored manifest without re-running discovery.
2. **Any change produces a new `manifest_id`.** A new commit, an edited constraint, or a schema bump all change the hash. There is no such thing as "the manifest changed" — only "there is a newer manifest."
3. **Every manifest resolves forever.** `GET /manifest/{id}` for a superseded manifest returns exactly what it returned on day one. Nothing is deleted or rewritten when a newer version arrives.

Alongside the content address, each manifest carries **lineage** metadata so the history of one repo is inspectable:

| Field | Meaning |
| --- | --- |
| `lineage_id` | `ln_` + hash of the normalised repo URL. Stable across every version of that repo's manifest. |
| `version` | Monotonic integer within the lineage, starting at 1. For humans; never used as a key. |
| `supersedes` | The `manifest_id` this one replaced, or `null` for v1. |
| `superseded_by` | The `manifest_id` that replaced this one, or `null` if it is current. This is the only field written to an existing manifest, and it is purely advisory. |
| `constraints_sha256` | Hash of the exact constraints text used. Powers the Contracts screen's "Verified" badge. |
| `commit_sha` | The commit that was cloned, so a manifest can always be traced back to source. |

URL normalisation folds away trailing slashes, a `.git` suffix, and case, so `https://github.com/x/y`, `https://github.com/x/y.git`, and `https://GitHub.com/x/y/` are one lineage rather than three.

## The contract this creates for the rest of the team

**Person 1 (kernel): pin a `manifest_id` for the lifetime of a run.** Resolve the manifest once when a task is dispatched, hold that id, and enforce against it until the run ends. Do not re-resolve "the latest manifest for this repo" mid-run. Because manifests are immutable, pinning is sufficient — there is no lock to take and no version to re-check. A task that started under v1 finishes under v1, even if v7 exists by the time it lands.

**Person 3 (frontend): treat a new `manifest_id` as a new roster.** After a re-ingest, show the new manifest for new work, but do not retroactively relabel tasks that are already running — their constraints came from the pinned manifest, and displaying the new numbers next to an in-flight task would misrepresent what is actually being enforced. The mockup's line *"Any change to this file requires re-ingesting before it takes effect"* is exactly right, and this design is what makes it true.

## Alternatives considered

**One mutable manifest per repo, updated in place.** Simplest to build and the obvious first instinct. Rejected because it is precisely the live-mutation hazard described above: it changes the rules under a running task, and it makes "what was enforced at 14:02?" unanswerable after the fact.

**Sequential ids with no content hash (`manifest-1`, `manifest-2`, …).** Rejected because it has no idempotency. A double-clicked Ingest button produces two identical manifests with different ids, and nothing downstream can tell they are the same.

**Timestamp or UUID ids.** Same objection, plus the id stops being derivable from its inputs, so you cannot ask "has this exact configuration already been ingested?" without a full table scan.

**Hashing the rendered manifest instead of the inputs.** Tempting, and it would dedupe slightly more aggressively — two different commits that produce an identical manifest would collapse to one id. Rejected because it makes the id depend on discovery's own behaviour: improving tool attribution would change the ids of manifests whose inputs never moved. Hashing inputs keeps the id stable against our own bug fixes.

## Consequences and things left undone

Storage grows monotonically. Each ingest writes one JSON file, on the order of a few KB. At hackathon scale that is irrelevant; a real deployment wants a retention policy that keeps every manifest still referenced by a run and prunes the rest.

There is deliberately **no delete endpoint**. Deleting a manifest would break the `GET` that a pinned kernel depends on, which is the one guarantee this whole design exists to provide.

The store is file-backed with atomic writes (temp file plus `os.replace`), which is safe against a crash mid-write but **not against two processes ingesting the same new manifest concurrently** — the index is read-modify-written without a lock, so a simultaneous first-ingest of two different repos could drop one index entry. Single-process deployment is fine. Multi-worker needs a lock or a real database, and is noted in the README as a known limitation rather than silently assumed away.

`MANIFEST_SCHEMA_VERSION` participates in the hash, so bumping it re-versions every manifest on next ingest by design. Bump it only when the stored shape changes in a way an older reader cannot handle.
