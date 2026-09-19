"""Task 3: merging graph structure and constraints into AgentManifestEntry."""
from __future__ import annotations

from conftest import DEMO_AGENT

import discovery
from constraints_loader import parse_constraints
from manifest import build_manifest, derive_id


def build(settings, constraints_yaml: str):
    discovered = discovery.discover(DEMO_AGENT, settings)
    return build_manifest(discovered, parse_constraints(constraints_yaml))


def test_demo_repo_produces_the_manifest_the_mockup_shows(settings):
    built = build(settings, (DEMO_AGENT / "constraints.yaml").read_text())
    by_id = {a.id: a for a in built.agents}

    assert set(by_id) == {"triage", "refund", "escalation"}

    refund = by_id["refund"]
    assert refund.node == "refund_node"
    assert refund.purpose == "Issues refunds, $100 cap"
    assert refund.tools == ["issue_refund"]
    assert refund.direct_assignable is True
    assert refund.constraints.max_refund_usd == 100

    escalation = by_id["escalation"]
    assert escalation.direct_assignable is False
    assert escalation.entry_only_via == ["refund_node", "triage_node"]
    assert escalation.constraints.requires_prior_node == "refund_node"

    assert built.warnings == []


def test_ids_are_derived_by_stripping_the_node_suffix():
    taken: set[str] = set()
    assert derive_id("refund_node", taken) == "refund"
    assert derive_id("escalation_agent", taken) == "escalation"
    assert derive_id("Weird Name!", taken) == "weird-name"


def test_id_collisions_fall_back_to_the_full_node_name():
    taken = {"refund"}
    assert derive_id("refund_node", taken) == "refund_node"


def test_explicit_id_in_constraints_overrides_the_derived_one(settings):
    built = build(settings, "constraints:\n  refund_node:\n    id: money-mover\n")
    assert {a.id for a in built.agents} >= {"money-mover"}


def test_constraints_purpose_overrides_the_docstring(settings):
    built = build(settings, "constraints:\n  refund_node:\n    purpose: Custom purpose\n")
    refund = next(a for a in built.agents if a.node == "refund_node")
    assert refund.purpose == "Custom purpose"


def test_purpose_falls_back_to_the_docstring_then_the_node_name(settings):
    built = build(settings, "")
    refund = next(a for a in built.agents if a.node == "refund_node")
    assert refund.purpose == "Issues refunds, $100 cap."


def test_declared_tools_replace_discovered_tools(settings):
    built = build(settings, "constraints:\n  refund_node:\n    tools: [issue_refund, wire_transfer]\n")
    refund = next(a for a in built.agents if a.node == "refund_node")
    assert refund.tools == ["issue_refund", "wire_transfer"]
    assert any("wire_transfer" in w for w in built.warnings)


def test_unlisted_agents_fail_closed(settings):
    """Silence in constraints.yaml must not grant assignability."""
    built = build(settings, "constraints:\n  refund_node:\n    direct_assignable: true\n")
    triage = next(a for a in built.agents if a.node == "triage_node")
    assert triage.direct_assignable is False
    assert any("no constraints block" in w for w in built.warnings)


def test_unknown_constraint_keys_survive_into_the_manifest(settings):
    """AgentConstraints allows extras so agents can declare their own shapes."""
    built = build(settings, "constraints:\n  refund_node:\n    daily_budget_usd: 2500\n")
    refund = next(a for a in built.agents if a.node == "refund_node")
    assert refund.constraints.model_dump()["daily_budget_usd"] == 2500


def test_one_entry_per_agent_node_and_no_sentinels(settings):
    built = build(settings, "")
    assert len(built.agents) == 3
    assert all(a.node not in discovery.SENTINELS for a in built.agents)
    assert built.graph.nodes[0] == "__start__"
