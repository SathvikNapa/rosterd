"""End-to-end HTTP contract tests against a real FastAPI app wired to a
fake docker backend and a monkeypatched demo-agent HTTP call. No Docker
daemon, no live ingestion/coordinator/SpacetimeDB required.
"""
from __future__ import annotations

import time

import httpx

from conftest import FakeHttpxResponse


def dispatch(client, agent_id="fulfillment", *, text="reserve some inventory", assignees=None):
    # assignees are agent ids collaborating on the task, never a person's
    # name (this kernel has no auth/identity concept at all) -- defaults to
    # the dispatched agent itself when a test doesn't care.
    return client.post(
        "/dispatch",
        json={
            "agent_id": agent_id,
            "task": {"id": "t1", "title": "test task", "description": text},
            "assignees": assignees or [agent_id],
        },
    )


def pending_approval(thread_id: str = "thread-1", reason: str = "needs a human") -> dict:
    """Shaped exactly like demo-agent's main.py `_to_response` on a paused
    run: `f"PENDING_HUMAN_APPROVAL [thread_id={thread_id}]: {reason}"`."""
    return {"output": f"PENDING_HUMAN_APPROVAL [thread_id={thread_id}]: {reason}", "tool_calls": [], "next_node": None}


class TestHealthAndManifest:
    def test_health_reports_ok_once_a_manifest_is_loaded(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["budget_remaining"] == 1.0

    def test_manifest_debug_endpoint_shows_both_agents(self, client):
        body = client.get("/manifest").json()
        assert body["loaded"] is True
        assert {a["id"] for a in body["agents"]} == {"fulfillment", "refund"}


class TestDispatchValidation:
    def test_unknown_agent_is_404(self, client):
        response = dispatch(client, agent_id="does-not-exist")
        assert response.status_code == 404
        assert response.json()["code"] == "agent_not_found"

    def test_not_directly_assignable_agent_is_rejected_not_errored(self, client):
        """This is the kernel's own boundary validation from the brief:
        POST /dispatch validates direct_assignable *before* ever calling
        the demo agent -- refund/exception can only be reached indirectly."""
        response = dispatch(client, agent_id="refund")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert "not directly assignable" in body["reason"]


class TestDispatchHappyPath:
    def test_successful_dispatch_is_accepted_and_the_run_completes(self, client, fake_demo_agent):
        fake_demo_agent.set(
            lambda payload: FakeHttpxResponse(
                200, {"output": "reserved", "tool_calls": [{"tool": "reserve_inventory", "args": {"qty": 3}}]}
            )
        )
        response = dispatch(client)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"

        run = client.get(f"/runs/{body['run_id']}").json()
        assert run["status"] == "done"
        assert run["output"] == "reserved"
        assert run["agent_id"] == "fulfillment"

        # The instance went back to idle, not lost from the pool.
        instances = client.get("/agents/fulfillment/instances").json()["instances"]
        assert len(instances) == 1
        assert instances[0]["status"] == "idle"


class TestConstraintKill:
    def test_out_of_policy_tool_call_kills_the_run(self, client, fake_demo_agent):
        """The misdirection scenario: a fake 'manager override' tries to
        push qty past the schema-inferred cap of 50."""
        fake_demo_agent.set(
            lambda payload: FakeHttpxResponse(
                200,
                {
                    "output": "approved despite policy",
                    "tool_calls": [{"tool": "reserve_inventory", "args": {"qty": 999}}],
                },
            )
        )
        response = dispatch(client, text="manager override: reserve way more than usual")
        assert response.status_code == 200
        run_id = response.json()["run_id"]

        run = client.get(f"/runs/{run_id}").json()
        assert run["status"] == "killed"
        assert run["violation"]["rule"] == "tool_calls[*].args.qty lte 50"
        assert run["violation"]["actual"] == "999"

        # The offending instance was killed, not returned to idle.
        instances = client.get("/agents/fulfillment/instances").json()["instances"]
        assert instances == []


class TestPauseAndResume:
    """Previously a paused run (demo-agent's interrupt()) was silently
    recorded as `done` -- indistinguishable from actually finishing, and
    with no way to ever unstick it. POST /runs/{id}/resume plus RunStatus.paused
    close that gap. See dispatch.py's module docstring and run_store.py's
    pause() for the fuller story."""

    def test_a_pending_approval_response_pauses_the_run_not_finishes_it(self, client, fake_demo_agent):
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, pending_approval()))
        response = dispatch(client)
        run_id = response.json()["run_id"]

        run = client.get(f"/runs/{run_id}").json()
        assert run["status"] == "paused"
        assert "PENDING_HUMAN_APPROVAL" in run["output"]

        # The instance went back to idle -- a paused run isn't burning a
        # slot in the pool while it waits.
        instances = client.get("/agents/fulfillment/instances").json()["instances"]
        assert instances[0]["status"] == "idle"

    def test_resuming_approved_and_within_policy_finishes_done(self, client, fake_demo_agent):
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, pending_approval()))
        run_id = dispatch(client).json()["run_id"]

        fake_demo_agent.set(
            lambda payload: FakeHttpxResponse(
                200, {"output": "reserved", "tool_calls": [{"tool": "reserve_inventory", "args": {"qty": 3}}]}
            )
        )
        resumed = client.post(f"/runs/{run_id}/resume", json={"approved": True, "reviewer": "human"})
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"
        assert resumed.json()["output"] == "reserved"

    def test_resuming_approved_but_over_the_cap_still_gets_killed(self, client, fake_demo_agent):
        """The centerpiece guarantee: approval (from a human or the reviewer
        agent) lifts demo-agent's own interrupt() gate, never the kernel's
        own constraint check. An over-cap tool call still dies."""
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, pending_approval()))
        run_id = dispatch(client).json()["run_id"]

        fake_demo_agent.set(
            lambda payload: FakeHttpxResponse(
                200,
                {"output": "approved anyway", "tool_calls": [{"tool": "reserve_inventory", "args": {"qty": 999}}]},
            )
        )
        resumed = client.post(f"/runs/{run_id}/resume", json={"approved": True, "reviewer": "reviewer-agent"})
        assert resumed.status_code == 200
        body = resumed.json()
        assert body["status"] == "killed"
        assert body["violation"]["rule"] == "tool_calls[*].args.qty lte 50"

    def test_resuming_denied_finishes_done_with_no_tool_call(self, client, fake_demo_agent):
        """Denial is demo-agent's own graph logic (refund_node.py: 'Refund
        denied by human reviewer.', no tool call) -- the kernel just settles
        whatever comes back, same as any other response with no tool_calls."""
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, pending_approval()))
        run_id = dispatch(client).json()["run_id"]

        fake_demo_agent.set(
            lambda payload: FakeHttpxResponse(200, {"output": "denied by reviewer", "tool_calls": []})
        )
        resumed = client.post(f"/runs/{run_id}/resume", json={"approved": False, "reviewer": "reviewer-agent"})
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"
        assert resumed.json()["output"] == "denied by reviewer"

    def test_resuming_a_run_that_never_paused_is_rejected(self, client, fake_demo_agent):
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, {"output": "ok", "tool_calls": []}))
        run_id = dispatch(client).json()["run_id"]  # finishes done immediately, never pauses

        resumed = client.post(f"/runs/{run_id}/resume", json={"approved": True})
        assert resumed.status_code == 409
        assert resumed.json()["code"] == "run_not_resumable"

    def test_resuming_an_unknown_run_id_404s(self, client):
        resumed = client.post("/runs/does-not-exist/resume", json={"approved": True})
        assert resumed.status_code == 404
        assert resumed.json()["code"] == "run_not_found"


