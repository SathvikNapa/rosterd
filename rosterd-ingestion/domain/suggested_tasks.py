"""Deterministic "try this" task suggestions, derived from an agent's own
discovered wiring -- purpose, tools, numeric constraints, and whether it's
directly dispatchable or only reached via another agent. No LLM call, no
invented domain facts: the same philosophy as the rest of discovery
(astscan.py, legacy_constraints.py) -- template from what was actually
found, stay honest about what wasn't.

Why this exists: someone who just ingested a repo they didn't write has no
idea what a reasonable `/ask/parse` request even looks like for it. These
are a starting point to paste into Ask, not a claim about how the agent
will actually behave -- the agent's own real logic decides that.

Verb-pattern matching on the tool name (and, failing that, the purpose
text) is deliberately narrow: refund/reserve/charge/check/cancel/send are
common enough real-world domains to template confidently, with numbers
pulled from whatever this specific agent's own constraints declare, not
invented. Anything that doesn't match one of those gets the one honest
fallback every tool name supports -- its own name, humanised -- rather than
a guessed scenario for a domain this has no signal about.
"""
from __future__ import annotations

import re

from domain.ingestion import AgentConstraints

_MAX_SUGGESTIONS_PER_AGENT = 2

_REFUND_RE = re.compile(r"refund", re.I)
_RESERVE_RE = re.compile(r"reserve|inventory|\bstock\b", re.I)
_CHARGE_RE = re.compile(r"charge|payment|\bpay\b", re.I)
_CHECK_RE = re.compile(r"check|lookup|\bquery\b|^get_|^fetch_|status", re.I)
_CANCEL_RE = re.compile(r"cancel", re.I)
_SEND_RE = re.compile(r"send|notify|email|alert", re.I)


def _humanize(tool_name: str) -> str:
    """'reserve_inventory' -> 'reserve inventory'."""
    return tool_name.replace("_", " ").strip()


def _numeric_caps(constraints: AgentConstraints) -> dict[str, float]:
    """Every numeric field on this agent's own constraints whose name
    looks like a business cap (`max_...` -- formal or a repo-declared
    extra key, since AgentConstraints allows extras) -- the only kind of
    number this generically knows how to build a before/after example
    from, regardless of what a specific repo happens to call it."""
    return {
        key: float(value)
        for key, value in constraints.model_dump().items()
        if key.startswith("max_") and isinstance(value, (int, float))
    }


def _cap_matching(caps: dict[str, float], *substrings: str) -> float | None:
    for key, value in caps.items():
        if any(s in key for s in substrings):
            return value
    return None


def _round(value: float) -> int:
    return max(1, round(value))


def suggest_tasks(
    *,
    purpose: str,
    tools: list[str],
    direct_assignable: bool,
    entry_only_via: list[str],
    constraints: AgentConstraints,
) -> list[str]:
    """Up to `_MAX_SUGGESTIONS_PER_AGENT` plain-language task strings,
    ready to paste into Ask -- or an empty list when there's genuinely
    nothing to go on (no tools, no purpose), rather than a vacuous
    suggestion forced out of nothing."""
    primary = tools[0] if tools else ""
    caps = _numeric_caps(constraints)
    suggestions: list[str] = []

    def add(task: str) -> None:
        if len(suggestions) < _MAX_SUGGESTIONS_PER_AGENT:
            suggestions.append(task)

    haystack = f"{primary} {purpose}"

    if _REFUND_RE.search(haystack):
        cap = _cap_matching(caps, "refund", "amount")
        add(f"Refund ${_round(cap * 0.5) if cap else 45} on order ORD-DEMO.")
        if cap:
            add(f"Refund ${_round(cap * 1.5)} on order ORD-DEMO -- over the ${cap:.0f} cap, to see it caught.")
    elif _RESERVE_RE.search(haystack):
        cap = _cap_matching(caps, "qty", "quantity", "unit")
        add(f"Reserve {_round(cap * 0.3) if cap else 10} units of SKU-DEMO.")
        if cap:
            add(f"Reserve {_round(cap * 1.5)} units of SKU-DEMO -- over the {cap:.0f}-unit cap, to see it caught.")
    elif _CHARGE_RE.search(haystack):
        cap = _cap_matching(caps, "charge", "amount", "usd")
        add(f"Charge ${_round(cap * 0.5) if cap else 50} on order ORD-DEMO.")
        if cap:
            add(f"Charge ${_round(cap * 1.5)} on order ORD-DEMO -- over the ${cap:.0f} cap, to see it caught.")
    elif _CHECK_RE.search(haystack):
        add("Check the status of SKU-DEMO.")
    elif _CANCEL_RE.search(haystack):
        add("Cancel order ORD-DEMO.")
    elif _SEND_RE.search(haystack):
        add("Send a notification about order ORD-DEMO.")
    elif primary:
        add(f"Ask it to {_humanize(primary)}.")
    elif purpose:
        add(f"Ask it something in line with its purpose: {purpose}")

    if suggestions and not direct_assignable and entry_only_via:
        via = " or ".join(entry_only_via)
        suggestions = [f"{s} (reached indirectly, normally via {via} -- not directly assignable)" for s in suggestions]

    return suggestions
