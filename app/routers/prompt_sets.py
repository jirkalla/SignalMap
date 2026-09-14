"""PromptSet routes: create under a client, view detail, add prompts to it.

(docs/REQUIREMENTS.md FR-4..FR-6). Prompt editing lives on the prompt
detail route (app/routers/prompts.py) since editing creates a new version
rather than changing anything here.
"""

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.errors import AppError
from app.models import Market, Prompt, PromptSet, Run
from app.routers.clients import _get_client_or_404
from app.routers.prompts import _delete_prompt_lineage
from app.services.prompt_import import (
    MAX_IMPORT_FILE_BYTES,
    MAX_IMPORT_ROWS,
    PromptImportError,
    normalize_prompt_text,
    parse_csv,
    parse_json,
    parse_xlsx,
    validate_and_check_duplicates,
)
from app.templating import get_t, render
from app.utils import market_options

_IMPORT_PARSERS = {"csv": parse_csv, "xlsx": parse_xlsx, "json": parse_json}

# One example row, shared by all three template formats (BIM-T9) — always the same shape
# parse_csv/parse_xlsx/parse_json expect, generated through the same libraries they're read
# with, never hand-typed. The JSON variant keeps real types (bool, not the string "true") since
# JSON has no CSV/XLSX-style string-only cell ambiguity to begin with.
_TEMPLATE_HEADER = ["text", "market_code", "topic", "is_active"]
_TEMPLATE_EXAMPLE_ROW = [
    "What are the most sustainable construction companies in Europe?",
    "en-US",
    "Sustainability",
    "true",
]
_TEMPLATE_EXAMPLE_JSON_ROW = {
    "text": "What are the most sustainable construction companies in Europe?",
    "market_code": "en-US",
    "topic": "Sustainability",
    "is_active": True,
}

router = APIRouter(tags=["prompt-sets"])

# Reused on every create/edit/delete route below (docs/TASKS_PHASE6.md P6-T6) — viewer can read
# everything on this router, but not change anything.
_editor_or_admin = [Depends(require_role("admin", "editor"))]


def _get_prompt_set_or_404(db: Session, request: Request, prompt_set_id: int) -> PromptSet:
    prompt_set = db.get(PromptSet, prompt_set_id)
    if prompt_set is None:
        raise AppError("prompt_set_not_found", get_t(request)("errors.prompt_set_not_found"), status_code=404)
    return prompt_set


def _prompt_set_run_count(db: Session, prompt_set_id: int) -> int:
    """How many runs exist under any prompt of this prompt set — the delete-block check."""
    return (
        db.scalar(
            select(func.count(Run.id)).join(Prompt, Run.prompt_id == Prompt.id).where(Prompt.prompt_set_id == prompt_set_id)
        )
        or 0
    )


def _current_prompts(db: Session, prompt_set_id: int) -> list[Prompt]:
    """This prompt set's current-version prompts, newest first — shared by the detail and delete-blocked views."""
    return db.scalars(
        select(Prompt)
        .where(Prompt.prompt_set_id == prompt_set_id, Prompt.is_current_version.is_(True))
        .order_by(Prompt.created_at.desc())
    ).all()


def _prompt_set_detail_context(
    db: Session, prompt_set: PromptSet, prompts: list[Prompt], *, imported: int | None = None, error: str | None = None
) -> dict:
    """Shared render context for `prompt_sets/detail.html`, built by both `prompt_set_detail`
    and `delete_prompt_set`'s blocked-delete re-render — factored out so the two never drift on
    which keys the template expects (the search/filter bar's `distinct_topics`/`distinct_markets`
    in particular; a template that references them unconditionally would hit Jinja's default
    `Undefined` on any render call that forgot to pass them).
    """
    return {
        "prompt_set": prompt_set,
        "prompts": prompts,
        "markets": market_options(db),
        "imported": imported,
        "error": error,
        "distinct_topics": sorted({p.topic for p in prompts if p.topic}),
        "distinct_markets": sorted({p.market.code for p in prompts}),
    }


def _build_prompt(prompt_set_id: int, text: str, market_id: int, topic: str | None, is_active: bool) -> Prompt:
    """Shared `Prompt` construction for `create_prompt` and `import_prompts_confirm`
    (code-review fix, 2026-09-14) — both used to build this independently, and confirm's
    docstring claimed reuse that didn't actually exist. Always version 1 (the model's own
    default); `topic` tolerates `None` (confirm reads it from a possibly-absent form field,
    unlike `create_prompt`'s `Form("", ...)` default).
    """
    return Prompt(
        prompt_set_id=prompt_set_id,
        text=text.strip(),
        market_id=market_id,
        topic=(topic or "").strip() or None,
        is_active=is_active,
    )


