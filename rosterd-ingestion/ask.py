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
    """
    tokens = set(tokenize(text))
    scored: list[AgentScore] = []

    for agent in agents:
        result = AgentScore(agent=agent)

        for tool in agent.tools:
            hits = _keywords(tool) & tokens
            if hits:
                result.score += _WEIGHT_TOOL * len(hits)
                result.matched.append(f"tool:{tool}")

        if _keywords(agent.id) & tokens:
            result.score += _WEIGHT_ID
            result.matched.append(f"id:{agent.id}")

        node_words = _keywords(re.sub(r"_(node|agent)$", "", agent.node))
        if node_words & tokens:
            result.score += _WEIGHT_NODE
            result.matched.append(f"node:{agent.node}")

        purpose_hits = _keywords(agent.purpose) & tokens
        if purpose_hits:
            result.score += _WEIGHT_PURPOSE * len(purpose_hits)
            result.matched.append(f"purpose:{'/'.join(sorted(purpose_hits))}")

        scored.append(result)

    return sorted(scored, key=lambda s: (-s.score, s.agent.id))


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
