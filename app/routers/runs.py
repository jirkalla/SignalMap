"""Run trigger + detail routes (docs/REQUIREMENTS.md FR-7..FR-16).

The run lifecycle is two-phase: a Run row is inserted as 'pending' before
the provider adapter is called, then updated in place to 'success'/'error'
once the call returns — see the note on app/models/run.py for why this
does not conflict with the "never overwrite evidence" rule. Either way the
request always redirects to the run's detail page, so the outcome (success
or a recorded error) is always visible in the UI, never silently missing
(FR-16).
"""

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.adapters import has_adapter
from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import (
    AIModel,
    AnalysisResult,
    Citation,
    Market,
    Persona,
    RawResponse,
    Run,
    SearchQuery,
    User,
)
from app.models.schedule import RunQueueItem, RunSchedule
from app.routers.clients import _get_client_or_404
from app.routers.prompts import _get_prompt_or_404
from app.services.export import (
    ExportContent,
    build_csv_zip,
    build_filename,
    build_json,
    build_xlsx,
    runs_for_client,
    runs_for_prompt,
    runs_for_run,
)
from app.services.run_execution import execute_run
from app.templating import get_t, render

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])

# Triggering a run spends real provider API budget; exporting is read-only but still gated
# (docs/ROADMAP.md §1 "who sees what" — viewer explicitly gets no export). Reused below
# instead of repeating the same Depends(...) call at each site (docs/TASKS_PHASE6.md P6-T6).
_editor_or_admin = [Depends(require_role("admin", "editor"))]

ExportFormat = Literal["csv", "xlsx", "json"]

_EXPORT_BUILDERS = {"csv": build_csv_zip, "xlsx": build_xlsx, "json": build_json}
_EXPORT_MEDIA_TYPES = {
    "csv": "application/zip",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}
_EXPORT_EXTENSIONS = {"csv": "zip", "xlsx": "xlsx", "json": "json"}

# Shared across all three export routes below (export_run/export_prompt_runs/
# export_client_runs) — reusing one Query() instance as a parameter default
# is the standard FastAPI pattern for identical params on multiple routes;
# FastAPI reads the parameter's name from the function signature, not from
# this object, so sharing it doesn't confuse which route/param it belongs to.
_EXPORT_FORMAT_QUERY = Query(
    "json", description="Export file format: csv (zip of runs.csv + citations.csv), xlsx (workbook), or json."
)
_EXPORT_CONTENT_QUERY = Query(
    "answer",
    description=(
        "How much of each run to include: answer (rendered text + citations + metadata), "
        "raw (untouched provider payload only), or full (both)."
    ),
)


