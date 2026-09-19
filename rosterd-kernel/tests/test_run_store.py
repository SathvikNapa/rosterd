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
