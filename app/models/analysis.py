"""AnalysisSkill, AnalysisResult — the analysis layer (docs/TASKS_PHASE3.md).

An AnalysisSkill is a registered unit of analysis run against a stored
RawResponse: input is implicit (the raw response plus whatever context the
skill needs, e.g. the client), output is a structured dict matching the
skill's own `output_schema` (documentation only, not enforced at the DB
level).

`execution_type` distinguishes how a skill computes its output:
'rule_based' (deterministic code, registered in app/analysis/) or
'llm_prompt' (an LLM call using `prompt_template`) — the first skill
(mention_visibility) is rule_based; no llm_prompt skill exists yet, but the
column exists now so adding one later doesn't need another schema change.

AnalysisResult rows are evidence, like RawResponse/Citation — never edited
or overwritten. `skill_version` copies `AnalysisSkill.version` at compute
time: a skill's logic changes via code deploy, not user edits, so a plain
integer is enough to know which version of the logic produced a given
result — unlike Prompt, this doesn't need a full versioned-row lineage.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AnalysisSkill(Base):
    """A registered analysis skill, e.g. 'mention_visibility'."""

    __tablename__ = "analysis_skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_template: Mapped[str | None] = mapped_column(Text)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnalysisResult(Base):
    """One computed result of one skill against one raw response. Never edited —
    a re-computation is always a new row (same evidence-retention discipline as
    RawResponse/Citation).
    """

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_response_id: Mapped[int] = mapped_column(ForeignKey("raw_responses.id", ondelete="CASCADE"), nullable=False)
    analysis_skill_id: Mapped[int] = mapped_column(ForeignKey("analysis_skills.id"), nullable=False)
    skill_version: Mapped[int] = mapped_column(Integer, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis_skill: Mapped["AnalysisSkill"] = relationship()
