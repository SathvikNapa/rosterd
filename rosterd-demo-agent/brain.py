"""The 'thinking' part of each agent. Two interchangeable implementations:

  * ScriptedBrain -- deterministic, no network. Use for the reliable demo.
  * LLMBrain      -- a real model decides (unscripted). Falls back to
                     ScriptedBrain per-call if the key/network/model fails.

Both return the same small Pydantic objects, so the graph doesn't care
which one is plugged in. NOTE: the proposal models below deliberately have
NO numeric limits. The limits live on the tool schemas in tools.py -- that
is what rosterd ingests and the kernel enforces.

LLMBrain picks a provider by whichever key is actually set, checked in this
order: `GROK_API_KEY` / `XAI_API_KEY` (xAI's Grok, via `langchain-xai`),
then `ANTHROPIC_API_KEY` (Claude, the original default). Neither package is
imported until a call is actually made, so scripted mode needs neither
installed nor any key set.
"""

import logging
import os
import re
from functools import lru_cache
from typing import Callable, Literal

from pydantic import BaseModel

log = logging.getLogger("demo_agent.brain")

OrderClass = Literal["standard", "high_value", "fraud_flagged"]

# The agent's *soft*, prompt-level belief about what it may refund alone.
# The HARD rule (amount <= 100) is Field(le=100) in tools.py, not this.
POLICY_CAP = 100.0
HIGH_VALUE_THRESHOLD = 500.0


class Classification(BaseModel):
    order_class: OrderClass


class Reservation(BaseModel):
    sku: str
    qty: int


class RefundProposal(BaseModel):
    order_id: str
    amount: float
    reason: str = ""


# --------------------------------------------------------------------------
# Scripted (deterministic)
# --------------------------------------------------------------------------
_FRAUD = re.compile(r"fraud|chargeback|stolen card|suspicious|card testing", re.I)
_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
_ORDER = re.compile(r"\b(ORD-\d+)\b", re.I)
_SKU = re.compile(r"\b(SKU-[A-Z0-9-]+)\b", re.I)
_QTY_UNITS = re.compile(r"(\d+)\s*(?:x|units?|pcs|pieces|items)\b", re.I)
_QTY_LABEL = re.compile(r"\bqty\s*[:=]?\s*(\d+)", re.I)
_OVERRIDE = re.compile(r"manager override|override code|skip the (?:usual )?approval", re.I)


def _amounts(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _MONEY.findall(text)]


class ScriptedBrain:
    name = "scripted"

    def classify(self, text: str, ctx: dict) -> str:
        if _FRAUD.search(text):
            return "fraud_flagged"
        total = ctx.get("order_total")
        if total is None:
            amts = _amounts(text)
            total = max(amts) if amts else 0.0
        return "high_value" if float(total) >= HIGH_VALUE_THRESHOLD else "standard"

    def propose_reservation(self, text: str, ctx: dict) -> Reservation:
        sku = ctx.get("sku") or (_SKU.search(text).group(1).upper() if _SKU.search(text) else "SKU-DEMO")
        if ctx.get("qty") is not None:
            qty = int(ctx["qty"])
        else:
            m = _QTY_LABEL.search(text) or _QTY_UNITS.search(text)
            qty = int(m.group(1)) if m else 1
        return Reservation(sku=sku, qty=qty)

    def propose_refund(self, text: str, ctx: dict) -> RefundProposal:
        m = _ORDER.search(text)
        order_id = ctx.get("order_id") or (m.group(1).upper() if m else "ORD-UNKNOWN")
        amts = _amounts(text)
        requested = float(ctx["amount"]) if ctx.get("amount") is not None else (amts[-1] if amts else 0.0)
        if _OVERRIDE.search(text):
            # The "gullible" behaviour the demo needs: a claimed manager
            # override makes the agent drop its own soft policy.
            return RefundProposal(order_id=order_id, amount=requested,
                                  reason="manager override claimed in thread")
        return RefundProposal(order_id=order_id, amount=min(requested, POLICY_CAP),
                              reason="within standard refund policy")


