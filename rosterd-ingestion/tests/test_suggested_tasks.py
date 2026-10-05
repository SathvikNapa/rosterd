"""domain/suggested_tasks.py: deterministic "try this" task suggestions
derived from an agent's own discovered wiring. No LLM, no invented
domain facts -- these tests pin exactly what each verb-pattern template
produces, and that the honest fallbacks (humanized tool name, purpose,
empty list) kick in when nothing more specific applies.
"""
from __future__ import annotations

from domain.ingestion import AgentConstraints
from domain.suggested_tasks import suggest_tasks


def _suggest(
    *,
    purpose: str = "",
    tools: list[str] | None = None,
    direct_assignable: bool = True,
    entry_only_via: list[str] | None = None,
    **constraint_kwargs,
) -> list[str]:
    return suggest_tasks(
        purpose=purpose,
        tools=tools or [],
        direct_assignable=direct_assignable,
        entry_only_via=entry_only_via or [],
        constraints=AgentConstraints(**constraint_kwargs),
    )


class TestRefund:
    def test_with_a_cap_gives_an_in_policy_and_an_over_cap_example(self):
        result = _suggest(tools=["issue_refund"], max_refund_usd=100)
        assert result == [
            "Refund $50 on order ORD-DEMO.",
            "Refund $150 on order ORD-DEMO -- over the $100 cap, to see it caught.",
        ]

    def test_with_no_cap_gives_one_generic_in_policy_example(self):
        result = _suggest(tools=["issue_refund"])
        assert result == ["Refund $45 on order ORD-DEMO."]

    def test_matches_on_purpose_even_with_no_refund_shaped_tool_name(self):
        result = _suggest(tools=["do_the_thing"], purpose="Issues a refund for a damaged item")
        assert result[0].startswith("Refund $")


class TestReserveInventory:
    def test_with_a_qty_cap(self):
        result = _suggest(tools=["reserve_inventory"], max_qty=50)
        assert result == [
            "Reserve 15 units of SKU-DEMO.",
            "Reserve 75 units of SKU-DEMO -- over the 50-unit cap, to see it caught.",
        ]

    def test_with_no_cap(self):
        assert _suggest(tools=["reserve_inventory"]) == ["Reserve 10 units of SKU-DEMO."]


class TestChargePayment:
    def test_with_a_charge_cap(self):
        result = _suggest(tools=["charge_payment"], max_charge_usd=2000)
        assert result == [
            "Charge $1000 on order ORD-DEMO.",
            "Charge $3000 on order ORD-DEMO -- over the $2000 cap, to see it caught.",
        ]


class TestOtherVerbShapes:
    def test_check_stock_needs_no_number(self):
        assert _suggest(tools=["check_stock"]) == ["Check the status of SKU-DEMO."]

    def test_cancel_order(self):
        assert _suggest(tools=["cancel_order"]) == ["Cancel order ORD-DEMO."]

    def test_send_notification(self):
        assert _suggest(tools=["send_email"]) == ["Send a notification about order ORD-DEMO."]


class TestFallbacks:
    def test_an_unrecognized_tool_name_falls_back_to_its_own_humanized_name(self):
        """No verb pattern matches -- the one honest fallback every tool
        name supports, not a guessed scenario for an unknown domain."""
        assert _suggest(tools=["frobnicate_the_widget"]) == ["Ask it to frobnicate the widget."]

    def test_no_tools_but_a_purpose_falls_back_to_the_purpose_text(self):
        result = _suggest(tools=[], purpose="Classifies incoming support tickets by urgency")
        assert result == ["Ask it something in line with its purpose: Classifies incoming support tickets by urgency"]

    def test_no_tools_and_no_purpose_is_an_empty_list_not_a_vacuous_guess(self):
        assert _suggest(tools=[], purpose="") == []


class TestGatedAgent:
    def test_an_indirectly_reached_agent_gets_its_suggestions_annotated(self):
        result = _suggest(
            tools=["issue_refund"],
            direct_assignable=False,
            entry_only_via=["order_intake"],
            max_refund_usd=100,
        )
        assert all("reached indirectly, normally via order_intake" in s for s in result)
        assert len(result) == 2

    def test_multiple_entry_paths_are_joined_with_or(self):
        result = _suggest(
            tools=["issue_refund"],
            direct_assignable=False,
            entry_only_via=["order_intake", "support_triage"],
        )
        assert "via order_intake or support_triage" in result[0]

    def test_a_direct_agent_with_entry_only_via_set_is_never_annotated(self):
        """entry_only_via is only meaningful when the agent is actually
        gated -- a direct_assignable=True agent's suggestions must not
        carry a misleading "reached indirectly" note."""
        result = _suggest(tools=["issue_refund"], direct_assignable=True, entry_only_via=["order_intake"])
        assert all("reached indirectly" not in s for s in result)


def test_never_exceeds_the_per_agent_cap():
    result = _suggest(tools=["issue_refund"], max_refund_usd=100)
    assert len(result) <= 2
