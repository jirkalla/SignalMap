"""mention_visibility — the first analysis skill (docs/TASKS_PHASE3.md).

Deliberately the simplest defensible metric: does the client's name (or a
known alias) appear in a run's rendered text, and is the client's own
domain among its citations. Purely deterministic (regex/string match, no
LLM call) — see design decisions 5 and 6 for the exact algorithm; this
module implements them as written, not a reinvented variant.
"""

from typing import Any

from app.analysis.matching import match_spans, matching_citation_domains
from app.models import Citation, Client


class MentionVisibilityRunner:
    """Rule-based runner for the mention_visibility skill — see app/analysis/base.py."""

    def run(self, rendered_text: str | None, citations: list[Citation], client: Client) -> dict[str, Any]:
        candidates = [c for c in [client.name] + [alias.alias for alias in client.aliases] if c]

        if rendered_text:
            spans, matched_terms = match_spans(rendered_text, candidates)
        else:
            spans, matched_terms = [], []

        cited_domains = matching_citation_domains(client.domain, citations)

        return {
            "text_mentioned": len(spans) > 0,
            "mention_count": len(spans),
            "first_mention_position": spans[0][0] if spans else None,
            "matched_terms": matched_terms,
            "match_spans": spans,
            "cited": len(cited_domains) > 0,
            "cited_domains": cited_domains,
        }
