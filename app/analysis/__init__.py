"""Registry mapping an AnalysisSkill.key to its rule_based runner class.

Adding a new rule_based skill later means adding a new runner module plus
one entry here — callers never import a specific runner class directly.
Mirrors app/adapters/__init__.py's ADAPTERS registry for providers.
"""

from app.analysis.base import AnalysisSkillRunner
from app.analysis.competitive_visibility import CompetitiveVisibilityRunner
from app.analysis.mention_visibility import MentionVisibilityRunner

ANALYSIS_SKILLS: dict[str, type[AnalysisSkillRunner]] = {
    "mention_visibility": MentionVisibilityRunner,
    "competitive_visibility": CompetitiveVisibilityRunner,
}


def get_runner(skill_key: str) -> AnalysisSkillRunner:
    """Instantiate the runner registered for `skill_key`."""
    return ANALYSIS_SKILLS[skill_key]()


def has_runner(skill_key: str) -> bool:
    """Whether `skill_key` has a working rule_based runner registered."""
    return skill_key in ANALYSIS_SKILLS
