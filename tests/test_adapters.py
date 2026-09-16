"""Unit tests for provider-specific search-query extraction (docs/TASKS_SEARCH_QUERIES.md SQ-T2).

No real API calls — these test the pure mapping functions against the
serialized response shape they actually receive in production:
`response.model_dump(mode="json")`, i.e. plain dicts, not SDK objects
(docs/TASKS_GEMINI_CITATIONS.md design decision 5). The shapes here were
verified 2026-09-16 against real stored `raw_responses.raw_payload` rows, and
the SDK field names behind them 2026-09-10 against the installed SDKs (see
docs/TASKS_SEARCH_QUERIES.md design decisions 4-5), so nothing here is guessed.
"""

from app.adapters.anthropic import _map_search_queries as anthropic_map_search_queries
from app.adapters.google import _map_search_queries as google_map_search_queries


def test_google_returns_web_search_queries_in_order():
    payload = {
        "candidates": [{"grounding_metadata": {"web_search_queries": ["first query", "second query"]}}]
    }
    assert google_map_search_queries(payload) == ["first query", "second query"]


def test_google_handles_missing_candidates():
    assert google_map_search_queries({}) == []
    assert google_map_search_queries({"candidates": []}) == []


def test_google_handles_missing_grounding_metadata():
    assert google_map_search_queries({"candidates": [{}]}) == []


def test_google_handles_grounding_metadata_present_but_no_search_queries():
    payload = {"candidates": [{"grounding_metadata": {"web_search_queries": None}}]}
    assert google_map_search_queries(payload) == []


def test_anthropic_returns_queries_from_web_search_blocks_in_order():
    payload = {
        "content": [
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "first"}},
            {"type": "text", "text": "..."},
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "second"}},
        ]
    }
    assert anthropic_map_search_queries(payload) == ["first", "second"]


def test_anthropic_handles_empty_content():
    assert anthropic_map_search_queries({"content": []}) == []
    assert anthropic_map_search_queries({}) == []


def test_anthropic_skips_non_web_search_tool_use_blocks():
    payload = {"content": [{"type": "server_tool_use", "name": "code_execution", "input": {"code": "x"}}]}
    assert anthropic_map_search_queries(payload) == []


def test_anthropic_handles_web_search_block_missing_query_key():
    payload = {"content": [{"type": "server_tool_use", "name": "web_search", "input": {}}]}
    assert anthropic_map_search_queries(payload) == []
