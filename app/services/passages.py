"""BM25 top-k passage selection over a captured page's text (docs/TASKS_CITATION_VERIFICATION.md

T12, design decision 21) — pure text functions, no DB, no network, same "own focused test file,
no ORM setup needed" split as app/services/quote_match.py (T8).

No new dependency: a small, self-contained Okapi BM25 (k1=1.5, b=0.75, the standard defaults),
the same choice quote_match.py already made for fuzzy matching (its own SequenceMatcher-based
matcher, not a fuzzy-matching library) — this module follows that precedent rather than pulling
in `rank_bm25` for one focused use.

Candidates are every sentence of the page AND every adjacent pair of sentences ("věty nebo
dvojice vět", design decision 21) — a claim's supporting text often spans a sentence boundary, so
scoring only single sentences would sometimes miss it. Verified against the citation-verification
prototype (claude.ai/artifact/39VLVcB4u27cvpv87PRcvs): its own stored "candidate" for run 311 is
exactly a two-sentence window with its own score/coverage fields, confirming this shape.
"""

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Splits after a sentence-ending punctuation mark, only when followed by whitespace and then an
# uppercase letter, a digit, or an opening quote — reduces (not eliminates) false splits on
# common abbreviations ("z.B. Holz" stays one candidate; "Holz. Beton" splits). Not a linguistic
# sentence tokenizer, just a pragmatic split good enough for BM25 candidate boundaries — the same
# "good enough at the margins" pragmatism quote_match.py's own docstring already accepts for its
# 20-character minimum chunk length.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9„\"'])")

_K1 = 1.5
_B = 0.75

_DEFAULT_K = 5
_DEFAULT_MAX_CHARS = 6000


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


def _bm25_scores(query_tokens: list[str], doc_token_lists: list[list[str]]) -> list[float]:
    """Okapi BM25 score of `query_tokens` against each entry of `doc_token_lists` — the "corpus"

    for IDF purposes is this one page's own candidate passages, since ranking only ever happens
    within one page at a time (there is no cross-page corpus to build here).
    """
    n = len(doc_token_lists)
    if n == 0 or not query_tokens:
        return [0.0] * n
    avg_len = sum(len(d) for d in doc_token_lists) / n

    doc_freq: Counter[str] = Counter()
    for doc in doc_token_lists:
        doc_freq.update(set(doc))

    scores: list[float] = []
    for doc in doc_token_lists:
        term_freq = Counter(doc)
        doc_len = len(doc)
        score = 0.0
        for term in query_tokens:
            tf = term_freq.get(term, 0)
            if tf == 0:
                continue
            n_q = doc_freq.get(term, 0)
            idf = math.log(1 + (n - n_q + 0.5) / (n_q + 0.5))
            denom = tf + _K1 * (1 - _B + _B * doc_len / avg_len)
            score += idf * (tf * (_K1 + 1)) / denom
        scores.append(score)
    return scores


def select_passages(claim_text: str, source_text: str, *, k: int = _DEFAULT_K, max_chars: int = _DEFAULT_MAX_CHARS) -> list[str]:
    """The top-`k` passages of `source_text` most relevant to `claim_text` by BM25, capped at a

    combined `max_chars` (design decision 21) — most-relevant first, so the LLM sees the
    strongest evidence before weaker context. Returns `[]` when `source_text` has no sentence at
    all, or none of `claim_text`'s tokens appear anywhere in it (every candidate scores 0 — no
    genuinely relevant passage to send, not "send something anyway").
    """
    sentences = _split_sentences(source_text)
    if not sentences:
        return []

    candidates = list(sentences)
    candidates.extend(sentences[i] + " " + sentences[i + 1] for i in range(len(sentences) - 1))

    query_tokens = _tokenize(claim_text)
    scores = _bm25_scores(query_tokens, [_tokenize(c) for c in candidates])

    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)

    selected: list[str] = []
    seen: set[str] = set()
    total_chars = 0
    for candidate, score in ranked:
        if score <= 0 or len(selected) >= k:
            break
        if candidate in seen or total_chars + len(candidate) > max_chars:
            continue
        selected.append(candidate)
        seen.add(candidate)
        total_chars += len(candidate)
    return selected
