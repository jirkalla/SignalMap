"""Anthropic Claude provider adapter, with the `web_search` tool enabled.

Unlike Gemini's Google Search grounding (no location parameter at all —
see app/adapters/google.py), Anthropic's `web_search` tool takes a real
`user_location`, so this adapter is the actual reason phase 2 adds a second
provider: real geographic search targeting instead of Gemini's text-only
locale hint (docs/REQUIREMENTS.md §3a, /findings).

Tool version/shape verified against platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
(fetched 2026-09-09) — that page documents three tool versions
(web_search_20250305/20260209/20260318); this adapter deliberately uses the
oldest, `web_search_20250305` ("basic web search"), since the newer versions
add dynamic filtering / response-inclusion controls that change the response
shape (nested code-execution blocks) for a benefit (token savings on
search-heavy agentic loops) SignalMap's single-shot Q&A use case doesn't need.

The mapping functions take the serialized response dict
(`response.model_dump(mode="json")`) rather than SDK objects, for the reason
documented in app/adapters/google.py's module docstring: the GC-T3 backfill
replays them over stored `raw_payload` rows.
"""

import anthropic

from app.adapters.base import AdapterCitation, RawResponsePayload, extract_domain
from app.config import get_settings

# A perception-tracking prompt is exploratory/comparative by nature (docs/
# REQUIREMENTS.md's "corporate perception" framing), so this sits above the
# "simple factual query" range Anthropic's own docs put at 1-3 searches —
# but still caps runaway search loops on one run. Adjust if real usage shows
# it's too tight.
_MAX_WEB_SEARCHES = 5

# Anthropic's Messages API requires max_tokens explicitly (no default).
# Sized for a complete grounded answer with citations, not the model's full
# capacity (128k on Sonnet/Opus) — that would be needlessly expensive for
# what this app asks for.
_DEFAULT_MAX_TOKENS = 4096


def _map_citations(payload: dict) -> tuple[list[AdapterCitation], bool]:
    """Turn every text block's inline `citations` into the canonical shape.

    Anthropic attaches citations directly on each text content block
    (`block["citations"]`), unlike Gemini's single grounding_metadata blob —
    so this walks all text blocks in appearance order and assigns a running
    position across the whole response, the same "position = order the
    provider presented it in" semantics the other two adapters use. The
    provider already emits one citation per (claim, source) pair, so no
    many-to-many flattening happens here and the walk itself is unchanged.
    has_citations is explicitly False (FR-13), not just an empty list, when
    no block carries any citation.

    What each citation's text means: `cited_text` is a passage quoted FROM THE
    SOURCE PAGE, so it goes to `source_passage`, not `cited_answer_span` —
    see AdapterCitation's docstring for the boundary. Storing it as the answer
    span is the defect this change fixes (measured 2026-09-16: the value was
    findable in the answer for only 17 of 158 stored rows, and those 17 were
    short-string coincidences), and it contradicted FR-12's definition of the
    column.

    `answer_span_start`/`answer_span_end` stay permanently None here. The API
    returns no offsets into the answer: `web_search_result_location` carries an
    `encrypted_index`, which indexes the search results, not the answer text.
    That is a property of the API, not an unfinished piece of this adapter —
    there is nothing to read them from, so do not "fix" it.
    """
    citations: list[AdapterCitation] = []
    for block in payload.get("content") or []:
        if (block or {}).get("type") != "text":
            continue
        # `or []`, not a default: on non-text blocks the key is present with a
        # scalar null, so `.get("citations", [])` would still hand back None.
        for citation in block.get("citations") or []:
            url = citation.get("url")
            citations.append(
                AdapterCitation(
                    source_url=url,
                    source_title=citation.get("title"),
                    source_domain=extract_domain(url),
                    citation_position=len(citations),
                    cited_answer_span=None,
                    answer_span_start=None,
                    answer_span_end=None,
                    source_passage=citation.get("cited_text"),
                )
            )
    return citations, bool(citations)


def _map_search_queries(payload: dict) -> list[str]:
    """Extract the queries from every `server_tool_use` web_search block, in order.

    Verified 2026-09-10 against anthropic==1.4.0 (ServerToolUseBlock:
    type/name/input, input untyped as Dict[str, object]) and the official
    web-search-tool docs, which show the exact shape: {"type":
    "server_tool_use", "name": "web_search", "input": {"query": "..."}}.
    `.get("query")`, not `["query"]`, since `input` isn't typed further by
    the SDK. A run can issue multiple searches (up to `_MAX_WEB_SEARCHES`),
    each its own block — this walks all of them, not just the first.
    """
    queries: list[str] = []
    for block in payload.get("content") or []:
        if (block or {}).get("type") != "server_tool_use":
            continue
        if block.get("name") != "web_search":
            continue
        query = (block.get("input") or {}).get("query")
        if query:
            queries.append(query)
    return queries


class AnthropicAdapter:
    """Adapter for the Anthropic Claude API (anthropic SDK)."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)

    def run(
        self,
        prompt_text: str,
        model_name: str,
        *,
        system_instruction: str | None = None,
        market_country: str | None = None,
    ) -> RawResponsePayload:
        """Run `prompt_text` against `model_name` with the `web_search` tool enabled.

        `market_country` (an ISO 3166-1 alpha-2 code, may be None) becomes
        the `web_search` tool's `user_location.country` — real geographic
        search targeting, not a text hint. `system_instruction`, if given,
        is passed as Anthropic's top-level `system` field.

        Response completion caveat: a very long-running search turn can
        come back with `stop_reason: "pause_turn"` instead of finishing —
        continuing that would mean resending the paused message in a new
        request. This first version does not loop on that (see
        docs/TASKS_PHASE2.md P2-T4) and just returns what came back;
        `raw_payload` always keeps the real `stop_reason`, so that state is
        never hidden, just not auto-continued.

        Raises whatever the anthropic SDK raises on transport/API errors
        (timeout, auth failure, rate limit) — the caller records that as a
        Run with status='error'.
        """
        web_search_tool: dict = {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": _MAX_WEB_SEARCHES,
        }
        if market_country:
            web_search_tool["user_location"] = {"type": "approximate", "country": market_country}

        create_kwargs: dict = {
            "model": model_name,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt_text}],
            "tools": [web_search_tool],
        }
        if system_instruction:
            create_kwargs["system"] = system_instruction

        response = self._client.messages.create(**create_kwargs)

        rendered_text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ) or None
        # Serialized once and handed to both the mappers and raw_payload, so
        # what gets stored is exactly what the citations were derived from.
        payload = response.model_dump(mode="json")
        citations, has_citations = _map_citations(payload)
        search_queries = _map_search_queries(payload)

        token_usage = response.usage.model_dump(mode="json") if response.usage is not None else None

        return RawResponsePayload(
            raw_payload=payload,
            rendered_text=rendered_text,
            has_citations=has_citations,
            citations=citations,
            search_queries=search_queries,
            token_usage=token_usage,
        )
