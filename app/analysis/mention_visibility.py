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

    Spans are deduped by start position across candidates (design decision 5)
    so an alias that overlaps the client's own name doesn't double-count the
    same occurrence — the longer match wins when two candidates share a
    start. A second pass drops any span whose start still falls inside the
    previous kept span (design decision 12) — near-impossible with
    word-boundary-anchored matches, but cheap insurance for highlighting.
    """
    ends_by_start: dict[int, int] = {}
    matched_terms: list[str] = []
    for candidate in candidates:
        pattern = r"(?<!\w)" + re.escape(candidate) + r"(?!\w)"
        found = False
        for m in re.finditer(pattern, rendered_text, re.IGNORECASE):
            found = True
            if m.start() not in ends_by_start or m.end() > ends_by_start[m.start()]:
                ends_by_start[m.start()] = m.end()
        if found:
            matched_terms.append(candidate)

    spans: list[list[int]] = []
    last_end = -1
    for start in sorted(ends_by_start):
        if start < last_end:
            continue
        end = ends_by_start[start]
        spans.append([start, end])
        last_end = end
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
