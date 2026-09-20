"""Merging discovered graph structure with constraints.yaml into the manifest.

This is the join the rest of the team reads.

* **Constraints win.** A value written by a human in constraints.yaml overrides
  anything inferred from the repo. Inference is a convenience; the file is the
  contract.
* **Otherwise, infer `direct_assignable` from the code.** Defaults to `true`
  unless the node's function body calls something named `interrupt`
  (`astscan.calls_interrupt` -- LangGraph's human-in-the-loop primitive,
  matched by bare call name, not a resolved import). `entry_only_via` infers
  from the graph alongside it, only when `direct_assignable` resolved to
  `false`: every non-`__start__` node with a *conditional* edge into this one
  (an unconditional edge doesn't encode a gate -- most graphs have one
  regardless). Both are still just a starting point, not an authority: Review
  requires a human to confirm before any of it governs a live dispatch, same
  as a fully hand-written contract.
* **This replaces an earlier, stricter "fail closed" rule** (an agent with no
  constraints block was unconditionally `direct_assignable: false`) --
  deliberately: that made every repo without a hand-written constraints.yaml
  come back with nothing dispatchable at all (confirmed live: reproduced the
  bug report "No agent in this manifest is directly assignable" this way),
  which defeated ingesting an arbitrary real repo end to end. The tradeoff is
  real -- inference from a bare call-name match can be wrong -- and it's
  accepted deliberately in exchange for a repo with no constraints.yaml still
  being usable, not silently inert.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from constraints_loader import ParsedConstraints
from discovery import DiscoveryResult
from ingestion import AgentConstraints, AgentManifestEntry
from shared import GraphSpec


@dataclass
class BuiltManifest:
    agents: list[AgentManifestEntry]
    graph: GraphSpec
    warnings: list[str] = field(default_factory=list)


def _humanise(node_name: str) -> str:
    """'refund_node' -> 'Refund'. Used only when no purpose is available."""
    stem = re.sub(r"_(node|agent)$", "", node_name)
    return stem.replace("_", " ").strip().capitalize() or node_name


def derive_id(node_name: str, taken: set[str]) -> str:
    """A short, stable, human-facing id for a node.

    'refund_node' -> 'refund', which is what the Roster screen puts on a bubble.
    Falls back to the full node name if the short form is already taken, so ids
    stay unique without silently renaming someone else's agent.
    """
    candidate = re.sub(r"_(node|agent)$", "", node_name)
    candidate = re.sub(r"[^a-z0-9_-]+", "-", candidate.lower()).strip("-_") or node_name
    if candidate in taken:
        candidate = re.sub(r"[^a-z0-9_-]+", "-", node_name.lower()).strip("-_")
    suffix = 2
    base = candidate
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _infer_entry_only_via(node_name: str, graph: GraphSpec) -> list[str]:
    """Every non-`__start__` node with a *conditional* edge into this one.

    Not raw reachability: a real graph commonly wires `__start__` directly to
    every node (for exactly this reason -- a kernel/coordinator dispatching
    straight at any agent), so `__start__`-reachability alone says nothing
    about whether a node is "normally" gated behind another agent's routing.
    A conditional edge from a real node is the closer signal: it means some
    other agent's own logic decided to route here, which is what
    `entry_only_via` is meant to describe. An unconditional edge is excluded
    for the same reason most edges are unconditional in a typical graph and
    would swamp this with noise.
    """
    sources = {
        edge.source
        for edge in graph.edges
        if edge.target == node_name and edge.source != "__start__" and edge.condition
    }
    return sorted(sources)


def build_manifest(discovered: DiscoveryResult, parsed: ParsedConstraints) -> BuiltManifest:
    """Produce one AgentManifestEntry per agent node in the graph."""
    warnings = list(discovered.warnings) + list(parsed.warnings)
    agents: list[AgentManifestEntry] = []
    taken: set[str] = set()

    for node_name in discovered.agent_nodes:
        node = discovered.nodes[node_name]
        block = parsed.per_node.get(node_name)
        warnings.extend(f"{node_name}: {w}" for w in node.warnings)

        agent_id = (block.id if block and block.id else derive_id(node_name, taken))
        taken.add(agent_id)

        # Purpose: the file, then the node's docstring, then the node's name.
        purpose = (block.purpose if block and block.purpose else node.purpose) or _humanise(node_name)

        # Tools: a declared list replaces discovery outright, so a repo that
        # hides its tools behind indirection can still be described accurately.
        if block and block.tools is not None:
            tools = list(block.tools)
            undiscovered = sorted(set(tools) - set(node.tools))
            if undiscovered and node.tools:
                warnings.append(
                    f"{node_name}: constraints.yaml declares tool(s) {undiscovered} that "
                    "discovery did not find in the repo."
                )
        else:
            tools = list(node.tools)
            if not tools:
                warnings.append(
                    f"{node_name}: no tools discovered. If it has tools, declare them "
                    "under constraints.{node}.tools so the kernel can gate them."
                    .replace("{node}", node_name)
                )

        if block and block.direct_assignable is not None:
            direct_assignable = bool(block.direct_assignable)
            inferred = False
        else:
            direct_assignable = not node.has_interrupt
            inferred = True

        if block and block.entry_only_via:
            entry_only_via = list(block.entry_only_via)
        elif not direct_assignable:
            # Only worth inferring when there's actually a gate to explain --
            # a directly-assignable agent needs no entry path.
            entry_only_via = _infer_entry_only_via(node_name, discovered.graph)
        else:
            entry_only_via = []

        if inferred:
            signal = "interrupt() found" if node.has_interrupt else "no interrupt() found"
            via = f", entry_only_via inferred as {entry_only_via}" if entry_only_via else ""
            warnings.append(
                f"{node_name}: direct_assignable not set in constraints.yaml -- inferred "
                f"{direct_assignable} ({signal}){via}. Review before confirming."
            )

        agents.append(
            AgentManifestEntry(
                id=agent_id,
                node=node_name,
                purpose=purpose,
                tools=tools,
                direct_assignable=direct_assignable,
                entry_only_via=entry_only_via,
                constraints=AgentConstraints(**(block.runtime if block else {})),
            )
        )

    return BuiltManifest(agents=agents, graph=discovered.graph, warnings=warnings)
