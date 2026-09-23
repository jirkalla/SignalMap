"""Unit tests for provider-specific citation and search-query extraction.

Search queries come from docs/TASKS_SEARCH_QUERIES.md SQ-T2; the citation
coverage is docs/TASKS_GEMINI_CITATIONS.md GC-T5, added because
`_map_citations` had no test at all for any provider — which is how a mapper
that dropped 59% of Gemini's claim-source links survived three phases.

No real API calls — these test the pure mapping functions against the
serialized response shape they actually receive in production:
`response.model_dump(mode="json")`, i.e. plain dicts, not SDK objects
(docs/TASKS_GEMINI_CITATIONS.md design decision 5). The shapes here were
verified 2026-09-16 against real stored `raw_responses.raw_payload` rows, and
the SDK field names behind them 2026-09-10 against the installed SDKs (see
docs/TASKS_SEARCH_QUERIES.md design decisions 4-5), so nothing here is guessed.
"""

import json

import pytest

from app.adapters.anthropic import _map_citations as anthropic_map_citations
from app.adapters.anthropic import _map_search_queries as anthropic_map_search_queries
from app.adapters.google import _map_citations as google_map_citations
from app.adapters.google import _map_search_queries as google_map_search_queries
from app.adapters.deepseek import _map_citations as deepseek_map_citations
from app.adapters.deepseek import _map_search_queries as deepseek_map_search_queries
from app.adapters.grok import _map_citations as grok_map_citations
from app.adapters.grok import _map_search_queries as grok_map_search_queries
from app.adapters.openai import _map_citations as openai_map_citations
from app.adapters.perplexity import _map_citations as perplexity_map_citations
from app.adapters.perplexity import _map_search_queries as perplexity_map_search_queries


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


# --- Gemini citations (GC-T5) ------------------------------------------------
#
# Fixture shapes mirror a real `candidates[0].grounding_metadata`: chunks carry
# an opaque vertexaisearch `uri` with the bare domain as `title` and a null
# `domain`, supports carry a `segment` plus the indices of every chunk backing
# it. The relation is many-to-many in both directions, which is what these
# tests pin down.


def _chunk(uri: str, title: str) -> dict:
    return {"web": {"uri": uri, "title": title, "domain": None}}


def _support(text: str, start: int | None, end: int, chunk_indices: list[int]) -> dict:
    return {
        "segment": {"text": text, "start_index": start, "end_index": end, "part_index": None},
        "grounding_chunk_indices": chunk_indices,
        "confidence_scores": None,
        "rendered_parts": None,
    }


def _gemini_payload(chunks: list[dict], supports: list[dict]) -> dict:
    return {"candidates": [{"grounding_metadata": {"grounding_chunks": chunks, "grounding_supports": supports}}]}


def test_gemini_one_support_backed_by_three_chunks_yields_one_row_per_source():
    """One claim backed by three sources is three links, not one row with a winner."""
    payload = _gemini_payload(
        [_chunk("https://vertex/0", "a.de"), _chunk("https://vertex/1", "b.de"), _chunk("https://vertex/2", "c.de")],
        [_support("Skoda is seen as reliable.", 0, 26, [0, 1, 2])],
    )

    citations, has_citations = google_map_citations(payload)

    assert has_citations is True
    assert len(citations) == 3
    assert [c.source_domain for c in citations] == ["a.de", "b.de", "c.de"]
    assert {c.cited_answer_span for c in citations} == {"Skoda is seen as reliable."}
    assert [c.citation_position for c in citations] == [0, 1, 2]


def test_gemini_regression_one_chunk_backing_two_supports_is_not_collapsed():
    """Regression test for the `break` bug this branch fixes (roadmap #11).

    The old mapper walked the chunks and stopped at the FIRST support referencing
    each one, so a source backing two separate claims produced a single row and
    the second claim-source link was lost — 803 of 1354 pairs across production
    payloads. This must return two rows: same source, different answer spans.
    """
    payload = _gemini_payload(
        [_chunk("https://vertex/0", "a.de")],
        [_support("First claim.", 0, 12, [0]), _support("Second claim.", 13, 26, [0])],
    )

    citations, _ = google_map_citations(payload)

    assert len(citations) == 2
    assert [c.source_domain for c in citations] == ["a.de", "a.de"]
    assert [c.cited_answer_span for c in citations] == ["First claim.", "Second claim."]
    assert [c.answer_span_start for c in citations] == [0, 13]
    assert [c.citation_position for c in citations] == [0, 1]


