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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_adapter, has_adapter
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Citation, Market, Provider, RawResponse, Run, SystemInstructionTemplate
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

ExportFormat = Literal["csv", "xlsx", "json"]

_EXPORT_BUILDERS = {"csv": build_csv_zip, "xlsx": build_xlsx, "json": build_json}
_EXPORT_MEDIA_TYPES = {
    "csv": "application/zip",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}
_EXPORT_EXTENSIONS = {"csv": "zip", "xlsx": "xlsx", "json": "json"}


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


@router.post("/prompts/{prompt_id}/runs")
def trigger_run(
    request: Request,
    prompt_id: int,
    model_id: int = Form(..., description="Which seeded AI model to run this prompt against."),
    market_id: int = Form(
        ..., description="Market to run under — defaults to the prompt's own market but can be overridden per run."
    ),
    db: Session = Depends(get_db),
):
    """Run a prompt against the selected model and market, and store the result (FR-7..FR-16).

    Always creates a Run row, whether the provider call succeeds or fails —
    a failed call is recorded with status='error' and a stored error
    message, never silently dropped. The market used is recorded on the
    run itself, so overriding it for one run never changes the prompt's
    own market or any other run's history.
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
        for c in payload.citations:
            db.add(
                Citation(
                    raw_response_id=raw_response.id,
                    source_url=c.source_url,
                    source_title=c.source_title,
                    source_domain=c.source_domain,
                    citation_position=c.citation_position,
                    cited_answer_span=c.cited_answer_span,
                )
            )
        db.commit()
        logger.info(
            "Run %s succeeded in %sms",
            run.id,
            run.latency_ms,
            extra={"extra_data": {"run_id": run.id, "latency_ms": run.latency_ms}},
        )

    target_url = f"/runs/{run.id}"
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=200)
        response.headers["HX-Redirect"] = target_url
        return response
    return RedirectResponse(url=target_url, status_code=303)


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int, db: Session = Depends(get_db)):
    """Show one run: metadata, rendered answer, raw JSON, and citations (FR-14)."""
    run = _get_run_or_404(db, request, run_id)
    raw_response = db.scalars(select(RawResponse).where(RawResponse.run_id == run_id)).first()
    citations = (
        db.scalars(select(Citation).where(Citation.raw_response_id == raw_response.id).order_by(Citation.citation_position)).all()
        if raw_response
        else []
    )
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
            "citations": citations,
            "raw_payload_json": raw_payload_json,
            "request_payload_json": request_payload_json,
        },
    )


@router.get("/runs/{run_id}/export")
def export_run(
    request: Request,
    run_id: int,
    format: ExportFormat = Query(
        "json", description="Export file format: csv (zip of runs.csv + citations.csv), xlsx (workbook), or json."
    ),
    content: ExportContent = Query(
        "answer",
        description=(
            "How much of the run to include: answer (rendered text + citations + metadata), "
            "raw (untouched provider payload only), or full (both)."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Download this run as CSV, XLSX, or JSON (docs/TASKS_EXPORT.md EX-T2).

    Single-run scope of the runs export feature — see app/services/export.py
    for the shared query/serialization logic reused by the prompt- and
    client-scope exports (EX-T3/EX-T4).
    """
    runs = runs_for_run(db, run_id)
    if not runs:
        raise AppError("run_not_found", get_t(request)("errors.run_not_found"), status_code=404)

    ext = _EXPORT_EXTENSIONS[format]
    body = _EXPORT_BUILDERS[format](runs, content)
    filename = build_filename("run", str(run_id), content, ext)
    return Response(
        content=body,
        media_type=_EXPORT_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/prompts/{prompt_id}/runs/export")
def export_prompt_runs(
    request: Request,
    prompt_id: int,
    format: ExportFormat = Query(
        "json", description="Export file format: csv (zip of runs.csv + citations.csv), xlsx (workbook), or json."
    ),
    content: ExportContent = Query(
        "answer",
        description=(
            "How much of each run to include: answer (rendered text + citations + metadata), "
            "raw (untouched provider payload only), or full (both)."
        ),
    ),
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

    ext = _EXPORT_EXTENSIONS[format]
    body = _EXPORT_BUILDERS[format](runs, content)
    filename = build_filename("prompt", str(prompt_id), content, ext)
    return Response(
        content=body,
        media_type=_EXPORT_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/clients/{client_id}/runs/export")
def export_client_runs(
    request: Request,
    client_id: int,
    format: ExportFormat = Query(
        "json", description="Export file format: csv (zip of runs.csv + citations.csv), xlsx (workbook), or json."
    ),
    content: ExportContent = Query(
        "answer",
        description=(
            "How much of each run to include: answer (rendered text + citations + metadata), "
            "raw (untouched provider payload only), or full (both)."
        ),
    ),
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

    ext = _EXPORT_EXTENSIONS[format]
    body = _EXPORT_BUILDERS[format](runs, content)
    filename = build_filename("client", client.slug, content, ext)
    return Response(
        content=body,
        media_type=_EXPORT_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
