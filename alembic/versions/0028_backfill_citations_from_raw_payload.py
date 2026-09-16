"""Recompute every stored citation from its untouched raw_payload (docs/
TASKS_GEMINI_CITATIONS.md GC-T3, design decision 7).

Migration 0027 added the columns and GC-T2 fixed the adapters, but both only affect runs executed
from now on. Without this, history stays wrong in two ways: Gemini rows are the flattened
one-per-source shape the old mapper produced (551 rows for 1354 real claim-source pairs, 59% of
the links dropped, in 40 of 52 answers), and Anthropic rows carry the source passage in
`cited_answer_span`, contradicting FR-12's definition of that column. Leaving history as it is
would also mean dashboard and export numbers jumped at the deploy date rather than moving
consistently across the whole archive, which is the practical reason the adapter fix and this
backfill have to land together.

This DELETEs and re-INSERTs rows in `citations`, which AI_INSTRUCTIONS.md section 3 lists as an
evidence table, so it is not a routine operation. It is legitimate here for one specific reason:
the real evidence is `raw_responses.raw_payload` (FR-10) and it is never touched — this migration
only reads it. `citations` is a derived, queryable index over that payload, not an independent
record. No information is added, removed or reinterpreted; the same payload is simply run back
through the same extraction function the live path now uses (design decision 5), which is also
what makes this idempotent: the input is always the unchanged payload, never the current
`citations` rows. Confirmed by the user 2026-09-16. `raw_responses` and `runs` are read with
SELECT only.

Which mapper to use is decided from the database (`runs` -> `ai_models` -> `providers`), never
guessed from the payload's shape. A provider with no mapper registered is skipped untouched, and
so is any raw response that yields no citations and has none stored — that combination means
there was never anything to extract, so rewriting it would only churn ids.

Measured expectations, so a later reader can tell whether this ran to completion (2026-09-16,
88 raw responses):

    provider   before -> after
    Gemini        583 -> 1386   (re-extraction: 551 -> 1354 historical, plus 32 already correct)
    Anthropic     167 ->  167   (count unchanged; cited_answer_span moves to source_passage)
    OpenAI         71 ->   71   (count unchanged; answer span offsets filled in)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-16

"""

import sqlalchemy as sa
from alembic import op

# The adapters' own mappers, so re-extraction can never drift from live extraction — the whole
# point of moving them onto plain dicts in GC-T2. These imports pull in the provider SDKs
# transitively (each adapter module imports its own SDK at module level), which is harmless in a
# migration context: no API client is constructed and no credentials are read, since that only
# happens in each adapter class's __init__, and the mappers themselves are pure functions over the
# payload dict. Verified by running this migration, not by reasoning about it.
from app.adapters.anthropic import _map_citations as _map_anthropic_citations
from app.adapters.google import _map_citations as _map_google_citations
from app.adapters.openai import _map_citations as _map_openai_citations

# revision identifiers, used by Alembic.
revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_MAPPERS = {
    "google_gemini": _map_google_citations,
    "anthropic": _map_anthropic_citations,
    "openai": _map_openai_citations,
}

# Today's archive is 88 raw responses, so batching buys nothing yet — it is here so the migration
# still runs in bounded memory on an archive orders of magnitude larger, since payloads are large
# JSON blobs and re-running this over a grown archive is exactly what roadmap #12 will want.
_BATCH_SIZE = 200

_RAW_RESPONSES_SQL = sa.text(
    """
    SELECT rr.id, p.code AS provider_code
    FROM raw_responses rr
    JOIN runs r ON r.id = rr.run_id
    JOIN ai_models m ON m.id = r.model_id
    JOIN providers p ON p.id = m.provider_id
    ORDER BY rr.id
    """
)

_PAYLOADS_SQL = sa.text("SELECT id, raw_payload FROM raw_responses WHERE id IN :ids").bindparams(
    sa.bindparam("ids", expanding=True)
)

