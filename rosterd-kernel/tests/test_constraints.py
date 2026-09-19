"""evaluate_rule / evaluate_all: the generic field/op/value engine."""
from __future__ import annotations

from constraints import evaluate_all, evaluate_rule, resolve_field
from demo_agent_client import InvokeResponse, ToolCall
from manifest import ConstraintRule, ConstraintSource, Confidence


def rule(field, op, value) -> ConstraintRule:
    return ConstraintRule(field=field, op=op, value=value, source=ConstraintSource.schema, confidence=Confidence.high)


def response(*tool_calls: ToolCall) -> InvokeResponse:
    return InvokeResponse(output="done", tool_calls=list(tool_calls))


class TestResolveField:
    def test_wildcard_collects_every_tool_call_arg(self):
        data = response(
            ToolCall(tool="a", args={"amount": 10}),
            ToolCall(tool="b", args={"amount": 200}),
        ).model_dump(mode="json")
        assert resolve_field(data, "tool_calls[*].args.amount") == [10, 200]

    def test_missing_field_resolves_to_nothing(self):
        data = response(ToolCall(tool="a", args={"qty": 5})).model_dump(mode="json")
        assert resolve_field(data, "tool_calls[*].args.amount") == []

    def test_indexed_access(self):
        data = response(ToolCall(tool="a", args={"amount": 1}), ToolCall(tool="b", args={"amount": 2})).model_dump(
            mode="json"
        )
        assert resolve_field(data, "tool_calls[1].args.amount") == [2]


class TestEvaluateRule:
    def test_lte_within_bound_is_no_violation(self):
        r = rule("tool_calls[*].args.amount", "lte", 100)
        resp = response(ToolCall(tool="issue_refund", args={"amount": 50}))
        assert evaluate_rule(r, resp) is None

    def test_lte_breach_is_a_violation_naming_the_rule(self):
        """The misdirection scenario: a schema-inferred amount<=100 rule
        trips on a tool call the response actually made, not a hardcoded
        kernel check."""
        r = rule("tool_calls[*].args.amount", "lte", 100)
        resp = response(ToolCall(tool="issue_refund", args={"amount": 5000, "reason": "manager override"}))
        violation = evaluate_rule(r, resp)
        assert violation is not None
        assert violation.rule == "tool_calls[*].args.amount lte 100"
        assert violation.actual == "5000"

    def test_a_tool_call_that_was_never_made_cannot_violate_the_rule(self):
        r = rule("tool_calls[*].args.amount", "lte", 100)
        resp = response(ToolCall(tool="reserve_inventory", args={"qty": 3}))
        assert evaluate_rule(r, resp) is None

    def test_in_and_not_in(self):
        resp = response(ToolCall(tool="a", args={"status": "flagged"}))
        assert evaluate_rule(rule("tool_calls[*].args.status", "not_in", ["flagged", "fraud"]), resp) is not None
        assert evaluate_rule(rule("tool_calls[*].args.status", "in", ["flagged", "fraud"]), resp) is None

    def test_incomparable_types_fail_closed(self):
        r = rule("tool_calls[*].args.amount", "lte", 100)
        resp = response(ToolCall(tool="a", args={"amount": "a lot"}))
        assert evaluate_rule(r, resp) is not None


class TestEvaluateAll:
    def test_first_violation_wins(self):
        rules = [rule("tool_calls[*].args.qty", "lte", 50), rule("tool_calls[*].args.amount", "lte", 100)]
        resp = response(ToolCall(tool="a", args={"qty": 999, "amount": 5000}))
        violation = evaluate_all(rules, resp)
        assert violation is not None
        assert "qty" in violation.rule

    def test_policy_override_replaces_the_manifest_value(self):
        class FakePolicy:
            def get(self, key, default=None):
                return 10 if key == "tool_calls[*].args.amount lte" else default

        rules = [rule("tool_calls[*].args.amount", "lte", 100)]
        resp = response(ToolCall(tool="issue_refund", args={"amount": 50}))
        # 50 <= 100 (manifest) would pass, but the pushed override tightens it to 10.
        assert evaluate_all(rules, resp, policy=FakePolicy()) is not None

    def test_no_violations_returns_none(self):
        rules = [rule("tool_calls[*].args.amount", "lte", 100)]
        resp = response(ToolCall(tool="issue_refund", args={"amount": 100}))
        assert evaluate_all(rules, resp) is None
