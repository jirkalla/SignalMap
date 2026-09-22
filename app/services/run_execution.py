"""Shared run execution (docs/TASKS_SCHEDULER.md T2) — extracted from `trigger_run`
(app/routers/runs.py) so both the HTTP router and the future scheduler worker
(docs/TASKS_SCHEDULER.md T3) call the exact same path from "insert a pending Run" through
"call the provider adapter" to "store evidence and run analysis skills", instead of the worker
duplicating ~200 lines of that logic.

Callable without a `Request` — the worker has none. Localized user-facing error messages
(`t(...)`) stay in the router; this module returns/raises neutral exceptions only (e.g. an
`IntegrityError` from the pending-Run insert propagates untouched, for the router's own
try/except to translate into a localized `AppError`, exactly as it did before this file existed).
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.analysis import get_runner, has_runner
from app.models import (
    AIModel,
    AnalysisResult,
    AnalysisSkill,
    Citation,
    Client,
    Market,
    Persona,
    Prompt,
    PromptSet,
    Provider,
    RawResponse,
    Run,
    SearchQuery,
    SystemInstructionTemplate,
)
from app.routers.settings import DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raised by `check_daily_quota` when a client has already hit its rolling-24h run cap.

    Deliberately a plain exception, not an `AppError` — this module has no `Request`/locale to
    build a translated message with (see module docstring), so each caller catches this and
    decides what "rejected" means in its own context: `app/routers/runs.py`'s manual trigger
    turns it into a localized 409, `app/worker.py` turns it into a `skipped`/`quota_exceeded`
    queue item. Carries `client_id` so a caller that only has this exception (not the client
    object) can still build a useful message or notification payload.
    """

    def __init__(self, client_id: int):
        self.client_id = client_id
        super().__init__(f"client {client_id} has exceeded its daily run quota")


def check_daily_quota(db: Session, *, client_id: int, now: datetime, default_limit: int) -> None:
    """Raise `QuotaExceededError` if `client_id` has already run `daily_run_limit` (or

    `default_limit`, when that column is NULL) Runs in the trailing 24h — docs/TASKS_SCHEDULER.md
    T10, design decision 26. Called from both the manual HTTP trigger and the scheduler worker so
    neither path can bypass the cap the other enforces; every status counts (including `pending`
    and `error`), since each Run row represents one dispatched attempt regardless of how it ended,
    not just the ones that happened to succeed.

    Takes a transaction-scoped Postgres advisory lock on `client_id` first (found in code review,
    2026-09-22) — without it, two concurrent callers for the same client (a manual trigger racing
    the worker, or two manual triggers) could both count before either commits its own new Run
    row, letting the "hard cap, never bypassed" guarantee slip by a run or two under real
    concurrency. `pg_advisory_xact_lock` releases automatically when the caller's own transaction
    commits or rolls back — for both callers, that happens right after the Run row this check is
    guarding is written — so a second concurrent caller blocks here until the first one's count
    already reflects that new row, the same "let Postgres serialize it" idiom `claim_next`
    (app/services/queue.py) already uses for the queue itself.
    """
    db.execute(select(func.pg_advisory_xact_lock(client_id)))
    client = db.get(Client, client_id)
    limit = client.daily_run_limit if client.daily_run_limit is not None else default_limit
    since = now - timedelta(hours=24)
    count = db.scalar(
        select(func.count(Run.id))
        .select_from(Run)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
        .where(PromptSet.client_id == client_id, Run.started_at >= since)
    )
    if (count or 0) >= limit:
        raise QuotaExceededError(client_id)


