"""Shared candidate-matching helper for rule_based analysis skills.

Used by both mention_visibility (docs/TASKS_PHASE3.md) and competitive_visibility
(docs/TASKS_PHASE5.md P5-T5) — moved here from mention_visibility.py so a second skill can
reuse the exact same word-boundary/case-insensitive/longest-match-first regex logic instead of
a second, independently-maintained copy (docs/TASKS_PHASE5.md design decision 2).
"""

import re


def match_spans(rendered_text: str, candidates: list[str]) -> tuple[list[list[int]], list[str]]:
    """Non-overlapping [start, end) spans across all candidates, plus which candidates matched.

    One combined regex (candidates alternated, longest first, each escaped) instead of one
    independent re.finditer per candidate — re.finditer never returns overlapping matches for a
    single pattern (it scans left to right and resumes after each match's end), so
    overlap-freedom is a property of the search itself, not a separate post-hoc filter. Ordering
    candidates longest-first makes the regex engine prefer the longer/more specific alternative
    whenever two candidates could start at the same position. matched_terms is derived from the
    surviving spans themselves, so it can never desync from what mention_count/match_spans say.
    """
    if not candidates:
        return [], []
    ordered = sorted(set(candidates), key=len, reverse=True)
    alternation = "|".join(f"(?P<c{i}>{re.escape(c)})" for i, c in enumerate(ordered))
    pattern = r"(?<!\w)(?:" + alternation + r")(?!\w)"

    spans: list[list[int]] = []
    matched: set[str] = set()
    for m in re.finditer(pattern, rendered_text, re.IGNORECASE):
        spans.append([m.start(), m.end()])
        matched.add(ordered[int(m.lastgroup[1:])])

    matched_terms = list(dict.fromkeys(c for c in candidates if c in matched))
    return spans, matched_terms
