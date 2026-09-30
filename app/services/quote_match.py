"""Deterministic literal-quote verification (docs/TASKS_CITATION_VERIFICATION.md T8, design

decisions 15-17) — pure text functions, no DB, no network. `citation_verification.py` is the
only caller; kept separate so the matching algorithm has its own focused test file
(tests/test_quote_match.py) independent of DB/ORM setup.

Three layers:
  - `normalize` — form-only normalization (typography, whitespace, HTML entities) that keeps a
    map back to the ORIGINAL text's offsets, so a match found in normalized space can still be
    reported with real offsets into the real page text. Never touches words or numbers.
  - `split_into_chunks` — a provider's citation quote is broken into pieces at the separators
    that provider uses to join non-contiguous source text (design decision 16); every piece
    must be found for the quote to count as fully verified, and a piece under 20 characters is
    too short to check meaningfully and is dropped.
  - `find_chunk` / `match_quote` — locate each chunk in the captured source text: exact match in
    normalized space first, then a similarity-based fallback (design decision 17) gated by the
    NUMBER RULE — a fuzzy match is rejected outright if the numeric sequences in the chunk and
    the matched window don't agree exactly ("150 Wohnungen" must never pass against "50
    Wohnungen" just because the rest of the sentence is a 95% textual match).
"""

import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

# --- normalization --------------------------------------------------------------------------

_ENTITY_RE = re.compile(r"&(#x?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")

_QUOTE_MAP = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‹": "'", "›": "'",
    "`": "'", "´": "'",
}
_DASH_MAP = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
}
_SOFT_HYPHEN = "­"
_SHARP_S = ("ß", "ẞ")  # ß, ẞ


@dataclass(frozen=True)
class NormalizedText:
    """`text` is the normalized form; `offsets[i]` is the index in the ORIGINAL text that

    `text[i]` was produced from — an expansion (ß -> ss, an entity -> its decoded chars) repeats
    the same original index for every character it produces; a deletion (a soft hyphen) simply
    contributes no output character and no offset entry.
    """

    text: str
    offsets: list[int]

    def to_original_range(self, start: int, end: int, original_length: int) -> tuple[int, int]:
        """Map a `[start, end)` range in `self.text` back to a range in the original text.

        The end bound is the offset of the NEXT normalized character after the match (or the
        original text's own length, if the match runs to the end) — not `offsets[end-1] + 1` —
        so a match ending mid-expansion (e.g. inside a decoded `&amp;`) still reports the FULL
        original span it came from, not just its first original character.
        """
        original_start = self.offsets[start]
        original_end = self.offsets[end] if end < len(self.offsets) else original_length
        return original_start, original_end


def _decode_entities(text: str) -> tuple[str, list[int]]:
    """HTML-entity decode with offset tracking — `html.unescape` on the whole string would lose

    the position mapping this module needs, so entities are decoded one match at a time instead.
    """
    out: list[str] = []
    offsets: list[int] = []
    pos = 0
    for match in _ENTITY_RE.finditer(text):
        for i in range(pos, match.start()):
            out.append(text[i])
            offsets.append(i)
        for ch in html.unescape(match.group(0)):
            out.append(ch)
            offsets.append(match.start())
        pos = match.end()
    for i in range(pos, len(text)):
        out.append(text[i])
        offsets.append(i)
    return "".join(out), offsets


def _expand_char(ch: str) -> str:
    """Form-only per-character expansion — everything design decision 15 asks for except

    whitespace collapsing and lowercasing, which need to see runs/the whole string.
    """
    if ch == _SOFT_HYPHEN:
        return ""
    if ch in _SHARP_S:
        return "ss"
    if ch in _QUOTE_MAP:
        return _QUOTE_MAP[ch]
    if ch in _DASH_MAP:
        return _DASH_MAP[ch]
    return unicodedata.normalize("NFKC", ch)


