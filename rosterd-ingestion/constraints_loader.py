"""Parsing and validating constraints.yaml against the discovered graph.

The file is the human-authored half of the manifest. Its node names are checked
against the graph, because a typo'd node name would otherwise fail open: the
kernel would look up constraints for `refund_nod`, find none, and let an
unconstrained refund agent run. That is the exact failure rosterd exists to
prevent, so it is a hard error, not a warning.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

import yaml

from errors import ConstraintsParseError, ConstraintsValidationError

#: Keys that shape the manifest entry itself rather than runtime constraints.
#: Everything else in a node's block is passed through to the kernel.
RESERVED_KEYS = {"id", "purpose", "tools", "direct_assignable", "entry_only_via"}


@dataclass
class NodeConstraintBlock:
    """One node's block from constraints.yaml, split into manifest vs. runtime."""

    node: str
    id: str | None = None
    purpose: str | None = None
    tools: list[str] | None = None
    direct_assignable: bool | None = None
    entry_only_via: list[str] = field(default_factory=list)
    runtime: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedConstraints:
    version: int | None
    per_node: dict[str, NodeConstraintBlock]
    warnings: list[str] = field(default_factory=list)


def _require_mapping(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConstraintsParseError(
            f"{where} must be a mapping, got {type(value).__name__}.", where=where
        )
    return value


def _as_str_list(value: Any, where: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConstraintsParseError(
        f"{where} must be a string or a list of strings.", where=where
    )


def parse_constraints(raw_yaml: str) -> ParsedConstraints:
    """Parse constraints.yaml into per-node blocks. No graph knowledge needed."""
    if not raw_yaml or not raw_yaml.strip():
        # An empty file is legal: discover the graph, constrain nothing.
        return ParsedConstraints(version=None, per_node={})

    try:
        loaded = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ConstraintsParseError(f"constraints.yaml is not valid YAML: {exc}") from exc

    if loaded is None:
        return ParsedConstraints(version=None, per_node={})

    document = _require_mapping(loaded, "constraints.yaml")

    version = document.get("version")
    if version is not None and not isinstance(version, int):
        raise ConstraintsParseError("`version` must be an integer.", where="version")

    # Canonical shape nests under `constraints:`. A bare mapping of node blocks
    # is also accepted, since that is what people write by hand first.
    if "constraints" in document:
        blocks = _require_mapping(document["constraints"], "constraints")
    else:
        blocks = {k: v for k, v in document.items() if k != "version"}

    per_node: dict[str, NodeConstraintBlock] = {}
    warnings: list[str] = []

    for node_name, raw_block in blocks.items():
        if not isinstance(node_name, str):
            raise ConstraintsParseError(
                f"Node keys must be strings, got {node_name!r}.", where="constraints"
            )
        where = f"constraints.{node_name}"
        block_dict = _require_mapping(raw_block if raw_block is not None else {}, where)

        block = NodeConstraintBlock(node=node_name)

        if "id" in block_dict:
            if not isinstance(block_dict["id"], str):
                raise ConstraintsParseError(f"{where}.id must be a string.", where=where)
            block.id = block_dict["id"]

        if "purpose" in block_dict:
            if not isinstance(block_dict["purpose"], str):
                raise ConstraintsParseError(f"{where}.purpose must be a string.", where=where)
            block.purpose = block_dict["purpose"]

        if "tools" in block_dict:
            block.tools = _as_str_list(block_dict["tools"], f"{where}.tools")

        if "direct_assignable" in block_dict:
            value = block_dict["direct_assignable"]
            if not isinstance(value, bool):
                raise ConstraintsParseError(
                    f"{where}.direct_assignable must be true or false.", where=where
                )
            block.direct_assignable = value

        if "entry_only_via" in block_dict:
            block.entry_only_via = _as_str_list(
                block_dict["entry_only_via"], f"{where}.entry_only_via"
            )

        block.runtime = {k: v for k, v in block_dict.items() if k not in RESERVED_KEYS}

        if node_name in per_node:
            warnings.append(f"Duplicate block for {node_name!r}; the later one wins.")
        per_node[node_name] = block

    return ParsedConstraints(version=version, per_node=per_node, warnings=warnings)


def _suggest(name: str, known: list[str]) -> str | None:
    matches = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    return matches[0] if matches else None


def validate_against_graph(
    parsed: ParsedConstraints, agent_nodes: list[str], edges: list
) -> list[str]:
    """Check every node name the file mentions actually exists in the graph.

    Raises on unknown names. Returns warnings for wiring that parses fine but
    looks wrong — an `entry_only_via` with no matching edge, or an agent that
    nothing can reach.
    """
    known = list(agent_nodes)
    unknown: list[dict[str, Any]] = []

    for node_name, block in parsed.per_node.items():
        if node_name not in known:
            unknown.append(
                {"name": node_name, "where": "constraints", "did_you_mean": _suggest(node_name, known)}
            )
        for via in block.entry_only_via:
            if via not in known:
                unknown.append(
                    {
                        "name": via,
                        "where": f"constraints.{node_name}.entry_only_via",
                        "did_you_mean": _suggest(via, known),
                    }
                )
        prior = block.runtime.get("requires_prior_node")
        if isinstance(prior, str) and prior not in known:
            unknown.append(
                {
                    "name": prior,
                    "where": f"constraints.{node_name}.requires_prior_node",
                    "did_you_mean": _suggest(prior, known),
                }
            )

    if unknown:
        listed = ", ".join(sorted({u["name"] for u in unknown}))
        raise ConstraintsValidationError(
            f"constraints.yaml references node(s) not in the graph: {listed}.",
            unknown_nodes=unknown,
            known_nodes=known,
        )

    warnings: list[str] = []
    incoming: dict[str, set[str]] = {n: set() for n in known}
    for edge in edges:
        if edge.target in incoming:
            incoming[edge.target].add(edge.source)

    for node_name, block in parsed.per_node.items():
        for via in block.entry_only_via:
            if via not in incoming.get(node_name, set()):
                warnings.append(
                    f"{node_name}.entry_only_via lists {via!r}, but the graph has no "
                    f"edge {via} -> {node_name}. The kernel will refuse that handoff."
                )
        if block.direct_assignable is False and not block.entry_only_via:
            warnings.append(
                f"{node_name} is not directly assignable and declares no entry_only_via, "
                "so nothing can route work to it."
            )

    return warnings
