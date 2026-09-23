"""DeepSeek provider adapter — OpenAI-compatible Chat Completions, no web search at all.

Fifth provider, and structurally the simplest: DeepSeek's API is `openai.OpenAI(base_url=
"https://api.deepseek.com")` against `chat.completions.create()`, the older Chat Completions
shape (not the Responses API `app/adapters/openai.py`/`app/adapters/perplexity.py` use) — no tool
is passed, because DeepSeek's API offers no search/grounding tool at all (confirmed by probe,
docs/TASKS_NEW_PROVIDERS.md NP-T1: no `tool_calls` on the message, `annotations` is `null`).

**`_map_citations` and `_map_search_queries` always return empty — permanently, not until a
tool gets added later.** This is docs/TASKS_NEW_PROVIDERS.md design decision 3: DeepSeek measures
a genuinely different thing than every other provider in this app — what the model says from its
own weights, with no grounding — and every run against it will have `has_citations = False`
forever. That is a structural fact about the provider, the same kind of permanent gap
`AdapterCitation`'s docstring (app/adapters/base.py) documents for `source_passage` on
Gemini/OpenAI and `cited_answer_span` on Anthropic — not a mapper that's missing work. Two
consequences elsewhere in the app follow directly from this and are NOT re-explained per call
site — see their own comments: `app/templates/runs/detail.html` shows a dedicated explanation
instead of the generic "no citations" text whenever `run.model.supports_web_search` is False, and
`app/services/dashboard.py`'s `_mention_visibility_base_query` excludes such models' runs from
`own_domain_rate` (a rate metric that would otherwise be silently deflated by runs that can never
be cited, regardless of how favorably the client was actually discussed).

Verified against a real `chat.completions.create()` call, 2026-09-23 (docs/TASKS_NEW_PROVIDERS.md
NP-T1, "Ověřené tvary odpovědí" → DeepSeek): confirmed real model ids `deepseek-flash` and
`deepseek-v4-pro` via `client.models.list()` (documentation's `deepseek-v4-flash` does not exist
there — deprecated). `usage.reasoning_content` on the message and
`usage.completion_tokens_details.reasoning_tokens` show DeepSeek reasons internally even without
an explicit "reasoning model" flag — those reasoning tokens are already inside `completion_tokens`
(no separate accounting needed, same as any other output tokens).
"""

import openai

from app.adapters.base import AdapterCitation, RawResponsePayload
from app.config import get_settings


def _map_citations(payload: dict) -> tuple[list[AdapterCitation], bool]:
    """Always `([], False)` — DeepSeek's Chat Completions response has no citation field of any
    kind (no `tool_calls`, `annotations` is `null`) to map. See module docstring for why this is
    permanent, not unfinished.
    """
    return [], False


def _map_search_queries(payload: dict) -> list[str]:
    """Always `[]` — DeepSeek's API has no search tool, so no query was ever issued. See module
    docstring.
    """
    return []


class DeepSeekAdapter:
    """Adapter for DeepSeek's OpenAI-compatible Chat Completions API."""

    def __init__(self) -> None:
        self._client = openai.OpenAI(base_url="https://api.deepseek.com", api_key=get_settings().deepseek_api_key)

    def run(
        self,
        prompt_text: str,
        model_name: str,
        *,
        system_instruction: str | None = None,
        market_country: str | None = None,
    ) -> RawResponsePayload:
        """Run `prompt_text` against `model_name`. No `web_search` tool exists to enable.

        `market_country` is accepted for interface parity but ignored — DeepSeek has no
        geographic search targeting of any kind (there's no search at all). `system_instruction`,
        if given, becomes a `system` role message, the Chat Completions convention (unlike the
        Responses API's top-level `instructions` field the other adapters use).

        Raises whatever the openai SDK raises on transport/API errors (timeout, auth failure,
        rate limit) — the caller records that as a Run with status='error'.
        """
        messages: list[dict] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt_text})

        response = self._client.chat.completions.create(model=model_name, messages=messages)

        rendered_text = response.choices[0].message.content if response.choices else None
        # Serialized once and handed to both the mappers and raw_payload, so what gets stored is
        # exactly what the (permanently empty) citations were derived from — same discipline as
        # every other adapter, even though the mappers here never read it.
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
