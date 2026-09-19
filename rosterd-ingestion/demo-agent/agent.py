"""A three-agent customer-support graph: triage -> refund -> escalation.

This is the fixture rosterd ingests. It matches the system shown in the product
mockup so the Contracts and Roster screens have real data behind them.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from tools import classify_request, create_ticket, issue_refund


class SupportState(TypedDict, total=False):
    """Conversation state threaded through the support graph."""

    request: str
    category: Annotated[str, "queue chosen by triage"]
    invoice_id: str
    refund_amount_usd: float
    resolution: str
    disputed: bool


def triage_node(state: SupportState) -> SupportState:
    """Classifies incoming requests."""
    category = classify_request.invoke({"text": state.get("request", "")})
    return {**state, "category": category}


def refund_node(state: SupportState) -> SupportState:
    """Issues refunds, $100 cap."""
    resolution = issue_refund.invoke(
        {
            "invoice_id": state.get("invoice_id", "unknown"),
            "amount_usd": state.get("refund_amount_usd", 0.0),
        }
    )
    return {**state, "resolution": resolution}


def escalation_node(state: SupportState) -> SupportState:
    """Hands off to a human."""
    resolution = create_ticket.invoke(
        {
            "subject": f"Escalation for {state.get('invoice_id', 'unknown')}",
            "body": state.get("request", ""),
        }
    )
    return {**state, "resolution": resolution}


def route_after_triage(state: SupportState) -> str:
    """Send refund requests to the refund agent, everything else to a human."""
    return "refund_node" if state.get("category") == "refund" else "escalation_node"


def route_after_refund(state: SupportState) -> str:
    """Escalate automatically when the customer disputes the outcome."""
    return "escalation_node" if state.get("disputed") else END


def build_graph() -> StateGraph:
    builder = StateGraph(SupportState)
    builder.add_node("triage_node", triage_node)
    builder.add_node("refund_node", refund_node)
    builder.add_node("escalation_node", escalation_node)

    builder.add_edge(START, "triage_node")
    builder.add_conditional_edges(
        "triage_node",
        route_after_triage,
        {"refund_node": "refund_node", "escalation_node": "escalation_node"},
    )
    builder.add_conditional_edges(
        "refund_node",
        route_after_refund,
        {"escalation_node": "escalation_node", END: END},
    )
    builder.add_edge("escalation_node", END)
    return builder


#: The compiled graph rosterd discovers. `langgraph.json` points here.
graph = build_graph().compile()
