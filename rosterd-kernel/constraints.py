"""The constraint evaluator: generic field/op/value rules against a demo
agent's InvokeResponse. One pure, unit-testable function per the design --
`evaluate_rule(rule, response) -> Violation | None`.

`rule.field` is a dot/bracket path into the InvokeResponse, e.g.
`"tool_calls[0].args.amount"` for a specific tool call, or
`"tool_calls[*].args.amount"` to check every tool call the response made
(the wildcard form is what the misdirection scenario needs: an
out-of-policy refund could be any tool call in the list, not always the
first).
"""
from __future__ import annotations

import re
from typing import Any

from demo_agent_client import InvokeResponse
from manifest import ConstraintRule
from shared import Violation

_SEGMENT_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)?((?:\[[^\]]+\])*)$")
_INDEX_RE = re.compile(r"\[([^\]]+)\]")


def _parse_segment(segment: str) -> tuple[str | None, list[str]]:
    match = _SEGMENT_RE.match(segment)
    if not match:
        raise ValueError(f"unparsable constraint field segment: {segment!r}")
    name, brackets = match.groups()
    indices = _INDEX_RE.findall(brackets or "")
    return name, indices


def _resolve(data: Any, segments: list[str]) -> list[Any]:
    if not segments:
        return [data]

    name, indices = _parse_segment(segments[0])
    rest = segments[1:]

    values: list[Any] = [data]
    if name is not None:
        if not isinstance(data, dict) or name not in data:
            return []
        values = [data[name]]

    for index in indices:
        next_values: list[Any] = []
        for value in values:
            if not isinstance(value, list):
                continue
            if index == "*":
                next_values.extend(value)
                continue
            try:
                i = int(index)
            except ValueError:
                continue
            if -len(value) <= i < len(value):
                next_values.append(value[i])
        values = next_values

    resolved: list[Any] = []
    for value in values:
        resolved.extend(_resolve(value, rest))
    return resolved


def resolve_field(response_data: dict, field: str) -> list[Any]:
    """All values a field path matches in a response payload. Empty list
    means the path didn't exist in this response at all (the rule simply
    doesn't apply, not a violation -- a tool that wasn't called can't have
    called it out of policy)."""
    segments = field.split(".") if field else []
    return _resolve(response_data, segments)


def _format_value(value: Any) -> str:
    """ConstraintRule.value is typed `float | str | list`, so a manifest's
    integer `50` round-trips through pydantic as `50.0`. Render whole-number
    floats without the trailing `.0` so `Violation.rule`/`expected` read the
    way a human (or Shruti's schema) actually wrote the bound."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _check_op(rule: ConstraintRule, actual: Any) -> Violation | None:
    op, value = rule.op, rule.value
    try:
        if op == "lte":
            ok = actual <= value
        elif op == "gte":
            ok = actual >= value
        elif op == "eq":
            ok = actual == value
        elif op == "in":
            ok = actual in value
        elif op == "not_in":
            ok = actual not in value
        else:  # pragma: no cover - Literal[...] on ConstraintRule.op prevents this
            raise ValueError(f"unknown constraint operator {op!r}")
    except TypeError:
        # Incomparable types (e.g. a string where a number was expected).
        # Fail closed: an actual value the rule can't even evaluate against
        # is treated as a violation, not silently skipped.
        ok = False

    if ok:
        return None
    rendered = _format_value(value)
    return Violation(rule=f"{rule.field} {op} {rendered}", expected=f"{op} {rendered}", actual=str(actual))


def evaluate_rule(rule: ConstraintRule, response: InvokeResponse) -> Violation | None:
    """Pure and unit-testable, per the design: one rule, one response, the
    first breach it finds (or None)."""
    data = response.model_dump(mode="json")
    for actual in resolve_field(data, rule.field):
        violation = _check_op(rule, actual)
        if violation is not None:
            return violation
    return None


def evaluate_all(rules: list[ConstraintRule], response: InvokeResponse, *, policy=None) -> Violation | None:
    """First violation across every rule wins -- the kill switch fires on
    the first breach found, it doesn't collect all of them.

    `policy` is an optional policy.PolicyStore: if the coordinator pushed an
    override for this exact rule (keyed by "field op value", the same
    string `Violation.rule` uses), that override's value is checked instead
    of the manifest's, so a policy push takes effect immediately without
    waiting for a new confirmed manifest.
    """
    for rule in rules:
        effective = rule
        if policy is not None:
            override = policy.get(f"{rule.field} {rule.op}")
            if override is not None:
                effective = rule.model_copy(update={"value": override})
        violation = evaluate_rule(effective, response)
        if violation is not None:
            return violation
    return None