def test_gemini_null_start_index_on_first_segment_becomes_zero():
    """`start_index` is absent on the answer's first segment and means 0 there.

    Observed on 30 of 740 production supports. Ordering None against an int would
    raise TypeError, so it is normalized before both sorting and storing.
    """
    payload = _gemini_payload(
        [_chunk("https://vertex/0", "a.de"), _chunk("https://vertex/1", "b.de")],
        [_support("Later claim.", 40, 52, [1]), _support("Opening claim.", None, 14, [0])],
    )

    citations, _ = google_map_citations(payload)

    assert [c.answer_span_start for c in citations] == [0, 40]
    assert [c.cited_answer_span for c in citations] == ["Opening claim.", "Later claim."]


def test_gemini_orders_pairs_by_position_in_the_answer_not_input_order():
    """`citation_position` means order in the answer, as it already did for the other two providers."""
    payload = _gemini_payload(
        [
            _chunk("https://vertex/0", "last.de"),
            _chunk("https://vertex/1", "first.de"),
            _chunk("https://vertex/2", "middle.de"),
        ],
        [
            _support("Third claim.", 200, 212, [0]),
            _support("First claim.", 0, 12, [1]),
            _support("Second claim.", 100, 113, [2]),
        ],
    )

    citations, _ = google_map_citations(payload)

    assert [c.source_domain for c in citations] == ["first.de", "middle.de", "last.de"]
    assert [c.citation_position for c in citations] == [0, 1, 2]
    assert [c.answer_span_start for c in citations] == [0, 100, 200]


def test_gemini_chunk_with_no_support_is_kept_last_without_a_span():
    """A source the model found but attached to no claim is still evidence (FR-12/FR-13).

    Production has none of these today — this guards the supports-driven walk
    against silently dropping one (design decision 3).
    """
    payload = _gemini_payload(
        [_chunk("https://vertex/0", "unused.de"), _chunk("https://vertex/1", "used.de")],
        [_support("A claim.", 0, 8, [1])],
    )

    citations, has_citations = google_map_citations(payload)

    assert has_citations is True
    assert [c.source_domain for c in citations] == ["used.de", "unused.de"]
    orphan = citations[-1]
    assert orphan.cited_answer_span is None
    assert orphan.answer_span_start is None
    assert orphan.answer_span_end is None
    assert orphan.citation_position == 1


def test_gemini_chunk_index_out_of_range_is_skipped_without_crashing():
    """A malformed payload loses that one pair, never the whole response."""
    payload = _gemini_payload(
        [_chunk("https://vertex/0", "a.de")],
        [_support("A claim.", 0, 8, [0, 7])],
    )

    citations, _ = google_map_citations(payload)

    assert len(citations) == 1
    assert citations[0].source_domain == "a.de"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param({"candidates": []}, id="no-candidates"),
        pytest.param({"candidates": [{"content": {"parts": [{"text": "x"}]}}]}, id="no-grounding-metadata"),
        pytest.param(
            {"candidates": [{"grounding_metadata": {"web_search_queries": ["q"]}}]}, id="metadata-without-chunks"
        ),
    ],
)
def test_gemini_without_grounding_chunks_reports_no_citations(payload: dict):
    """has_citations is explicitly False, not inferred from list length (FR-13).

    The no-grounding-metadata case is also the Gemini 3 response shape (design
    decision 9): out of scope to map, but it must not raise.
    """
    assert google_map_citations(payload) == ([], False)


def test_gemini_never_fills_source_passage():
    """Gemini returns the answer segment, never a passage quoted from the page."""
    payload = _gemini_payload([_chunk("https://vertex/0", "a.de")], [_support("A claim.", 0, 8, [0])])

    citations, _ = google_map_citations(payload)

    assert citations[0].source_passage is None
    assert citations[0].cited_answer_span == "A claim."


