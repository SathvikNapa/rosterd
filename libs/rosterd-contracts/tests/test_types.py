"""Smoke tests: the package builds, imports, and the types hold the shapes
every rosterd service depends on. Not meant to re-test pydantic itself."""
from rosterd_contracts import (
    GraphEdge,
    GraphSpec,
    Priority,
    RunStatus,
    SiteStatus,
    Violation,
)


def test_run_status_has_the_paused_value_the_interrupt_flow_depends_on():
    assert RunStatus.paused == "paused"
    assert {s.value for s in RunStatus} == {"working", "done", "killed", "paused"}


def test_priority_values():
    assert {p.value for p in Priority} == {"low", "medium", "high"}


def test_site_status_values():
    assert {s.value for s in SiteStatus} == {"healthy", "violation", "offline"}


def test_violation_is_a_plain_rule_expected_actual_triple():
    v = Violation(rule="tool_calls[*].args.qty lte 50", expected="lte 50", actual="999")
    assert v.rule and v.expected and v.actual


def test_graph_spec_holds_nodes_and_edges():
    spec = GraphSpec(
        nodes=["order_intake", "fulfillment"],
        edges=[GraphEdge(source="order_intake", target="fulfillment", condition="not_flagged")],
    )
    assert spec.nodes == ["order_intake", "fulfillment"]
    assert spec.edges[0].condition == "not_flagged"
