"""PatternDetector: parse_rule + the site-threshold / cooldown / tightening logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import Settings
from patterns import PatternDetector, parse_rule


def now():
    return datetime.now(timezone.utc)


class TestParseRule:
    def test_splits_field_op_and_bound(self):
        assert parse_rule("tool_calls[*].args.amount lte 100") == ("tool_calls[*].args.amount", "lte", "100")

    def test_non_constraint_rules_are_unparsable(self):
        assert parse_rule("dispatch.timeout") is None
        assert parse_rule("budget.max_tool_calls") is None
        assert parse_rule("manual_kill") is None


def make_detector(**overrides) -> PatternDetector:
    defaults = dict(pattern_site_threshold=2, pattern_window_sec=300.0, pattern_push_cooldown_sec=60.0, pattern_tighten_factor=0.5)
    defaults.update(overrides)
    return PatternDetector(Settings(**defaults))


class TestRecordViolation:
    def test_a_single_site_does_not_trigger_a_pattern(self):
        detector = make_detector()
        assert detector.record_violation("tool_calls[*].args.amount lte 100", "site-a", now()) is None

    def test_two_distinct_sites_trigger_and_halve_an_lte_bound(self):
        detector = make_detector()
        detector.record_violation("tool_calls[*].args.amount lte 100", "site-a", now())
        match = detector.record_violation("tool_calls[*].args.amount lte 100", "site-b", now())
        assert match is not None
        assert match.rule_key == "tool_calls[*].args.amount lte"
        assert match.value == 50.0
        assert match.distinct_sites == 2

    def test_the_same_site_reporting_twice_does_not_count_as_two_sites(self):
        detector = make_detector()
        detector.record_violation("tool_calls[*].args.amount lte 100", "site-a", now())
        assert detector.record_violation("tool_calls[*].args.amount lte 100", "site-a", now()) is None

    def test_gte_raises_the_floor_instead_of_lowering_it(self):
        detector = make_detector()
        detector.record_violation("score gte 80", "site-a", now())
        match = detector.record_violation("score gte 80", "site-b", now())
        assert match is not None
        assert match.value == 160.0

    def test_non_numeric_ops_detect_but_do_not_push(self):
        detector = make_detector()
        detector.record_violation("status not_in ['flagged']", "site-a", now())
        assert detector.record_violation("status not_in ['flagged']", "site-b", now()) is None

    def test_cooldown_suppresses_a_second_push_for_the_same_rule(self):
        detector = make_detector(pattern_push_cooldown_sec=300.0)
        t = now()
        detector.record_violation("amount lte 100", "site-a", t)
        first = detector.record_violation("amount lte 100", "site-b", t)
        assert first is not None
        # A third site sees it moments later -- still within cooldown.
        second = detector.record_violation("amount lte 100", "site-c", t + timedelta(seconds=1))
        assert second is None

    def test_sightings_outside_the_window_are_pruned_and_do_not_count(self):
        detector = make_detector(pattern_window_sec=10.0)
        stale = now() - timedelta(seconds=100)
        detector.record_violation("amount lte 100", "site-a", stale)
        # Only site-b is within the window now; site-a's old sighting was pruned.
        assert detector.record_violation("amount lte 100", "site-b", now()) is None

    def test_unparsable_rule_never_triggers(self):
        detector = make_detector()
        detector.record_violation("dispatch.timeout", "site-a", now())
        assert detector.record_violation("dispatch.timeout", "site-b", now()) is None
