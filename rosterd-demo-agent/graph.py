"""The 3-agent LangGraph: order_intake -> fulfillment | refund_exception.

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
from tools import reserve_inventory


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


def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("order_intake", order_intake_node)
    builder.add_node("fulfillment", fulfillment_node)
    builder.add_node("refund_exception", refund_exception_node)

    # Any of the three agents can be dispatched directly (see EntryNode in demo_agent.py).
    builder.add_conditional_edges(
        START,
        route_entry,
        {"order_intake": "order_intake", "fulfillment": "fulfillment", "refund_exception": "refund_exception"},
    )
    builder.add_conditional_edges(
        "order_intake",
        route_after_intake,
        {"not_flagged": "fulfillment", "fraud_flagged": "refund_exception"},
    )
    builder.add_edge("fulfillment", END)
    builder.add_edge("refund_exception", END)
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
