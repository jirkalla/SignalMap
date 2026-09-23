"""xAI Grok provider adapter, against the Responses API (openai SDK, non-default `base_url`).

Sixth provider — same request shape as `app/adapters/openai.py` and `app/adapters/perplexity.py`
(`responses.create()`, `input`/`instructions`/`tools`), via the openai SDK with
`base_url="https://api.x.ai/v1"`, since xAI supports this exact API surface. No custom HTTP
client — same discipline as every other Responses-API-backed adapter here.

Verified against a real `responses.create()` call, 2026-09-23 (docs/TASKS_NEW_PROVIDERS.md NP-T1,
"Ověřené tvary odpovědí" → xAI Grok), not against xAI's documentation — the documentation-sourced
assumption in this project's own planning doc (design decisions 1/4) turned out to be wrong on
where citations live:

- **Citations are NOT in a top-level `response.citations` array** (that field doesn't exist at
  all — `"citations" in data` was `False` in the verified payload). Grok uses the **same shape as
  OpenAI**: an `output` item with `type: "message"` whose `content[0].annotations` carries
  `type: "url_citation"` records (`url`, `title`, `start_index`, `end_index`) — so
  `_map_citations` below is structurally the same walk as `app/adapters/openai.py`'s.
- **The span genuinely can't be used, but not because it's a flat list without offsets** (what
  the pre-verification design decision assumed). All 11 annotations in the verified response had
  `start_index == 0` and `end_index == 0` — present fields, but zero/invalid — and `title` was
  always identical to `url` (no real page title). `cited_answer_span`/`answer_span_start`/
  `answer_span_end`/`source_passage` are therefore always left `None` here, same end result as
  OpenAI's genuinely-offset-less siblings (Anthropic/Perplexity), but for a different underlying
  reason — this mapper deliberately never reads `start_index`/`end_index` at all, rather than
  reading them and getting zero-length slices that would misrepresent "no span" as "an empty
  span". Do not "restore" reading these fields without new evidence they carry real offsets.
- **Search queries** come from `web_search_call` output items' `action.query` — the exact same
  field OpenAI's own `_map_search_queries` reads, so the two functions are structurally identical
  (kept separate per-adapter rather than shared, matching this app's existing one-mapper-per-file
  convention).
- **`market_country` → real geo targeting**, unlike Perplexity: xAI's `web_search` tool accepts
  `user_location` in the same `{"type": "approximate", "country": ...}` shape as OpenAI/Anthropic
  — confirmed by a raw HTTP probe (not just the openai SDK, which could silently drop an unknown
  field): the API echoed the sent `user_location` back verbatim in the response's `tools` field,
  the same acknowledgment pattern OpenAI's `user_location` support has. Wired through exactly like
  `app/adapters/openai.py`.
"""

import openai

from app.adapters.base import AdapterCitation, RawResponsePayload, extract_domain
from app.config import get_settings


def _map_citations(payload: dict) -> tuple[list[AdapterCitation], bool]:
    """Turn every `message` output item's `url_citation` annotations into the canonical shape.

    Structurally the same walk as `app/adapters/openai.py`'s `_map_citations` (same annotation
    shape) — but `start_index`/`end_index` are deliberately never read here, unlike OpenAI's
    version: see module docstring for why (they were always 0/0 in the verified payload, not
    real offsets). `has_citations` is explicitly False (FR-13), not just an empty list, when no
    block carries any `url_citation` annotation.
    """
    citations: list[AdapterCitation] = []
    for item in payload.get("output") or []:
        if (item or {}).get("type") != "message":
            continue
        for block in item.get("content") or []:
            if (block or {}).get("type") != "output_text":
                continue
            for annotation in block.get("annotations") or []:
                if (annotation or {}).get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                citations.append(
                    AdapterCitation(
                        source_url=url,
                        source_title=annotation.get("title"),
                        source_domain=extract_domain(url),
                        citation_position=len(citations),
                        cited_answer_span=None,
                        answer_span_start=None,
                        answer_span_end=None,
                        source_passage=None,
                    )
                )
    return citations, bool(citations)


def _map_search_queries(payload: dict) -> list[str]:
    """Extract the query from every `web_search_call` output item, in order — same field
    (`action.query`) and item shape as `app/adapters/openai.py`'s own `_map_search_queries`.
    """
    queries: list[str] = []
    for item in payload.get("output") or []:
        if (item or {}).get("type") != "web_search_call":
            continue
        query = (item.get("action") or {}).get("query")
        if query:
            queries.append(query)
    return queries


class GrokAdapter:
    """Adapter for the xAI Grok Responses API (openai SDK, non-default `base_url`)."""

    def __init__(self) -> None:
        self._client = openai.OpenAI(base_url="https://api.x.ai/v1", api_key=get_settings().xai_api_key)

    def run(
        self,
        prompt_text: str,
        model_name: str,
        *,
        system_instruction: str | None = None,
        market_country: str | None = None,
    ) -> RawResponsePayload:
        """Run `prompt_text` against `model_name` with the `web_search` tool enabled.

        `market_country` becomes the `web_search` tool's `user_location.country` — real
        geographic search targeting, confirmed working against xAI's API (module docstring),
        same as `app/adapters/openai.py` and `app/adapters/anthropic.py`. `system_instruction`,
        if given, is passed as the Responses API's top-level `instructions` field.

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
        # Serialized once and handed to both the mappers and raw_payload, so what gets stored is
        # exactly what the citations were derived from — same discipline as every other adapter.
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
