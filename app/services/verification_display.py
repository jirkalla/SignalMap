"""Turns stored citation-verification data into what the run detail page needs (docs/

TASKS_CITATION_VERIFICATION.md T9) — kept out of app/routers/runs.py (routers stay thin,
signalmap-conventions skill) since this is real assembly logic, not "parse Form, call ORM,
render". The router calls `build_verification_display` once and hands the result to the
template; nothing here talks HTML or i18n — that split matches every other router/template pair
in this app (routers/services build data, templates format and translate it).
"""

from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.run import Citation, RawResponse
from app.models.verification import CitationVerification, SourceDocument, SourceText, VerificationJob
from app.services.claims import DerivedClaim

# The one place a new verdict (T10's archive_only/page_changed, T11/T12's llm_*, a future
# xAI reachability check) gets wired into the UI — one line here, nothing else to touch.
# `rank` picks which verdict's tone wins when several citations share one highlighted claim
# (design decision 18): lower rank is more attention-worthy and wins over a higher one.


@dataclass(frozen=True)
class VerdictStyle:
    tone: str  # key into TONE_BADGE_CLASSES / TONE_UNDERLINE_CLASSES below
    label_key: str  # i18n key, app/i18n/{en,de}.json
    rank: int


VERDICT_STYLES: dict[str, VerdictStyle] = {
    "not_found": VerdictStyle("red", "run.verdict_not_found", 0),
    "llm_not_supported": VerdictStyle("red", "run.verdict_llm_not_supported", 0),
    "llm_contradicted": VerdictStyle("red", "run.verdict_llm_contradicted", 0),
    "partially_found": VerdictStyle("amber", "run.verdict_partially_found", 1),
    "llm_partial": VerdictStyle("amber", "run.verdict_llm_partial", 1),
    "archive_only": VerdictStyle("amber", "run.verdict_archive_only", 1),
    "unverifiable": VerdictStyle("stone", "run.verdict_unverifiable", 2),
    "source_reachable": VerdictStyle("sky", "run.verdict_source_reachable", 2),
    "verified_exact": VerdictStyle("emerald", "run.verdict_verified_exact", 3),
    "verified_normalized": VerdictStyle("emerald", "run.verdict_verified_normalized", 3),
    "page_changed": VerdictStyle("emerald", "run.verdict_page_changed", 3),
    "llm_supported": VerdictStyle("emerald", "run.verdict_llm_supported", 3),
}

# Same five tones as the rest of the app's badge vocabulary (macros.html's status_badge/
# pill_badge/tag_badge: emerald/amber/red/stone) plus "sky", already used elsewhere for
# informational-not-evaluative markers (macros.html's avatar_badge, schedule_target_cell) —
# deliberately not the citation-verification prototype's own 7-colour palette, so this reads as
# part of the same app, not a bolted-on design system.
TONE_BADGE_CLASSES = {
    "emerald": "bg-emerald-100 text-emerald-800",
    "amber": "bg-amber-100 text-amber-800",
    "red": "bg-red-100 text-red-800",
    "stone": "bg-stone-100 text-stone-600",
    "sky": "bg-sky-50 text-sky-700",
}
TONE_UNDERLINE_CLASSES = {
    "emerald": "decoration-emerald-500 hover:bg-emerald-50",
    "amber": "decoration-amber-500 hover:bg-amber-50",
    "red": "decoration-red-500 hover:bg-red-50",
    "stone": "decoration-stone-400 hover:bg-stone-100",
    "sky": "decoration-sky-500 hover:bg-sky-50",
}

_CONTEXT_CHARS = 150
_TEXT_FRAGMENT_CHARS = 80