def _build_system_instruction(db: Session, provider: Provider, market: Market, persona: Persona) -> str | None:
    """Build a locale-framing, persona-framing hint from a run's market and persona, using

    `provider`'s editable template (see /settings and app/models/settings.py). No saved row yet
    -> the built-in DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE (app.routers.settings). A row with an
    empty template -> None, meaning no system_instruction is sent for this provider at all.

    This is a text-only hint for the answer's *language* — every provider
    gets it, since none expose a real "respond in language X" API
    parameter. It is not a substitute for real geographic *search* bias
    where a provider's API offers one: Gemini's grounding tool has no
    location parameter at all, so its template stays the fuller default
    (language + location-simulation); Anthropic's web_search tool takes a
    real `user_location` (app/adapters/anthropic.py, via this function's
    caller passing `market_country` separately), so its saved template
    should be trimmed to language-only — see docs/TASKS_PHASE2.md P2-T4
    follow-up for why keeping both wouldn't be wrong, just redundant.

    `persona.label` (e.g. "person", "manager", "politician" — app/models/persona.py) is passed
    as the `{persona}` placeholder — a saved template that doesn't reference it (like Anthropic's
    trimmed one above) simply ignores this kwarg, since `str.format()` never errors on an unused
    one.
    """
    row = db.scalar(select(SystemInstructionTemplate).where(SystemInstructionTemplate.provider_id == provider.id))
    if row is None:
        template = DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE
    elif not row.template.strip():
        return None
    else:
        template = row.template

    return template.format(
        market_code=market.code,
        market_language=market.language,
        market_country=market.country or "",
        market_locale_name=market.locale_name or market.code,
        persona=persona.label,
    )


def _run_active_analysis_skills(
    db: Session, raw_response_id: int, rendered_text: str | None, citations: list[Citation], client: Client
) -> None:
    """Compute and store every active rule_based analysis skill's result for one raw response.

    Takes `rendered_text`/`citations` directly rather than a `RawResponse` to
    read them off of — the caller's own `db.commit()` (default
    `expire_on_commit=True`) expires whatever it just built, so reading them
    back off the ORM object here would force two avoidable reload queries for
    data the caller already had in memory a moment earlier.

    llm_prompt skills are skipped here — none exist yet (docs/TASKS_PHASE3.md
    design decision 1 prepares the column, phase 3 only ships mention_visibility).
    The caller wraps this in try/except: a failure here must never affect the
    Run's own status or roll back the evidence already committed (design
    decision 7) — this is a best-effort derived layer, not part of what "the
    run succeeded" means.
    """
    skills = db.scalars(select(AnalysisSkill).where(AnalysisSkill.is_active.is_(True))).all()
    for skill in skills:
        if skill.execution_type != "rule_based" or not has_runner(skill.key):
            continue
        output = get_runner(skill.key).run(rendered_text, citations, client)
        db.add(
            AnalysisResult(
                raw_response_id=raw_response_id,
                analysis_skill_id=skill.id,
                skill_version=skill.version,
                output=output,
            )
        )
    db.commit()


def build_request_payload(db: Session, *, prompt: Prompt, model: AIModel, market: Market, persona: Persona) -> dict:
    """The exact payload recorded as `Run.request_payload` and sent to the provider adapter,

    built before any Run row exists so it's available whether the caller is about to create one
    (the manual trigger path in `execute_run` below) or already created one itself (the
    scheduler worker, which must write its Run's id to the queue row before ever calling the
    adapter — docs/TASKS_SCHEDULER.md design decision 13, `app/worker.py`'s
    `process_claimed_item`). Recomputing this is cheap and side-effect-free, so both callers do
    it fresh rather than one passing a payload to the other.
    """
    return {
        "model": model.model_name,
        "prompt_text": prompt.text,
        "system_instruction": _build_system_instruction(db, model.provider, market, persona),
        "market_country": market.country,
        "persona": persona.label,
    }


