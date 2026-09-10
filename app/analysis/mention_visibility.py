"""mention_visibility — the first analysis skill (docs/TASKS_PHASE3.md).

Deliberately the simplest defensible metric: does the client's name (or a
known alias) appear in a run's rendered text, and is the client's own
domain among its citations. Purely deterministic (regex/string match, no
LLM call) — see design decisions 5 and 6 for the exact algorithm; this
module implements them as written, not a reinvented variant.
"""

import re
from typing import Any

from app.models import Citation, Client


def _match_spans(rendered_text: str, candidates: list[str]) -> tuple[list[list[int]], list[str]]:
    """Non-overlapping [start, end) spans across all candidates, plus which candidates matched.

    One combined regex (candidates alternated, longest first, each escaped)
    instead of one independent re.finditer per candidate — re.finditer never
    returns overlapping matches for a single pattern (it scans left to right
    and resumes after each match's end), so overlap-freedom is a property of
    the search itself, not a separate post-hoc filter. Ordering candidates
    longest-first makes the regex engine prefer the longer/more specific
    alternative whenever two candidates could start at the same position
    (design decision 5). Earlier revisions matched each candidate
    independently and filtered overlaps afterwards, which could drop a
    span for a candidate while still listing it in matched_terms — matched_terms
    is now derived from the surviving spans themselves, so the two can't
    desync.
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


def _normalize_domain(domain: str) -> str:
    """Lowercase, strip a leading 'www.' — the shared form used to compare domains."""
    domain = domain.strip().lower()
    if domain.startswith("www."):
        domain = domain[len("www.") :]
    return domain


def _matching_citation_domains(client_domain: str | None, citations: list[Citation]) -> list[str]:
    """Original (non-normalized) source_domain values that match the client's own domain,
    exactly or as a subdomain (design decision 6).
    """
    if not client_domain:
        return []
    normalized_client_domain = _normalize_domain(client_domain)
    matched: list[str] = []
    for citation in citations:
        if not citation.source_domain:
            continue
        normalized_citation_domain = _normalize_domain(citation.source_domain)
        if normalized_citation_domain == normalized_client_domain or normalized_citation_domain.endswith(
            "." + normalized_client_domain
        ):
            matched.append(citation.source_domain)
    return matched


class MentionVisibilityRunner:
    """Rule-based runner for the mention_visibility skill — see app/analysis/base.py."""

    def run(self, rendered_text: str | None, citations: list[Citation], client: Client) -> dict[str, Any]:
        candidates = [c for c in [client.name] + [alias.alias for alias in client.aliases] if c]

        if rendered_text:
            spans, matched_terms = _match_spans(rendered_text, candidates)
        else:
            spans, matched_terms = [], []

        cited_domains = _matching_citation_domains(client.domain, citations)

        return {
            "text_mentioned": len(spans) > 0,
            "mention_count": len(spans),
            "first_mention_position": spans[0][0] if spans else None,
            "matched_terms": matched_terms,
            "match_spans": spans,
            "cited": len(cited_domains) > 0,
            "cited_domains": cited_domains,
        }