def _export_response(runs: list[Run], scope: str, identifier: str, format: ExportFormat, content: ExportContent) -> Response:
    """Build the download Response shared by every export route: pick the writer/media type/extension for
    `format`, serialize `runs`, and set `Content-Disposition` from `build_filename` — the one piece of logic
    all three scopes (run/prompt/client) need identically, factored out so it isn't hand-copied three times.
    """
    ext = _EXPORT_EXTENSIONS[format]
    body = _EXPORT_BUILDERS[format](runs, content)
    filename = build_filename(scope, identifier, content, ext)
    return Response(
        content=body,
        media_type=_EXPORT_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _get_run_or_404(db: Session, request: Request, run_id: int) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise AppError("run_not_found", get_t(request)("errors.run_not_found"), status_code=404)
    return run


def _highlight_matches(text: str, spans: list[list[int]]) -> Markup:
    """Wrap `spans` (from a stored AnalysisResult.output["match_spans"]) in <mark>, HTML-escaping
    everything else. Never recomputes matching against the client's current aliases (design
    decision 12) — spans are exactly what was evidenced at compute time, so highlighting can't
    silently drift from what the stored AnalysisResult actually says.
    """
    if not spans:
        return escape(text)
    parts: list[Markup] = []
    cursor = 0
    for start, end in spans:
        parts.append(escape(text[cursor:start]))
        parts.append(Markup("<mark class=\"bg-amber-200 rounded px-0.5\">") + escape(text[start:end]) + Markup("</mark>"))
        cursor = end
    parts.append(escape(text[cursor:]))
    return Markup("").join(parts)


@router.post("/prompts/{prompt_id}/runs", dependencies=_editor_or_admin)
def trigger_run(
    request: Request,
    prompt_id: int,
    model_id: int = Form(..., description="Which seeded AI model to run this prompt against."),
    market_id: int = Form(
        ..., description="Market to run under — defaults to the prompt's own market but can be overridden per run."
    ),
    persona_id: int = Form(
        ...,
        description="Persona to frame the question as — defaults to the default persona but can be overridden per run.",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Run a prompt against the selected model, market, and persona, and store the result (FR-7..FR-16).

    Rejects an inactive prompt or an inactive model outright (409). `Prompt.is_active`
    (app/models/prompt.py) has always documented itself as "offer this prompt for new runs or
    not", but nothing actually enforced that until now. `AIModel.is_active` had the identical
    gap: `_runnable_model_groups` (app/routers/prompts.py) keeps an inactive model out of the
    run-trigger dropdown, but that's UI-only filtering — this handler never checked the
    submitted `model_id`'s own `is_active` flag server-side, so a deactivated model (e.g. one an
    admin turned off via /ai-models to stop further spend) could still be triggered by anyone
    who still had its id. Both flags are now enforced here, matching how
    `_run_active_analysis_skills` already skips an inactive `AnalysisSkill`.

    Always creates a Run row, whether the provider call succeeds or fails —
    a failed call is recorded with status='error' and a stored error
    message, never silently dropped. The market and persona used are
    recorded on the run itself, so overriding either for one run never
    changes the prompt's own market, the default persona, or any other
    run's history. `triggered_by_user_id` records who ran it
    (docs/TASKS_PHASE6.md P6-T7) — nullable on the model itself for a
    future scheduler-triggered run with no human behind it, not relevant here
    since every manual trigger has a logged-in user.

    Rejects triggering a second run on the same prompt **and model**
    combination while one is already `pending`
    (docs/TASKS_CHATGPT_PERSONA_PRICING.md CPH-T9,
    docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T1) — this endpoint blocks
    synchronously on the provider call (5-28s observed against real
    providers), with no visual feedback beyond the run-trigger form's own
    JS button-disable, so a double-click or a second browser tab could
    otherwise fire a second, real, paid run against the exact same prompt
    and model. Scoped to `model_id` (not just `prompt_id`) so that
    triggering several different models for the same prompt at once
    (BIM-T2) can run concurrently instead of blocking each other. The
    `SELECT`-then-`INSERT` guard alone has a TOCTOU race window for two
    genuinely concurrent requests; migration 0024's partial unique index
    (`idx_runs_one_pending_per_prompt_model`) closes that at the database
    level, and the `IntegrityError` it can raise on commit is caught below
    and converted to the same 409.
    """
    t = get_t(request)
    prompt = _get_prompt_or_404(db, request, prompt_id)

    if not prompt.is_active:
        raise AppError("prompt_inactive", t("errors.prompt_inactive"), status_code=409)

    pending_run = db.scalar(
        select(Run.id)
        .where(Run.prompt_id == prompt_id, Run.model_id == model_id, Run.status == "pending")
        .limit(1)
    )
    if pending_run is not None:
        raise AppError("run_already_pending", t("errors.run_already_pending"), status_code=409)

    model = db.get(AIModel, model_id)
    if model is None:
        raise AppError("model_not_found", t("errors.model_not_found"), status_code=400)
    if not model.is_active:
        raise AppError("model_inactive", t("errors.model_inactive"), status_code=409)
    if not has_adapter(model.provider.code):
        raise AppError("provider_not_supported", t("errors.provider_not_supported"), status_code=400)

    market = db.get(Market, market_id)
    if market is None:
        raise AppError("market_not_found", t("errors.market_not_found"), status_code=400)

    persona = db.get(Persona, persona_id)
    if persona is None:
        raise AppError("persona_not_found", t("errors.persona_not_found"), status_code=400)

    try:
        run = execute_run(
            db,
            prompt=prompt,
            model=model,
            market=market,
            persona=persona,
            trigger_type="manual",
            triggered_by_user_id=user.id,
        )
    except IntegrityError as exc:
        # Backstop for the race the plain pending_run SELECT above can't fully close (BIM
        # code-review finding, migration 0024's idx_runs_one_pending_per_prompt_model): a
        # second request for the same prompt+model that passed the SELECT before this one
        # committed hits the partial unique index instead, converted to the same 409 a
        # non-racy duplicate submission already gets. Scoped to that ONE constraint by name
        # (code-review fix, 2026-09-15) — a bare `except IntegrityError` here also caught an
        # unrelated FK violation (e.g. the model/market/persona row being deleted by someone
        # else between this handler's own db.get() checks and this commit) and misreported it
        # as "run already pending" while silently discarding the real cause; any other
        # integrity error is logged and surfaced as a distinct, generic failure instead.
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "idx_runs_one_pending_per_prompt_model":
            raise AppError("run_already_pending", t("errors.run_already_pending"), status_code=409) from exc
        logger.exception("Unexpected integrity error creating run for prompt_id=%s, model_id=%s", prompt_id, model_id)
        raise AppError("run_creation_failed", t("errors.run_creation_failed"), status_code=409) from exc

    target_url = f"/runs/{run.id}"
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=200)
        response.headers["HX-Redirect"] = target_url
        return response
    return RedirectResponse(url=target_url, status_code=303)


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int, db: Session = Depends(get_db)):
    """Show one run: metadata, rendered answer, raw JSON, citations, and search queries (FR-14)."""
    run = _get_run_or_404(db, request, run_id)
    raw_response = db.scalars(select(RawResponse).where(RawResponse.run_id == run_id)).first()
    citations = (
        db.scalars(select(Citation).where(Citation.raw_response_id == raw_response.id).order_by(Citation.citation_position)).all()
        if raw_response
        else []
    )
    search_queries = (
        db.scalars(
            select(SearchQuery).where(SearchQuery.raw_response_id == raw_response.id).order_by(SearchQuery.query_position)
        ).all()
        if raw_response
        else []
    )
    analysis_results = (
        db.scalars(
            select(AnalysisResult)
            .where(AnalysisResult.raw_response_id == raw_response.id)
            .options(joinedload(AnalysisResult.analysis_skill))
        ).all()
        if raw_response
        else []
    )
    rendered_text_html = None
    if raw_response and raw_response.rendered_text:
        match_spans = next(
            (r.output.get("match_spans") for r in analysis_results if r.analysis_skill.key == "mention_visibility"),
            None,
        )
        rendered_text_html = _highlight_matches(raw_response.rendered_text, match_spans or [])
    competitive_result = next(
        (r for r in analysis_results if r.analysis_skill.key == "competitive_visibility"), None
    )
    # Every other skill's results (currently just mention_visibility) — competitive_visibility
    # gets its own section below, so it's excluded here rather than filtered again in the
    # template, which would leave the "Analysis" section heading rendered with an empty body
    # whenever a run has a competitive_visibility result and nothing else.
    single_entity_analysis_results = [r for r in analysis_results if r.analysis_skill.key != "competitive_visibility"]
    # Distinct from competitive_result.output.share_of_voice/position being None (which can also
    # legitimately mean "client itself wasn't mentioned") — this specifically flags "there is
    # nothing configured to compare against", so the template can show an explanatory empty
    # state instead of a technically-correct-but-misleading number (docs/TASKS_PHASE5.md P5-T6).
    tracked_entities_configured = bool(run.prompt.prompt_set.client.tracked_entities)
    raw_payload_json = json.dumps(raw_response.raw_payload, indent=2, ensure_ascii=False) if raw_response else None
    request_payload_json = (
        json.dumps(run.request_payload, indent=2, ensure_ascii=False) if run.request_payload else None
    )
    # `triggered_by_user_id IS NULL` on a scheduled run isn't "nobody" (design decision 21) — it's
    # the scheduler pseudo-user, and `run.triggered_by` alone has no path back to which schedule or
    # who set it up. Only looked up for scheduled runs: a manual run's RunQueueItem, if any, never
    # carries a schedule anyway (design decision 5).
    schedule_attribution = None
    if run.trigger_type == "scheduled":
        queue_item = db.scalars(
            select(RunQueueItem)
            .options(joinedload(RunQueueItem.schedule).joinedload(RunSchedule.created_by))
            .where(RunQueueItem.run_id == run.id)
        ).first()
        if queue_item is not None and queue_item.schedule is not None:
            schedule_attribution = {
                "schedule_id": queue_item.schedule.id,
                "created_by_name": queue_item.schedule.created_by.name,
            }
    return render(
        request,
        "runs/detail.html",
        {
            "run": run,
            "raw_response": raw_response,
            "rendered_text_html": rendered_text_html,
            "citations": citations,
            "search_queries": search_queries,
            "single_entity_analysis_results": single_entity_analysis_results,
            "competitive_result": competitive_result,
            "tracked_entities_configured": tracked_entities_configured,
            "raw_payload_json": raw_payload_json,
            "request_payload_json": request_payload_json,
            "schedule_attribution": schedule_attribution,
        },
    )


@router.get("/runs/{run_id}/export", dependencies=_editor_or_admin)
def export_run(
    request: Request,
    run_id: int,
    format: ExportFormat = _EXPORT_FORMAT_QUERY,
    content: ExportContent = _EXPORT_CONTENT_QUERY,
    db: Session = Depends(get_db),
):
    """Download this run as CSV, XLSX, or JSON (docs/TASKS_EXPORT.md EX-T2).

    Single-run scope of the runs export feature — see app/services/export.py
    for the shared query/serialization logic reused by the prompt- and
    client-scope exports (EX-T3/EX-T4), and `_export_response` above for the
    Response-building step every export route shares.
    """
    runs = runs_for_run(db, run_id)
    if not runs:
        raise AppError("run_not_found", get_t(request)("errors.run_not_found"), status_code=404)
    return _export_response(runs, "run", str(run_id), format, content)


@router.get("/prompts/{prompt_id}/runs/export", dependencies=_editor_or_admin)
def export_prompt_runs(
    request: Request,
    prompt_id: int,
    format: ExportFormat = _EXPORT_FORMAT_QUERY,
    content: ExportContent = _EXPORT_CONTENT_QUERY,
    versions: Literal["current", "all"] = Query(
        "current",
        description=(
            "Which prompt versions to include: 'current' exports only the exact version named by "
            "prompt_id (matching what this page shows); 'all' walks the prompt's full edit history."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Download every run of one prompt as CSV, XLSX, or JSON (docs/TASKS_EXPORT.md EX-T3).

    Defaults to the exact prompt version in the URL — "export what you
    see" (docs/TASKS_EXPORT.md design decision 5) — rather than the whole
    version lineage; `versions=all` opts into that wider scope. A prompt
    with no runs yet still produces a valid, empty export, not an error.
    """
    _get_prompt_or_404(db, request, prompt_id)
    runs = runs_for_prompt(db, prompt_id, all_versions=(versions == "all"))
    return _export_response(runs, "prompt", str(prompt_id), format, content)


@router.get("/clients/{client_id}/runs/export", dependencies=_editor_or_admin)
def export_client_runs(
    request: Request,
    client_id: int,
    format: ExportFormat = _EXPORT_FORMAT_QUERY,
    content: ExportContent = _EXPORT_CONTENT_QUERY,
    db: Session = Depends(get_db),
):
    """Download every run belonging to one client as CSV, XLSX, or JSON (docs/TASKS_EXPORT.md EX-T4).

    The widest export scope — every run across every prompt set, prompt,
    and prompt version under this client, regardless of provider. Unlike
    the prompt-scope export there is no version filter to choose: a client
    export already spans every version of every prompt it owns. A client
    with no runs yet still produces a valid, empty export, not an error.
    """
    client = _get_client_or_404(db, request, client_id)
    runs = runs_for_client(db, client.id)
    return _export_response(runs, "client", client.slug, format, content)
