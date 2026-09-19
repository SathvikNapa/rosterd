"""Task 2: parsing constraints.yaml and validating it against the graph."""
from __future__ import annotations

import pytest

from constraints_loader import parse_constraints, validate_against_graph
from errors import ConstraintsParseError, ConstraintsValidationError
from shared import GraphEdge


def test_parses_canonical_nested_shape():
    parsed = parse_constraints(
        """
        version: 1
        constraints:
          refund_node:
            max_refund_usd: 100
            direct_assignable: true
        """
    )
    assert parsed.version == 1
    block = parsed.per_node["refund_node"]
    assert block.direct_assignable is True
    assert block.runtime == {"max_refund_usd": 100}


def test_accepts_bare_mapping_without_constraints_key():
    parsed = parse_constraints("refund_node:\n  direct_assignable: true\n")
    assert "refund_node" in parsed.per_node


def test_empty_file_is_legal():
    assert parse_constraints("").per_node == {}
    assert parse_constraints("   \n").per_node == {}
    assert parse_constraints("# just a comment\n").per_node == {}


def test_reserved_keys_are_split_from_runtime_constraints():
    parsed = parse_constraints(
        """
        constraints:
          escalation_node:
            purpose: Hands off to a human
            tools: [create_ticket]
            direct_assignable: false
            entry_only_via: [refund_node]
            requires_prior_node: refund_node
            custom_budget_usd: 5
        """
    )
    block = parsed.per_node["escalation_node"]
    assert block.purpose == "Hands off to a human"
    assert block.tools == ["create_ticket"]
    assert block.direct_assignable is False
    assert block.entry_only_via == ["refund_node"]
    # Unknown keys survive untouched for the kernel to interpret.
    assert block.runtime == {"requires_prior_node": "refund_node", "custom_budget_usd": 5}


def test_entry_only_via_accepts_a_bare_string():
    parsed = parse_constraints("constraints:\n  a_node:\n    entry_only_via: b_node\n")
    assert parsed.per_node["a_node"].entry_only_via == ["b_node"]


@pytest.mark.parametrize(
    "bad",
    [
        "constraints:\n  refund_node:\n    direct_assignable: yes-please\n",
        "constraints:\n  refund_node:\n    tools: 5\n",
        "constraints: [not, a, mapping]\n",
        "version: not-an-int\n",
        "just a string\n",
    ],
)
def test_malformed_constraints_are_rejected(bad):
    with pytest.raises(ConstraintsParseError):
        parse_constraints(bad)


def test_invalid_yaml_is_rejected():
    with pytest.raises(ConstraintsParseError, match="not valid YAML"):
        parse_constraints("constraints:\n  a: [unclosed\n")


def test_unknown_node_name_is_a_hard_error_with_a_suggestion():
    """A typo must fail loudly: silently dropping it would run the agent uncapped."""
    parsed = parse_constraints("constraints:\n  refund_nod:\n    max_refund_usd: 100\n")
    with pytest.raises(ConstraintsValidationError) as excinfo:
        validate_against_graph(parsed, ["triage_node", "refund_node"], [])
    details = excinfo.value.details
    assert details["unknown_nodes"][0]["name"] == "refund_nod"
    assert details["unknown_nodes"][0]["did_you_mean"] == "refund_node"


def test_unknown_entry_only_via_target_is_rejected():
    parsed = parse_constraints(
        "constraints:\n  refund_node:\n    entry_only_via: [ghost_node]\n"
    )
    with pytest.raises(ConstraintsValidationError):
        validate_against_graph(parsed, ["refund_node"], [])


def test_unknown_requires_prior_node_is_rejected():
    parsed = parse_constraints(
        "constraints:\n  refund_node:\n    requires_prior_node: ghost_node\n"
    )
    with pytest.raises(ConstraintsValidationError):
        validate_against_graph(parsed, ["refund_node"], [])


def test_entry_only_via_without_a_matching_edge_warns():
    parsed = parse_constraints(
        "constraints:\n  escalation_node:\n    entry_only_via: [refund_node]\n"
    )
    warnings = validate_against_graph(
        parsed,
        ["refund_node", "escalation_node"],
        [GraphEdge(source="__start__", target="refund_node")],
    )
    assert any("no edge refund_node -> escalation_node" in w for w in warnings)


def test_unreachable_agent_warns():
    parsed = parse_constraints("constraints:\n  orphan_node:\n    direct_assignable: false\n")
    warnings = validate_against_graph(parsed, ["orphan_node"], [])
    assert any("nothing can route work to it" in w for w in warnings)


def test_valid_wiring_produces_no_warnings():
    parsed = parse_constraints(
        "constraints:\n  escalation_node:\n    direct_assignable: false\n"
        "    entry_only_via: [refund_node]\n"
    )
    warnings = validate_against_graph(
        parsed,
        ["refund_node", "escalation_node"],
        [GraphEdge(source="refund_node", target="escalation_node", condition="route")],
    )
    assert warnings == []
