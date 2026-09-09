"""Add Anthropic provider/models, and pricing/context/audit columns on providers and ai_models.

Extends ai_models with is_free, context_window_tokens, max_output_tokens, and
audit columns (created_at/updated_at) on both providers and ai_models — these
were deliberately skipped in 0008 (docs/TASKS_HARDENING.md design decision 7)
because neither table had an edit path yet; the phase-2 admin UI (P2-T2/P2-T3)
adds that path, so the columns are no longer dead schema.

is_free is left at its default FALSE for the existing Gemini rows: FR-8 makes
Google Search grounding mandatory for every real run, and the billing status
of the Google Cloud project behind the app's GOOGLE_API_KEY was confirmed
(2026-09-09, user-verified in Google Cloud Console) to have billing enabled —
so no Gemini run this app makes is reliably free. See docs/TASKS_PHASE2.md
design decision 5 (and its revision) for the full reasoning.

Anthropic model pricing/context/max-output values are copied from Anthropic's
own pricing page (platform.claude.com/docs/en/about-claude/models/overview,
fetched 2026-09-09) rather than left NULL, since they were actually verified
against the primary source rather than guessed.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-09

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "providers",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "ai_models",
        sa.Column("is_free", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("ai_models", sa.Column("context_window_tokens", sa.Integer(), nullable=True))
    op.add_column("ai_models", sa.Column("max_output_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "ai_models",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "ai_models",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.execute("INSERT INTO providers (code, name) VALUES ('anthropic', 'Anthropic Claude')")

    op.execute(
        """
        INSERT INTO ai_models (
            provider_id, model_name, display_name, capability_tier,
            cost_per_1k_input_usd, cost_per_1k_output_usd,
            context_window_tokens, max_output_tokens,
            supports_web_search, is_free, is_active, notes
        )
        VALUES
            (
                (SELECT id FROM providers WHERE code = 'anthropic'),
                'claude-haiku-4-5-20251001',
                'Claude Haiku 4.5',
                'economy',
                0.001,
                0.005,
                200000,
                64000,
                TRUE,
                FALSE,
                TRUE,
                'Pricing verified against platform.claude.com/docs/en/about-claude/models/overview on 2026-09-09.'
            ),
            (
                (SELECT id FROM providers WHERE code = 'anthropic'),
                'claude-sonnet-5',
                'Claude Sonnet 5',
                'standard',
                0.002,
                0.010,
                1000000,
                128000,
                TRUE,
                FALSE,
                TRUE,
                'Pricing verified against platform.claude.com/docs/en/about-claude/models/overview on 2026-09-09.'
            ),
            (
                (SELECT id FROM providers WHERE code = 'anthropic'),
                'claude-opus-5',
                'Claude Opus 5',
                'flagship',
                0.005,
                0.025,
                1000000,
                128000,
                TRUE,
                FALSE,
                TRUE,
                'Pricing verified against platform.claude.com/docs/en/about-claude/models/overview on 2026-09-09.'
            )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'anthropic')"
    )
    op.execute("DELETE FROM providers WHERE code = 'anthropic'")
    op.drop_column("ai_models", "updated_at")
    op.drop_column("ai_models", "created_at")
    op.drop_column("ai_models", "max_output_tokens")
    op.drop_column("ai_models", "context_window_tokens")
    op.drop_column("ai_models", "is_free")
    op.drop_column("providers", "updated_at")
    op.drop_column("providers", "created_at")
