"""_introspect_worker.py: module loading (file path vs dotted import) and the
factory-with-config fallback.

The worker normally runs as a subprocess (see discovery.py's module
docstring for why) -- but `_load_module`/`_load_attr` are plain functions,
importable and callable directly in-process for a fast, deterministic unit
test. The real dotted-import-against-a-freshly-installed-venv path was
verified live against a real repo (bytedance/deer-flow); these tests pin the
dispatch logic itself using stdlib modules as a cheap, always-available
stand-in.
"""
from __future__ import annotations

import sys

import pytest

import _introspect_worker as worker


class TestLoadModule:
    def test_dotted_prefix_does_a_real_import(self):
        """`dotted:` is stripped and the rest is importlib.import_module'd --
        confirmed against a real installed package (json, stdlib) so this
        doesn't just prove the string got stripped, it proves the whole
        dispatch does a genuine import."""
        module = worker._load_module("dotted:json")
        assert module is sys.modules["json"]

    def test_dotted_prefix_on_a_nested_module_path(self):
        module = worker._load_module("dotted:os.path")
        assert module is sys.modules["os.path"]

    def test_no_dotted_prefix_loads_as_a_file_path(self, tmp_path):
        target = tmp_path / "plain_module.py"
        target.write_text("value = 42\n")
        module = worker._load_module(str(target))
        assert module.value == 42

    def test_unresolvable_dotted_import_raises(self):
        with pytest.raises(ModuleNotFoundError):
            worker._load_module("dotted:this_module_does_not_exist_anywhere")


class TestLoadAttr:
    def test_plain_attribute_with_get_graph_is_returned_as_is(self, tmp_path):
        target = tmp_path / "has_graph.py"
        target.write_text(
            "class FakeCompiled:\n"
            "    def get_graph(self):\n"
            "        return 'the graph'\n"
            "graph = FakeCompiled()\n"
        )
        result = worker._load_attr(str(target), "graph")
        assert result.get_graph() == "the graph"

    def test_zero_arg_factory_is_called(self, tmp_path):
        target = tmp_path / "factory.py"
        target.write_text(
            "class FakeCompiled:\n"
            "    def get_graph(self):\n"
            "        return 'built'\n"
            "def make_graph():\n"
            "    return FakeCompiled()\n"
        )
        result = worker._load_attr(str(target), "make_graph")
        assert result.get_graph() == "built"

    def test_factory_requiring_a_config_arg_falls_back_to_empty_dict(self, tmp_path):
        """A real LangGraph Server deployment commonly exports a factory
        taking a RunnableConfig, e.g. bytedance/deer-flow's
        make_lead_agent(config) -- confirmed live against that real repo.
        RunnableConfig is an unvalidated TypedDict, so an empty dict is a
        reasonable stand-in purely for introspection."""
        target = tmp_path / "configured_factory.py"
        target.write_text(
            "class FakeCompiled:\n"
            "    def get_graph(self):\n"
            "        return 'built with config'\n"
            "def make_graph(config):\n"
            "    assert config == {}\n"
            "    return FakeCompiled()\n"
        )
        result = worker._load_attr(str(target), "make_graph")
        assert result.get_graph() == "built with config"

    def test_a_builder_with_compile_but_no_get_graph_is_compiled(self, tmp_path):
        target = tmp_path / "builder.py"
        target.write_text(
            "class FakeCompiled:\n"
            "    def get_graph(self):\n"
            "        return 'compiled'\n"
            "class FakeBuilder:\n"
            "    def compile(self):\n"
            "        return FakeCompiled()\n"
            "builder = FakeBuilder()\n"
        )
        result = worker._load_attr(str(target), "builder")
        assert result.get_graph() == "compiled"

    def test_missing_attribute_raises(self, tmp_path):
        target = tmp_path / "empty.py"
        target.write_text("other = 1\n")
        with pytest.raises(AttributeError):
            worker._load_attr(str(target), "graph")

    def test_something_that_is_neither_a_graph_nor_a_buildable_thing_raises(self, tmp_path):
        target = tmp_path / "wrong.py"
        target.write_text("graph = 42\n")
        with pytest.raises(TypeError):
            worker._load_attr(str(target), "graph")


class TestRenameMap:
    """A plain LangChain Runnable's `.get_graph()` (LCEL -- no LangGraph at
    all) names its nodes very differently from a LangGraph StateGraph's --
    confirmed empirically against a real chain, not assumed. `_rename_map`
    is what makes the rest of this file (and discovery.py downstream, which
    never has to know the difference) see a clean, readable graph either
    way."""

    def _lcel_chain(self):
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.runnables import RunnableLambda

        def fake_llm(payload):
            return "a fake response"

        return ChatPromptTemplate.from_template("Answer about {sku}") | RunnableLambda(fake_llm) | StrOutputParser()

    def test_lcel_nodes_are_renamed_to_something_readable_not_a_hash(self):
        drawable = self._lcel_chain().get_graph()
        rename = worker._rename_map(drawable)
        names = {v for v in rename.values() if v is not None}
        # The wrapped function's own name survives; the prompt/parser steps
        # get their class name. None of these is the opaque hex key.
        assert names == {"ChatPromptTemplate", "fake_llm", "StrOutputParser"}
        for node_id, name in rename.items():
            if name is not None:
                assert name != node_id

    def test_lcel_input_and_output_schema_markers_map_to_none(self):
        """The two auto-generated Pydantic schema nodes at the ends of any
        plain Runnable's graph (never present on a LangGraph StateGraph --
        confirmed empirically) -- noise, not a step anyone wrote."""
        drawable = self._lcel_chain().get_graph()
        rename = worker._rename_map(drawable)
        dropped = [node_id for node_id, name in rename.items() if name is None]
        assert len(dropped) == 2
        for node_id in dropped:
            assert isinstance(drawable.nodes[node_id].data, type)

    def test_langgraph_style_nodes_are_unaffected_name_already_equals_key(self):
        """Every LangGraph StateGraph node's own `.name` already equals its
        dict key (confirmed against the real demo-agent graph) -- so this
        is a documented no-op for the case that was already working,
        pinned here with a minimal stand-in graph rather than importing
        the full demo-agent service into this test."""
        from langgraph.graph import StateGraph

        def noop_node(state):
            return state

        builder = StateGraph(dict)
        builder.add_node("order_intake", noop_node)
        builder.set_entry_point("order_intake")
        drawable = builder.compile().get_graph()

        rename = worker._rename_map(drawable)
        assert rename["order_intake"] == "order_intake"

    def test_duplicate_names_get_a_numeric_suffix_not_silently_overwritten(self):
        """Two sibling steps that would otherwise both report the same
        `.name` (no wrapping function to disambiguate either) must not
        collide into one entry in the caller's `nodes` dict."""
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.runnables import RunnableParallel

        chain = RunnableParallel(
            a=ChatPromptTemplate.from_template("{x}"),
            b=ChatPromptTemplate.from_template("{x}"),
        )
        drawable = chain.get_graph()
        rename = worker._rename_map(drawable)
        names = [v for v in rename.values() if v is not None]
        assert len(names) == len(set(names)), f"expected no duplicates, got {names}"
