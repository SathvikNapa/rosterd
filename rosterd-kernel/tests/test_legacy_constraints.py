"""legacy_constraints.py: the stopgap translating ingestion's real dict-
shaped `constraints` into the finalized brief's `list[ConstraintRule]`.

Written after a live ingest -> confirm -> kernel-poll round trip against
the bundled demo-agent fixture failed Pydantic validation outright -- see
manifest_source.py and manifest.py's module docstrings for the full story.
"""
from __future__ import annotations

from legacy_constraints import DEFAULT_LEGACY_CONSTRAINT_MAP, adapt_legacy_constraints, is_legacy_shape
from manifest import AgentManifestEntry, ConstraintRule


class TestIsLegacyShape:
    def test_a_dict_is_legacy(self):
        assert is_legacy_shape({"max_refund_usd": 100}) is True

    def test_a_list_is_not_legacy(self):
        assert is_legacy_shape([]) is False
        assert is_legacy_shape([{"field": "x", "op": "lte", "value": 1, "source": "default", "confidence": "low"}]) is False


class TestAdaptLegacyConstraints:
    def test_known_key_becomes_a_constraint_rule_dict(self):
        rules = adapt_legacy_constraints({"max_refund_usd": 100.0})
        assert rules == [
            {"field": "tool_calls[*].args.amount_usd", "op": "lte", "value": 100.0, "source": "default", "confidence": "low"}
        ]

    def test_none_valued_keys_are_dropped(self):
        assert adapt_legacy_constraints({"max_refund_usd": None}) == []

    def test_requires_prior_node_is_dropped_not_mapped(self):
        """It's graph ordering, not a per-response check -- already
        represented by entry_only_via."""
        assert adapt_legacy_constraints({"requires_prior_node": "refund_node"}) == []

    def test_an_unrecognized_key_is_dropped_not_raised(self):
        assert adapt_legacy_constraints({"some_future_key": 42}) == []

    def test_the_resulting_dicts_construct_real_constraint_rules(self):
        rules = adapt_legacy_constraints({"max_refund_usd": 50})
        rule = ConstraintRule.model_validate(rules[0])
        assert rule.field == "tool_calls[*].args.amount_usd"
        assert rule.op == "lte"
        assert rule.value == 50
        assert rule.confidence == "low"


class TestAgentManifestEntryValidator:
    def test_a_legacy_dict_shaped_constraints_field_is_adapted_on_construction(self):
        entry = AgentManifestEntry.model_validate(
            {
                "id": "refund",
                "node": "refund_node",
                "purpose": "Issues refunds",
                "tools": ["issue_refund"],
                "direct_assignable": True,
                "constraints": {"max_refund_usd": 100.0, "requires_prior_node": None},
            }
        )
        assert len(entry.constraints) == 1
        assert entry.constraints[0].field == "tool_calls[*].args.amount_usd"
        assert entry.constraints[0].value == 100.0

    def test_an_already_finalized_list_shape_passes_through_unchanged(self):
        """Once ingestion moves to list[ConstraintRule], this validator
        should be a no-op -- proven here, not just asserted in a comment."""
        entry = AgentManifestEntry.model_validate(
            {
                "id": "refund",
                "node": "refund_node",
                "purpose": "Issues refunds",
                "direct_assignable": True,
                "constraints": [
                    {
                        "field": "tool_calls[*].args.amount",
                        "op": "lte",
                        "value": 100,
                        "source": "schema",
                        "confidence": "high",
                    }
                ],
            }
        )
        assert len(entry.constraints) == 1
        assert entry.constraints[0].source == "schema"
        assert entry.constraints[0].confidence == "high"

    def test_default_map_only_recognizes_the_bundled_fixtures_key(self):
        assert set(DEFAULT_LEGACY_CONSTRAINT_MAP) == {"max_refund_usd"}
