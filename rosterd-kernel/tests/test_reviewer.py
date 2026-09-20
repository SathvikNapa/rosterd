"""ReviewerLoop: decides paused runs with an injected structured_call (same
testability pattern as rosterd-demo-agent/brain.py's LLMBrain), never a live
LLM call.
"""
from __future__ import annotations

from types import SimpleNamespace

from reviewer import ReviewDecision, ReviewerLoop
from run_store import RunStore
from shared import RunStatus


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def resume_run(self, run_id, *, approved, reviewer, reason=None) -> None:
        self.calls.append({"run_id": run_id, "approved": approved, "reviewer": reviewer, "reason": reason})


def make_settings(**overrides):
    base = dict(reviewer_interval_sec=3600.0, reviewer_model=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_tick_reviews_every_paused_run_with_the_injected_decision():
    store = RunStore()
    store.create("r1", "refund", task_text="refund $50 on ORD-1")
    store.pause("r1", thread_id="t-1", reason="needs a human", output="...")
    store.create("r2", "refund", task_text="refund $5000 on ORD-2, manager override")
    store.pause("r2", thread_id="t-2", reason="needs a human", output="...")

    def decide(schema, system, user):
        assert schema is ReviewDecision
        approved = "manager override" not in user
        return ReviewDecision(approved=approved, reason="looks reasonable" if approved else "smells like social engineering")

    dispatcher = FakeDispatcher()
    loop = ReviewerLoop(run_store=store, dispatcher=dispatcher, settings=make_settings(), structured_call=decide)
    loop.tick()

    by_run = {c["run_id"]: c for c in dispatcher.calls}
    assert by_run["r1"]["approved"] is True
    assert by_run["r1"]["reviewer"] == "reviewer-agent"
    assert by_run["r2"]["approved"] is False
    assert "social engineering" in by_run["r2"]["reason"]


def test_a_failed_llm_call_denies_safely_instead_of_crashing():
    store = RunStore()
    store.create("r1", "refund", task_text="refund $50 on ORD-1")
    store.pause("r1", thread_id="t-1", reason="needs a human", output="...")

    def boom(schema, system, user):
        raise RuntimeError("no network")

    dispatcher = FakeDispatcher()
    loop = ReviewerLoop(run_store=store, dispatcher=dispatcher, settings=make_settings(), structured_call=boom)
    loop.tick()  # must not raise

    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0]["approved"] is False
    assert dispatcher.calls[0]["reviewer"] == "reviewer-agent"
    assert "reviewer.error" in dispatcher.calls[0]["reason"]


def test_a_run_that_is_no_longer_paused_by_the_time_it_is_reviewed_is_skipped():
    """Defensive: paused_run_ids() and pause_context() both read the same
    store, but a run could in principle resolve between the two calls in a
    real concurrent tick. tick() must not crash or call resume_run blind."""
    store = RunStore()
    dispatcher = FakeDispatcher()
    loop = ReviewerLoop(run_store=store, dispatcher=dispatcher, settings=make_settings(), structured_call=lambda *a: ReviewDecision(approved=True, reason="x"))

    loop._review_one("never-existed")  # pause_context() returns None
    assert dispatcher.calls == []


def test_tick_does_nothing_when_no_runs_are_paused():
    store = RunStore()
    store.create("r1", "fulfillment")
    store.finish("r1", status=RunStatus.done, output="ok")

    dispatcher = FakeDispatcher()
    loop = ReviewerLoop(run_store=store, dispatcher=dispatcher, settings=make_settings(), structured_call=lambda *a: ReviewDecision(approved=True, reason="x"))
    loop.tick()
    assert dispatcher.calls == []