def test_gemini_mapping_is_identical_over_a_stored_json_payload():
    """Design decision 5, the claim the GC-T3 backfill rests on.

    The mapper must give the same result whether it is handed the freshly
    serialized response or that same payload read back out of JSONB — otherwise
    re-extracting history from `raw_responses.raw_payload` would not reproduce
    what the live path stored. The json round-trip is what JSONB storage does to
    the dict.
    """
    payload = _gemini_payload(
        [_chunk("https://vertex/0", "a.de"), _chunk("https://vertex/1", "b.de")],
        [_support("Opening claim.", None, 14, [0, 1]), _support("Later claim.", 40, 52, [1])],
    )

    live, live_has = google_map_citations(payload)
    stored, stored_has = google_map_citations(json.loads(json.dumps(payload)))

    assert live == stored
    assert live_has == stored_has
    assert len(live) == 3


# --- Anthropic citations (GC-T5) ---------------------------------------------


def _anthropic_citation(url: str, title: str, cited_text: str) -> dict:
    return {
        "type": "web_search_result_location",
        "url": url,
        "title": title,
        "cited_text": cited_text,
        "encrypted_index": "Eo8BCioIExgC",
    }


def test_anthropic_cited_text_is_stored_as_source_passage_not_as_the_answer_span():
    """`cited_text` is quoted FROM THE SOURCE PAGE, so it is not the answer span.

    Storing it in `cited_answer_span` contradicted FR-12 and is the second defect
    this branch fixes: measured over production rows, the stored value was
    findable in the answer for only 17 of 158 Anthropic citations.
    """
    payload = {
        "content": [
            {
                "type": "text",
                "text": "Skoda is seen as reliable.",
                "citations": [
                    _anthropic_citation("https://autoscout24.de/x", "AutoScout24", "Skoda gilt als zuverlässig.")
                ],
            }
        ]
    }

    citations, has_citations = anthropic_map_citations(payload)

    assert has_citations is True
    assert len(citations) == 1
    citation = citations[0]
    assert citation.source_passage == "Skoda gilt als zuverlässig."
    assert citation.cited_answer_span is None
    assert citation.source_domain == "autoscout24.de"
    # Permanently None: the API exposes no offsets into the answer, only an
    # encrypted index into the search results.
    assert citation.answer_span_start is None
    assert citation.answer_span_end is None


def test_anthropic_tolerates_scalar_null_citations_on_non_text_blocks():
    """Non-text blocks carry `"citations": null`, not a missing key or an empty list."""
    payload = {
        "content": [
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "q"}, "citations": None},
            {"type": "web_search_tool_result", "content": [], "citations": None},
            {"type": "text", "text": "A claim.", "citations": [_anthropic_citation("https://a.de/x", "A", "passage")]},
        ]
    }

    citations, _ = anthropic_map_citations(payload)

    assert [c.source_passage for c in citations] == ["passage"]


def test_anthropic_position_runs_across_several_text_blocks():
    payload = {
        "content": [
            {
                "type": "text",
                "text": "First.",
                "citations": [_anthropic_citation("https://a.de/x", "A", "first passage")],
            },
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "q"}, "citations": None},
            {
                "type": "text",
                "text": "Second.",
                "citations": [
                    _anthropic_citation("https://b.de/y", "B", "second passage"),
                    _anthropic_citation("https://c.de/z", "C", "third passage"),
                ],
            },
        ]
    }

    citations, _ = anthropic_map_citations(payload)

    assert [c.citation_position for c in citations] == [0, 1, 2]
    assert [c.source_passage for c in citations] == ["first passage", "second passage", "third passage"]


def test_anthropic_without_citations_reports_none():
    assert anthropic_map_citations({"content": [{"type": "text", "text": "x", "citations": None}]}) == ([], False)
    assert anthropic_map_citations({}) == ([], False)


# --- OpenAI citations (GC-T5) ------------------------------------------------


def _openai_payload(text: str, annotations: list[dict]) -> dict:
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text, "annotations": annotations}]}]
    }


