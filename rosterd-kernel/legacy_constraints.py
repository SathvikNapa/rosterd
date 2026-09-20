"""Stopgap: translates ingestion's real (legacy, pre-brief) manifest shape
into the finalized brief's `list[ConstraintRule]`.

Confirmed by actually running ingestion and pointing a kernel at it (see
manifest_source.py's module docstring): `AgentManifestEntry.constraints` in
`rosterd-ingestion/ingestion.py` is still a flat
`{max_refund_usd, requires_prior_node, ...}` object, human-authored in
`constraints.yaml`, not the schema-inferred `ConstraintRule` list this
kernel's own contract (and Param's own brief) specify. Without this module,
every real confirmed manifest fails Pydantic validation outright.

This is deliberately narrow, not a generic solver: it maps *known,
named* legacy keys to a field path, by hand, because there is no way to
derive "which tool argument does max_refund_usd constrain" from the flat
number alone -- that's domain knowledge about a specific demo-agent's tool
schema, not something inferable from `{"max_refund_usd": 100}`. That's
exactly why it's a constant here, not something spread across the parsing
code: whichever demo-agent a site is actually pointed at, this is the one
place to repoint it.

Updated for `rosterd-demo-agent` (Shruti's real service, now in this repo):
her `issue_refund(order_id: str, amount: float)` names the arg `amount`,
not `amount_usd` -- the field path below was changed to match. The earlier
value (`tool_calls[*].args.amount_usd`) matched
`rosterd-ingestion/demo-agent/tools.py`'s bundled discovery fixture, which
is exercised by ingestion's own probe/discovery tests but is not the
service any real kernel is ever configured against
(`ROSTERD_KERNEL_SIMULATED_AGENT_URL` / the real Docker network point at
`rosterd-demo-agent`, never at ingestion's fixture) -- see the README's
"Verified against the real ingestion service" section for the run that
used the old value, and the note just below it for this change.

`confidence` is deliberately `low` for every legacy-adapted rule (never
`high`, which `manifest.py`'s own `ConstraintRule` reserves for a real
schema-inferred bound): this is a name-matching heuristic, not something
that read a Pydantic `Field(le=...)` off a tool's args_schema. A Review
screen should flag these for a human exactly the way it flags anything
else with low confidence.

`requires_prior_node` is intentionally NOT mapped here: it's a
graph-ordering fact, not a per-response check `evaluate_rule` could ever
test against a single InvokeResponse, and it's already fully represented by
`AgentManifestEntry.entry_only_via`, which the kernel enforces directly at
the /dispatch boundary. Mapping it to a fake ConstraintRule would just be
noise. Any other unmapped key is skipped with a debug log line, not an
error -- a stopgap that hard-fails on a key nobody anticipated would be
worse than one that quietly ignores it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger("rosterd.kernel.legacy_constraints")

# Deliberately no import from manifest.py: manifest.py imports THIS module
# (to adapt a dict-shaped `constraints` before validating it as
# ConstraintRule), so this stays a dependency-free leaf module and returns
# plain dicts rather than ConstraintRule instances -- manifest.py is the
# one place that actually constructs the pydantic model.


@dataclass(frozen=True)
class LegacyMapping:
    field: str
    op: Literal["lte", "gte", "eq", "in", "not_in"]


#: Edit this to match whatever demo-agent is actually running.
#: Keyed by the legacy AgentConstraints field name.
DEFAULT_LEGACY_CONSTRAINT_MAP: dict[str, LegacyMapping] = {
    "max_refund_usd": LegacyMapping(field="tool_calls[*].args.amount", op="lte"),
    # rosterd-demo-agent's constraints.yaml declares this on `fulfillment`
    # (mirrors tools.ReserveInventoryArgs.qty = Field(le=50)); AgentConstraints'
    # extra="allow" lets it through ingestion, but nothing mapped it to a
    # field path until now -- confirmed live (dispatch a 200-unit reserve
    # through the real kernel + rosterd-demo-agent: `status: done,
    # violation: null` with this key absent from the map). Unlike
    # max_refund_usd, this one's reachable through a live, un-interrupted
    # dispatch (fulfillment_node has no interrupt() gate), so it's the
    # constraint a real demo can actually trigger end to end.
    "max_qty": LegacyMapping(field="tool_calls[*].args.qty", op="lte"),
}

#: Keys that are known to NOT be per-response constraints (see module
#: docstring) -- skipped silently, not logged as "unmapped", so they don't
#: look like an oversight every time they're encountered.
_KNOWN_NON_CONSTRAINT_KEYS = {"requires_prior_node"}


def is_legacy_shape(value: Any) -> bool:
    """A finalized-brief manifest's `constraints` is a list; ingestion's
    real, current shape is a dict. Anything else (None, missing) counts as
    "nothing to adapt" and is handled by the caller."""
    return isinstance(value, dict)


def adapt_legacy_constraints(
    raw: dict[str, Any], mapping: dict[str, LegacyMapping] = DEFAULT_LEGACY_CONSTRAINT_MAP
) -> list[dict[str, Any]]:
    """Returns plain dicts shaped exactly like `manifest.ConstraintRule`'s
    fields (field/op/value/source/confidence) -- the caller (manifest.py)
    constructs the actual pydantic model, since this module doesn't import
    manifest.py (see the note at the top of the file)."""
    rules: list[dict[str, Any]] = []
    for key, value in raw.items():
        if value is None:
            continue
        legacy = mapping.get(key)
        if legacy is None:
            if key not in _KNOWN_NON_CONSTRAINT_KEYS:
                logger.debug("legacy constraint key %r has no field mapping, skipping", key)
            continue
        rules.append(
            {
                "field": legacy.field,
                "op": legacy.op,
                "value": value,
                "source": "default",  # not schema/code/interrupt -- a legacy YAML value
                "confidence": "low",  # see module docstring
            }
        )
    return rules
