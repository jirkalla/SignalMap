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
"""

from urllib.parse import urlparse

import anthropic

from app.adapters.base import AdapterCitation, RawResponsePayload
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


def _extract_domain(url: str | None) -> str | None:
    """Best-effort domain extraction from a citation URL; None if unparsable."""
    if not url:
        return None
    try:
        return urlparse(url).netloc or None
    except ValueError:
        return None


def _map_citations(content: list) -> tuple[list[AdapterCitation], bool]:
    """Turn every text block's inline `citations` into the canonical shape.

    Anthropic attaches citations directly on each text content block
    (`block.citations`), unlike Gemini's single grounding_metadata blob —
    so this walks all text blocks in appearance order and assigns a running
    position across the whole response, the same "position = order the
    provider presented it in" semantics app/adapters/google.py uses.
    has_citations is explicitly False (FR-13), not just an empty list, when
    no block carries any citation.
    """
    citations: list[AdapterCitation] = []
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        for citation in getattr(block, "citations", None) or []:
            url = getattr(citation, "url", None)
            citations.append(
                AdapterCitation(
                    source_url=url,
                    source_title=getattr(citation, "title", None),
                    source_domain=_extract_domain(url),
                    citation_position=len(citations),
                    cited_answer_span=getattr(citation, "cited_text", None),
                )
            )
    return citations, bool(citations)


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
        citations, has_citations = _map_citations(response.content)

        token_usage = response.usage.model_dump(mode="json") if response.usage is not None else None

        return RawResponsePayload(
            raw_payload=response.model_dump(mode="json"),
            rendered_text=rendered_text,
            has_citations=has_citations,
            citations=citations,
            token_usage=token_usage,
        )
