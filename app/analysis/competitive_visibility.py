"""competitive_visibility — second-generation analysis skill (docs/TASKS_PHASE5.md P5-T5).

Extends mention_visibility's single-entity mention/citation detection to a configurable list
of tracked competitors (docs/TASKS_PHASE5.md P5-T3) and derives the client's own share-of-voice
and position among whichever entities actually got mentioned in one run. Purely deterministic
(same word-boundary/case-insensitive regex as mention_visibility, via the shared
app.analysis.matching module) — no LLM call.

The client itself is never read from a tracked_entities row — its candidates come from
Client.name/ClientAlias (phase 3), matching docs/TASKS_PHASE5.md design decision 1: exactly one
source of truth for what the client is called. Each entity (own client and every tracked
competitor) is matched independently against its own candidate list — no cross-entity
deduplication of overlapping substrings, unlike a single entity's own name + aliases, which
match_spans() already deduplicates internally (design decision 3).
"""

from typing import Any

from app.analysis.matching import match_spans, matching_citation_domains
from app.models import Citation, Client


def _entity_result(
    name: str,
    is_own_client: bool,
    domain: str | None,
    candidates: list[str],
    rendered_text: str | None,
    citations: list[Citation],
) -> dict[str, Any]:
    """One entity's mention/citation result for this run — the same shape mention_visibility
    computes for the client alone, just built once per tracked entity here.
    """
    if rendered_text:
        spans, _matched_terms = match_spans(rendered_text, candidates)
    else:
        spans = []

    cited_domains = matching_citation_domains(domain, citations)

    return {
        "name": name,
        "is_own_client": is_own_client,
        "mentioned": len(spans) > 0,
        "mention_count": len(spans),
        "first_position": spans[0][0] if spans else None,
        "cited": len(cited_domains) > 0,
        "cited_domains": cited_domains,
    }


class CompetitiveVisibilityRunner:
    """Rule-based runner for the competitive_visibility skill — see app/analysis/base.py."""

    def run(self, rendered_text: str | None, citations: list[Citation], client: Client) -> dict[str, Any]:
        own_candidates = [c for c in [client.name] + [alias.alias for alias in client.aliases] if c]
        entities = [
            _entity_result(client.name, True, client.domain, own_candidates, rendered_text, citations)
        ]
        for entity in client.tracked_entities:
            candidates = [c for c in [entity.name] + [alias.alias for alias in entity.aliases] if c]
            entities.append(
                _entity_result(entity.name, False, entity.domain, candidates, rendered_text, citations)
            )

        own = next(e for e in entities if e["is_own_client"])
        total_mention_count = sum(e["mention_count"] for e in entities)
        share_of_voice = round(own["mention_count"] / total_mention_count, 4) if total_mention_count else None

        # Rank among mentioned entities only, ascending by first_position; ties broken
        # alphabetically by name for a deterministic order across identical requests (design
        # decision 5) — practically near-impossible with word-boundary matches, but not
        # inconceivable with very short overlapping candidates.
        mentioned_entities = sorted(
            (e for e in entities if e["mention_count"] > 0),
            key=lambda e: (e["first_position"], e["name"]),
        )
        position = None
        if own["mention_count"] > 0:
            position = next(rank for rank, e in enumerate(mentioned_entities, start=1) if e is own)

        return {
            "entities": entities,
            "share_of_voice": share_of_voice,
            "position": position,
        }
