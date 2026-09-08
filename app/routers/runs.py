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
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import ADAPTERS, get_adapter
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Citation, Market, RawResponse, Run
from app.routers.prompts import _get_prompt_or_404
from app.templating import get_t, render

router = APIRouter(tags=["runs"])


def _market_system_instruction(market: Market) -> str:
    """Build a locale-framing hint from a prompt's market.

    Text-only hint, not real geographic search bias — see the docstring on
    ProviderAdapter.run for why (Gemini's grounding tool has no location
    parameter at all; Anthropic's web_search tool does and should use its
    real `user_location` instead of this once that adapter exists).
    """
    where = market.label or market.code
    return (
        f"The person asking this question is located in {where} and writing in "
        f"{market.language}. Answer in {market.language}, using regional context and "
        f"examples relevant there where applicable."
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
    db: Session = Depends(get_db),
):
    """Run a prompt against the selected model and store the result (FR-7..FR-16).

    Always creates a Run row, whether the provider call succeeds or fails —
    a failed call is recorded with status='error' and a stored error
    message, never silently dropped.
    """
    t = get_t(request)
    prompt = _get_prompt_or_404(db, request, prompt_id)

    model = db.get(AIModel, model_id)
    if model is None:
        raise AppError("model_not_found", t("errors.model_not_found"), status_code=400)
    if model.provider.code not in ADAPTERS:
        raise AppError("provider_not_supported", t("errors.provider_not_supported"), status_code=400)

    run = Run(prompt_id=prompt.id, model_id=model.id, trigger_type="manual", status="pending")
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.perf_counter()
    try:
        adapter = get_adapter(model.provider.code)
        payload = adapter.run(
            prompt_text=prompt.text,
            model_name=model.model_name,
            system_instruction=_market_system_instruction(prompt.market),
        )
    except Exception as exc:  # provider/transport failure — record it, don't raise (FR-16)
        run.status = "error"
        run.error_message = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        db.commit()
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
    return render(
        request,
        "runs/detail.html",
        {
            "run": run,
            "raw_response": raw_response,
            "citations": citations,
            "raw_payload_json": raw_payload_json,
        },
    )
