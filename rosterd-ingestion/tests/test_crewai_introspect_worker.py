"""crewai_introspect_worker.py's own dispatch logic, pinned with lightweight
stand-in objects -- same convention test_introspect_worker.py already uses
for the LangChain worker (FakeCompiled/FakeBuilder, not a real LangGraph
object), for the same reason: these tests exercise this file's OWN
mapping decisions, not whether CrewAI itself behaves as documented. That
part was verified live against a real installed crewai (1.15.23) crew
before this file was written -- see discovery.py's module docstring and
docs/DISCOVERY.md for what that confirmed. rosterd-ingestion does not
carry crewai as a dependency (a heavy one -- litellm, chromadb, etc. --
for a repo that merely needs to introspect one), so nothing here imports
it either.
"""
from __future__ import annotations

from types import SimpleNamespace

import crewai_introspect_worker as worker


def _fake_agent(role: str) -> SimpleNamespace:
    return SimpleNamespace(role=role)


def _fake_task(*, agent=None, name=None) -> SimpleNamespace:
    return SimpleNamespace(agent=agent, name=name)


class TestTaskName:
    def test_an_explicit_name_wins(self):
        task = _fake_task(agent=_fake_agent("Researcher"), name="custom_name")
        assert worker._task_name(task, {}) == "custom_name"

    def test_falls_back_to_the_agents_role(self):
        task = _fake_task(agent=_fake_agent("Researcher"))
        assert worker._task_name(task, {}) == "Researcher"

    def test_a_second_task_for_the_same_agent_gets_a_numeric_suffix(self):
        role_counts: dict[str, int] = {}
        researcher = _fake_agent("Researcher")
        first = worker._task_name(_fake_task(agent=researcher), role_counts)
        second = worker._task_name(_fake_task(agent=researcher), role_counts)
        assert first == "Researcher"
        assert second == "Researcher_2"

    def test_no_agent_at_all_falls_back_to_unassigned(self):
        task = _fake_task(agent=None)
        assert worker._task_name(task, {}) == "unassigned"


class TestFirstSentence:
    def test_takes_only_the_first_sentence(self):
        text = "Look up the price for a SKU. Then report it back."
        assert worker._first_sentence(text) == "Look up the price for a SKU."

    def test_collapses_internal_whitespace_and_newlines(self):
        text = "Look up the\nprice   for a SKU."
        assert worker._first_sentence(text) == "Look up the price for a SKU."

    def test_empty_text_is_empty(self):
        assert worker._first_sentence("") == ""
        assert worker._first_sentence(None) == ""
