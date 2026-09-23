"""Google Gemini provider adapter, with Google Search grounding enabled.

Maps the Gemini API's grounding metadata (grounding_chunks / grounding_supports)
into the project's canonical citation shape, while keeping the complete,
untouched SDK response for raw_payload (FR-10).

The mapping functions take the serialized response dict
(`response.model_dump(mode="json")`) rather than SDK objects, so the exact same
function can be replayed over a `raw_responses.raw_payload` row stored earlier
— which is how the GC-T3 backfill recomputes historical citations, and how
roadmap #12 will re-extract them again later. One function, two callers,
instead of a live mapper and a drifting re-extraction copy of it
(docs/TASKS_GEMINI_CITATIONS.md design decision 5).
"""

from google import genai
from google.genai import types

from app.adapters.base import AdapterCitation, RawResponsePayload, extract_domain
from app.config import get_settings


def _resolve_source_domain(web: dict | None, source_url: str | None) -> str | None:
    """Pick the best available domain for one grounding chunk.

    Gemini's grounding chunks expose `web.uri` as an opaque
    vertexaisearch.cloud.google.com redirect link, not the publisher URL —
    parsing it never yields the real domain. The `domain` field is the right
    source when populated, but as observed it currently comes back None;
    `title` is, in practice, already the bare domain (e.g. "wikipedia.org")
    for Google Search grounding chunks, so it's the reliable fallback.
    URL-parsing is the last resort only.
    """
    domain = web.get("domain") if web else None
    if domain:
        return domain
    title = web.get("title") if web else None
    if title:
        return title
    return extract_domain(source_url)


def _grounding_metadata(payload: dict) -> dict:
    """The first candidate's grounding_metadata, or an empty dict.

    Both mappers below start here, so the "no candidates / no metadata" shape
    is handled once rather than twice.
    """
    candidates = payload.get("candidates") or []
    if not candidates:
        return {}
    return (candidates[0] or {}).get("grounding_metadata") or {}


def _citation_from_chunk(
    chunk: dict,
    position: int,
    *,
    cited_answer_span: str | None = None,
    answer_span_start: int | None = None,
    answer_span_end: int | None = None,
) -> AdapterCitation:
    """Build one AdapterCitation from a grounding chunk plus the span it backs."""
    web = chunk.get("web") or {}
    source_url = web.get("uri")
    return AdapterCitation(
        source_url=source_url,
        source_title=web.get("title"),
        source_domain=_resolve_source_domain(web, source_url),
        citation_position=position,
        cited_answer_span=cited_answer_span,
        answer_span_start=answer_span_start,
        answer_span_end=answer_span_end,
        # Gemini returns no quoted passage from the source page, only the
        # answer segment — see AdapterCitation's docstring for the boundary.
        # Filling this is roadmap #12's job (it fetches the page itself).
        source_passage=None,
    )


