# ADR-002: The confirm gate

**Status:** accepted · **Owner:** Person 2 (ingestion) · **Affects:** Person 1 (kernel), Person 3 (frontend) · **Builds on:** [ADR-001](ADR-001-manifest-versioning.md)

## Context

The revised brief introduces a draft/confirmed split: ingestion writes a manifest as `draft`, a human reviews it, and `POST /manifest/{id}/confirm` makes it live. Kernels act only on confirmed manifests.

The reason is that discovery **infers**. It reads docstrings for purpose, matches tool names by AST, and derives assignability from a constraints file that a human may have typo'd. Any of that can be wrong. Confirmation is where a person takes responsibility for what the kernel is about to enforce, and it is the trust boundary the product's whole pitch rests on.

The question this ADR answers is mechanical but consequential: when someone confirms a manifest, possibly editing it on the Review screen, does the existing manifest change?

## Decision

**Confirming derives a new immutable manifest. It never mutates the draft.**

`POST /manifest/{id}/confirm` reads the draft, takes the (possibly edited) agent list, and writes a **new** record with `status: confirmed`. `ConfirmResponse.manifest_id` returns the id of that new manifest, which is **not** the id that was confirmed.

The confirmed manifest is content-addressed like every other:

```
confirmed_id = "mf_" + sha256({draft: <draft_id>, agents: <canonical agents>, status: "confirmed"})[:16]
```

Two properties fall out. Confirming the same draft with the same edits twice is **idempotent** — same id, no duplicate record. And confirming the same draft with *different* edits yields a **different** id, so two reviewers who approve different things cannot silently overwrite each other.

## Why not just flip a status flag

Flipping `status` on the draft in place is the obvious cheaper option, and it is wrong for two reasons.

**It breaks ADR-001's pinning guarantee.** That ADR's entire argument is that a kernel can pin a `manifest_id` for the life of a run and know the rules cannot move underneath it. A mutable `status` field reintroduces exactly the hazard: a manifest that was inert when a task was authorised becomes live mid-run. Worse, if confirmation also replaces the agent list, the constraints themselves change under a running task — the live privilege escalation ADR-001 exists to prevent.

**It destroys the audit trail.** Keeping both records means you can always answer "what did discovery infer, and what did a human actually approve?" by diffing the draft against the confirmed manifest. For a product whose pitch is that constraints are enforced rather than suggested, being able to show that a person reviewed and changed a limit — and exactly which limit — is worth more than the storage it costs. Overwriting in place throws that away permanently.

The brief explicitly left this open ("new `manifest_id` per re-ingest/re-confirm... or overwrite in place") and noted the new-id option is safer for demoing a live update. It is also the only option consistent with ADR-001.

## What this requires of the rest of the team

**Person 3 (frontend): use the `manifest_id` that `/confirm` returns.** The id you posted to is the draft; the id you get back is the live one. Everything after confirmation — Contracts, Roster, `/ask/parse` — must use the returned id. Treating the draft id as still-current after confirming is the one mistake this design makes easy to make, and it fails loudly rather than silently: `/ask/parse` rejects a draft with `409 manifest_not_confirmed`.

**Person 1 (kernel): ignore drafts entirely.** A draft manifest governs nothing. When SpacetimeDB lands this becomes a subscription filtered to `status: confirmed`; until then, check `provenance.status` on the manifest you pinned.

## Consequences

`/ask/parse` requires a confirmed manifest and returns `409 manifest_not_confirmed` for a draft. That is deliberate: assigning work against rules nobody approved would route around the gate this ADR exists to build.

`/ask/parse` also only proposes agents with `direct_assignable: true`. An agent the contract says is reachable only via another must not become a direct assignee because a request described it well. In the demo repo this is visible: asking to "escalate this to a human" does **not** return the escalation agent, and comes back with `low` confidence instead.

Storage grows by one record per confirmation. Same trade as ADR-001, same answer: a few KB against a permanent record of who approved what.

## Scope note

This build implements the confirm gate and `/ask/parse` on the **existing** architecture. Three things from the revised brief are deliberately **not** here yet, and each is flagged rather than half-built:

| Not implemented | Why |
| --- | --- |
| SpacetimeDB storage | Person 3 owns the module, and the brief says to agree the `manifests` table schema together first. Storage stays file-backed; `ManifestStore` is the single seam to swap. |
| Inferred `ConstraintRule`s (schema/code/interrupt sources) | Needs Person 4's typed tool schemas and at least one `interrupt()` call to work against. `constraints.yaml` remains the source of constraints for now. |
| OTel spans | No collector in the stack yet. |

`AgentManifestEntry.constraints` is therefore still `AgentConstraints` (an open dict), not `list[ConstraintRule]`. **That is a real divergence from the revised brief and must be reconciled with Person 1 before the kernel is wired up.**
