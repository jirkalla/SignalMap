"""Derive the cited CLAIM (the model's own sentence) from a citation, per provider.

docs/TASKS_CITATION_VERIFICATION.md T1, design decisions 4-5. A provider's citation metadata
points at different halves of the (claim, source) link — see app/adapters/base.py's
AdapterCitation docstring for that boundary. Anthropic gives the source passage but not the
claim; OpenAI and Gemini give an answer-text span, but OpenAI's is only a link marker
(`([domain](url))`) and Gemini's is often a fragment cut mid-sentence. `derive_claim` recovers
the actual claim sentence for all three, so the analyst sees what the model claimed, not what
the provider happened to expose.

A pure function, not a stored column (design decision 5): the claim is computed at display/export
time (T2) from data already in `raw_responses.raw_payload` and `citations`, so it never needs a
migration or backfill, and improving the derivation later changes nothing about stored evidence.
It only later gets persisted once, in `citation_verifications.claim_text` (T7), alongside which
method produced it.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _CitationLike(Protocol):
    """The subset of app.models.run.Citation this module reads.

    A Protocol, not the ORM class itself, so tests can pass a bare `Citation(...)` built without
    a session (or any other object with these attributes) without pulling SQLAlchemy machinery
    into what is otherwise a pure-function unit test.
    """

    citation_position: int | None
    source_url: str | None
    cited_answer_span: str | None
    answer_span_start: int | None
    answer_span_end: int | None


@dataclass(frozen=True)
class DerivedClaim:
    """One derived claim: the text, its span in `rendered_text`, and how it was derived.

    `start`/`end` are character offsets into `rendered_text` (never provider-specific byte
    offsets — see AdapterCitation's docstring on why offsets must never be assumed portable
    across providers). `method` is one of "anthropic_block", "openai_before_marker",
    "gemini_sentence" — recorded so T7's `citation_verifications.claim_method` can distinguish
    them without re-deriving.
    """

    text: str
    start: int
    end: int
    method: str


# A citation marker as every adapter's rendered_text literally contains it for OpenAI
# (`([title](url))`, app/adapters/openai.py) — used only to find the END of a PRECEDING marker,
# not the one this citation is itself about (that one is always `citation.answer_span_start`).
_MARKER_RE = re.compile(r"\(\[[^\]]*\]\([^)]*\)\)")

# End of a sentence: terminator, optional closing quote/bracket, then whitespace.
_SENTENCE_END_RE = re.compile(r"[.!?][\"'“”\)\]]?\s")

# A word character immediately preceding a "." match — used to tell a real sentence end from a
# one-letter abbreviation ("z. B. Carbonbeton" / "u. a.", verified against run 422: without this,
# "Betontechnologien (z. B. Carbonbeton)" reads as two sentence ends, "z." and "B.", and the
# claim gets truncated to "B. Carbonbeton)..." instead of the full list item).
_WORD_CHAR_RE = re.compile(r"\w")

# Leading markdown/list noise to strip off a derived claim's front: bold/italic markers, heading
# hashes, a bullet (-, *, +) or a numbered-list marker, or a table-cell pipe.
_LEADING_TOKEN_RE = re.compile(r"\A(?:\*\*|__|#+\s*|[-*+]\s+|\d+[.)]\s+|\|\s*)")

# Below this length a derived OpenAI claim is considered too short to be useful on its own
# (usually a mid-sentence fragment) — design decision 4 point 3: also take the previous sentence.
_MIN_OPENAI_CLAIM_LENGTH = 15


def derive_claim(
    provider_code: str,
    citation: _CitationLike,
    rendered_text: str | None,
    raw_payload: dict[str, Any],
) -> DerivedClaim | None:
    """Derive the answer claim `citation` backs, or None when it can't be derived.

    Returns None (never a guess) when: `rendered_text` is empty, the provider has no way to
    derive a claim (Perplexity, xAI — neither API links a source to a specific sentence), or the
    provider-specific derivation itself can't establish a reliable match (see each `_derive_*`
    function for what that means for it).
    """
    if not rendered_text:
        return None
    if provider_code == "anthropic":
        return _derive_anthropic_claim(citation, rendered_text, raw_payload)
    if provider_code == "openai":
        return _derive_openai_claim(citation, rendered_text)
    if provider_code == "google_gemini":
        return _derive_gemini_claim(citation, rendered_text)
    return None


def _derive_anthropic_claim(
    citation: _CitationLike, rendered_text: str, raw_payload: dict[str, Any]
) -> DerivedClaim | None:
    """The full text of the block `citation` was attached to (design decision 4).

    `rendered_text` is exactly `"".join(block["text"] for block in content if type=="text")`
    (app/adapters/anthropic.py's `run`), so a text block's offset in it is the sum of every
    earlier text block's length. Citations are matched to blocks by `citation_position`, walking
    blocks-then-citations-in-block in the same order `_map_citations` assigned that position in
    — verified against a real stored response (run 108: all 14 citations line up positionally).

    Returns None and logs, rather than guessing, when the payload's citation at that position
    doesn't carry the same `url` the DB row has (design decision 4 point 2) — a payload that has
    been reshaped since the row was written (a future re-extraction, a hand-edited fixture) must
    not silently produce a claim attributed to the wrong source.
    """
    text_blocks = [block for block in (raw_payload.get("content") or []) if (block or {}).get("type") == "text"]

    flattened: list[tuple[int, dict[str, Any]]] = []
    for block_index, block in enumerate(text_blocks):
        for block_citation in block.get("citations") or []:
            flattened.append((block_index, block_citation))

    position = citation.citation_position
    if position is None or not 0 <= position < len(flattened):
        logger.warning(
            "claims: anthropic citation_position %r out of range (%d citations found in payload)",
            position,
            len(flattened),
        )
        return None

    block_index, payload_citation = flattened[position]
    if payload_citation.get("url") != citation.source_url:
        logger.warning(
            "claims: anthropic citation %s url mismatch (db=%r, payload=%r) — not deriving a claim",
            position,
            citation.source_url,
            payload_citation.get("url"),
        )
        return None

    block_start = sum(len(block.get("text") or "") for block in text_blocks[:block_index])
    block_text = text_blocks[block_index].get("text") or ""
    stripped = block_text.strip()
    if not stripped:
        return None
    leading_ws = len(block_text) - len(block_text.lstrip())
    start = block_start + leading_ws
    return DerivedClaim(text=stripped, start=start, end=start + len(stripped), method="anthropic_block")


def _preceding_word_length(text: str, pos: int) -> int:
    """The length of the run of word characters immediately before `text[pos]`.

    Used to reject a "." match whose preceding token is one letter long — a near-universal sign
    of an abbreviation ("z. B.", "u. a.") rather than a real sentence end.
    """
    i = pos
    while i > 0 and _WORD_CHAR_RE.match(text[i - 1]):
        i -= 1
    return pos - i


def _boundary_before(text: str, pos: int) -> int:
    """The nearest claim-start boundary before `pos`: end of the previous sentence, line, table

    cell, or citation marker (design decision 4 point 3) — whichever is closest to `pos`.

    Candidates are tried closest-first and skipped when the text between them and `pos` is blank
    (design decision 4 point 3's "own" case: an OpenAI marker sits immediately after the CLAIM's
    own terminating period, e.g. "...Städten. ([...))" — the nearest sentence-end match is that
    same period, which is the END of the claim, not its start; walking to the next-closest
    candidate the first time that happens is what actually finds the start of the sentence the
    claim is part of). Falls back to 0 (start of text) when every candidate is exhausted.
    """
    candidates = [
        match.end()
        for match in _SENTENCE_END_RE.finditer(text, 0, pos)
        if _preceding_word_length(text, match.start()) > 1
    ]
    newline = text.rfind("\n", 0, pos)
    if newline != -1:
        candidates.append(newline + 1)
    pipe = text.rfind("|", 0, pos)
    if pipe != -1:
        candidates.append(pipe + 1)
    candidates.extend(match.end() for match in _MARKER_RE.finditer(text, 0, pos))

    for candidate in sorted(candidates, reverse=True):
        if text[candidate:pos].strip():
            return candidate
    return 0


def _clean_leading(raw: str) -> str:
    """Strip surrounding whitespace and leading markdown/list noise (design decision 4).

    Only ever removes from the FRONT and BACK, never the middle — the result stays a literal
    substring of `raw` at an unbroken run of characters, so callers can relocate its offsets with
    a plain `str.find` instead of re-deriving them.
    """
    cleaned = raw.strip()
    while True:
        match = _LEADING_TOKEN_RE.match(cleaned)
        if not match:
            return cleaned
        cleaned = cleaned[match.end() :].lstrip()


def _derive_openai_claim(citation: _CitationLike, rendered_text: str) -> DerivedClaim | None:
    """The sentence immediately before the citation's link marker (design decision 4).

    `answer_span_start`/`answer_span_end` are OpenAI's own character offsets into the answer
    text (app/adapters/openai.py) and, for a single-message response, into `rendered_text`
    itself — `cited_answer_span` is exactly `rendered_text[start:end]`, the marker
    `([domain](url))`, not a claim (that mislabeling is what T1/T2 exist to fix). The claim is
    whatever text sits between the nearest preceding boundary and that marker; when it comes out
    under `_MIN_OPENAI_CLAIM_LENGTH` characters (usually a fragment like "sein." from a boundary
    that landed mid-clause), the previous sentence is pulled in too.
    """
    start_index = citation.answer_span_start
    end_index = citation.answer_span_end
    if start_index is None or end_index is None:
        return None
    if not 0 <= start_index <= end_index <= len(rendered_text):
        return None

    boundary = _boundary_before(rendered_text, start_index)
    claim = _clean_leading(rendered_text[boundary:start_index])
    if len(claim) < _MIN_OPENAI_CLAIM_LENGTH and boundary > 0:
        earlier_boundary = _boundary_before(rendered_text, boundary)
        if earlier_boundary < boundary:
            claim = _clean_leading(rendered_text[earlier_boundary:start_index])
            boundary = earlier_boundary

    if not claim:
        return None
    real_start = rendered_text.find(claim, boundary, start_index)
    if real_start == -1:
        return None
    return DerivedClaim(
        text=claim, start=real_start, end=real_start + len(claim), method="openai_before_marker"
    )


def _derive_gemini_claim(citation: _CitationLike, rendered_text: str) -> DerivedClaim | None:
    """`cited_answer_span` expanded to the full sentence / list item it's part of (design decision 4).

    Gemini's `start_index`/`end_index` are UTF-8 BYTE offsets (app/adapters/google.py), not
    character offsets, so they are never used to slice `rendered_text` here — `cited_answer_span`
    is instead located as TEXT (`str.find`), which is offset-unit-agnostic and always exact
    (it's the provider's own segment text, copied verbatim). The found span is then widened
    backward to the previous sentence/line boundary and forward to the next sentence terminator,
    since Gemini's segments are frequently cut mid-sentence (verified against run 422: segment
    "Carbonbeton), um den Materialeinsatz zu optimieren" widens to the complete list item
    "Große Baukonzerne wie ... um den Materialeinsatz zu optimieren.").
    """
    segment = citation.cited_answer_span
    if not segment:
        return None

    start = rendered_text.find(segment)
    if start == -1:
        logger.warning(
            "claims: gemini segment for citation %s not found in rendered_text as text",
            citation.citation_position,
        )
        return None
    segment_end = start + len(segment)

    forward_end = len(rendered_text)
    for i in range(segment_end, len(rendered_text)):
        char = rendered_text[i]
        if char in ".!?":
            forward_end = i + 1
            break
        if char == "\n":
            forward_end = i
            break

    boundary = _boundary_before(rendered_text, start)
    claim = _clean_leading(rendered_text[boundary:forward_end])
    if not claim:
        return None
    real_start = rendered_text.find(claim, boundary, forward_end + 1)
    if real_start == -1:
        return None
    return DerivedClaim(text=claim, start=real_start, end=real_start + len(claim), method="gemini_sentence")
