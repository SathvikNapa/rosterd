"""Tools available to the support agents.

Plain `@tool` callables so the graph compiles and runs with no API key and no
network. The kernel (Person 1) is what actually gates these at dispatch time —
the bodies here are deliberately dumb stand-ins.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def classify_request(text: str) -> str:
    """Classify an inbound support request into a queue name."""
    lowered = text.lower()
    if "refund" in lowered or "charged" in lowered:
        return "refund"
    if "password" in lowered or "login" in lowered:
        return "account"
    return "general"


@tool
def issue_refund(invoice_id: str, amount_usd: float) -> str:
    """Issue a refund against an invoice, in USD."""
    return f"refunded ${amount_usd:.2f} against {invoice_id}"


@tool
def create_ticket(subject: str, body: str) -> str:
    """Open a human-review ticket."""
    return f"ticket opened: {subject}"