@router.post("/clients/{client_id}/prompt-sets", dependencies=_editor_or_admin)
def create_prompt_set(
    request: Request,
    client_id: int,
    name: str = Form(..., description="Name for this group of prompts, e.g. 'Q1 2026 brand tracking'."),
    db: Session = Depends(get_db),
):
    """Create a new prompt set under a client (FR-4)."""
    client = _get_client_or_404(db, request, client_id)
    prompt_set = PromptSet(client_id=client.id, name=name.strip())
    db.add(prompt_set)
    db.commit()
    db.refresh(prompt_set)
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.get("/prompt-sets/{prompt_set_id}")
def prompt_set_detail(
    request: Request,
    prompt_set_id: int,
    imported: int | None = Query(None, description="Number of prompts just created by a bulk import — shows a summary banner."),
    db: Session = Depends(get_db),
):
    """Show one prompt set: its current-version prompts (FR-6) and the add-prompt form.

    Superseded versions (see app/models/prompt.py) are omitted here — reach
    them via the "version history" on a current prompt's detail page. `imported` is set by
    `import_prompts_confirm`'s redirect (BIM-T6) to show how many prompts the bulk import just
    created — absent on every other way of reaching this page.
    """
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    prompts = _current_prompts(db, prompt_set_id)
    return render(
        request,
        "prompt_sets/detail.html",
        _prompt_set_detail_context(db, prompt_set, prompts, imported=imported),
    )


@router.get("/prompt-sets/{prompt_set_id}/edit", dependencies=_editor_or_admin)
def edit_prompt_set_form(request: Request, prompt_set_id: int, db: Session = Depends(get_db)):
    """Render the prompt-set edit form, pre-filled with the current name."""
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    t = get_t(request)
    return render(
        request,
        "prompt_sets/form.html",
        {
            "title": t("prompt_set.edit_title"),
            "action": f"/prompt-sets/{prompt_set_id}/edit",
            "cancel_url": f"/prompt-sets/{prompt_set_id}",
            "prompt_set": prompt_set,
        },
    )


@router.post("/prompt-sets/{prompt_set_id}/edit", dependencies=_editor_or_admin)
def update_prompt_set(
    request: Request,
    prompt_set_id: int,
    name: str = Form(..., description="Name for this group of prompts, e.g. 'Q1 2026 brand tracking'."),
    db: Session = Depends(get_db),
):
    """Update a prompt set's name."""
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    prompt_set.name = name.strip()
    db.commit()
    return RedirectResponse(url=f"/prompt-sets/{prompt_set_id}", status_code=303)


@router.post("/prompt-sets/{prompt_set_id}/delete", dependencies=_editor_or_admin)
def delete_prompt_set(request: Request, prompt_set_id: int, db: Session = Depends(get_db)):
    """Delete a prompt set and its prompts, unless any of them has a recorded run.

    Blocked (inline error, not a raw API error) the same way client and
    market delete are — deleting evidence is never allowed (NFR-6).

    Prompts are deleted via `_delete_prompt_lineage` rather than left to a
    bare `db.delete(prompt_set)` cascade — see that helper's docstring
    (app/routers/prompts.py) for why a plain cascade would violate
    `fk_prompts_root_prompt_id_prompts` on any prompt with edit history.
    """
    t = get_t(request)
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    run_count = _prompt_set_run_count(db, prompt_set_id)
    if run_count:
        return render(
            request,
            "prompt_sets/detail.html",
            _prompt_set_detail_context(
                db,
                prompt_set,
                _current_prompts(db, prompt_set_id),
                error=t("errors.prompt_set_in_use").format(count=run_count),
            ),
            status_code=409,
        )
    client_id = prompt_set.client_id
    prompts = db.scalars(select(Prompt).where(Prompt.prompt_set_id == prompt_set_id)).all()
    _delete_prompt_lineage(db, prompts)
    db.delete(prompt_set)
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/prompt-sets/{prompt_set_id}/prompts", dependencies=_editor_or_admin)
def create_prompt(
    request: Request,
    prompt_set_id: int,
    text: str = Form(..., description="The exact question text sent to the AI provider."),
    market_id: int = Form(..., description="Which language/country market this prompt targets."),
    topic: str = Form("", description="Optional topic label for grouping/filtering prompts."),
    is_active: bool = Form(False, description="Inactive prompts are kept for history but not offered for new runs."),
    db: Session = Depends(get_db),
):
    """Add a new prompt to a prompt set (FR-5). Always created at version 1."""
    t = get_t(request)
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    market = db.get(Market, market_id)
    if market is None:
        raise AppError("market_not_found", t("errors.market_not_found"), status_code=400)
    prompt = _build_prompt(prompt_set.id, text, market.id, topic, is_active)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return RedirectResponse(url=f"/prompt-sets/{prompt_set_id}", status_code=303)


