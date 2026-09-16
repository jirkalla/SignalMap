"""Split the citation answer span from the source passage (docs/TASKS_GEMINI_CITATIONS.md GC-T1,
design decision 6).

`citations.cited_answer_span` has carried two different meanings depending on the provider: the
span OF THE ANSWER for Gemini and OpenAI, but the passage FROM THE SOURCE PAGE
(`citation.cited_text`) for Anthropic. Measured against production data on 2026-09-16, the stored
span is findable in `raw_responses.rendered_text` for 551/551 Gemini and 71/71 OpenAI rows, but
only 17/158 Anthropic ones — and those 17 are short-string coincidences. FR-12 defines the column
as the answer span, so Anthropic's use of it is a defect, not a second valid reading.

This migration only adds the room to separate the two: `source_passage` for the source-side text
and `answer_span_start`/`answer_span_end` for the offsets that locate the answer-side span in
`rendered_text`. All three are nullable with no default — no provider fills every one of them, and
Anthropic's offset columns stay permanently NULL because its API exposes no answer offsets. Moving
the existing values into the right columns happens later, in the GC-T3 backfill, once the adapters
write them (GC-T2); nothing reads these columns yet.

Purely additive — no column is dropped or renamed (schema flag confirmed 2026-09-16).

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-16

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("citations", sa.Column("answer_span_start", sa.Integer(), nullable=True))
    op.add_column("citations", sa.Column("answer_span_end", sa.Integer(), nullable=True))
    op.add_column("citations", sa.Column("source_passage", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("citations", "source_passage")
    op.drop_column("citations", "answer_span_end")
    op.drop_column("citations", "answer_span_start")
