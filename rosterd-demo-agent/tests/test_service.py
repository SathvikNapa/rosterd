import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

import brain
import scenarios
from main import app
from tools import ChargePaymentArgs, IssueRefundArgs, ReserveInventoryArgs

client = TestClient(app)


def invoke(body):
    r = client.post("/invoke", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---- contract shape -------------------------------------------------------
def test_tool_schema_limits_are_literal_and_readable():
    assert ReserveInventoryArgs.model_json_schema()["properties"]["qty"]["maximum"] == 50
    assert IssueRefundArgs.model_json_schema()["properties"]["amount"]["maximum"] == 100
    assert ChargePaymentArgs.model_json_schema()["properties"]["amount"]["maximum"] == 2000


def test_graph_endpoint():
    g = client.get("/graph").json()
    assert set(g["nodes"]) == {"order_intake", "fulfillment", "refund_exception", "catalog", "payment"}
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


# ---- catalog + payment (added for the Black Friday / peak-load scenario) -
def test_catalog_checks_stock_directly_no_approval_needed():
    out = invoke(scenarios.CATALOG_CHECK)
    assert out["tool_calls"][0]["tool"] == "check_stock"
    assert out["tool_calls"][0]["args"] == {"sku": "SKU-SNEAKER-9"}
    assert not out["tool_calls"][0]["result"].startswith("REJECTED")


def test_payment_charges_within_cap():
    out = invoke(scenarios.PAYMENT_CHARGE)
    assert out["tool_calls"][0]["tool"] == "charge_payment"
    assert out["tool_calls"][0]["args"] == {"order_id": "ORD-5001", "amount": 89.0}
    assert not out["tool_calls"][0]["result"].startswith("REJECTED")


def test_payment_over_cap_is_recorded_intact_and_rejected_by_schema():
    """Same pattern as test_misdirection_args_are_intact_and_exceed_schema_limit:
    the agent's own proposed amount is recorded as-is, never clamped, so the
    kernel can evaluate its inferred rule against exactly what was attempted."""
    out = invoke(scenarios.PAYMENT_OVER_CAP)
    call = out["tool_calls"][0]
    assert call["tool"] == "charge_payment"
    assert call["args"]["amount"] == 5000.0
    assert call["args"]["amount"] > ChargePaymentArgs.model_json_schema()["properties"]["amount"]["maximum"]
    assert call["result"].startswith("REJECTED")


def test_catalog_and_payment_are_directly_dispatchable_like_the_original_three():
    for entry_node in ("order_intake", "fulfillment", "refund_exception", "catalog", "payment"):
        r = client.post(
            "/invoke",
            json={"entry_node": entry_node, "input": {"text": "Reserve 1 unit of SKU-DEMO"}},
        )
        assert r.status_code == 200, (entry_node, r.text)


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


def test_llm_mode_falls_back_when_the_model_proposes_zero_on_text_with_a_real_amount():
    """Found live against a real Grok call: text that plainly states a
    dollar figure sometimes still comes back amount=0 -- a structurally
    valid but wrong answer, not a call failure (that's the test above).
    $0 is far more likely an extraction miss than a genuine answer, so
    this falls back to the same regex extraction the scripted brain uses
    rather than silently showing "$0.00" on a run that obviously proposed
    refunding something."""
    def zero(schema, system, user):
        return brain.RefundProposal(order_id="ORD-7001", amount=0, reason="")

    b = brain.LLMBrain(structured_call=zero)
    proposal = b.propose_refund("Customer is asking for a $180 refund on order ORD-7001.", {})
    assert proposal.order_id == "ORD-7001"
    assert proposal.amount == 100.0  # capped at POLICY_CAP, same as the scripted brain would


def test_llm_mode_does_not_override_a_genuine_nonzero_proposal():
    """The zero-fallback above must not kick in for an ordinary answer --
    only exactly 0 is treated as suspect."""
    def real_answer(schema, system, user):
        return brain.RefundProposal(order_id="ORD-9", amount=42, reason="within policy")

    b = brain.LLMBrain(structured_call=real_answer)
    assert b.propose_refund("refund $42 on ORD-9", {}).amount == 42


def test_llm_mode_end_to_end_without_key_still_works():
    # Pop every provider key _llm_structured_call checks, not just
    # Anthropic's -- otherwise this test silently stops exercising the
    # no-key fallback path the moment GROK_API_KEY/XAI_API_KEY is set in
    # whatever environment runs it.
    os.environ.pop("GROK_API_KEY", None)
    os.environ.pop("XAI_API_KEY", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    body = {**scenarios.MISDIRECTION, "input": {**scenarios.MISDIRECTION["input"],
            "context": {"order_class": "standard", "mode": "llm"}}}
    assert invoke(body)["tool_calls"][0]["args"]["amount"] == 480.0