@router.get("/prompt-sets/{prompt_set_id}/prompts/import/template", dependencies=_editor_or_admin)
def import_template(
    request: Request,
    prompt_set_id: int,
    format: Literal["csv", "xlsx", "json"] = Query("csv", description="Template file format to download."),
    db: Session = Depends(get_db),
):
    """Download a correctly-formatted CSV/XLSX/JSON template for bulk prompt import (BIM-T9).

    Built through the same `csv.writer`/`openpyxl`/`json` machinery the app already parses
    with, so it's guaranteed well-formed — the cheapest way to avoid the class of
    hand-typed-CSV mistakes (unquoted commas, wrong delimiter) BIM-T8's sniffing/row-shape
    checks exist to catch after the fact. Nothing is persisted; the file is built in memory per
    request. JSON is included alongside CSV/XLSX for parity with the app's existing three-format
    export pattern (`export_button_group`), even though JSON's structure can't suffer the same
    delimiter/quoting ambiguity CSV can.
    """
    _get_prompt_set_or_404(db, request, prompt_set_id)
    if format == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(_TEMPLATE_HEADER)
        sheet.append(_TEMPLATE_EXAMPLE_ROW)
        buffer = io.BytesIO()
        workbook.save(buffer)
        content: bytes = buffer.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "prompt_import_template.xlsx"
    elif format == "json":
        content = json.dumps([_TEMPLATE_EXAMPLE_JSON_ROW], indent=2, ensure_ascii=False).encode("utf-8")
        media_type = "application/json"
        filename = "prompt_import_template.json"
    else:
        text_buffer = io.StringIO()
        csv.writer(text_buffer).writerows([_TEMPLATE_HEADER, _TEMPLATE_EXAMPLE_ROW])
        content = text_buffer.getvalue().encode("utf-8-sig")
        media_type = "text/csv"
        filename = "prompt_import_template.csv"
    return Response(
        content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/prompt-sets/{prompt_set_id}/prompts/import", dependencies=_editor_or_admin)
def import_prompts_form(request: Request, prompt_set_id: int, db: Session = Depends(get_db)):
    """Render the bulk prompt import upload form (docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T5).

    Lets an editor/admin pick a CSV/XLSX/JSON file plus a default market applied to any row
    that doesn't specify its own `market_code` column — nothing is parsed or saved until the
    file is submitted to the preview step below.
    """
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    return render(
        request,
        "prompt_sets/import.html",
        {"prompt_set": prompt_set, "markets": market_options(db)},
    )


@router.post("/prompt-sets/{prompt_set_id}/prompts/import/preview", dependencies=_editor_or_admin)
def import_prompts_preview(
    request: Request,
    prompt_set_id: int,
    file: UploadFile = File(..., description="CSV, XLSX, or JSON file with one prompt per row."),
    default_market_id: int = Form(
        ..., description="Market applied to any row that doesn't specify its own market_code."
    ),
    db: Session = Depends(get_db),
):
    """Parse an uploaded bulk-import file and show a preview of what would be created (BIM-T5).

    Validates rows and flags duplicates but writes nothing to the database — only the confirm
    step (BIM-T6) actually creates `Prompt` rows, and only for whichever rows are left checked
    on the preview screen. File size is enforced here via a bounded read (code-review fix,
    2026-09-14: `file.file.read()` used to read the whole upload into memory before the size
    check ran; `read(MAX_IMPORT_FILE_BYTES + 1)` now never materializes more than one byte past
    the limit, regardless of how large the real upload is). Row-count is enforced by the parsers
    themselves now, not here (see `app.services.prompt_import`'s module docstring) — a
    `PromptImportError("import_too_many_rows")` raised mid-parse is caught by the same
    `except PromptImportError` below as every other file-level parsing error.
    """
    t = get_t(request)
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    market = db.get(Market, default_market_id)
    if market is None:
        raise AppError("market_not_found", t("errors.market_not_found"), status_code=400)

    filename = file.filename or ""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    parser = _IMPORT_PARSERS.get(extension)
    if parser is None:
        raise AppError("import_parse_failed", t("errors.import_parse_failed"), status_code=400)

    content = file.file.read(MAX_IMPORT_FILE_BYTES + 1)
    if len(content) > MAX_IMPORT_FILE_BYTES:
        raise AppError(
            "import_file_too_large",
            t("errors.import_file_too_large").format(max_mb=MAX_IMPORT_FILE_BYTES // 1_000_000),
            status_code=400,
        )

    try:
        rows = parser(content)
    except PromptImportError as exc:
        raise AppError(
            exc.error_code, t(f"errors.{exc.error_code}").format(max_rows=MAX_IMPORT_ROWS), status_code=400
        ) from exc

    rows = validate_and_check_duplicates(rows, db, prompt_set_id, market.id)

    return render(
        request,
        "prompt_sets/import_preview.html",
        {
            "prompt_set": prompt_set,
            "rows": rows,
            "markets": market_options(db),
            "new_count": sum(1 for r in rows if r.status == "new"),
            "duplicate_count": sum(1 for r in rows if r.status == "duplicate"),
            "error_count": sum(1 for r in rows if r.status == "error"),
        },
    )


@router.post("/prompt-sets/{prompt_set_id}/prompts/import/confirm", dependencies=_editor_or_admin)
async def import_prompts_confirm(request: Request, prompt_set_id: int, db: Session = Depends(get_db)):
    """Create `Prompt` rows from the bulk-import preview's checked rows (BIM-T6).

    The preview screen (BIM-T5) posts a variable number of rows as indexed fields
    (`rows-0-text`, `rows-1-text`, ...) rather than a fixed set of `Form(...)` parameters, so
    this reads `request.form()` directly and discovers which row indices exist from the
    `-text` keys actually present. `market_id` is **re-resolved against the database**, never
    trusted from the resubmitted form value — the browser round-trip in between is not a
    security boundary (design decision 12). Only rows with `include=true` become `Prompt`
    rows, always at version 1, via `_build_prompt` — the same helper `create_prompt` uses
    (code-review fix, 2026-09-14: this docstring used to claim that reuse without it actually
    existing). A row missing text or a valid market is silently skipped rather than erroring
    the whole confirm — the preview screen is what already told the user which rows would
    import cleanly.

    Three more code-review fixes, 2026-09-14: (1) a malformed `rows-{i}-*` field name/value
    (e.g. a non-numeric index or market id) no longer crashes the whole request with an
    unhandled 500 — it's skipped like any other invalid row, matching this docstring's own
    "silently skipped" claim, which the unguarded `int()` calls used to contradict. (2) The
    number of rows is capped at `MAX_IMPORT_ROWS`, same as the preview step — previously this
    route had no limit at all, so posting directly here (bypassing preview) could create an
    unbounded number of `Prompt` rows in one request. (3) Duplicate detection is re-run against
    the database's current state at confirm time (via `normalize_prompt_text`, the same
    normalization `validate_and_check_duplicates` uses for preview), not just trusted from the
    preview snapshot — a row that became a duplicate in the time between preview and confirm
    (e.g. a concurrent import) is skipped rather than creating a second copy.
    """
    t = get_t(request)
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    form = await request.form()

    row_indices: set[int] = set()
    for key in form.keys():
        if key.startswith("rows-") and key.endswith("-text"):
            try:
                row_indices.add(int(key.split("-", 2)[1]))
            except ValueError:
                continue  # malformed field name — ignore rather than crash the whole request
    row_indices = sorted(row_indices)

    if len(row_indices) > MAX_IMPORT_ROWS:
        raise AppError(
            "import_too_many_rows", t("errors.import_too_many_rows").format(max_rows=MAX_IMPORT_ROWS), status_code=400
        )

    existing_texts_by_market: dict[int, set[str]] = {}

    def existing_texts(market_id: int) -> set[str]:
        if market_id not in existing_texts_by_market:
            texts = db.scalars(
                select(Prompt.text).where(
                    Prompt.prompt_set_id == prompt_set.id,
                    Prompt.market_id == market_id,
                    Prompt.is_current_version.is_(True),
                )
            ).all()
            existing_texts_by_market[market_id] = {normalize_prompt_text(text) for text in texts}
        return existing_texts_by_market[market_id]

    imported = 0
    for i in row_indices:
        if form.get(f"rows-{i}-include") != "true":
            continue
        text = str(form.get(f"rows-{i}-text") or "").strip()
        if not text:
            continue
        market_id_raw = form.get(f"rows-{i}-market_id")
        market = None
        if market_id_raw:
            try:
                market = db.get(Market, int(market_id_raw))
            except ValueError:
                market = None
        if market is None:
            continue

        normalized = normalize_prompt_text(text)
        seen = existing_texts(market.id)
        if normalized in seen:
            continue  # duplicate against the DB's current state, or against an earlier row in this same confirm
        seen.add(normalized)

        topic = form.get(f"rows-{i}-topic")
        is_active = form.get(f"rows-{i}-is_active") == "true"
        db.add(_build_prompt(prompt_set.id, text, market.id, topic, is_active))
        imported += 1

    db.commit()
    return RedirectResponse(url=f"/prompt-sets/{prompt_set_id}?imported={imported}", status_code=303)
