"""Human verdicts on citation checks (docs/TASKS_CITATION_VERIFICATION.md T14, design decision

27) — the gate before `auto_verify_citations` (T13) is trusted beyond Knauf's own pilot. Two
flows:

- Blind labeling (`/verification/label`): a stratified sample of already LLM-judged citations,
  shown WITHOUT the LLM's own verdict/reason/quote — only the claim, the captured page's nearest
  passages (the same evidence `judge_citations`, T12, saw), and a link to the source.
- Reviewing one specific LLM verdict already on screen (the run detail page's "Souhlasím /
  Nesouhlasím") — `agrees_with_verification_id` records exactly which `CitationVerification` the
  human is agreeing or disagreeing with.

Both write `VerificationLabel` rows (append-only, NFR-6) — never `CitationVerification` itself,
which stays exactly what a verifier (quote/LLM) produced.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Citation, Provider, RawResponse, Run, User
from app.models.verification import HUMAN_VERDICTS, CitationVerification, SourceDocument, SourceText, VerificationLabel
from app.services.claims import derive_claim
from app.services.passages import select_passages
from app.templating import get_t, render

router = APIRouter(tags=["verification"])

_editor_or_admin = [Depends(require_role("admin", "editor"))]

# The inverse of app/services/claim_judge.py's own verdict map — "Souhlasím" copies the LLM's
# verdict across in the human's own vocabulary (design decision 22's four words), so agreement is
# just `verdict == <the human-vocabulary form of the LLM's own verdict>`, no separate boolean.
_LLM_TO_HUMAN_VERDICT = {
    "llm_supported": "supported",
    "llm_partial": "partially_supported",
    "llm_not_supported": "not_supported",
    "llm_contradicted": "contradicted",
}


def _citation_strata(db: Session) -> dict[int, tuple[str, str]]:
    """`citation_id -> (provider_code, latest LLM verdict)` for every citation with a REAL LLM

    judgement — the universe both the stratified sample and the per-stratum label counts are
    drawn from. Deliberately excludes `check_type='llm'` rows whose own verdict is `unverifiable`
    (found manually, 2026-09-29, checking real data before the browser walkthrough: `judge_citations`
    writes one of THOSE when the LLM was never actually asked to judge anything, e.g. its source
    capture failed) — there is no judgement there for a human to agree or disagree with, so
    offering one for blind labeling would ask someone to evaluate a citation the LLM never
    actually reached an opinion on.

    Loaded into Python (not aggregated in SQL) since at today's/near-term scale (T15's own
    ~100-citation session) this is a few hundred rows at most, and the stratification logic below
    is far simpler to get right this way than as one aggregate query.
    """
    rows = db.execute(
        select(CitationVerification.citation_id, CitationVerification.verdict, Provider.code)
        .join(Citation, CitationVerification.citation_id == Citation.id)
        .join(RawResponse, Citation.raw_response_id == RawResponse.id)
        .join(Run, RawResponse.run_id == Run.id)
        .join(AIModel, Run.model_id == AIModel.id)
        .join(Provider, AIModel.provider_id == Provider.id)
        .where(CitationVerification.check_type == "llm", CitationVerification.verdict.in_(_LLM_TO_HUMAN_VERDICT.keys()))
        .order_by(CitationVerification.citation_id, CitationVerification.created_at.desc())
    ).all()
    strata: dict[int, tuple[str, str]] = {}
    for citation_id, verdict, provider_code in rows:
        if citation_id not in strata:  # first row per citation_id is the newest (ORDER BY ... DESC)
            strata[citation_id] = (provider_code, verdict)
    return strata


def _next_blind_citation_id(db: Session, user_id: int) -> int | None:
    """The next citation to blind-label for `user_id` — from whichever (provider, LLM verdict)

    stratum has the FEWEST blind labels recorded so far across ALL users (design decision 27's
    "vzorek stratifikovaně"), so the accumulated sample stays balanced rather than skewing toward
    whichever combination happens to be most common. Excludes citations `user_id` already
    blind-labeled; a DIFFERENT user may still be offered the same one (T15's "~20 z nich nezávisle
    druhý člověk"). Ties broken by citation_id, so this is deterministic for tests.
    """
    strata = _citation_strata(db)
    if not strata:
        return None

    already_labeled = set(
        db.scalars(
            select(VerificationLabel.citation_id).where(VerificationLabel.user_id == user_id, VerificationLabel.mode == "blind")
        ).all()
    )
    eligible_ids = [citation_id for citation_id in strata if citation_id not in already_labeled]
    if not eligible_ids:
        return None

    stratum_counts: dict[tuple[str, str], int] = {}
    for citation_id in db.scalars(select(VerificationLabel.citation_id).where(VerificationLabel.mode == "blind")).all():
        stratum = strata.get(citation_id)
        if stratum is not None:
            stratum_counts[stratum] = stratum_counts.get(stratum, 0) + 1

    return min(eligible_ids, key=lambda citation_id: (stratum_counts.get(strata[citation_id], 0), citation_id))


def _latest_source_document(db: Session, url: str) -> SourceDocument | None:
    """Mirrors app/services/citation_verification.py's own `_latest_source_document` — kept as

    its own copy here rather than a shared import, same reasoning app/services/claim_judge.py's
    `_locate`/`_locate_page` already gave for not sharing small generic lookups across an
    unrelated module boundary.
    """
    return db.scalar(
        select(SourceDocument).where(SourceDocument.requested_url == url).order_by(SourceDocument.fetched_at.desc()).limit(1)
    )


@router.get("/verification/label", dependencies=_editor_or_admin)
def label_next_citation(request: Request, db: Session = Depends(get_db), user: User = Depends(current_active_user)):
    """Show the next citation for this user to blind-label, or an empty state when there is

    nothing left eligible (every LLM-judged citation has either been labeled by this user
    already, or none has ever been LLM-judged yet).
    """
    citation_id = _next_blind_citation_id(db, user.id)
    if citation_id is None:
        return render(request, "verification/label.html", {"citation": None})

    citation = db.get(Citation, citation_id)
    raw_response = db.get(RawResponse, citation.raw_response_id)
    provider_code = raw_response.run.model.provider.code
    derived_claim = derive_claim(provider_code, citation, raw_response.rendered_text, raw_response.raw_payload)

    passages: list[str] = []
    if citation.source_url and derived_claim is not None:
        document = _latest_source_document(db, citation.source_url)
        if document is not None and document.text_sha256:
            source_text_row = db.get(SourceText, document.text_sha256)
            if source_text_row is not None:
                passages = select_passages(derived_claim.text, source_text_row.text)

    return render(
        request,
        "verification/label.html",
        {
            "citation": citation,
            "claim_text": derived_claim.text if derived_claim else None,
            "passages": passages,
            "human_verdicts": HUMAN_VERDICTS,
        },
    )


@router.post("/verification/label", dependencies=_editor_or_admin)
def submit_blind_label(
    request: Request,
    citation_id: int = Form(..., description="The citation this blind label is about."),
    verdict: str = Form(..., description="One of the four human verdict words — required for a blind label."),
    note: str = Form("", description="Optional free-text note."),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Record one blind label and move on to the next citation (T14 point 2)."""
    t = get_t(request)
    if verdict not in HUMAN_VERDICTS:
        raise AppError("invalid_verdict", t("errors.invalid_verdict"), status_code=400)
    citation = db.get(Citation, citation_id)
    if citation is None:
        raise AppError("citation_not_found", t("errors.citation_not_found"), status_code=404)

    db.add(VerificationLabel(citation_id=citation_id, user_id=user.id, verdict=verdict, mode="blind", note=note.strip() or None))
    db.commit()
    return RedirectResponse(url="/verification/label", status_code=303)


@router.post("/runs/{run_id}/citations/{citation_id}/review", dependencies=_editor_or_admin)
def review_citation_verdict(
    request: Request,
    run_id: int,
    citation_id: int,
    agree: str = Form(..., description="'true' for Agree, 'false' for Disagree."),
    verdict: str = Form("", description="Corrected verdict — only meaningful (and optional) when disagreeing."),
    note: str = Form("", description="Optional free-text note."),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Record "Souhlasím / Nesouhlasím" (+ optionally the correct verdict) against the citation's

    own newest LLM verdict (T14 point 3). "Agree" copies that verdict across in the human
    vocabulary (`_LLM_TO_HUMAN_VERDICT`) rather than requiring the form to resubmit it — the
    server, not the client, is the source of truth for what the LLM actually said.
    """
    t = get_t(request)
    citation = db.scalar(
        select(Citation).join(RawResponse, Citation.raw_response_id == RawResponse.id).where(Citation.id == citation_id, RawResponse.run_id == run_id)
    )
    if citation is None:
        raise AppError("citation_not_found", t("errors.citation_not_found"), status_code=404)

    latest = db.scalar(
        select(CitationVerification)
        .where(CitationVerification.citation_id == citation_id, CitationVerification.check_type == "llm")
        .order_by(CitationVerification.created_at.desc())
        .limit(1)
    )
    if latest is None:
        raise AppError("citation_verification_not_found", t("errors.citation_verification_not_found"), status_code=404)

    if agree == "true":
        chosen_verdict = _LLM_TO_HUMAN_VERDICT.get(latest.verdict)
    elif verdict:
        if verdict not in HUMAN_VERDICTS:
            raise AppError("invalid_verdict", t("errors.invalid_verdict"), status_code=400)
        chosen_verdict = verdict
    else:
        chosen_verdict = None  # a bare "disagree", no correction offered — still real signal

    db.add(
        VerificationLabel(
            citation_id=citation_id, user_id=user.id, verdict=chosen_verdict, mode="review",
            agrees_with_verification_id=latest.id, note=note.strip() or None,
        )
    )
    db.commit()
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)
