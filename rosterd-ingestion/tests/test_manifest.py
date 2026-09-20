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


def test_unlisted_agents_infer_direct_assignable_from_interrupt(settings):
    """Silence in constraints.yaml no longer means direct_assignable: false
    outright (manifest.py's docstring covers the tradeoff and why it changed)
    -- it's inferred from whether the node's own function body calls
    interrupt(). The bundled demo-agent fixture's triage_node has no
    interrupt() call anywhere, so it infers assignable."""
    built = build(settings, "constraints:\n  refund_node:\n    direct_assignable: true\n")
    triage = next(a for a in built.agents if a.node == "triage_node")
    assert triage.direct_assignable is True
    assert any("direct_assignable not set in constraints.yaml" in w for w in built.warnings)


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


def test_direct_assignable_and_entry_only_via_infer_from_interrupt_and_edges(settings, make_repo):
    """No constraints.yaml at all for either node -- both direct_assignable
    and entry_only_via come entirely from inference. gated_node calls
    interrupt(); intake_node's conditional edge into it is the only signal
    for entry_only_via (see _infer_entry_only_via's docstring for why raw
    __start__ reachability isn't used instead)."""
    _, path = make_repo(
        "interrupt-gated",
        {
            "agent.py": (
                "from typing import TypedDict\n"
                "from langgraph.graph import END, START, StateGraph\n"
                "from langgraph.types import interrupt\n"
                "\n"
                "class S(TypedDict, total=False):\n"
                "    text: str\n"
                "\n"
                "def intake_node(state: S) -> S:\n"
                "    return state\n"
                "\n"
                "def route(state: S) -> str:\n"
                "    return 'gated_node' if state.get('text') == 'flagged' else 'fast_node'\n"
                "\n"
                "def gated_node(state: S) -> S:\n"
                "    interrupt({'reason': 'needs a human'})\n"
                "    return state\n"
                "\n"
                "def fast_node(state: S) -> S:\n"
                "    return state\n"
                "\n"
                "builder = StateGraph(S)\n"
                "builder.add_node('intake_node', intake_node)\n"
                "builder.add_node('gated_node', gated_node)\n"
                "builder.add_node('fast_node', fast_node)\n"
                "builder.add_edge(START, 'intake_node')\n"
                "builder.add_conditional_edges('intake_node', route, "
                "{'gated_node': 'gated_node', 'fast_node': 'fast_node'})\n"
                "builder.add_edge('gated_node', END)\n"
                "builder.add_edge('fast_node', END)\n"
                "graph = builder.compile()\n"
            ),
        },
    )
    discovered = discovery.discover(path, settings)
    built = build_manifest(discovered, parse_constraints("version: 1\nconstraints: {}\n"))
    by_node = {a.node: a for a in built.agents}

    assert by_node["gated_node"].direct_assignable is False
    assert by_node["gated_node"].entry_only_via == ["intake_node"]
    assert by_node["fast_node"].direct_assignable is True
    assert by_node["fast_node"].entry_only_via == []
    assert any(
        "gated_node: direct_assignable not set in constraints.yaml -- inferred False "
        "(interrupt() found)" in w
        for w in built.warnings
    )