# docs/TASKS_CITATION_VERIFICATION.md T16 — the one place the 12-value verdict enum (design
# decision 28) collapses into the 4 buckets the dashboard/`/ops` aggregations show ("unverifiable"
# is deliberately its own bucket, never folded into "unsupported" — a page that failed to load
# says nothing about whether the claim it would have supported is true or false). Adding a new
# verdict without adding it here just makes it vanish from every T16 aggregate rather than error,
# same trade-off `VERDICT_STYLES` above already accepts for the run detail page.
VERDICT_BUCKETS: dict[str, str] = {
    "verified_exact": "verified",
    "verified_normalized": "verified",
    "page_changed": "verified",
    "archive_only": "verified",
    "llm_supported": "verified",
    "partially_found": "partial",
    "llm_partial": "partial",
    "not_found": "unsupported",
    "llm_not_supported": "unsupported",
    "llm_contradicted": "unsupported",
    "unverifiable": "unverifiable",
    # 'source_reachable' (xAI) deliberately absent — design decision 29 keeps it out of this
    # bucketing entirely, aggregated on its own (app.services.dashboard.xai_reviewed_sources_count).
}


def latest_verification_query() -> Select:
    """One row per citation — the newest `CitationVerification` (append-only, NFR-6) — via
    Postgres `DISTINCT ON`, for aggregations across many citations at once
    (app.services.dashboard/app.services.ops_dashboard, T16) where `latest_verifications`'s
    per-response Python dict would mean fetching every historical row for a whole client's runs
    just to throw all but the newest away.

    Returns a bare, unscoped `Select` — callers `.join(Citation, ...).join(RawResponse, ...)
    .where(RawResponse.run_id.in_(run_ids_query))` on top of this before wrapping it in
    `.subquery()`, the same "shared base, caller adds its own scope" shape
    `_mention_visibility_base_query` already uses. The scoping join/where MUST be added before
    Postgres picks "latest" — DISTINCT ON operates on the already-filtered rows, so a client's
    scope has to be part of the same statement, not applied afterwards.
    """
    return (
        select(CitationVerification)
        .distinct(CitationVerification.citation_id)
        .order_by(CitationVerification.citation_id, CitationVerification.created_at.desc())
    )


def latest_verifications(db: Session, raw_response_id: int) -> dict[int, CitationVerification]:
    """The newest CitationVerification per citation_id for this response — append-only (design

    decision 2), so "latest" is the only one display ever needs. `(citation_id, created_at
    DESC)` (migration 0037) is exactly the index this ORDER BY wants.
    """
    rows = db.scalars(
        select(CitationVerification)
        .join(Citation, Citation.id == CitationVerification.citation_id)
        .where(Citation.raw_response_id == raw_response_id)
        .order_by(CitationVerification.citation_id, CitationVerification.created_at.desc())
    ).all()
    latest: dict[int, CitationVerification] = {}
    for row in rows:
        latest.setdefault(row.citation_id, row)  # first row per id wins = newest, given the ORDER BY
    return latest


def is_capture_pending(db: Session, raw_response_id: int, *, has_verifications: bool) -> bool:
    """Whether this response's sources are still waiting on their capture job — the run detail

    page shows a "waiting for capture" state instead of the verification summary while this is
    True, rather than a misleading "nothing to verify".

    `has_verifications` short-circuits this to False regardless of the job row: a handful of
    pre-T5 runs (backfilled or verified before `enqueue_capture`, T3, started running after every
    trigger) have `citation_verifications` rows but no matching `verification_jobs` row at all —
    without this check, `job is None` below would call a run that's already fully verified
    "pending" and hide its verdicts behind a "check back in a minute" message that will never
    resolve (found manually, 2026-09-29, walking through run 108 in the browser).
    """
    if has_verifications:
        return False
    job = db.scalar(
        select(VerificationJob)
        .where(VerificationJob.raw_response_id == raw_response_id, VerificationJob.kind == "capture")
        .order_by(VerificationJob.created_at.desc())
        .limit(1)
    )
    return job is None or job.status in ("queued", "leased", "deferred")


@dataclass(frozen=True)
class ClaimGroup:
    """One highlighted region of the rendered answer — one or more citations whose derived claim

    (app/services/claims.py) covers the exact same span, shown as a single underline with one
    citation-number button per citation (design decision 18).
    """

    start: int
    end: int
    citation_ids: list[int]
    tone: str