_EXISTING_COUNTS_SQL = sa.text(
    "SELECT raw_response_id, count(*) AS n FROM citations WHERE raw_response_id IN :ids GROUP BY 1"
).bindparams(sa.bindparam("ids", expanding=True))

_DELETE_SQL = sa.text("DELETE FROM citations WHERE raw_response_id IN :ids").bindparams(
    sa.bindparam("ids", expanding=True)
)

_INSERT_SQL = sa.text(
    """
    INSERT INTO citations (
        raw_response_id, source_url, source_title, source_domain,
        citation_position, cited_answer_span, answer_span_start, answer_span_end, source_passage
    ) VALUES (
        :raw_response_id, :source_url, :source_title, :source_domain,
        :citation_position, :cited_answer_span, :answer_span_start, :answer_span_end,
        :source_passage
    )
    """
)


def backfill_citations(connection) -> dict[str, int]:
    """Recompute `citations` for every raw response from its stored payload.

    Takes the connection explicitly rather than reaching for `op.get_bind()`, so the exact same
    function can be run against an already-migrated database to prove idempotence (GC-T3 step 5)
    without touching Alembic's revision history.

    Returns counters describing what it did — rewritten/skipped raw responses and
    deleted/inserted citation rows — for verification by the caller.
    """
    stats = {"rewritten": 0, "skipped": 0, "deleted": 0, "inserted": 0}
    targets = connection.execute(_RAW_RESPONSES_SQL).all()

    for offset in range(0, len(targets), _BATCH_SIZE):
        batch = targets[offset : offset + _BATCH_SIZE]
        providers = {row.id: row.provider_code for row in batch}
        ids = list(providers)

        payloads = dict(connection.execute(_PAYLOADS_SQL, {"ids": ids}).all())
        existing = dict(connection.execute(_EXISTING_COUNTS_SQL, {"ids": ids}).all())

        ids_to_rewrite: list[int] = []
        rows_to_insert: list[dict] = []
        for raw_response_id in ids:
            mapper = _MAPPERS.get(providers[raw_response_id])
            if mapper is None:
                stats["skipped"] += 1
                continue

            citations, _ = mapper(payloads.get(raw_response_id) or {})
            stored = existing.get(raw_response_id, 0)
            # Nothing extractable and nothing stored: there was never anything here to correct.
            if not citations and not stored:
                stats["skipped"] += 1
                continue

            ids_to_rewrite.append(raw_response_id)
            stats["rewritten"] += 1
            stats["deleted"] += stored
            rows_to_insert.extend(
                {
                    "raw_response_id": raw_response_id,
                    "source_url": c.source_url,
                    "source_title": c.source_title,
                    "source_domain": c.source_domain,
                    "citation_position": c.citation_position,
                    "cited_answer_span": c.cited_answer_span,
                    "answer_span_start": c.answer_span_start,
                    "answer_span_end": c.answer_span_end,
                    "source_passage": c.source_passage,
                }
                for c in citations
            )

        if ids_to_rewrite:
            connection.execute(_DELETE_SQL, {"ids": ids_to_rewrite})
        if rows_to_insert:
            connection.execute(_INSERT_SQL, rows_to_insert)
            stats["inserted"] += len(rows_to_insert)

    return stats


def upgrade() -> None:
    backfill_citations(op.get_bind())


def downgrade() -> None:
    """Deliberately does nothing — there is no meaningful reverse of this.

    Going back would mean collapsing 1354 claim-source pairs into 551 flattened rows and putting
    Anthropic's source passages back into the answer-span column: lossy in exactly the way this
    migration exists to undo, and pointless, because `raw_payload` was never modified. "Rolling
    back" this data means running an older mapper over that same untouched payload, not reversing
    anything here. Same precedent as 0026_drop_flat_model_prices.py, whose downgrade also
    knowingly declines to restore data (design decision 7).
    """
