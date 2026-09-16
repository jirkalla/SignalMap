"""Shared shape every provider adapter must return.

A router never calls a provider SDK directly — always through an adapter
implementing `run()` below, so a provider can be swapped or mocked without
touching the rest of the app. See signalmap-conventions skill, "Backend
conventions".
"""

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse


def extract_domain(url: str | None) -> str | None:
    """Best-effort domain extraction from a citation URL; None if unparsable.

    Shared by every adapter that maps provider citations into
    AdapterCitation (app/adapters/google.py, app/adapters/anthropic.py) —
    lives here, not copy-pasted per adapter, since it's not provider-specific.
    """
    if not url:
        return None
    try:
        return urlparse(url).netloc or None
    except ValueError:
        return None


@dataclass
class AdapterCitation:
    """One claim-source link extracted from a provider's grounding/citation metadata.

    An instance is one (answer segment, source) pair, not one source: when a
    provider backs several answer segments with the same URL, or one segment
    with several URLs, every pair gets its own instance. `citation_position`
    is the running index of the pair in answer order, uniform across
    providers.

    This docstring is where the claim/source-passage boundary is defined for
    all three adapters (docs/TASKS_GEMINI_CITATIONS.md design decision 6) —
    the per-adapter docstrings point here instead of restating it:

    - `cited_answer_span` + `answer_span_start`/`answer_span_end` describe the
      span OF THE ANSWER the source supports: the model's own claim, and the
      offsets that locate it in the rendered answer text. The offsets are
      stored exactly as the provider returned them, and their UNIT IS
      PROVIDER-SPECIFIC — measured against stored payloads 2026-09-16, Gemini
      counts UTF-8 bytes (1316 of 1354 spans resolve only as bytes) while
      OpenAI counts characters (71 of 71). Slicing a Python string with a
      Gemini offset therefore goes wrong on the first non-ASCII character, and
      any consumer must either decode per provider or, simpler, match
      `cited_answer_span` as text — it always holds the exact span.
    - `source_passage` is the passage FROM THE SOURCE PAGE the provider quoted
      as backing for that claim.

    No provider fills both halves — they expose different sides of the same
    link (Gemini and OpenAI the answer span, Anthropic the source passage), so
    every field defaults to None and an adapter sets only what its API returns.
    Which adapter fills what, and why the gaps are permanent rather than
    unfinished, is on each adapter's own `_map_citations`.
    """

    source_url: str | None = None
    source_title: str | None = None
    source_domain: str | None = None
    citation_position: int | None = None
    cited_answer_span: str | None = None
    answer_span_start: int | None = None
    answer_span_end: int | None = None
    source_passage: str | None = None


@dataclass
class RawResponsePayload:
    """The canonical result of running one prompt against one AI model.

    `raw_payload` must be the complete, untouched provider response —
    never transformed before storing (FR-10). Everything else here is
    derived from it for convenience.
    """

    raw_payload: dict[str, Any]
    rendered_text: str | None
    has_citations: bool
    citations: list[AdapterCitation] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    token_usage: dict[str, Any] | None = None


class ProviderAdapter(Protocol):
    """Interface every provider adapter (app/adapters/<provider>.py) implements."""

    def run(
        self,
        prompt_text: str,
        model_name: str,
        *,
        system_instruction: str | None = None,
        market_country: str | None = None,
    ) -> RawResponsePayload:
        """Run one prompt against one model and return the canonical payload.

        `system_instruction`, when given, is a locale-framing hint built
        from the prompt's market (see app.routers.runs) — an adapter should
        apply it if the provider's API supports a system/persona layer, but
        it is not a substitute for real geographic search targeting where a
        provider's API offers one.

        `market_country` is the run's market's ISO 3166-1 alpha-2 country
        code (may be None), passed as a plain string — never a Market ORM
        object — so adapters stay decoupled from the DB layer. An adapter
        whose provider API offers real location targeting (e.g. Anthropic's
        `web_search` tool's `user_location`) should use it directly; one
        that doesn't (Gemini's Google Search grounding has no location
        parameter at all) accepts and ignores it, falling back to
        `system_instruction` as the best available signal instead.

        Raises on transport/API failure — callers are responsible for
        catching that and recording it as a Run with status='error'.
        """
        ...
