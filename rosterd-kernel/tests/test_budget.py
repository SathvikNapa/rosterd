"""BudgetTracker: tool-call count and elapsed time per run_id."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from budget import BudgetTracker
from config import Settings


def make_tracker(**overrides) -> BudgetTracker:
    defaults = dict(max_tool_calls_per_run=3, max_run_seconds=60.0)
    defaults.update(overrides)
    return BudgetTracker(Settings(**defaults))


def test_no_violation_under_the_limits():
    tracker = make_tracker()
    tracker.start("r1")
    tracker.record_tool_calls("r1", 2)
    tracker.finish("r1")
    assert tracker.check("r1") is None


def test_tool_call_count_breach():
    tracker = make_tracker()
    tracker.start("r1")
    tracker.record_tool_calls("r1", 5)
    tracker.finish("r1")
    violation = tracker.check("r1")
    assert violation is not None
    assert violation.rule == "budget.max_tool_calls"
    assert violation.actual == "5"


def test_elapsed_time_breach():
    tracker = make_tracker(max_run_seconds=10.0)
    tracker.start("r1")
    budget = tracker._runs["r1"]  # noqa: SLF001 - whitebox to force elapsed time without sleeping
    budget.started_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    tracker.finish("r1")
    violation = tracker.check("r1")
    assert violation is not None
    assert violation.rule == "budget.max_run_seconds"


def test_remaining_budget_is_1_when_nothing_is_in_flight():
    tracker = make_tracker()
    assert tracker.remaining_budget() == 1.0


def test_remaining_budget_tightens_as_an_active_run_uses_its_allowance():
    tracker = make_tracker()
    tracker.start("r1")
    assert tracker.remaining_budget() > 0.99  # essentially 1.0; a hair of wall-clock time has already passed
    tracker.record_tool_calls("r1", 3)  # 3 of 3 allowed, not yet finished
    assert tracker.remaining_budget() == 0.0


def test_unknown_run_id_is_not_a_violation():
    tracker = make_tracker()
    assert tracker.check("does-not-exist") is None
