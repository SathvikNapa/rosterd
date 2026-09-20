"""/ask/parse — plain language in, a proposed task out.

Deliberately deterministic, not an LLM call. Three reasons: it runs with no API
key and no network, it is unit-testable, and a demo cannot fail live because a
provider is slow. `score_agents` is the seam — swap its body for a model call
and the rest of the endpoint is unchanged.

The output is a *proposal*. The frontend shows it on a card for a human to
accept, which is the same confirm-gate idea as the manifest itself: inference
proposes, a person decides.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingestion import AgentManifestEntry, Confidence, ParsedTask
from shared import Priority

#: Words that carry no routing signal.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could", "do",
    "does", "for", "from", "get", "had", "has", "have", "i", "if", "in", "is",
    "it", "its", "me", "my", "need", "needs", "of", "on", "or", "our", "out",
    "please", "so", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "to", "up", "us", "was", "we", "were", "what",
    "when", "which", "who", "will", "with", "would", "you", "your",
}

_HIGH_PRIORITY = {"urgent", "urgently", "asap", "immediately", "critical", "emergency", "now", "escalated"}
_LOW_PRIORITY = {"whenever", "eventually", "someday", "backlog", "low-priority", "nonurgent"}

#: How much each kind of match is worth. A tool name is the strongest signal —
#: it names a concrete capability rather than a vague description.
_WEIGHT_TOOL = 3.0
_WEIGHT_ID = 3.0
_WEIGHT_NODE = 2.0
_WEIGHT_PURPOSE = 1.0


@dataclass
class AgentScore:
    agent: AgentManifestEntry
    score: float = 0.0
    matched: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stopwords removed."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _keywords(value: str) -> set[str]:
    """Split an identifier or phrase into comparable tokens."""
    return {w for w in re.split(r"[^a-z0-9]+", value.lower()) if w and len(w) > 1}


def score_agents(text: str, agents: list[AgentManifestEntry]) -> list[AgentScore]:
    """Rank agents by how well they match the request.

    This is the swap point for an LLM-backed parser.

    Two real bugs, found live against a real report ("Act 4/5/6 doesn't
    work from demo") that both come down to the same root cause -- generic
    English words accidentally carrying more weight than they should:

    1. `id`/`node` used to fire on ANY single keyword overlap (e.g. just
       the word "order" matching one half of "order_intake"). "order" is
       common enough in ordinary e-commerce text that it fired for nearly
       any request, regardless of relevance -- confirmed live: "reserve
       200 units of SKU-DEMO for a bulk order" scored order_intake
       *higher* than fulfillment (whose own tool, reserve_inventory,
       is the actually-correct signal), purely because the word "order"
       incidentally appears in both the request and this agent's own id.
       Fixed: id/node now require ALL of their keyword parts present, not
       any one -- matching "order_intake" needs both "order" and "intake"
       in the text, not just whichever one happens to be common. Tool
       names are NOT changed the same way (see below) -- a real regression
       check confirmed why: "reserve 3 units of SKU-EARBUDS-BLK" only
       contains "reserve", not "inventory", so requiring the whole tool
       name would have broken that legitimate match instead of fixing a
       false one. Identifiers (id/node) and natural-language tool/purpose
       matching behave differently on purpose.

    2. A single matched word used to count separately in EVERY category it
       happened to appear in (tool AND id AND node AND purpose), stacking
       weights for what is really one piece of evidence, not four.
       Confirmed live: "payment" alone (from "swapping payment cards")
       scored the payment agent 9.0 -- tool:charge_payment (3) + id:payment
       (3) + node:payment (2) + purpose:payment (1) -- for one overlapping
       word, while order_intake, the agent the request actually needed,
       scored 0. Fixed: each token is credited to the FIRST (strongest)
       category it matches and never re-counted lower down the list.
    """
    tokens = set(tokenize(text))
    scored: list[AgentScore] = []

    for agent in agents:
        result = AgentScore(agent=agent)
        claimed: set[str] = set()

        for tool in agent.tools:
            hits = (_keywords(tool) & tokens) - claimed
            if hits:
                result.score += _WEIGHT_TOOL * len(hits)
                result.matched.append(f"tool:{tool}")
                claimed |= hits

        id_kw = _keywords(agent.id)
        if id_kw and id_kw <= tokens and not id_kw <= claimed:
            result.score += _WEIGHT_ID
            result.matched.append(f"id:{agent.id}")
            claimed |= id_kw

        node_kw = _keywords(re.sub(r"_(node|agent)$", "", agent.node))
        if node_kw and node_kw <= tokens and not node_kw <= claimed:
            result.score += _WEIGHT_NODE
            result.matched.append(f"node:{agent.node}")
            claimed |= node_kw

        purpose_hits = (_keywords(agent.purpose) & tokens) - claimed
        if purpose_hits:
            result.score += _WEIGHT_PURPOSE * len(purpose_hits)
            result.matched.append(f"purpose:{'/'.join(sorted(purpose_hits))}")
            claimed |= purpose_hits

        scored.append(result)

    # A tie at zero is not a routing decision at all -- nothing about the
    # request matched anything. Rather than an arbitrary alphabetical pick
    # (confirmed live as a real failure mode: "catalog" only won a real
    # fraud-review request because it sorts before "order_intake" and
    # "payment"), prefer whichever tied agent has the FEWEST tools of its
    # own: a node with no tools is structurally a router/classifier rather
    # than an action-taking specialist, and is the more sensible default
    # for a request with no clear signal for any specific action.
    return sorted(scored, key=lambda s: (-s.score, len(s.agent.tools), s.agent.id))


def _confidence(ranked: list[AgentScore]) -> Confidence:
    """How much to trust the top match.

    High needs both a real signal and a clear margin over the runner-up —
    a strong score that two agents tie on is not a confident routing decision.
    """
    if not ranked or ranked[0].score == 0:
        return Confidence.low

    top = ranked[0].score
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0

    if top >= _WEIGHT_TOOL and top >= runner_up * 2:
        return Confidence.high
    if top >= _WEIGHT_NODE:
        return Confidence.medium
    return Confidence.low


def detect_priority(text: str) -> Priority:
    tokens = set(tokenize(text))
    if tokens & _HIGH_PRIORITY:
        return Priority.high
    if tokens & _LOW_PRIORITY:
        return Priority.low
    return Priority.medium


def _title(text: str) -> str:
    """First sentence, trimmed to something that fits on a card."""
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return "Untitled task"
    first = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    # A card title reads better without terminal punctuation.
    first = first.rstrip(".!?").strip() or cleaned
    if len(first) > 72:
        first = first[:69].rsplit(" ", 1)[0] + "..."
    return first


def derive_criteria(text: str, agent: AgentManifestEntry) -> list[str]:
    """Expectation criteria for the task card.

    Two sources: what the agent's contract already guarantees, and any explicit
    requirement in the request. Surfacing the contract here means the person
    approving the card sees the limits that will actually be enforced, rather
    than having to remember them.
    """
    criteria: list[str] = []

    constraints = agent.constraints.model_dump(exclude_none=True)
    if (cap := constraints.pop("max_refund_usd", None)) is not None:
        criteria.append(f"Refund amount ≤ ${cap:g}, per contract")
    if (prior := constraints.pop("requires_prior_node", None)) is not None:
        criteria.append(f"Must run after {prior}, per contract")
    for key, value in sorted(constraints.items()):
        criteria.append(f"{key.replace('_', ' ').capitalize()}: {value}, per contract")

    if agent.entry_only_via:
        criteria.append("Reachable only via " + ", ".join(agent.entry_only_via))

    # Explicit requirements the requester wrote down.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text.strip()):
        sentence = " ".join(sentence.split())
        if not sentence:
            continue
        if re.search(r"\b(must|should|within|ensure|make sure|no more than|at least|by)\b",
                     sentence, re.IGNORECASE):
            criteria.append(sentence.rstrip("."))

    # Preserve order, drop duplicates.
    return list(dict.fromkeys(criteria))


def parse_ask(text: str, agents: list[AgentManifestEntry]) -> tuple[str, ParsedTask, Confidence]:
    """Turn a request into (agent_id, proposed task, confidence).

    `agents` should already be filtered to the directly assignable ones — an
    agent the contract says can only be reached via another must not be
    proposable as a direct assignee.
    """
    ranked = score_agents(text, agents)
    best = ranked[0]
    confidence = _confidence(ranked)

    task = ParsedTask(
        title=_title(text),
        description=" ".join(text.strip().split()),
        priority=detect_priority(text),
        expectation_criteria=derive_criteria(text, best.agent),
    )
    return best.agent.id, task, confidence