def test_openai_keeps_the_offsets_and_slices_the_span_from_them():
    """OpenAI exposes the answer side: offsets into the answer plus the slice they name.

    Unlike Gemini's, these offsets are character offsets — see AdapterCitation's
    docstring; the mapper stores whatever the provider returned either way.
    """
    text = "Skoda is seen as reliable in Germany."
    payload = _openai_payload(
        text,
        [{"type": "url_citation", "url": "https://a.de/x", "title": "A", "start_index": 0, "end_index": 25}],
    )

    citations, has_citations = openai_map_citations(payload)

    assert has_citations is True
    citation = citations[0]
    assert citation.answer_span_start == 0
    assert citation.answer_span_end == 25
    assert citation.cited_answer_span == text[0:25]
    assert citation.source_passage is None


def test_openai_skips_non_url_citation_annotations():
    payload = _openai_payload(
        "A claim.",
        [
            {"type": "file_citation", "file_id": "f-1"},
            {"type": "url_citation", "url": "https://a.de/x", "title": "A", "start_index": 0, "end_index": 8},
        ],
    )

    citations, _ = openai_map_citations(payload)

    assert [c.source_domain for c in citations] == ["a.de"]
    assert [c.citation_position for c in citations] == [0]


def test_openai_without_annotations_reports_no_citations():
    assert openai_map_citations(_openai_payload("A claim.", [])) == ([], False)
    assert openai_map_citations({}) == ([], False)


# --- Perplexity citations and search queries (NP-T2) -------------------------


def _perplexity_search_results_item(queries: list[str], results: list[dict]) -> dict:
    return {"type": "search_results", "queries": queries, "results": results}


def _perplexity_result(url: str, title: str, snippet: str = "...") -> dict:
    return {"id": 1, "url": url, "title": title, "source": "web", "snippet": snippet, "date": None, "last_updated": "2026-09-23"}


def test_perplexity_maps_citations_from_search_results_item():
    """Citations are a dedicated `search_results` output item, not `url_citation` annotations —
    shape verified in docs/TASKS_NEW_PROVIDERS.md NP-T1, unlike OpenAI/Grok.
    """
    payload = {
        "output": [
            _perplexity_search_results_item(
                ["Knauf AG company products"],
                [
                    _perplexity_result("https://knauf.com/en", "Knauf | Building materials"),
                    _perplexity_result("https://en.wikipedia.org/wiki/Knauf", "Knauf - Wikipedia"),
                ],
            ),
            {"type": "message", "content": [{"type": "output_text", "text": "Knauf makes gypsum products.", "annotations": []}]},
        ]
    }

    citations, has_citations = perplexity_map_citations(payload)

    assert has_citations is True
    assert [c.source_url for c in citations] == ["https://knauf.com/en", "https://en.wikipedia.org/wiki/Knauf"]
    assert [c.citation_position for c in citations] == [0, 1]
    assert citations[0].source_domain == "knauf.com"
    # Permanently None: no offsets into the answer text exist for this provider (design decision 4).
    assert citations[0].cited_answer_span is None
    assert citations[0].answer_span_start is None
    assert citations[0].source_passage is None


def test_perplexity_position_runs_across_several_search_results_items():
    payload = {
        "output": [
            _perplexity_search_results_item(["first query"], [_perplexity_result("https://a.de/x", "A")]),
            _perplexity_search_results_item(
                ["second query"], [_perplexity_result("https://b.de/y", "B"), _perplexity_result("https://c.de/z", "C")]
            ),
        ]
    }

    citations, _ = perplexity_map_citations(payload)

    assert [c.citation_position for c in citations] == [0, 1, 2]
    assert [c.source_url for c in citations] == ["https://a.de/x", "https://b.de/y", "https://c.de/z"]


def test_perplexity_without_search_results_reports_no_citations():
    assert perplexity_map_citations({"output": [{"type": "message", "content": []}]}) == ([], False)
    assert perplexity_map_citations({}) == ([], False)


def test_perplexity_returns_search_queries_from_search_results_items_in_order():
    payload = {
        "output": [
            _perplexity_search_results_item(["first query"], []),
            {"type": "message", "content": []},
            _perplexity_search_results_item(["second query", "third query"], []),
        ]
    }

    assert perplexity_map_search_queries(payload) == ["first query", "second query", "third query"]