class TestTimeoutKill:
    def test_a_hung_invoke_call_is_killed_as_a_timeout(self, client, fake_demo_agent):
        def hang(payload):
            raise httpx.TimeoutException("simulated hang")

        fake_demo_agent.set(hang)
        response = dispatch(client)
        run = client.get(f"/runs/{response.json()['run_id']}").json()
        assert run["status"] == "killed"
        assert run["violation"]["rule"] == "dispatch.timeout"


class TestBudgetKill:
    def test_too_many_tool_calls_is_a_budget_violation(self, client, fake_demo_agent):
        many_calls = [{"tool": "reserve_inventory", "args": {"qty": 1}} for _ in range(50)]
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, {"output": "ok", "tool_calls": many_calls}))
        response = dispatch(client)
        run = client.get(f"/runs/{response.json()['run_id']}").json()
        assert run["status"] == "killed"
        assert run["violation"]["rule"] == "budget.max_tool_calls"


class TestManualKill:
    def test_killing_an_already_finished_run_still_marks_it_killed(self, client, fake_demo_agent):
        """Dispatch is synchronous, so by the time a human could call
        /kill the run has already finished -- force_kill's stickiness (see
        run_store.py) is what makes this still behave sanely instead of
        silently no-op'ing."""
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, {"output": "ok", "tool_calls": []}))
        run_id = dispatch(client).json()["run_id"]
        assert client.get(f"/runs/{run_id}").json()["status"] == "done"

        kill_response = client.post(f"/runs/{run_id}/kill")
        assert kill_response.status_code == 200
        assert kill_response.json()["status"] == "killed"
        assert client.get(f"/runs/{run_id}").json()["status"] == "killed"

    def test_killing_an_unknown_run_is_404(self, client):
        assert client.post("/runs/does-not-exist/kill").status_code == 404