def _group_tone(citation_ids: list[int], verifications: dict[int, CitationVerification]) -> str:
    styles = [
        VERDICT_STYLES[verifications[cid].verdict]
        for cid in citation_ids
        if cid in verifications and verifications[cid].verdict in VERDICT_STYLES
    ]
    if not styles:
        return "stone"  # none of this claim's citations have a verdict yet (still capturing)
    return min(styles, key=lambda style: style.rank).tone


def group_claims(
    citations: list[Citation], claims: dict[int, DerivedClaim | None], verifications: dict[int, CitationVerification]
) -> list[ClaimGroup]:
    """Group citations by identical derived-claim span, in `citations`' own order (citation_position)

    — `citation_ids` within a group therefore lists citations in the same order the citation list
    below already shows them. A later group that would start before an earlier one already ended
    is dropped rather than drawn overlapping: `derive_claim` can occasionally produce spans that
    overlap for adjacent citations, and nested `<span>` markup around overlapping ranges has no
    well-defined rendering.
    """
    spans: dict[tuple[int, int], list[int]] = {}
    for citation in citations:
        claim = claims.get(citation.id)
        if claim is None:
            continue
        spans.setdefault((claim.start, claim.end), []).append(citation.id)

    ordered = sorted(spans.items(), key=lambda item: (item[0][0], -item[0][1]))
    groups: list[ClaimGroup] = []
    cursor = 0
    for (start, end), citation_ids in ordered:
        if start < cursor:
            continue
        groups.append(ClaimGroup(start=start, end=end, citation_ids=citation_ids, tone=_group_tone(citation_ids, verifications)))
        cursor = end
    return groups


@dataclass(frozen=True)
class EvidenceContext:
    before: str
    matched: str
    after: str


@dataclass(frozen=True)
class Evidence:
    """Everything the "Evidence" panel shows for one citation — raw values only, the template

    does the t()/number formatting (this module never imports app.templating, same split as
    every other service in this app).

    `verification_id` (T14) is the underlying `CitationVerification.id`, `None` while pending —
    the run detail page's "Souhlasím / Nesouhlasím" control (app/routers/verification.py) needs
    it to record exactly which verdict a human reviewed, not just its value.
    """

    citation_id: int
    verification_id: int | None
    verdict: str | None
    reason: str | None
    tone: str
    label_key: str
    similarity: float | None
    claim_text: str | None
    context: EvidenceContext | None
    location_path: list[str]
    collapsed_title: str | None
    page_number: int | None
    open_url: str | None
    resolved_url: str | None
    http_status: int | None
    chars: int | None
    duration_ms: int | None


def _open_url(document: SourceDocument | None, verification: CitationVerification) -> str | None:
    """A link that jumps straight to the matched passage — a native browser text-fragment

    (`#:~:text=`) for HTML, `#page=N` for a PDF.

    For a live document, built on `document.requested_url` (== the citation's own link), NEVER
    `document.final_url` — design decision 6 forbids substituting Google's own grounding-redirect
    link with anything else as the clickable target. `final_url` is surfaced separately, as plain
    text only, via `Evidence.resolved_url`.

    For an archive.org-backed document (`method='archive'`, T10), `requested_url` is the original
    live URL — exactly the page that's now gone or changed, not useful to link to at all — so
    `final_url` (the human-viewable Wayback page app/services/citation_verification.py's
    `_store_archive_document` stores there) is used instead. Design decision 6 doesn't apply
    here: it protects a provider's own citation link, not an archive.org fallback page.
    """
    if document is None:
        return None
    base = document.final_url if document.method == "archive" and document.final_url else document.requested_url
    if verification.page_number is not None:
        return f"{base}#page={verification.page_number}"
    if verification.matched_text:
        snippet = verification.matched_text.strip()[:_TEXT_FRAGMENT_CHARS]
        return f"{base}#:~:text={quote(snippet)}"
    return None


