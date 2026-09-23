"""Perplexity provider adapter, against the Agent API (not Sonar Chat Completions).

Fourth provider — same `responses.create()` request shape as `app/adapters/openai.py`
(input/instructions/tools), but a different response shape for citations. Built against the
**Agent API**, not Sonar: Perplexity's own docs state "Sonar Chat Completions is now Agent API.
Sonar will be supported until September 27, 2026" — building against an API that's being retired
would be pointless. Do not "simplify" this back to a `chat.completions.create()` call against
Sonar; the Agent API is the one with a future.

Verified against a real `responses.create()` call, 2026-09-23 (docs/TASKS_NEW_PROVIDERS.md NP-T1,
"Ověřené tvary odpovědí" → Perplexity), not against Perplexity's own documentation — the docs
turned out to be wrong on the request path:

- **`base_url` must be `"https://api.perplexity.ai/v1"`**, not `"https://api.perplexity.ai"`. The
  openai SDK always POSTs to `{base_url}/responses`; without `/v1` in `base_url` that request 404s.
  The documented path `/v1/agent` does not exist as a real endpoint — `POST /v1/agent/responses`
  answers 405, `POST /v1/responses` is the real one.
- **`model` takes a `{vendor}/{model}` string** (e.g. `"perplexity/sonar"`, `"xai/grok-4.7"`), not
  a Perplexity-specific "preset" as the original task plan assumed. The Agent API is a router
  across several vendors' models, not just Perplexity's own Sonar family — `client.models.list()`
  returns the full multi-vendor catalog. `ai_models.model_name` stores that full `vendor/model`
  string, since it's exactly what goes into the `model` field of the request.
- Citations do **not** arrive as `url_citation` annotations on a `message` output item (unlike
  OpenAI and, it turns out, Grok — see `app/adapters/grok.py`). They are a separate `output` item
  with `type: "search_results"`, carrying its own `queries` (the searches that produced it) and
  `results` (the actual sources) — see `_map_citations`/`_map_search_queries` below.
- The verified response's `output_text` carried no numbered `[1]`-style inline citation markers
  (`content[0].annotations` was an empty list) — so, per docs/TASKS_NEW_PROVIDERS.md design
  decision 4, `cited_answer_span`/`answer_span_start`/`answer_span_end` are never populated here.
  `source_passage` also stays `None`: a `search_results` item's `snippet` field is a longer,
  `...`-joined excerpt of several page fragments, not one passage the model quoted as backing a
  specific claim (the distinction `AdapterCitation`'s docstring draws against Anthropic's
  `cited_text`) — using it as `source_passage` would misrepresent what it actually is.

No geographic search targeting: the Agent API's `web_search` tool accepts a `filters` object per
Perplexity's docs, but NP-T1's probe never exercised it (no verified shape), so `market_country`
is accepted and silently ignored here — same as `app/adapters/google.py` — rather than guessing at
an unverified parameter shape. `system_instruction` is still applied via the Responses API's
`instructions` field, same as OpenAI.
"""

import openai

from app.adapters.base import AdapterCitation, RawResponsePayload, extract_domain
from app.config import get_settings

_BASE_URL = "https://api.perplexity.ai/v1"


def _map_citations(payload: dict) -> tuple[list[AdapterCitation], bool]:
    """Turn every `search_results` output item's `results` into the canonical shape.

    A run can produce more than one `search_results` item (one per search step the Agent API
    took), so `citation_position` runs across all of them in appearance order — same "position =
    order the provider presented it in" semantics as every other adapter's `_map_citations`.
    `has_citations` is explicitly False (FR-13), not just an empty list, when no item carries any
    result.
    """
    citations: list[AdapterCitation] = []
    for item in payload.get("output") or []:
        if (item or {}).get("type") != "search_results":
            continue
        for result in item.get("results") or []:
            url = (result or {}).get("url")
            citations.append(
                AdapterCitation(
                    source_url=url,
                    source_title=(result or {}).get("title"),
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
    """Extract `queries` from every `search_results` output item, in order.

    Unlike OpenAI's separate `web_search_call` items, Perplexity's search queries live on the same
    `search_results` item as the results they produced — one item, two things to read off it.
    """
    queries: list[str] = []
    for item in payload.get("output") or []:
        if (item or {}).get("type") != "search_results":
            continue
        queries.extend(item.get("queries") or [])
    return queries


class PerplexityAdapter:
    """Adapter for the Perplexity Agent API (openai SDK, non-default `base_url`)."""

    def __init__(self) -> None:
        self._client = openai.OpenAI(base_url=_BASE_URL, api_key=get_settings().perplexity_api_key)

    def run(
        self,
        prompt_text: str,
        model_name: str,
        *,
        system_instruction: str | None = None,
        market_country: str | None = None,
    ) -> RawResponsePayload:
        """Run `prompt_text` against `model_name` (a `{vendor}/{model}` string) with `web_search` on.

        `market_country` is accepted for interface parity but ignored — see module docstring for
        why. `system_instruction`, if given, is passed as the Responses API's top-level
        `instructions` field, same as `app/adapters/openai.py`.

        Raises whatever the openai SDK raises on transport/API errors (timeout, auth failure,
        rate limit) — the caller records that as a Run with status='error'.
        """
        create_kwargs: dict = {
            "model": model_name,
            "input": prompt_text,
            "tools": [{"type": "web_search"}],
        }
        if system_instruction:
            create_kwargs["instructions"] = system_instruction

        response = self._client.responses.create(**create_kwargs)

        rendered_text = response.output_text or None
        # Serialized once and handed to both the mappers and raw_payload, so what gets stored is
        # exactly what the citations were derived from (same discipline as every other adapter).
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