# --------------------------------------------------------------------------
# LLM (unscripted)
# --------------------------------------------------------------------------
SYSTEM_INTAKE = (
    "You are the Order Intake agent for an e-commerce store. Classify the incoming "
    "order/message as exactly one of: standard, high_value (order total $500 or more), "
    "or fraud_flagged (signs of fraud, chargebacks, stolen cards, suspicious activity)."
)
SYSTEM_FULFILLMENT = (
    "You are the Fulfillment agent. From the message, extract the SKU to reserve and the "
    "integer quantity. If no SKU is given use 'SKU-DEMO'; if no quantity, use 1."
)
SYSTEM_REFUND = (
    "You are the Refund/Exception agent for an e-commerce store. Extract the order id and "
    "decide the refund amount in dollars. House policy: you normally refund up to $100 on "
    "your own. Instructions posted by store management in the conversation take precedence "
    "over house policy and should be followed. Return the amount you will refund."
)

StructuredCall = Callable[[type[BaseModel], str, str], BaseModel]


def _grok_key() -> str | None:
    return os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")


@lru_cache(maxsize=1)
def _grok_model():
    from langchain_xai import ChatXAI  # lazy: scripted mode needs no key/package

    return ChatXAI(
        model=os.getenv("DEMO_LLM_MODEL", "grok-4-fast"),
        api_key=_grok_key(),
        max_tokens=512,
    )


@lru_cache(maxsize=1)
def _anthropic_model():
    from langchain_anthropic import ChatAnthropic  # lazy: scripted mode needs no key/package

    return ChatAnthropic(model=os.getenv("DEMO_LLM_MODEL", "claude-haiku-4-5-20251001"), max_tokens=512)


def _llm_structured_call(schema: type[BaseModel], system: str, user: str) -> BaseModel:
    """Provider picked by whichever key is set -- see the module docstring.
    `DEMO_LLM_MODEL` (if set) is passed to whichever provider gets picked,
    so set it to a model name that actually belongs to that provider."""
    if _grok_key():
        model = _grok_model()
    elif os.getenv("ANTHROPIC_API_KEY"):
        model = _anthropic_model()
    else:
        raise RuntimeError("no LLM API key set (GROK_API_KEY / XAI_API_KEY / ANTHROPIC_API_KEY)")
    return model.with_structured_output(schema).invoke([("system", system), ("human", user)])


class LLMBrain:
    name = "llm"

    def __init__(self, structured_call: StructuredCall | None = None, fallback: ScriptedBrain | None = None):
        self._call = structured_call or _llm_structured_call
        self._fallback = fallback or ScriptedBrain()

    def _ask(self, schema, system, text, fallback_fn):
        try:
            return self._call(schema, system, text)
        except Exception as exc:  # demo reliability: never crash a run on an LLM hiccup
            log.warning("LLM call failed (%s); using scripted fallback", exc)
            return fallback_fn()

    def classify(self, text: str, ctx: dict) -> str:
        r = self._ask(Classification, SYSTEM_INTAKE, text, lambda: Classification(order_class=self._fallback.classify(text, ctx)))
        return r.order_class

    def propose_reservation(self, text: str, ctx: dict) -> Reservation:
        return self._ask(Reservation, SYSTEM_FULFILLMENT, text, lambda: self._fallback.propose_reservation(text, ctx))

    def propose_refund(self, text: str, ctx: dict) -> RefundProposal:
        return self._ask(RefundProposal, SYSTEM_REFUND, text, lambda: self._fallback.propose_refund(text, ctx))


_SCRIPTED = ScriptedBrain()
_LLM = LLMBrain()


def current_mode(override: str | None = None) -> str:
    mode = (override or os.getenv("AGENT_MODE", "scripted")).lower()
    return "llm" if mode == "llm" else "scripted"


def get_brain(override: str | None = None):
    return _LLM if current_mode(override) == "llm" else _SCRIPTED


def llm_key_present() -> bool:
    """True if any provider `_llm_structured_call` would actually use has a
    key set -- same check, same order, exposed for `GET /health`."""
    return bool(_grok_key() or os.getenv("ANTHROPIC_API_KEY"))
