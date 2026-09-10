"""Shared interface every rule_based analysis skill runner implements.

An AnalysisSkill registry entry (app/analysis/__init__.py) never gets called
directly by a router — always through a runner implementing `run()` below,
so a skill can be swapped/tested without touching the caller. Mirrors
app/adapters/base.py's ProviderAdapter shape for providers.
"""

from typing import Any, Protocol

from app.models import Citation, Client


class AnalysisSkillRunner(Protocol):
    """Interface every rule_based skill runner (app/analysis/<skill_key>.py) implements."""

    def run(self, rendered_text: str | None, citations: list[Citation], client: Client) -> dict[str, Any]:
        """Compute one skill's output for a single raw response.

        `rendered_text` may be None or empty — a runner must handle that
        without raising. Returns a JSON-serializable dict matching the
        skill's own `AnalysisSkill.output_schema`.
        """
        ...
