"""RunStore: killed is sticky against a late-arriving `done` write."""
from __future__ import annotations

from run_store import RunStore
from shared import RunStatus


def test_finish_records_the_outcome():
    store = RunStore()
    store.create("r1", "fulfillment")
    result = store.finish("r1", status=RunStatus.done, output="ok")
    assert result.status == RunStatus.done
    assert result.output == "ok"


def test_force_kill_after_finish_still_marks_it_killed():
    store = RunStore()
    store.create("r1", "fulfillment")
    store.finish("r1", status=RunStatus.done, output="ok")
    killed = store.force_kill("r1", reason="manual kill requested")
    assert killed.status == RunStatus.killed
    assert store.get("r1").status == RunStatus.killed


def test_finish_cannot_downgrade_a_killed_run_back_to_done():
    store = RunStore()
    store.create("r1", "fulfillment")
    store.force_kill("r1", reason="manual kill requested")
    store.finish("r1", status=RunStatus.done, output="late success")
    assert store.get("r1").status == RunStatus.killed


def test_instance_for_unknown_run_is_none():
    store = RunStore()
    assert store.instance_for("nope") is None


def test_pause_records_status_thread_id_and_reason():
    store = RunStore()
    store.create("r1", "refund", task_text="refund $5000 on ORD-1")
    result = store.pause("r1", thread_id="t-1", reason="needs a human", output="PENDING_HUMAN_APPROVAL [thread_id=t-1]: needs a human")
    assert result.status == RunStatus.paused
    assert store.pause_context("r1") == ("t-1", "refund $5000 on ORD-1", "needs a human")


def test_paused_run_ids_lists_only_currently_paused_runs():
    store = RunStore()
    store.create("r1", "refund")
    store.create("r2", "fulfillment")
    store.pause("r1", thread_id="t-1", reason="x", output="...")
    store.finish("r2", status=RunStatus.done, output="ok")
    assert store.paused_run_ids() == ["r1"]


def test_resuming_removes_a_run_from_paused_run_ids():
    store = RunStore()
    store.create("r1", "refund")
    store.pause("r1", thread_id="t-1", reason="x", output="...")
    assert store.paused_run_ids() == ["r1"]
    store.finish("r1", status=RunStatus.done, output="approved and completed")
    assert store.paused_run_ids() == []


def test_a_manual_kill_racing_a_pause_stays_killed():
    """Same 'killed is sticky' guard finish() has -- a kill that lands before
    a late pause write must not un-kill the run."""
    store = RunStore()
    store.create("r1", "refund")
    store.force_kill("r1", reason="manual kill requested")
    store.pause("r1", thread_id="t-1", reason="x", output="...")
    assert store.get("r1").status == RunStatus.killed


def test_pause_context_is_none_for_a_run_that_never_paused():
    store = RunStore()
    store.create("r1", "fulfillment")
    assert store.pause_context("r1") is None