def normalize(text: str) -> NormalizedText:
    """Normalize `text` for matching, keeping an offset map back to `text` itself.

    Order: decode HTML entities, expand form-variant characters (quotes/dashes/soft hyphen/ß/
    NFKC) and lowercase, then collapse whitespace runs to a single space and strip the ends.
    Never changes a letter's identity or a digit — only typography, case, and whitespace.
    """
    decoded, decoded_offsets = _decode_entities(text)

    expanded_chars: list[str] = []
    expanded_offsets: list[int] = []
    for ch, offset in zip(decoded, decoded_offsets):
        for out_ch in _expand_char(ch):
            expanded_chars.append(out_ch.lower())
            expanded_offsets.append(offset)

    collapsed_chars: list[str] = []
    collapsed_offsets: list[int] = []
    prev_was_space = False
    for ch, offset in zip(expanded_chars, expanded_offsets):
        if ch.isspace():
            if prev_was_space:
                continue
            ch, prev_was_space = " ", True
        else:
            prev_was_space = False
        collapsed_chars.append(ch)
        collapsed_offsets.append(offset)

    start = 0
    end = len(collapsed_chars)
    while start < end and collapsed_chars[start] == " ":
        start += 1
    while end > start and collapsed_chars[end - 1] == " ":
        end -= 1

    return NormalizedText(text="".join(collapsed_chars[start:end]), offsets=collapsed_offsets[start:end])


# --- chunking (design decision 16) -----------------------------------------------------------

_MIN_CHUNK_LENGTH = 20

# Anthropic joins non-contiguous source excerpts with an ellipsis or a middle dot — verified
# against a real stored citation (run 108 #5: "...um den großen Herausforderungen · unserer
# Z..."). "..." is the literal three-period form (as opposed to the single "…" codepoint) —
# both appear in real provider output, so both are split on.
_BASE_SPLIT_RE = re.compile(r"\.\.\.|…|\s+·\s+")

# Perplexity additionally renders its snippet as Markdown over multiple lines (verified against
# a real run 290 snippet: "- **Top: Škoda Enyaq bekommt die Testnote 1,6**", table rows like
# "|Fahreigenschaften|2,4|") — newlines and table-cell pipes are extra split points, and leading
# bullet/heading markers plus bold/italic markers are stripped from what's left of each piece.
_PERPLEXITY_SPLIT_RE = re.compile(r"\.\.\.|…|\s+·\s+|\n+|\|")
_MARKDOWN_STRIP_RE = re.compile(r"\*\*|__|^#{1,6}\s*|^[-*+]\s+", re.MULTILINE)


def split_into_chunks(text: str, provider_code: str) -> list[str]:
    """Split a provider's citation quote into checkable pieces — every piece long enough to

    matter (`>= _MIN_CHUNK_LENGTH` chars, design decision 16) must be found for the quote to
    verify; a chunk this function drops is never silently treated as "found".
    """
    pattern = _PERPLEXITY_SPLIT_RE if provider_code == "perplexity" else _BASE_SPLIT_RE
    chunks = []
    for piece in pattern.split(text):
        cleaned = _MARKDOWN_STRIP_RE.sub("", piece) if provider_code == "perplexity" else piece
        cleaned = cleaned.strip()
        if len(cleaned) >= _MIN_CHUNK_LENGTH:
            chunks.append(cleaned)
    return chunks


# --- matching (design decision 17) ------------------------------------------------------------

_DIGIT_SEQUENCE_RE = re.compile(r"\d+")
_SIMILARITY_THRESHOLD = 0.9


@dataclass(frozen=True)
class ChunkMatch:
    """The result of locating one chunk in one source text."""

    chunk: str
    kind: str  # "exact" | "fuzzy" | "not_found"
    similarity: float | None
    matched_text: str | None
    match_start: int | None
    match_end: int | None


def _numbers_preserved(chunk_normalized: str, window_normalized: str) -> bool:
    """Every numeric sequence in the chunk must appear, unchanged and in order, in the matched

    window — "150 Wohnungen" must never be accepted as a fuzzy match for "50 Wohnungen" just
    because the surrounding text is a 95%+ textual match (design decision 17's own example).
    """
    return _DIGIT_SEQUENCE_RE.findall(chunk_normalized) == _DIGIT_SEQUENCE_RE.findall(window_normalized)


