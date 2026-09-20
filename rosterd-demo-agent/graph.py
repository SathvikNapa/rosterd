"""The 5-agent LangGraph: order_intake -> fulfillment | refund_exception,
plus catalog and payment as their own directly-dispatchable agents.

catalog and payment were added for the Black Friday / peak-load scenario
(high concurrency across order intake AND catalog lookups AND payment
charges, not just fulfillment) without touching the original 3-node
order_intake -> fulfillment | refund_exception flow or its routing at all:
both are wired into route_entry exactly like the original three (any of
the five agents can be dispatched directly) and end at END, same as
fulfillment and refund_exception -- purely additive, so every existing
test and every existing constraint stays exactly what it was.

Kept deliberately literal (string node names, literal routing map) because
rosterd's ingestion reads this file statically.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from brain import get_brain
from refund_node import refund_exception_node
from shared import GraphEdge, GraphSpec
from toolrun import attempt_tool
from tools import charge_payment, check_stock, reserve_inventory


class AgentState(TypedDict, total=False):
    entry_node: str
    text: str
    context: dict
    mode: str | None
    order_class: str
    tool_calls: Annotated[list, operator.add]
    outputs: Annotated[list, operator.add]
    next_node: str | None


def route_entry(state: AgentState) -> str:
    return state["entry_node"]


def order_intake_node(state: AgentState) -> dict:
    order_class = get_brain(state.get("mode")).classify(state["text"], state.get("context") or {})
    target = "refund_exception" if order_class == "fraud_flagged" else "fulfillment"
    return {
        "order_class": order_class,
        "next_node": target,
        "outputs": [f"Order classified as {order_class}; routing to {target}."],
    }


def route_after_intake(state: AgentState) -> str:
    return "fraud_flagged" if state["order_class"] == "fraud_flagged" else "not_flagged"


def fulfillment_node(state: AgentState) -> dict:
    res = get_brain(state.get("mode")).propose_reservation(state["text"], state.get("context") or {})
    call = attempt_tool(reserve_inventory, {"sku": res.sku, "qty": res.qty})
    return {
        "tool_calls": [call],
        "outputs": [f"Fulfillment attempted to reserve {res.qty} x {res.sku}."],
    }


def catalog_node(state: AgentState) -> dict:
    """Read-only stock lookup -- no interrupt() gate, no approval needed,
    same as fulfillment. Deliberately no LLM-only path required: under
    peak (Black Friday) load this is the highest-volume, lowest-risk
    agent, so it stays cheap and fast in scripted mode."""
    check = get_brain(state.get("mode")).propose_stock_check(state["text"], state.get("context") or {})
    call = attempt_tool(check_stock, {"sku": check.sku})
    return {
        "tool_calls": [call],
        "outputs": [f"Catalog checked stock for {check.sku}."],
    }


def payment_node(state: AgentState) -> dict:
    """Charges payment for an order. The cap lives on
    tools.ChargePaymentArgs (Field(le=2000)), same pattern as fulfillment's
    max_qty and refund_exception's max_refund_usd -- a real business
    number the kernel can enforce, not checked here."""
    charge = get_brain(state.get("mode")).propose_payment(state["text"], state.get("context") or {})
    call = attempt_tool(charge_payment, {"order_id": charge.order_id, "amount": charge.amount})
    return {
        "tool_calls": [call],
        "outputs": [f"Payment attempted to charge ${charge.amount:.2f} on {charge.order_id}."],
    }


def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("order_intake", order_intake_node)
    builder.add_node("fulfillment", fulfillment_node)
    builder.add_node("refund_exception", refund_exception_node)
    builder.add_node("catalog", catalog_node)
    builder.add_node("payment", payment_node)

    # Any of the five agents can be dispatched directly (see EntryNode in
    # demo_agent.py). catalog and payment are additive: they don't sit on
    # order_intake's own routing (route_after_intake is unchanged, still
    # only fulfillment | refund_exception), so the original 3-node flow's
    # behavior and tests are untouched.
    builder.add_conditional_edges(
        START,
        route_entry,
        {
            "order_intake": "order_intake",
            "fulfillment": "fulfillment",
            "refund_exception": "refund_exception",
            "catalog": "catalog",
            "payment": "payment",
        },
    )
    builder.add_conditional_edges(
        "order_intake",
        route_after_intake,
        {"not_flagged": "fulfillment", "fraud_flagged": "refund_exception"},
    )
    builder.add_edge("fulfillment", END)
    builder.add_edge("refund_exception", END)
    builder.add_edge("catalog", END)
    builder.add_edge("payment", END)
    return builder.compile(checkpointer=checkpointer)


# Flip to True if ingestion's GraphSpec includes __start__/__end__ pseudo-nodes.
INCLUDE_TERMINALS = False


def extract_graph_spec(compiled) -> GraphSpec:
    """Read the structure back out of the compiled graph (so /graph can't drift from the code)."""
    g = compiled.get_graph()
    hidden = set() if INCLUDE_TERMINALS else {"__start__", "__end__"}
    nodes = [n for n in g.nodes if n not in hidden]
    edges = [
        GraphEdge(source=e.source, target=e.target, condition=e.data if e.conditional else None)
        for e in g.edges
        if e.source not in hidden and e.target not in hidden
    ]
    edges.sort(key=lambda e: (e.source, e.target))
    return GraphSpec(nodes=nodes, edges=edges)


# Module-level compiled graph, uncheckpointed, purely so rosterd's ingestion
# service (and the LangGraph CLI convention it follows first) has something
# to import: `langgraph.json` points `order_fulfillment` at `graph.py:graph`.
# main.py builds its OWN graph with a MemorySaver checkpointer for serving
# /invoke -- this one is for static/import-time discovery only, never served.
graph = build_graph()
