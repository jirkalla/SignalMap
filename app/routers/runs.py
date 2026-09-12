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
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.adapters import get_adapter, has_adapter
from app.analysis import get_runner, has_runner
from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import (
    AIModel,
    AnalysisResult,
    AnalysisSkill,
    Citation,
    Client,
    Market,
    Provider,
    RawResponse,
    Run,
    SearchQuery,
    SystemInstructionTemplate,
    User,
)
from app.routers.clients import _get_client_or_404
from app.routers.prompts import _get_prompt_or_404
from app.routers.settings import DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE
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


def _market_system_instruction(db: Session, provider: Provider, market: Market) -> str | None:
    """Build a locale-framing hint from a prompt's market, using `provider`'s

    editable template (see /settings and app/models/settings.py). No saved
    row yet -> the built-in DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE (app.routers.
    settings). A row with an empty template -> None, meaning no
    system_instruction is sent for this provider at all.

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


@router.post("/prompts/{prompt_id}/runs", dependencies=_editor_or_admin)
def trigger_run(
    request: Request,
    prompt_id: int,
    model_id: int = Form(..., description="Which seeded AI model to run this prompt against."),
    market_id: int = Form(
        ..., description="Market to run under — defaults to the prompt's own market but can be overridden per run."
    ),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Run a prompt against the selected model and market, and store the result (FR-7..FR-16).

    Always creates a Run row, whether the provider call succeeds or fails —
    a failed call is recorded with status='error' and a stored error
    message, never silently dropped. The market used is recorded on the
    run itself, so overriding it for one run never changes the prompt's
    own market or any other run's history. `triggered_by_user_id` records who
    ran it (docs/TASKS_PHASE6.md P6-T7) — nullable on the model itself for a
    future scheduler-triggered run with no human behind it, not relevant here
    since every manual trigger has a logged-in user.
    """
    t = get_t(request)
    prompt = _get_prompt_or_404(db, request, prompt_id)

    model = db.get(AIModel, model_id)
    if model is None:
        raise AppError("model_not_found", t("errors.model_not_found"), status_code=400)
    if not has_adapter(model.provider.code):
        raise AppError("provider_not_supported", t("errors.provider_not_supported"), status_code=400)

    market = db.get(Market, market_id)
    if market is None:
        raise AppError("market_not_found", t("errors.market_not_found"), status_code=400)

    system_instruction = _market_system_instruction(db, model.provider, market)
    request_payload = {
        "model": model.model_name,
        "prompt_text": prompt.text,
        "system_instruction": system_instruction,
        "market_country": market.country,
    }

    run = Run(
        prompt_id=prompt.id,
        model_id=model.id,
        market_id=market.id,
        trigger_type="manual",
        status="pending",
        request_payload=request_payload,
        triggered_by_user_id=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

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
