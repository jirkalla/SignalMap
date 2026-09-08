"""Google Gemini provider adapter, with Google Search grounding enabled.

Maps the Gemini API's grounding metadata (grounding_chunks / grounding_supports)
into the project's canonical citation shape, while keeping the complete,
untouched SDK response for raw_payload (FR-10).
"""

from urllib.parse import urlparse

from google import genai
from google.genai import types

from app.adapters.base import AdapterCitation, RawResponsePayload
from app.config import get_settings


def _extract_domain(url: str | None) -> str | None:
    """Best-effort domain extraction from a citation URL; None if unparsable."""
    if not url:
        return None
    try:
        return urlparse(url).netloc or None
    except ValueError:
        return None


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
    return _extract_domain(source_url)


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


class GoogleGeminiAdapter:
    """Adapter for the Google Gemini API (google-genai SDK)."""

    def __init__(self) -> None:
        self._client = genai.Client(api_key=get_settings().google_api_key)

    def run(self, prompt_text: str, model_name: str) -> RawResponsePayload:
        """Run `prompt_text` against `model_name` with Google Search grounding enabled.

        Raises whatever the google-genai SDK raises on transport/API errors
        (timeout, auth failure, rate limit) — the caller records that as a
        Run with status='error'.
        """
        config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        response = self._client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config=config,
        )

        candidate = response.candidates[0] if response.candidates else None
        citations, has_citations = _map_citations(candidate)

        token_usage = None
        if response.usage_metadata is not None:
            token_usage = response.usage_metadata.model_dump(mode="json")

        return RawResponsePayload(
            raw_payload=response.model_dump(mode="json"),
            rendered_text=response.text,
            has_citations=has_citations,
            citations=citations,
            token_usage=token_usage,
        )