def execute_run(
    db: Session,
    *,
    prompt: Prompt,
    model: AIModel,
    market: Market,
    persona: Persona,
    trigger_type: str,
    triggered_by_user_id: int | None,
    run_id: int | None = None,
    reraise_on_failure: bool = False,
) -> Run:
    """Run `prompt` against `model` (framed by `market`/`persona`) and store the result (FR-7..FR-16).

    Always leaves behind a Run row, whether the provider call succeeds or
    fails — a failed call is recorded with status='error' and a stored error
    message, never silently dropped. The market and persona used are
    recorded on the run itself, so overriding either for one run never
    changes the prompt's own market, the default persona, or any other
    run's history.

    `run_id=None` (today's only caller, the manual HTTP trigger) inserts a new `pending` Run row
    here and lets any `IntegrityError` from that insert (the migration 0024 partial-unique-index
    backstop for the "one pending run per prompt+model" race) propagate to the caller untouched —
    this function never catches or translates it, since the localized 409 it becomes is a router
    concern (see app/routers/runs.py's trigger_run). `run_id=<int>` instead loads an already-
    inserted `pending` Run and skips straight to calling the adapter — for the future scheduler
    worker (docs/TASKS_SCHEDULER.md design decision 13), which must write its own Run's id onto
    the queue row *before* the adapter is ever called, so a worker killed mid-call can be
    reconciled instead of silently retried and paid for twice.

    `reraise_on_failure=False` (the default, and the only mode the HTTP router ever uses) matches
    every behavior this function had before the scheduler worker existed: an adapter failure is
    swallowed, recorded on the Run, and this function returns normally. `reraise_on_failure=True`
    (app/worker.py only) does exactly the same recording, but additionally re-raises the original
    exception afterward — the worker needs the real exception object (not just its stringified
    `error_message`) to tell a retryable transport/429 failure from a terminal one
    (docs/TASKS_SCHEDULER.md T3), and this function is the only place that exception is ever seen.
    """
    request_payload = build_request_payload(db, prompt=prompt, model=model, market=market, persona=persona)
    system_instruction = request_payload["system_instruction"]

    if run_id is None:
        run = Run(
            prompt_id=prompt.id,
            model_id=model.id,
            market_id=market.id,
            persona_id=persona.id,
            trigger_type=trigger_type,
            status="pending",
            request_payload=request_payload,
            triggered_by_user_id=triggered_by_user_id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
    else:
        run = db.get(Run, run_id)

    logger.info(
        "Triggering run %s (prompt_id=%s, model=%s, market=%s)",
        run.id,
        prompt.id,
        model.model_name,
        market.code,
        extra={
            "extra_data": {
                "run_id": run.id,
                "prompt_id": prompt.id,
                "model": model.model_name,
                "market": market.code,
            }
        },
    )

    started = time.perf_counter()
    try:
        adapter = get_adapter(model.provider.code)
        payload = adapter.run(
            prompt_text=prompt.text,
            model_name=model.model_name,
            system_instruction=system_instruction,
            market_country=market.country,
        )
    except Exception as exc:  # provider/transport failure — record it, don't raise (FR-16)
        run.status = "error"
        run.error_message = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        db.commit()
        logger.error(
            "Run %s failed after %sms: %s",
            run.id,
            run.latency_ms,
            exc,
            exc_info=True,
            extra={"extra_data": {"run_id": run.id, "latency_ms": run.latency_ms}},
        )
        if reraise_on_failure:
            raise
    else:
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        raw_response = RawResponse(
            run_id=run.id,
            raw_payload=payload.raw_payload,
            rendered_text=payload.rendered_text,
            token_usage=payload.token_usage,
            has_citations=payload.has_citations,
        )
        db.add(raw_response)
        db.flush()  # need raw_response.id before creating citations
        raw_response_id = raw_response.id
        citations: list[Citation] = []
        for c in payload.citations:
            citation = Citation(
                raw_response_id=raw_response_id,
                source_url=c.source_url,
                source_title=c.source_title,
                source_domain=c.source_domain,
                citation_position=c.citation_position,
                cited_answer_span=c.cited_answer_span,
                answer_span_start=c.answer_span_start,
                answer_span_end=c.answer_span_end,
                source_passage=c.source_passage,
            )
            db.add(citation)
            citations.append(citation)
        for position, query_text in enumerate(payload.search_queries):
            db.add(
                SearchQuery(
                    raw_response_id=raw_response_id,
                    query_text=query_text,
                    query_position=position,
                )
            )
        # _run_active_analysis_skills needs this data right after the commit
        # below. Default expire_on_commit=True would otherwise force a fresh
        # reload query for every one of these on next access — for
        # analysis_client/rendered_text that's avoided by resolving them into
        # locals now, but the `citations` list above holds ORM objects whose
        # *attributes* (not just their existence) would still be wiped by an
        # expiring commit; disabling it for the rest of this request's
        # session is what keeps those already-in-memory values intact too.
        analysis_client = prompt.prompt_set.client
        rendered_text = payload.rendered_text
        db.expire_on_commit = False
        db.commit()
        logger.info(
            "Run %s succeeded in %sms",
            run.id,
            run.latency_ms,
            extra={"extra_data": {"run_id": run.id, "latency_ms": run.latency_ms}},
        )

        try:
            _run_active_analysis_skills(db, raw_response_id, rendered_text, citations, analysis_client)
        except Exception as exc:  # analysis is a best-effort derived layer — never fail the run over it
            logger.error(
                "Analysis skills failed for run %s: %s",
                run.id,
                exc,
                exc_info=True,
                extra={"extra_data": {"run_id": run.id}},
            )

    return run
