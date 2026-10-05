"""Probe a public LangGraph repo and print the wiring rosterd discovers.

Usage:
    .venv/bin/python scripts/probe.py <repo_url> [<repo_url> ...]
    .venv/bin/python scripts/probe.py --suite        # a set of well-known repos

A single `discover()` call does the whole job: it tries `import` mode
first and falls back to `static` on its own -- both when the repo's own
dependencies aren't installed here (the normal case for someone else's
repo) and when it fails for a reason no dependency install fixes (a
required config.yaml, an API key), confirmed against a real one,
bytedance/deer-flow. `result.mode` (not a loop variable here) says which
one actually produced the answer, so a result is never mistaken for
something it is not.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from application import discovery  # noqa: E402
from adapters.filesystem.repo import fetch_repo  # noqa: E402
from config import get_settings  # noqa: E402
from domain.errors import IngestError  # noqa: E402

SUITE = [
    "https://github.com/langchain-ai/react-agent",
    "https://github.com/langchain-ai/memory-agent",
    "https://github.com/langchain-ai/retrieval-agent-template",
    "https://github.com/langchain-ai/data-enrichment",
    "https://github.com/langchain-ai/new-langgraph-project",
]


def probe_one(url: str) -> dict:
    """Clone a repo and try to discover its wiring. Never raises."""
    summary = {"url": url, "error": None, "result": None}
    settings = get_settings()
    try:
        with fetch_repo(url, settings) as fetched:
            summary["result"] = discovery.discover(fetched.path, settings)
    except IngestError as exc:
        summary["error"] = f"{type(exc).__name__}: {exc.message}"
    except Exception as exc:  # noqa: BLE001 - a probe must not crash the run
        summary["error"] = f"{type(exc).__name__}: {exc}"
    return summary


def render(summary: dict) -> None:
    name = summary["url"].rstrip("/").split("/")[-1]
    print(f"\n{'=' * 72}\n{name}  ({summary['url']})\n{'=' * 72}")

    result = summary["result"]
    if result is None:
        print(f"  FAILED: {summary['error']}")
        return

    print(f"  mode: {result.mode}   located via: {result.graph_attr}")

    print(f"\n  agents ({len(result.agent_nodes)}):")
    for node_name in result.agent_nodes:
        node = result.nodes[node_name]
        tools = ", ".join(node.tools) if node.tools else "-"
        print(f"    {node_name:<28} tools: {tools}")
        if node.purpose:
            print(f"    {'':<28} {node.purpose[:64]}")

    print(f"\n  wiring ({len(result.graph.edges)} edges):")
    for edge in result.graph.edges:
        arrow = "==>" if edge.condition else "-->"
        label = f"  [{edge.condition}]" if edge.condition else ""
        print(f"    {edge.source:<24} {arrow} {edge.target}{label}")

    if result.warnings:
        print("\n  warnings:")
        for warning in result.warnings:
            print(f"    - {warning}")


def main() -> int:
    args = sys.argv[1:]
    urls = SUITE if not args or args[0] == "--suite" else args
    summaries = [probe_one(url) for url in urls]
    for summary in summaries:
        render(summary)

    ok = sum(1 for s in summaries if s["result"] is not None)
    print(f"\n{'=' * 72}\n{ok}/{len(summaries)} repos discovered\n{'=' * 72}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
