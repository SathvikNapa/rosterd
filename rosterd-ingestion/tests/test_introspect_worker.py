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