def _map_citations(payload: dict) -> tuple[list[AdapterCitation], bool]:
    """Turn a serialized Gemini response's grounding metadata into canonical citations.

    Gemini expresses grounding as a many-to-many relation: `grounding_chunks`
    lists the sources, `grounding_supports` lists answer segments, and each
    support's `grounding_chunk_indices` names every source backing that one
    segment. This walks the SUPPORTS and emits one citation per
    (support, chunk index) pair, so a segment backed by three sources becomes
    three rows and a source backing five segments likewise becomes five.

    That is the fix this function exists for: the previous version walked the
    chunks instead and kept only the first support referencing each one, which
    dropped 803 of 1354 real claim-source pairs across production data, in 40
    of 52 answers (docs/TASKS_GEMINI_CITATIONS.md design decision 1).

    Pairs are ordered by where they appear in the answer, so
    `citation_position` means the same thing here as in the Anthropic and
    OpenAI adapters. `segment.start_index` is absent on the answer's first
    segment (30 of 740 supports) and means 0 there, not "unknown" — it is
    normalized before both sorting and storing, since ordering None against an
    int would otherwise raise TypeError (design decision 2).

    The offsets are stored as returned, and Gemini returns UTF-8 BYTE offsets,
    not character offsets: replaying this mapper over the 52 stored responses
    on 2026-09-16, 1316 of 1354 spans matched only
    `rendered_text.encode("utf-8")[start:end]`, and the 38 that also matched a
    plain string slice were ASCII-only spans where the two coincide. OpenAI's
    equivalent offsets are character offsets, so the two providers' values are
    not in the same unit — see AdapterCitation's docstring. `cited_answer_span`
    always carries the exact text, so text matching is the portable option.

    A chunk no support points at still gets a row, appended after every pair in
    the provider's own chunk order, with no answer span: a source the model
    found but attached to no specific claim is evidence too (FR-12/FR-13), and
    walking supports must not be able to silently drop one. Production data has
    none of these today; this is a guard, not a repair (design decision 3).

    Duplicate URLs are kept, not deduplicated — one row is one link the
    provider actually returned (design decision 4).

    Returns (citations, has_citations). has_citations is explicitly False (not
    just an empty list) whenever grounding metadata is absent, per FR-13 — the
    caller stores this flag directly, never inferring it from list length
    alone. A payload with no grounding_metadata at all (e.g. the different
    response shape Gemini 3 is documented to return, design decision 9) returns
    ([], False) rather than raising.
    """
    metadata = _grounding_metadata(payload)
    chunks = metadata.get("grounding_chunks") or []
    if not chunks:
        return [], False

    supports = metadata.get("grounding_supports") or []

    # (answer_span_start, answer_span_end, chunk_index, segment_text)
    pairs: list[tuple[int, int | None, int, str | None]] = []
    cited_chunk_indices: set[int] = set()
    for support in supports:
        segment = (support or {}).get("segment") or {}
        # `or 0`, not `is None`: absent and 0 mean the same "answer starts here".
        start = segment.get("start_index") or 0
        end = segment.get("end_index")
        text = segment.get("text")
        for chunk_index in (support or {}).get("grounding_chunk_indices") or []:
            # A malformed payload pointing past the chunk list loses that one
            # pair rather than the whole response.
            if not isinstance(chunk_index, int) or not 0 <= chunk_index < len(chunks):
                continue
            pairs.append((start, end, chunk_index, text))
            cited_chunk_indices.add(chunk_index)

    # end_index is never null in observed data, but sorting must not depend on
    # that; chunk_index is the final tiebreak so equal offsets stay stable.
    pairs.sort(key=lambda pair: (pair[0], pair[1] if pair[1] is not None else -1, pair[2]))

    citations = [
        _citation_from_chunk(
            chunks[chunk_index] or {},
            position,
            cited_answer_span=text,
            answer_span_start=start,
            answer_span_end=end,
        )
        for position, (start, end, chunk_index, text) in enumerate(pairs)
    ]

    for chunk_index, chunk in enumerate(chunks):
        if chunk_index not in cited_chunk_indices:
            citations.append(_citation_from_chunk(chunk or {}, len(citations)))

    return citations, True


def _map_search_queries(payload: dict) -> list[str]:
    """Turn a serialized Gemini response's grounding metadata into the search queries the model issued.

    Verified 2026-09-10 against the installed google-genai==2.22.0 SDK:
    GroundingMetadata.web_search_queries is `list[str] | None`, populated on
    the same grounding_metadata object _map_citations already reads
    grounding_chunks/grounding_supports from. Empty list, not an error, when
    grounding metadata is absent or the model didn't search.
    """
    return list(_grounding_metadata(payload).get("web_search_queries") or [])


class GoogleGeminiAdapter:
    """Adapter for the Google Gemini API (google-genai SDK)."""

    # See ProviderAdapter.supports_geo_targeting (app/adapters/base.py) — Google Search grounding
    # has no location parameter at all, so `market_country` is accepted and ignored (module docstring).
    supports_geo_targeting = False

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

        # Serialized once and handed to both the mappers and raw_payload, so
        # what gets stored is exactly what the citations were derived from.
        payload = response.model_dump(mode="json")
        citations, has_citations = _map_citations(payload)
        search_queries = _map_search_queries(payload)

        token_usage = None
        if response.usage_metadata is not None:
            token_usage = response.usage_metadata.model_dump(mode="json")

        return RawResponsePayload(
            raw_payload=payload,
            rendered_text=response.text,
            has_citations=has_citations,
            citations=citations,
            search_queries=search_queries,
            token_usage=token_usage,
        )