def _best_fuzzy_window(chunk: str, source: str) -> tuple[int, int, float] | None:
    """The best same-length-as-chunk window of `source` for `chunk`, if its ratio clears

    `_SIMILARITY_THRESHOLD` — anchored on the longest common matching block `SequenceMatcher`
    finds between the whole chunk and the whole source, rather than sliding a window across
    the entire source at every offset (real pages run to tens of thousands of characters;
    `SequenceMatcher.ratio()` at every offset would be far more work than this needs for text
    that, per design decision 17, differs from the source by only minor residual formatting).
    """
    if not chunk:
        return None
    blocks = SequenceMatcher(None, source, chunk, autojunk=False).get_matching_blocks()
    longest = max(blocks, key=lambda b: b.size, default=None)
    if longest is None or longest.size == 0:
        return None

    window_start = max(0, longest.a - longest.b)
    window_end = min(len(source), window_start + len(chunk))
    window_start = max(0, window_end - len(chunk))
    candidate = source[window_start:window_end]

    ratio = SequenceMatcher(None, candidate, chunk, autojunk=False).ratio()
    if ratio < _SIMILARITY_THRESHOLD:
        return None
    return window_start, window_end, ratio


def find_chunk(chunk: str, source_text: str) -> ChunkMatch:
    """Locate one chunk in `source_text`: exact match in normalized space, else a fuzzy window

    gated by the number rule, else `not_found`. `matched_text`/`match_start`/`match_end` are
    always in `source_text`'s own (non-normalized) coordinates.
    """
    normalized_chunk = normalize(chunk)
    normalized_source = normalize(source_text)

    exact_index = normalized_source.text.find(normalized_chunk.text)
    if exact_index != -1:
        start, end = normalized_source.to_original_range(
            exact_index, exact_index + len(normalized_chunk.text), len(source_text)
        )
        return ChunkMatch(chunk=chunk, kind="exact", similarity=1.0, matched_text=source_text[start:end], match_start=start, match_end=end)

    fuzzy = _best_fuzzy_window(normalized_chunk.text, normalized_source.text)
    if fuzzy is not None:
        norm_start, norm_end, ratio = fuzzy
        if _numbers_preserved(normalized_chunk.text, normalized_source.text[norm_start:norm_end]):
            start, end = normalized_source.to_original_range(norm_start, norm_end, len(source_text))
            return ChunkMatch(chunk=chunk, kind="fuzzy", similarity=ratio, matched_text=source_text[start:end], match_start=start, match_end=end)

    return ChunkMatch(chunk=chunk, kind="not_found", similarity=None, matched_text=None, match_start=None, match_end=None)


@dataclass(frozen=True)
class QuoteMatchResult:
    """The overall result of checking a whole (possibly multi-chunk) quote against one source.

    `matched_text`/`match_start`/`match_end` describe the FIRST found chunk only — good enough
    for "jump to this location" (design decision 18); the full per-chunk detail is in
    `fragments` for anything that needs it (T9's evidence panel).
    """

    verdict: str
    fragments: list[ChunkMatch]
    matched_text: str | None
    match_start: int | None
    match_end: int | None
    similarity: float | None


def match_quote(quote: str, provider_code: str, source_text: str) -> QuoteMatchResult | None:
    """Check `quote` (a provider's raw citation text) against `source_text` (a captured page's

    extracted text). Returns None when `quote` has no chunk long enough to check at all — that
    is "nothing to verify", never silently "not found".
    """
    chunks = split_into_chunks(quote, provider_code)
    if not chunks:
        return None

    fragments = [find_chunk(chunk, source_text) for chunk in chunks]
    found = [f for f in fragments if f.kind != "not_found"]

    if not found:
        verdict = "not_found"
    elif len(found) < len(fragments):
        verdict = "partially_found"
    elif all(f.kind == "exact" for f in fragments):
        verdict = "verified_exact"
    else:
        verdict = "verified_normalized"

    primary = found[0] if found else None
    similarities = [f.similarity for f in found if f.similarity is not None]
    return QuoteMatchResult(
        verdict=verdict,
        fragments=fragments,
        matched_text=primary.matched_text if primary else None,
        match_start=primary.match_start if primary else None,
        match_end=primary.match_end if primary else None,
        similarity=min(similarities) if similarities else None,
    )
