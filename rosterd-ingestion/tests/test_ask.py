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

    def test_id_or_node_needs_every_keyword_part_not_just_one(self):
        """Found live: "order_intake" used to fire on the word "order" ALONE
        (one half of a two-word id), which is common enough in ordinary
        e-commerce text to fire for nearly any request. Confirmed live:
        "reserve 200 units of SKU-DEMO for a bulk order" scored
        order_intake HIGHER than fulfillment, whose own tool
        (reserve_inventory) is the actually-correct signal -- purely
        because "order" incidentally appears in both the request and this
        agent's own id."""
        order_intake = agent("order_intake", "order_intake", "Classifies orders")
        fulfillment = agent("fulfillment", "fulfillment", "Reserves inventory", ["reserve_inventory"])

        ranked = score_agents("reserve 200 units of SKU-DEMO for a bulk order", [order_intake, fulfillment])
        assert ranked[0].agent.id == "fulfillment"
        assert not any(m.startswith("id:") for m in next(r for r in ranked if r.agent.id == "order_intake").matched)

        # The full id -- both "order" AND "intake" present -- must still match.
        ranked_full = score_agents("route this to the order intake agent", [order_intake, fulfillment])
        assert any(m.startswith("id:") for m in ranked_full[0].matched)
        assert ranked_full[0].agent.id == "order_intake"

    def test_one_matched_word_is_not_double_counted_across_categories(self):
        """Found live: a single overlapping word ("payment") used to score
        separately in EVERY category it happened to appear in -- tool, id,
        node, AND purpose -- stacking weights for one piece of evidence,
        not four. Confirmed live: "payment" alone scored an agent named
        (and tooled, and purposed) around "payment" a 9.0, drowning out
        the agent the request actually needed. Each word should count
        once, at its strongest category."""
        payment = agent("payment", "payment", "Charges payment for an order", ["charge_payment"])
        ranked = score_agents("a payment issue came up", [payment])
        # tool:charge_payment claims "payment" first; id/node/purpose all
        # also nominally contain "payment" but must not add on top of it.
        assert ranked[0].score == pytest.approx(3.0)  # _WEIGHT_TOOL, once

    def test_a_zero_score_tie_prefers_the_agent_with_fewer_tools(self):
        """Found live: a genuine no-signal request (deliberately
        keyword-sparse fraud/social-engineering language) used to break a
        tie alphabetically by agent id, which is an arbitrary, meaningless
        default. A node with NO tools of its own is structurally a
        router/classifier rather than an action-taking specialist, and is
        the more sensible default when nothing about the request matched
        any agent at all."""
        router = agent("order_intake", "order_intake", "Classifies incoming orders")
        specialist_a = agent("catalog", "catalog", "Checks stock", ["check_stock"])
        specialist_b = agent("payment", "payment", "Charges payment", ["charge_payment"])

        ranked = score_agents("zzz completely unrelated nonsense qqq", [specialist_a, specialist_b, router])
        assert ranked[0].score == 0.0
        assert ranked[0].agent.id == "order_intake"


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
