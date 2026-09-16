"""OpenAI ChatGPT provider adapter, with the Responses API `web_search` tool enabled.

Third provider — same architecture as Gemini (grounding tool, app/adapters/google.py) and
Anthropic (`web_search` tool with real `user_location`, app/adapters/anthropic.py). Uses the
Responses API (`client.responses.create`), not the older Chat Completions API — OpenAI's own
docs (developers.openai.com/api/docs/guides/tools-web-search, fetched 2026-09-13) show
`web_search` as a Responses-API-only tool with no Chat Completions equivalent.

Verified against developers.openai.com/api/docs/guides/tools-web-search and
.../api/docs/guides/text (fetched 2026-09-13) — not assumed from memory/training data:
- Tool type string is exactly `"web_search"` (not `"web_search_preview"`).
- `user_location` shape: `{"type": "approximate", "country": ..., "city": ..., "region": ...,
  "timezone": ...}` — same `type: "approximate"` convention as Anthropic's `web_search` tool.
- Citations appear as `url_citation` annotations (`type`/`url`/`title`/`start_index`/
  `end_index`) on each `output_text` content block of a `message`-type output item — unlike
  Anthropic's citations, there's no separate "cited text" field, so `cited_answer_span` is
  sliced from the block's own `text` using `start_index`/`end_index`.
- Search queries appear as separate `web_search_call` output items
  (`action.type == "search"`, `action.query`), analogous to Anthropic's `server_tool_use` blocks.
- No `max_uses`-style cap on the tool exists in the docs (unlike Anthropic's `web_search`) — none
  is applied here.
- `instructions` is the Responses API's top-level system-prompt field (replaces Chat
  Completions' `system` role message).

The mapping functions take the serialized response dict (`response.model_dump(mode="json")`)
rather than SDK objects, for the reason documented in app/adapters/google.py's module docstring:
the GC-T3 backfill replays them over stored `raw_payload` rows.
"""

import openai

from app.adapters.base import AdapterCitation, RawResponsePayload, extract_domain
from app.config import get_settings


def _map_citations(payload: dict) -> tuple[list[AdapterCitation], bool]:
    """Turn every `message` output item's `url_citation` annotations into the canonical shape.

    Walks every `output_text` content block of every `message`-type item, in appearance order,
    assigning a running position — same "position = order the provider presented it in"
    semantics as app/adapters/google.py and app/adapters/anthropic.py. The provider already
    emits one annotation per (claim, source) pair, so the walk itself is unchanged by the
    many-to-many fix that rewrote Gemini's mapper. `has_citations` is explicitly False (FR-13),
    not just an empty list, when no block carries any `url_citation` annotation.

    What each field means: the annotation carries offsets into the answer, so
    `cited_answer_span` is sliced from the block's own text and the raw `start_index`/`end_index`
    are kept alongside it in `answer_span_start`/`answer_span_end` — the slice for reading, the
    offsets for locating it again without re-deriving them. `source_passage` stays None: unlike
    Anthropic, the annotation quotes nothing from the source page (see AdapterCitation's
    docstring for the boundary).
    """
    citations: list[AdapterCitation] = []
    for item in payload.get("output") or []:
        if (item or {}).get("type") != "message":
            continue
        for block in item.get("content") or []:
            if (block or {}).get("type") != "output_text":
                continue
            text = block.get("text") or ""
            for annotation in block.get("annotations") or []:
                if (annotation or {}).get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                start = annotation.get("start_index")
                end = annotation.get("end_index")
                cited_answer_span = text[start:end] if start is not None and end is not None else None
                citations.append(
                    AdapterCitation(
                        source_url=url,
                        source_title=annotation.get("title"),
                        source_domain=extract_domain(url),
                        citation_position=len(citations),
                        cited_answer_span=cited_answer_span,
                        answer_span_start=start,
                        answer_span_end=end,
                        source_passage=None,
                    )
                )
    return citations, bool(citations)


def _map_search_queries(payload: dict) -> list[str]:
    """Extract the query from every `web_search_call` output item, in order.

    Analogous to app/adapters/anthropic.py's `_map_search_queries` for `server_tool_use` blocks
    — a run can issue multiple searches, each its own output item.
    """
    queries: list[str] = []
    for item in payload.get("output") or []:
        if (item or {}).get("type") != "web_search_call":
            continue
        query = (item.get("action") or {}).get("query")
        if query:
            queries.append(query)
    return queries


class OpenAIAdapter:
    """Adapter for the OpenAI Responses API (openai SDK)."""

    def __init__(self) -> None:
        self._client = openai.OpenAI(api_key=get_settings().openai_api_key)

    def run(
        self,
        prompt_text: str,
        model_name: str,
        *,
        system_instruction: str | None = None,
        market_country: str | None = None,
    ) -> RawResponsePayload:
        """Run `prompt_text` against `model_name` with the `web_search` tool enabled.

        `market_country` (an ISO 3166-1 alpha-2 code, may be None) becomes the `web_search`
        tool's `user_location.country` — real geographic search targeting, same as Anthropic's
        adapter, on parity with it rather than Gemini's text-only hint. `system_instruction`, if
        given, is passed as the Responses API's top-level `instructions` field.

        Raises whatever the openai SDK raises on transport/API errors (timeout, auth failure,
        rate limit) — the caller records that as a Run with status='error'.
        """
        web_search_tool: dict = {"type": "web_search"}
        if market_country:
            web_search_tool["user_location"] = {"type": "approximate", "country": market_country}

        create_kwargs: dict = {
            "model": model_name,
            "input": prompt_text,
            "tools": [web_search_tool],
        }
        if system_instruction:
            create_kwargs["instructions"] = system_instruction

        response = self._client.responses.create(**create_kwargs)

        rendered_text = response.output_text or None
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
