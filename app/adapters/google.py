"""Google Gemini provider adapter, with Google Search grounding enabled.

Maps the Gemini API's grounding metadata (grounding_chunks / grounding_supports)
into the project's canonical citation shape, while keeping the complete,
untouched SDK response for raw_payload (FR-10).
"""

from google import genai
from google.genai import types

from app.adapters.base import AdapterCitation, RawResponsePayload, extract_domain
from app.config import get_settings


def _resolve_source_domain(web: types.Web | None, source_url: str | None) -> str | None:
    """Pick the best available domain for one grounding chunk.

    Gemini's grounding chunks expose `web.uri` as an opaque
    vertexaisearch.cloud.google.com redirect link, not the publisher URL —
    parsing it never yields the real domain. The SDK's `web.domain` field is
    the right source when populated, but as observed it currently comes
    back None; `web.title` is, in practice, already the bare domain (e.g.
    "wikipedia.org") for Google Search grounding chunks, so it's the
    reliable fallback. URL-parsing is the last resort only.
    """
    domain = getattr(web, "domain", None) if web else None
    if domain:
        return domain
    title = getattr(web, "title", None) if web else None
    if title:
        return title
    return extract_domain(source_url)


def _map_citations(candidate: types.Candidate | None) -> tuple[list[AdapterCitation], bool]:
    """Turn one candidate's grounding metadata into canonical citations.

    Returns (citations, has_citations). has_citations is explicitly False
    (not just an empty list) whenever grounding metadata is absent, per
    FR-13 — the caller stores this flag directly, never inferring it from
    list length alone.
    """
    metadata = getattr(candidate, "grounding_metadata", None) if candidate else None
    chunks = getattr(metadata, "grounding_chunks", None) or []
    if not chunks:
        return [], False

    supports = getattr(metadata, "grounding_supports", None) or []

    citations: list[AdapterCitation] = []
    for position, chunk in enumerate(chunks):
        web = getattr(chunk, "web", None)
        source_url = getattr(web, "uri", None) if web else None
        source_title = getattr(web, "title", None) if web else None

        # First grounding_support whose grounding_chunk_indices references
        # this chunk gives us the answer span it backs, where exposed.
        cited_answer_span = None
        for support in supports:
            indices = getattr(support, "grounding_chunk_indices", None) or []
            if position in indices:
                segment = getattr(support, "segment", None)
                cited_answer_span = getattr(segment, "text", None) if segment else None
                break

        citations.append(
            AdapterCitation(
                source_url=source_url,
                source_title=source_title,
                source_domain=_resolve_source_domain(web, source_url),
                citation_position=position,
                cited_answer_span=cited_answer_span,
            )
        )

    return citations, True


def _map_search_queries(candidate: types.Candidate | None) -> list[str]:
    """Turn one candidate's grounding metadata into the search queries the model issued.

    Verified 2026-09-10 against the installed google-genai==2.22.0 SDK:
    GroundingMetadata.web_search_queries is `list[str] | None`, populated on
    the same grounding_metadata object _map_citations already reads
    grounding_chunks/grounding_supports from. Empty list, not an error, when
    grounding metadata is absent or the model didn't search.
    """
    metadata = getattr(candidate, "grounding_metadata", None) if candidate else None
    return list(getattr(metadata, "web_search_queries", None) or [])


class GoogleGeminiAdapter:
    """Adapter for the Google Gemini API (google-genai SDK)."""

    def __init__(self) -> None:
        self._client = genai.Client(api_key=get_settings().google_api_key)

    def run(
        self,
        prompt_text: str,
        model_name: str,
        *,
        system_instruction: str | None = None,
        market_country: str | None = None,
    ) -> RawResponsePayload:
        """Run `prompt_text` against `model_name` with Google Search grounding enabled.

        `system_instruction`, if given, is applied as-is — Gemini's
        Google Search grounding tool has no location/language API
        parameter (confirmed against the current API docs), so this is the
        only lever available here to nudge answer language and regional
        framing. It does not change what the underlying search retrieves.

        `market_country` is accepted (per the shared ProviderAdapter
        interface) but unused — there is nowhere to put it in Gemini's API.

        Raises whatever the google-genai SDK raises on transport/API errors
        (timeout, auth failure, rate limit) — the caller records that as a
        Run with status='error'.
        """
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            system_instruction=system_instruction,
        )
        response = self._client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config=config,
        )

        candidate = response.candidates[0] if response.candidates else None
        citations, has_citations = _map_citations(candidate)
        search_queries = _map_search_queries(candidate)

        token_usage = None
        if response.usage_metadata is not None:
            token_usage = response.usage_metadata.model_dump(mode="json")

        return RawResponsePayload(
            raw_payload=response.model_dump(mode="json"),
            rendered_text=response.text,
            has_citations=has_citations,
            citations=citations,
            search_queries=search_queries,
            token_usage=token_usage,
        )