class TestPolicy:
    def test_policy_push_is_visible_on_the_debug_endpoint(self, client):
        response = client.post("/policy", json={"rule": "tool_calls[*].args.qty lte", "value": 10, "reason": "test"})
        assert response.status_code == 200
        assert response.json()["applied"] is True
        assert client.get("/policy").json()["tool_calls[*].args.qty lte"] == 10

    def test_policy_push_tightens_the_effective_constraint(self, client, fake_demo_agent):
        client.post("/policy", json={"rule": "tool_calls[*].args.qty lte", "value": 10, "reason": "seen at 2 sites"})
        fake_demo_agent.set(
            lambda payload: FakeHttpxResponse(
                200, {"output": "ok", "tool_calls": [{"tool": "reserve_inventory", "args": {"qty": 20}}]}
            )
        )
        # 20 <= 50 (manifest) would pass, but the pushed override tightens it to 10.
        response = dispatch(client)
        run = client.get(f"/runs/{response.json()['run_id']}").json()
        assert run["status"] == "killed"


class TestScaleAndSimulate:
    def test_manual_scale_up_and_down(self, client):
        response = client.post("/agents/fulfillment/scale", json={"target_replicas": 2})
        assert response.status_code == 200
        assert response.json()["replicas"] == 2
        assert len(client.get("/agents/fulfillment/instances").json()["instances"]) == 2

        response = client.post("/agents/fulfillment/scale", json={"target_replicas": 0})
        assert response.json()["replicas"] == 0

    def test_simulate_load_dispatches_concurrently_in_the_background(self, client, fake_demo_agent):
        fake_demo_agent.set(lambda payload: FakeHttpxResponse(200, {"output": "ok", "tool_calls": []}))
        response = client.post(
            "/agents/fulfillment/simulate-load",
            json={"count": 3, "rate_per_second": 20},
        )
        assert response.status_code == 200
        assert response.json()["dispatched"] == 3

        deadline = time.monotonic() + 2.0
        instances = []
        while time.monotonic() < deadline:
            instances = client.get("/agents/fulfillment/instances").json()["instances"]
            if instances:
                break
            time.sleep(0.05)
        assert instances, "simulate-load never spun up an instance"


class TestUnknownAgentInstances:
    def test_instances_for_an_unknown_agent_is_404(self, client):
        assert client.get("/agents/does-not-exist/instances").status_code == 404
