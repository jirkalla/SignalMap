"""Unit tests for provider-specific search-query extraction (docs/TASKS_SEARCH_QUERIES.md SQ-T2).

No real API calls — these test the pure mapping functions against
duck-typed stand-ins for the SDK response objects, verified 2026-09-10
against the actual installed SDKs (see docs/TASKS_SEARCH_QUERIES.md design
decisions 4-5) so the shapes here aren't guessed.
"""

from types import SimpleNamespace

from app.adapters.anthropic import _map_search_queries as anthropic_map_search_queries
from app.adapters.google import _map_search_queries as google_map_search_queries


def test_google_returns_web_search_queries_in_order():
    candidate = SimpleNamespace(
        grounding_metadata=SimpleNamespace(web_search_queries=["first query", "second query"])
    )
    assert google_map_search_queries(candidate) == ["first query", "second query"]


def test_google_handles_missing_candidate():
    assert google_map_search_queries(None) == []


def test_google_handles_missing_grounding_metadata():
    assert google_map_search_queries(SimpleNamespace()) == []


def test_google_handles_grounding_metadata_present_but_no_search_queries():
    candidate = SimpleNamespace(grounding_metadata=SimpleNamespace(web_search_queries=None))
    assert google_map_search_queries(candidate) == []


def test_anthropic_returns_queries_from_web_search_blocks_in_order():
    content = [
        SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "first"}),
        SimpleNamespace(type="text", text="..."),
        SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "second"}),
    ]
    assert anthropic_map_search_queries(content) == ["first", "second"]


def test_anthropic_handles_empty_content():
    assert anthropic_map_search_queries([]) == []


def test_anthropic_skips_non_web_search_tool_use_blocks():
    content = [SimpleNamespace(type="server_tool_use", name="code_execution", input={"code": "x"})]
    assert anthropic_map_search_queries(content) == []


def test_anthropic_handles_web_search_block_missing_query_key():
    content = [SimpleNamespace(type="server_tool_use", name="web_search", input={})]
    assert anthropic_map_search_queries(content) == []