def test_perplexity_handles_missing_output():
    assert perplexity_map_search_queries({}) == []
    assert perplexity_map_search_queries({"output": []}) == []


# --- DeepSeek: permanently empty citations/queries (NP-T3) -------------------


def _deepseek_payload(content: str) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {"content": content, "role": "assistant", "annotations": None, "tool_calls": None},
            }
        ]
    }


def test_deepseek_never_returns_citations():
    """No citation field exists in DeepSeek's payload at all — design decision 3, a structural
    fact about the provider, not an unfinished mapper (see app/adapters/deepseek.py docstring).
    """
    assert deepseek_map_citations(_deepseek_payload("Some answer.")) == ([], False)
    assert deepseek_map_citations({}) == ([], False)


def test_deepseek_never_returns_search_queries():
    assert deepseek_map_search_queries(_deepseek_payload("Some answer.")) == []
    assert deepseek_map_search_queries({}) == []


# --- Grok citations and search queries (NP-T4) --------------------------------


def _grok_message(text: str, annotations: list[dict]) -> dict:
    return {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": annotations}]}


def _grok_url_citation(url: str, title: str, start_index: int = 0, end_index: int = 0) -> dict:
    """xAI's real payload always had start_index == end_index == 0 (NP-T1) — the default here
    matches that, and the mapper must ignore them regardless (see test below).
    """
    return {"type": "url_citation", "url": url, "title": title, "start_index": start_index, "end_index": end_index}


def _grok_web_search_call(query: str) -> dict:
    return {"type": "web_search_call", "action": {"type": "search", "query": query, "sources": []}}


def test_grok_maps_citations_from_url_citation_annotations():
    """Same annotation shape as OpenAI (design decision correction, NP-T1) — NOT a flat
    response.citations array, as originally assumed before the real call was made.
    """
    payload = {
        "output": [
            _grok_message(
                "Knauf makes gypsum products.",
                [
                    _grok_url_citation("https://knauf.com/en/who-we-are", "https://knauf.com/en/who-we-are"),
                    _grok_url_citation("https://en.wikipedia.org/wiki/Knauf", "https://en.wikipedia.org/wiki/Knauf"),
                ],
            )
        ]
    }

    citations, has_citations = grok_map_citations(payload)

    assert has_citations is True
    assert [c.source_url for c in citations] == ["https://knauf.com/en/who-we-are", "https://en.wikipedia.org/wiki/Knauf"]
    assert [c.citation_position for c in citations] == [0, 1]
    assert citations[0].source_domain == "knauf.com"


def test_grok_never_populates_the_span_even_when_offsets_are_present():
    """start_index/end_index exist on the annotation but are always 0/0 in real data (NP-T1) —
    the mapper must ignore them entirely, not slice a misleading empty-string span from them.
    Uses NONZERO offsets here specifically to prove they're ignored, not just untested.
    """
    payload = {"output": [_grok_message("A claim.", [_grok_url_citation("https://a.de/x", "A", start_index=0, end_index=25)])]}

    citations, _ = grok_map_citations(payload)

    assert citations[0].cited_answer_span is None
    assert citations[0].answer_span_start is None
    assert citations[0].answer_span_end is None
    assert citations[0].source_passage is None


def test_grok_skips_non_url_citation_annotations():
    payload = {
        "output": [
            _grok_message(
                "A claim.",
                [{"type": "file_citation", "file_id": "f-1"}, _grok_url_citation("https://a.de/x", "A")],
            )
        ]
    }

    citations, _ = grok_map_citations(payload)

    assert [c.source_domain for c in citations] == ["a.de"]


def test_grok_without_annotations_reports_no_citations():
    assert grok_map_citations({"output": [_grok_message("A claim.", [])]}) == ([], False)
    assert grok_map_citations({}) == ([], False)


def test_grok_returns_search_queries_from_web_search_call_items_in_order():
    payload = {
        "output": [
            _grok_web_search_call("first query"),
            _grok_message("answer", []),
            _grok_web_search_call("second query"),
        ]
    }

    assert grok_map_search_queries(payload) == ["first query", "second query"]


def test_grok_handles_missing_output():
    assert grok_map_search_queries({}) == []
    assert grok_map_search_queries({"output": []}) == []
