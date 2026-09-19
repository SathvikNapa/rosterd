import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

import brain
import scenarios
from main import app
from tools import IssueRefundArgs, ReserveInventoryArgs

client = TestClient(app)


def invoke(body):
    r = client.post("/invoke", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---- contract shape -------------------------------------------------------
def test_tool_schema_limits_are_literal_and_readable():
    assert ReserveInventoryArgs.model_json_schema()["properties"]["qty"]["maximum"] == 50
    assert IssueRefundArgs.model_json_schema()["properties"]["amount"]["maximum"] == 100


def test_graph_endpoint():
    g = client.get("/graph").json()
    assert set(g["nodes"]) == {"order_intake", "fulfillment", "refund_exception"}
    edges = {(e["source"], e["target"], e["condition"]) for e in g["edges"]}
    assert ("order_intake", "fulfillment", "not_flagged") in edges
    assert ("order_intake", "refund_exception", "fraud_flagged") in edges
    assert ("fulfillment", "__end__", None) not in edges  # terminals hidden


def test_health():
    assert client.get("/health").json()["status"] == "ok"


# ---- routing + tool calls -------------------------------------------------
def test_standard_order_routes_to_fulfillment_with_tool_call():
    out = invoke(scenarios.STANDARD_ORDER)
    assert out["next_node"] == "fulfillment"
    assert out["tool_calls"][0]["tool"] == "reserve_inventory"
    assert out["tool_calls"][0]["args"] == {"sku": "SKU-LAMP-01", "qty": 3}


def test_flash_sale_direct_fulfillment():
    out = invoke(scenarios.FLASH_SALE_BURST)
    assert out["tool_calls"][0]["args"] == {"sku": "SKU-SNEAKER-9", "qty": 5}


def test_fraud_order_pauses_then_resumes():
    out = invoke(scenarios.FRAUD_ORDER)
    assert out["output"].startswith("PENDING_HUMAN_APPROVAL")
    tid = out["output"].split("thread_id=")[1].split("]")[0]
    done = client.post("/resume", json={"thread_id": tid, "approved": True}).json()
    assert done["tool_calls"][0]["tool"] == "issue_refund"
    denied_out = invoke(scenarios.FRAUD_ORDER)
    tid2 = denied_out["output"].split("thread_id=")[1].split("]")[0]
    denied = client.post("/resume", json={"thread_id": tid2, "approved": False}).json()
    assert denied["tool_calls"] == []


def test_resume_unknown_thread_404():
    assert client.post("/resume", json={"thread_id": "nope", "approved": True}).status_code == 404


# ---- the misdirection demo ------------------------------------------------
def test_normal_refund_within_policy():
    out = invoke(scenarios.NORMAL_REFUND)
    assert out["tool_calls"][0]["args"] == {"order_id": "ORD-1002", "amount": 45.0}
    assert not out["tool_calls"][0]["result"].startswith("REJECTED")


def test_misdirection_args_are_intact_and_exceed_schema_limit():
    out = invoke(scenarios.MISDIRECTION)
    call = out["tool_calls"][0]
    assert call["tool"] == "issue_refund"
    assert call["args"]["amount"] == 480.0  # NOT clamped, NOT stripped
    assert call["args"]["amount"] > IssueRefundArgs.model_json_schema()["properties"]["amount"]["maximum"]


def test_soft_policy_caps_when_no_override():
    body = {"entry_node": "refund_exception",
            "input": {"text": "Please refund $250 on ORD-9", "context": {"order_class": "standard"}}}
    assert invoke(body)["tool_calls"][0]["args"]["amount"] == 100.0


# ---- LLM mode -------------------------------------------------------------
def test_llm_mode_uses_injected_model():
    def fooled(schema, system, user):
        assert schema is brain.RefundProposal
        return brain.RefundProposal(order_id="ORD-4417", amount=480, reason="manager said so")

    b = brain.LLMBrain(structured_call=fooled)
    assert b.propose_refund("x", {}).amount == 480


def test_llm_mode_falls_back_when_call_fails():
    def boom(*a):
        raise RuntimeError("no network")

    b = brain.LLMBrain(structured_call=boom)
    assert b.classify("chargeback on card", {}) == "fraud_flagged"
    assert b.propose_refund("refund $30 on ORD-1", {}).amount == 30


def test_llm_mode_end_to_end_without_key_still_works():
    os.environ.pop("ANTHROPIC_API_KEY", None)
    body = {**scenarios.MISDIRECTION, "input": {**scenarios.MISDIRECTION["input"],
            "context": {"order_class": "standard", "mode": "llm"}}}
    assert invoke(body)["tool_calls"][0]["args"]["amount"] == 480.0
