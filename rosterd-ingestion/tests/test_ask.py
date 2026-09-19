"""Unit tests for the /ask/parse parser internals."""
from __future__ import annotations

import pytest

from ask import derive_criteria, detect_priority, parse_ask, score_agents, tokenize
from ingestion import AgentConstraints, AgentManifestEntry, Confidence
from shared import Priority


def agent(agent_id, node, purpose="", tools=(), **constraints) -> AgentManifestEntry:
    return AgentManifestEntry(
        id=agent_id,
        node=node,
        purpose=purpose,
        tools=list(tools),
        direct_assignable=True,
        constraints=AgentConstraints(**constraints),
    )


REFUND = agent("refund", "refund_node", "Issues refunds, $100 cap", ["issue_refund"], max_refund_usd=100)
TRIAGE = agent("triage", "triage_node", "Classifies incoming requests", ["classify_request"])
ROSTER = [REFUND, TRIAGE]


class TestTokenize:
    def test_strips_stopwords_and_punctuation(self):
        assert tokenize("Please issue a refund for the customer!") == [
            "issue", "refund", "customer",
        ]

    def test_handles_empty_input(self):
        assert tokenize("") == []
        assert tokenize("the a of") == []


class TestScoring:
    def test_a_tool_name_is_the_strongest_signal(self):
        ranked = score_agents("issue_refund now", ROSTER)
        assert ranked[0].agent.id == "refund"
        assert any(m.startswith("tool:") for m in ranked[0].matched)

    def test_purpose_words_route_when_no_tool_matches(self):
        ranked = score_agents("classifies things", ROSTER)
        assert ranked[0].agent.id == "triage"

    def test_ranking_is_stable_when_scores_tie(self):
        """Equal scores must not make the answer depend on dict ordering."""
        first = score_agents("something unrelated entirely", ROSTER)
        second = score_agents("something unrelated entirely", list(reversed(ROSTER)))
        assert [s.agent.id for s in first] == [s.agent.id for s in second]


class TestConfidence:
    def test_a_clear_tool_match_is_high(self):
        _, _, confidence = parse_ask("please issue_refund for this invoice", ROSTER)
        assert confidence is Confidence.high

    def test_no_signal_is_low(self):
        _, _, confidence = parse_ask("zzzz qqqq", ROSTER)
        assert confidence is Confidence.low

    def test_a_tie_is_not_high(self):
        """Two agents matching equally well is not a confident routing decision."""
        twin_a = agent("alpha", "alpha_node", "handles billing", ["shared_tool"])
        twin_b = agent("beta", "beta_node", "handles billing", ["shared_tool"])
        _, _, confidence = parse_ask("shared_tool billing", [twin_a, twin_b])
        assert confidence is not Confidence.high


class TestPriority:
    @pytest.mark.parametrize("text", ["refund this urgently", "ASAP please", "critical issue"])
    def test_high_priority_words(self, text):
        assert detect_priority(text) is Priority.high

    def test_low_priority_words(self):
        assert detect_priority("handle this whenever") is Priority.low

    def test_defaults_to_medium(self):
        assert detect_priority("issue a refund") is Priority.medium


class TestCriteria:
    def test_contract_limits_become_criteria(self):
        criteria = derive_criteria("refund it", REFUND)
        assert "Refund amount ≤ $100, per contract" in criteria

    def test_requires_prior_node_becomes_a_criterion(self):
        gated = agent("esc", "escalation_node", requires_prior_node="refund_node")
        assert any("Must run after refund_node" in c for c in derive_criteria("go", gated))

    def test_unknown_constraint_keys_still_surface(self):
        custom = agent("x", "x_node", daily_budget_usd=2500)
        assert any("Daily budget usd: 2500" in c for c in derive_criteria("go", custom))

    def test_explicit_requirements_are_extracted(self):
        criteria = derive_criteria("Refund it. Must respond within 2 minutes.", REFUND)
        assert any("within 2 minutes" in c for c in criteria)

    def test_criteria_are_deduplicated(self):
        criteria = derive_criteria("Must do it. Must do it.", REFUND)
        assert len(criteria) == len(set(criteria))

    def test_entry_only_via_is_surfaced(self):
        gated = AgentManifestEntry(
            id="esc", node="escalation_node", purpose="p", entry_only_via=["refund_node"]
        )
        assert any("Reachable only via refund_node" in c for c in derive_criteria("go", gated))


class TestParsedTask:
    def test_title_is_the_first_sentence(self):
        _, task, _ = parse_ask("Refund invoice 4482. It was a duplicate charge.", ROSTER)
        assert task.title == "Refund invoice 4482"

    def test_long_titles_are_truncated(self):
        _, task, _ = parse_ask("refund " * 40, ROSTER)
        assert len(task.title) <= 72
        assert task.title.endswith("...")

    def test_description_keeps_the_whole_request(self):
        _, task, _ = parse_ask("Refund it.  Now.", ROSTER)
        assert task.description == "Refund it. Now."

    def test_empty_text_does_not_crash(self):
        agent_id, task, confidence = parse_ask("", ROSTER)
        assert task.title == "Untitled task"
        assert confidence is Confidence.low
        assert agent_id in {"refund", "triage"}
