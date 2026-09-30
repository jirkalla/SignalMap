"""LLM paraphrase check for OpenAI/Gemini citations (docs/TASKS_CITATION_VERIFICATION.md T12,

design decisions 20-24) — the counterpart to app/services/citation_verification.py's literal
quote check (T8) for the two providers that give no source text to check against (design
decision 4): OpenAI/Gemini expose the ANSWER side of a citation (a claim), never a passage FROM
the source, so the only way to verify them is to ask an LLM whether the captured page supports
that claim.

Wired into app/services/verification_queue.py (T13): `process_verification_job` calls
`judge_citations` inline, right after `verify_citations_by_quote`, whenever the response's
client has `auto_verify_citations` on — see that module's own docstring for why this rides
along on the capture job rather than a separately-enqueued 'judge' job. The 'judge' `kind` on
`VerificationJob` itself is reserved for the two explicit, one-off paths that don't have a
freshly-captured source to piggyback on: the "Verify citations" button and a client's retroactive
bulk-verify run (both `app/routers/runs.py`/`app/routers/clients.py`, T13).
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.models.provider import AIModel
from app.models.run import Citation, RawResponse
from app.models.verification import CitationVerification, SourceDocument, SourceText
from app.services.claims import derive_claim
from app.services.cost import estimate_run_cost, load_price_components, prices_at, raw_input_output_tokens
from app.services.passages import select_passages
from app.services.quote_match import ChunkMatch, find_chunk
from app.services.verification_display import has_verification_since, latest_source_document as _latest_source_document, locate as _locate, locate_page as _locate_page

logger = logging.getLogger(__name__)

VERIFIER_VERSION = "1.0"

# design decision 4's routing table: OpenAI/Gemini give the answer-side claim, no source passage
# to run a literal-quote check against (Anthropic/Perplexity, T8) — an LLM judgement against the
# captured page is the only way to check them at all. Public (no leading underscore, T13): both
# this module and app/routers/runs.py (to decide whether to show the "Verify citations" button) and
# app/routers/clients.py (the bulk-verify preview/confirm) need to ask the same question.
LLM_JUDGE_PROVIDERS = ("openai", "google_gemini")

# design decision 20's stated default — a fixed lookup rather than a new Settings field or admin
# UI: T13 doesn't ask for a way to configure a different judge model, and "the model is a row in
# ai_models" (decision 20) is satisfied by resolving this well-known (provider, model_name) pair
# to its row at call time (`_default_judge_model` in app/services/verification_queue.py), not by
# inventing new configuration surface for a choice nobody has asked to make yet.
DEFAULT_JUDGE_PROVIDER_CODE = "anthropic"
DEFAULT_JUDGE_MODEL_NAME = "claude-haiku-4-5-20251001"

# Same threshold quote_match.py's own `_MIN_CHUNK_LENGTH` uses (design decision 16) — kept as its
# own local constant rather than importing that private module constant: a 3-character "quote"
# could spuriously fuzzy-match almost anywhere in a long page, the same risk T8 already guards
# against for provider-supplied quotes, and there is no reason an LLM-supplied one is any safer.
_MIN_QUOTE_LENGTH = 20

_SYSTEM_PROMPT = (
    "You check whether a web page supports a claim that an AI assistant made while citing that page.\n"
    "Judge only against the page text you are given. Do not use outside knowledge.\n"
    "Verdicts: supported | partially_supported | not_supported | contradicted\n"
    'Reply with JSON only: {"verdict": "...", "reason": "<one sentence>", "quote": "<the single most '
    'relevant sentence from the page, copied character for character, or empty string>"}'
)

# design decision 28's LLM verdict enum — the prompt's own verdict words map onto it one-to-one.
_VERDICT_MAP = {
    "supported": "llm_supported",
    "partially_supported": "llm_partial",
    "not_supported": "llm_not_supported",
    "contradicted": "llm_contradicted",
}

# The 4 verdicts a REAL LLM judgement can produce (design decision 22) — `judge_citations` can
# also write `verdict="unverifiable"` for a citation whose capture failed before the LLM was ever
# asked anything, which is a placeholder, not a judgement. Shared so "did this citation get a
# real LLM opinion" is answered the same way everywhere it matters: app/routers/verification.py's
# `_citation_strata` (excludes unverifiable placeholders from blind-labeling, found 2026-09-29)
# and app/routers/clients.py's bulk-verify exclusion (code-review finding, 2026-09-30 — used to
# treat any check_type='llm' row, placeholder included, as "already judged", permanently
# excluding a response whose source later got captured successfully from ever being re-verified).
LLM_JUDGED_VERDICTS = tuple(_VERDICT_MAP.values())

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
# Anchored on the literal, distinctive `"quote"`/closing-brace text rather than a generic
# quote-matching group — design decision 22 / finding 7's real failure mode is ONE unescaped `"`
# inside `reason` or `quote` (a straight quote the model forgot to escape), and Python's `re` is
# greedy-by-default-with-backtracking: `.*?` before a fixed anchor still finds the LAST valid stop
# point that makes the anchor match, not the first `"` it happens to see, so one embedded quote
# does not truncate the value early.
_VERDICT_RE = re.compile(r'"verdict"\s*:\s*"([^"]*)"')
_REASON_RE = re.compile(r'"reason"\s*:\s*"(.*?)"\s*,\s*"quote"', re.DOTALL)
_QUOTE_RE = re.compile(r'"quote"\s*:\s*"(.*)"\s*\}', re.DOTALL)


@dataclass(frozen=True)
class ParsedJudgement:
    verdict: str
    reason: str
    quote: str


def _parse_judgement(text: str) -> ParsedJudgement | None:
    """Parse a judge() reply's `{"verdict":..., "reason":..., "quote":...}` — strict JSON first,

    then a regex fallback for a reply that is not quite valid JSON (design decision 22, finding 7:
    an unescaped `"` inside `reason`/`quote` is a real, observed failure mode). `None` only when
    neither approach can find a `"verdict"` value at all.
    """
    stripped = _CODE_FENCE_RE.sub("", text.strip()).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and data.get("verdict"):
        return ParsedJudgement(verdict=data["verdict"], reason=data.get("reason") or "", quote=data.get("quote") or "")

    verdict_match = _VERDICT_RE.search(stripped)
    if verdict_match is None:
        return None
    reason_match = _REASON_RE.search(stripped)
    quote_match = _QUOTE_RE.search(stripped)
    return ParsedJudgement(
        verdict=verdict_match.group(1),
        reason=reason_match.group(1) if reason_match else "",
        quote=quote_match.group(1) if quote_match else "",
    )


def _build_user_prompt(claim_text: str, passages: list[str]) -> str:
    passage_block = "\n\n".join(f"[{i + 1}] {p}" for i, p in enumerate(passages))
    return f"Claim: {claim_text}\n\nPage text:\n{passage_block}"

def judge_citations(
    db: Session,
    raw_response: RawResponse,
    *,
    judge_model: AIModel,
    now: datetime,
    since: datetime | None = None,
    deadline: float | None = None,
) -> bool:
    """Run the LLM paraphrase check for every OpenAI/Gemini citation on `raw_response` whose

    source has already been captured (design decisions 20-24). Writes one `CitationVerification`
    row per checked citation, `check_type='llm'` — never touches an existing row (NFR-6,
    append-only). `judge_model` is a plain parameter (an `ai_models` row) rather than looked up
    internally: which model is "the" judge model is a caller/config decision (T13), not this
    function's. Commits after each citation, not once at the end (code-review finding,
    2026-09-30): a single trailing commit meant one citation's failure discarded every other
    citation's already-paid LLM verdict from the same call.

    `since` (typically `job.created_at`, passed by `process_verification_job`) skips a citation
    that already has a `check_type='llm'` row created at/after `since` — lets a retried attempt
    of the SAME job resume instead of re-judging (and re-paying for) citations an earlier attempt
    already committed, while a genuinely new job (e.g. the user clicking "Verify citations" again
    later) still re-judges everything, since old rows all predate that new job's `since`.

    `deadline` (a `time.monotonic()` value, code-review finding 2026-09-30 round 2) bails this out
    before a citation-heavy, LLM-call-heavy response can run long enough to approach the job's
    lease expiry, which could otherwise let a second worker reclaim and concurrently double-
    process (and double-pay for) the same job. Returns `True` when every citation was checked,
    `False` when the deadline cut the pass short.

    Silently does nothing for a citation when: its provider isn't OpenAI/Gemini, its source
    hasn't been captured yet (still queued — a later pass, once capture finishes, is what makes
    this normally not the case), or `derive_claim` found no claim to check at all. A capture that
    already finished and FAILED (403, robots, ...) still gets an explicit `unverifiable` row
    (mirrors T8's own `verify_citations_by_quote`), so a citation's evidence never just silently
    goes missing versus "not checked yet".
    """
    provider_code = raw_response.run.model.provider.code
    if provider_code not in LLM_JUDGE_PROVIDERS:
        return True

    judge_adapter = get_adapter(judge_model.provider.code)
    price_components = load_price_components(db, [judge_model.id])
    prices = prices_at(price_components.get(judge_model.id, []), now)
    # Captured once, up front, rather than read via `raw_response.raw_payload`/`.rendered_text`/
    # `judge_model.model_name`/`.id` inside the loop below (code-review finding, 2026-09-30 round
    # 2): `SessionLocal` defaults to `expire_on_commit=True`, so the per-citation `db.commit()`
    # this function now does would otherwise silently force a fresh SELECT of these rows' full
    # columns on every subsequent citation, for no reason — nothing about `raw_response`/
    # `judge_model` themselves changes mid-loop.
    raw_payload = raw_response.raw_payload
    rendered_text = raw_response.rendered_text
    response_id = raw_response.id
    judge_model_name = judge_model.model_name
    judge_model_id = judge_model.id

    def _row(
        citation: Citation,
        derived_claim,
        *,
        source_document: SourceDocument,
        verdict: str,
        reason: str | None = None,
        match: ChunkMatch | None = None,
        llm_reason: str | None = None,
        llm_quote: str | None = None,
        llm_quote_found: bool | None = None,
        needs_review: bool = False,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> CitationVerification:
        return CitationVerification(
            citation_id=citation.id,
            source_document_id=source_document.id,
            claim_text=derived_claim.text if derived_claim else None,
            claim_method=derived_claim.method if derived_claim else None,
            check_type="llm",
            verdict=verdict,
            reason=reason,
            similarity=round(match.similarity, 3) if match and match.similarity is not None else None,
            matched_text=match.matched_text if match else None,
            match_start=match.match_start if match else None,
            match_end=match.match_end if match else None,
            page_number=_locate_page(match.match_start, source_document.page_starts) if match else None,
            location=_locate(match.match_start, source_document.locations) if match else None,
            llm_model_id=judge_model_id,
            llm_reason=llm_reason,
            llm_quote=llm_quote,
            llm_quote_found=llm_quote_found,
            needs_review=needs_review,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            verifier_version=VERIFIER_VERSION,
        )

    citations = db.scalars(select(Citation).where(Citation.raw_response_id == response_id)).all()
    for citation in citations:
        if deadline is not None and time.monotonic() > deadline:
            return False
        if not citation.source_url:
            continue
        derived_claim = derive_claim(provider_code, citation, rendered_text, raw_payload)
        if derived_claim is None:
            continue
        if since is not None and has_verification_since(db, citation.id, "llm", since):
            continue

        document = _latest_source_document(db, citation.source_url)
        if document is None:
            continue  # capture hasn't run yet

        if document.error_reason is not None:
            db.add(_row(citation, derived_claim, source_document=document, verdict="unverifiable", reason=document.error_reason))
            db.commit()
            continue

        source_text_row = db.get(SourceText, document.text_sha256) if document.text_sha256 else None
        if source_text_row is None:
            continue

        passages = select_passages(derived_claim.text, source_text_row.text)
        if not passages:
            db.add(_row(citation, derived_claim, source_document=document, verdict="unverifiable", reason="no_checkable_text"))
            db.commit()
            continue

        response = judge_adapter.judge(_SYSTEM_PROMPT, _build_user_prompt(derived_claim.text, passages), judge_model_name)
        tokens_in, tokens_out = raw_input_output_tokens(response.token_usage)
        cost_usd = estimate_run_cost(response.token_usage, judge_model, prices)

        parsed = _parse_judgement(response.text)
        verdict = _VERDICT_MAP.get(parsed.verdict.strip().lower()) if parsed else None
        if verdict is None:
            logger.warning("claim_judge: could not extract a recognized verdict for citation %s", citation.id)
            logger.debug("claim_judge: raw reply for citation %s: %r", citation.id, response.text)
            db.add(
                _row(
                    citation, derived_claim, source_document=document, verdict="unverifiable", needs_review=True,
                    llm_reason="Could not parse a recognized verdict from the judge's reply.",
                    tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd,
                )
            )
            db.commit()
            continue

        match: ChunkMatch | None = None
        llm_quote_found: bool | None = None
        needs_review = False
        if parsed.quote and len(parsed.quote) >= _MIN_QUOTE_LENGTH:
            match = find_chunk(parsed.quote, source_text_row.text)
            llm_quote_found = match.kind != "not_found"
            needs_review = not llm_quote_found

        db.add(
            _row(
                citation, derived_claim, source_document=document, verdict=verdict, match=match,
                llm_reason=parsed.reason or None, llm_quote=parsed.quote or None,
                llm_quote_found=llm_quote_found, needs_review=needs_review,
                tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd,
            )
        )
        db.commit()
    return True
