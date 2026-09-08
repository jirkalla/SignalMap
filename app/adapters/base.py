"""Shared shape every provider adapter must return.

A router never calls a provider SDK directly — always through an adapter
implementing `run()` below, so a provider can be swapped or mocked without
touching the rest of the app. See signalmap-conventions skill, "Backend
conventions".
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AdapterCitation:
    """One source extracted from a provider's grounding/citation metadata."""

    source_url: str | None = None
    source_title: str | None = None
    source_domain: str | None = None
    citation_position: int | None = None
    cited_answer_span: str | None = None


@dataclass
class RawResponsePayload:
    """The canonical result of running one prompt against one AI model.

    `raw_payload` must be the complete, untouched provider response —
    never transformed before storing (FR-10). Everything else here is
    derived from it for convenience.
    """

    raw_payload: dict[str, Any]
    rendered_text: str | None
    has_citations: bool
    citations: list[AdapterCitation] = field(default_factory=list)
    token_usage: dict[str, Any] | None = None


class ProviderAdapter(Protocol):
    """Interface every provider adapter (app/adapters/<provider>.py) implements."""

    def run(self, prompt_text: str, model_name: str) -> RawResponsePayload:
        """Run one prompt against one model and return the canonical payload.

        Raises on transport/API failure — callers are responsible for
        catching that and recording it as a Run with status='error'.
        """
        ...