def _context(source_text: str, start: int, end: int) -> EvidenceContext:
    return EvidenceContext(
        before=source_text[max(0, start - _CONTEXT_CHARS) : start],
        matched=source_text[start:end],
        after=source_text[end : end + _CONTEXT_CHARS],
    )


def build_evidence(
    db: Session, citation: Citation, claim: DerivedClaim | None, verification: CitationVerification | None
) -> Evidence:
    if verification is None:
        return Evidence(
            citation_id=citation.id,
            verification_id=None,
            verdict=None,
            reason=None,
            tone="stone",
            label_key="run.verdict_pending",
            similarity=None,
            claim_text=claim.text if claim else None,
            context=None,
            location_path=[],
            collapsed_title=None,
            page_number=None,
            open_url=None,
            resolved_url=None,
            http_status=None,
            chars=None,
            duration_ms=None,
        )

    style = VERDICT_STYLES.get(verification.verdict)
    document = db.get(SourceDocument, verification.source_document_id) if verification.source_document_id else None

    context: EvidenceContext | None = None
    chars: int | None = None
    if verification.match_start is not None and verification.match_end is not None and document is not None and document.text_sha256:
        source_text_row = db.get(SourceText, document.text_sha256)
        if source_text_row is not None:
            context = _context(source_text_row.text, verification.match_start, verification.match_end)
            chars = source_text_row.chars

    location: dict[str, Any] = verification.location or {}
    return Evidence(
        citation_id=citation.id,
        verification_id=verification.id,
        verdict=verification.verdict,
        reason=verification.reason,
        tone=style.tone if style else "stone",
        label_key=style.label_key if style else "run.verdict_unverifiable",
        similarity=float(verification.similarity) if verification.similarity is not None else None,
        claim_text=verification.claim_text or (claim.text if claim else None),
        context=context,
        location_path=[heading for heading in (location.get("headings") or []) if heading],
        collapsed_title=location.get("collapsed_title") if location.get("collapsed") else None,
        page_number=verification.page_number,
        open_url=_open_url(document, verification),
        # Plain-text-only "expanded address" (design decision 6) — only for a LIVE document that
        # actually followed a redirect somewhere else, so a non-redirecting URL doesn't show a
        # "resolves to" line pointing right back at the link already shown above it. Excludes
        # `method='archive'` (T10): there, `final_url` is the Wayback page _open_url already
        # links to above — showing it a second time as "resolves to" would be a confusing
        # duplicate of that link, not a Gemini-style redirect disclosure.
        resolved_url=(
            document.final_url
            if document and document.method == "live" and document.final_url and document.final_url != document.requested_url
            else None
        ),
        http_status=document.http_status if document else None,
        chars=chars,
        duration_ms=document.duration_ms if document else None,
    )


@dataclass(frozen=True)
class VerificationDisplay:
    capture_pending: bool
    groups: list[ClaimGroup]
    evidence: dict[int, Evidence]
    verdict_counts: dict[str, int]
    unique_url_count: int
    checked_count: int


def build_verification_display(
    db: Session, raw_response: RawResponse, citations: list[Citation], claims: dict[int, DerivedClaim | None]
) -> VerificationDisplay:
    verifications = latest_verifications(db, raw_response.id)
    verdict_counts = dict(Counter(v.verdict for v in verifications.values()))
    return VerificationDisplay(
        capture_pending=is_capture_pending(db, raw_response.id, has_verifications=bool(verifications)),
        groups=group_claims(citations, claims, verifications),
        evidence={citation.id: build_evidence(db, citation, claims.get(citation.id), verifications.get(citation.id)) for citation in citations},
        verdict_counts=verdict_counts,
        unique_url_count=len({citation.source_url for citation in citations if citation.source_url}),
        # "captured and checked" = has a verdict at all other than 'unverifiable' — an
        # approximation, not a separate capture-success count, but good enough for a one-line
        # summary (design decision-level detail lives in the citation list/evidence panel below).
        checked_count=sum(count for verdict, count in verdict_counts.items() if verdict != "unverifiable"),
    )
