"""Refund/Exception node.

The interrupt() call is the signal ingestion reads to mark this node
direct_assignable: false. It guards what a schema can't express: refunds on
fraud-flagged or high-value orders need a human, whatever the amount.
The amount limit itself is NOT checked here -- it lives in
tools.IssueRefundArgs (Field(le=100)) and is enforced by the kernel.
"""

from langgraph.types import interrupt

from brain import get_brain
from toolrun import attempt_tool
from tools import issue_refund


def refund_exception_node(state: dict) -> dict:
    ctx = state.get("context") or {}
    order_class = state.get("order_class") or ctx.get("order_class") or "standard"

    if order_class in ("fraud_flagged", "high_value"):
        decision = interrupt(
            {
                "reason": "refund on flagged/high-value order needs human approval",
                "order_class": order_class,
                "request": state["text"][:300],
            }
        )
        if not (decision or {}).get("approved"):
            return {"outputs": ["Refund denied by human reviewer."]}

    proposal = get_brain(state.get("mode")).propose_refund(state["text"], ctx)
    call = attempt_tool(issue_refund, {"order_id": proposal.order_id, "amount": proposal.amount})
    return {
        "tool_calls": [call],
        "outputs": [f"Refund agent attempted refund of ${proposal.amount:.2f} on {proposal.order_id} ({proposal.reason})."],
    }
