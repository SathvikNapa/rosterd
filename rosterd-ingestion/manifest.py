"""Merging discovered graph structure with constraints.yaml into the manifest.

This is the join the rest of the team reads. Two rules govern it:

* **Constraints win.** A value written by a human in constraints.yaml overrides
  anything inferred from the repo. Inference is a convenience; the file is the
  contract.
* **Fail closed.** An agent nobody wrote a constraints block for is *not*
  directly assignable. Silence must not grant capability.
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

        direct_assignable = bool(block.direct_assignable) if block and block.direct_assignable is not None else False
        entry_only_via = list(block.entry_only_via) if block else []

        if block is None:
            warnings.append(
                f"{node_name}: no constraints block. Defaulting to unconstrained "
                "and not directly assignable."
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
