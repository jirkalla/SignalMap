"""CSV/XLSX/JSON parsing and duplicate detection for bulk prompt import
(docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T4).

Parsing (`parse_csv`/`parse_xlsx`/`parse_json`) never touches the database — each returns
`ParsedPromptRow` objects with only file-derived fields set (`market_code` is the raw string
from the file, not yet resolved to a `Market` id). `validate_and_check_duplicates` is the one
function that talks to the database, resolving market codes and flagging duplicates against
both existing prompts and other rows in the same batch — kept separate so parsing stays
unit-testable without a database session (design decisions 7-10).

This module has no `Request`/`t()` access, so it never builds user-facing text itself —
`PromptImportError` carries only an English `error_code` (plus optional interpolation kwargs);
the route that calls this module (BIM-T5) is responsible for turning that into
`t(f"errors.{error_code}")`, per the project's convention that localization happens at the
request layer, not in backend services (AI_INSTRUCTIONS.md §3). Row/file size limits
(`MAX_IMPORT_ROWS`/`MAX_IMPORT_FILE_BYTES`) are enforced by that same caller, not here (design
decision 13) — this module only ever sees a file that already passed those checks.
"""

import csv
import io
import json
from dataclasses import dataclass
from typing import Literal

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Market, Prompt

MAX_IMPORT_ROWS = 500
MAX_IMPORT_FILE_BYTES = 2_000_000

RowStatus = Literal["new", "duplicate", "error"]

_TRUE_VALUES = {"true", "1", "yes"}
_FALSE_VALUES = {"false", "0", "no"}


class PromptImportError(Exception):
    """A recoverable, user-facing problem found while parsing an uploaded bulk-import file.

    Only ever raised for file-level problems (unreadable encoding, wrong shape, no rows) — a
    problem with one specific row (bad market code, empty text) is never raised, only recorded
    on that `ParsedPromptRow` via `status`/`error_message` so the rest of the file still gets a
    preview (design decision that a single bad row must never block the whole import).
    """

    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)


@dataclass
class ParsedPromptRow:
    """One row parsed from an uploaded bulk-import file, before or after validation.

    `market_code` is the raw string from the file (or `None` if the column was absent/blank)
    until `validate_and_check_duplicates` resolves it into `market_id` — that function is also
    the only thing that ever sets `status` to `"duplicate"` or `"error"`; parsing alone always
    leaves a row at the `"new"` default.
    """

    row_number: int
    text: str
    market_code: str | None = None
    topic: str | None = None
    is_active: bool = True
    status: RowStatus = "new"
    error_message: str | None = None
    market_id: int | None = None


def _normalize_text(text: str) -> str:
    """Collapse whitespace and case-fold, so duplicate detection matches on meaning, not exact bytes."""
    return " ".join(text.split()).casefold()


def _parse_is_active(raw: str | None) -> bool:
    """`is_active` from a spreadsheet cell/CSV field is always text — accept the common spellings,
    default to active (matching `Prompt.is_active`'s own default) for anything blank or unrecognized
    rather than erroring an otherwise-valid row over one optional column.
    """
    if raw is None:
        return True
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return True


def parse_csv(file: bytes) -> list[ParsedPromptRow]:
    """Parse an uploaded CSV file into `ParsedPromptRow` objects (design decision 7).

    Expects a header row with a `text` column (required) plus optional `market_code`/`topic`/
    `is_active` columns, matched case-insensitively. Tries `utf-8-sig` first (handles a BOM some
    spreadsheet tools add), falling back to `cp1252` (common for CSVs exported by German-locale
    Excel) — a file in neither encoding raises `import_parse_failed` rather than silently
    mangling accented characters (design decision 9). A fully blank line anywhere in the file is
    skipped, not treated as a row with an empty (error) `text` — the same "blank rows don't
    count" behavior `parse_xlsx` needs for its own format.
    """
    try:
        text_content = file.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text_content = file.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise PromptImportError("import_parse_failed") from exc

    reader = csv.DictReader(io.StringIO(text_content))
    if reader.fieldnames is None:
        raise PromptImportError("import_no_rows_found")
    field_map = {(name or "").strip().lower(): name for name in reader.fieldnames}
    if "text" not in field_map:
        raise PromptImportError("import_parse_failed")

    def cell(raw_row: dict, key: str) -> str | None:
        if key not in field_map:
            return None
        value = raw_row.get(field_map[key])
        return value.strip() if isinstance(value, str) else None

    rows: list[ParsedPromptRow] = []
    for raw_row in reader:
        if all((v or "").strip() == "" for v in raw_row.values()):
            continue  # fully blank line — skip, not an error
        rows.append(
            ParsedPromptRow(
                row_number=len(rows) + 1,
                text=cell(raw_row, "text") or "",
                market_code=cell(raw_row, "market_code") or None,
                topic=cell(raw_row, "topic") or None,
                is_active=_parse_is_active(cell(raw_row, "is_active")),
            )
        )
    if not rows:
        raise PromptImportError("import_no_rows_found")
    return rows


def parse_xlsx(file: bytes) -> list[ParsedPromptRow]:
    """Parse an uploaded XLSX file into `ParsedPromptRow` objects (design decision 7).

    Reads the first sheet only; the first row is the header (same column names/matching as
    `parse_csv`), and a fully blank row is skipped wherever it appears rather than treated as
    the end of data or as an error.
    """
    try:
        workbook = load_workbook(io.BytesIO(file), read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        header = next(rows_iter, None)
    except Exception as exc:  # corrupt/non-XLSX upload — openpyxl's own exceptions vary by failure mode
        raise PromptImportError("import_parse_failed") from exc

    if header is None:
        raise PromptImportError("import_no_rows_found")
    field_map = {str(name).strip().lower(): idx for idx, name in enumerate(header) if name}
    if "text" not in field_map:
        raise PromptImportError("import_parse_failed")

    def cell(raw_row: tuple, key: str) -> str | None:
        if key not in field_map:
            return None
        idx = field_map[key]
        value = raw_row[idx] if idx < len(raw_row) else None
        return str(value).strip() if value is not None else None

    rows: list[ParsedPromptRow] = []
    for raw_row in rows_iter:
        if raw_row is None or all(v is None or str(v).strip() == "" for v in raw_row):
            continue  # fully blank row — skip, not an error
        rows.append(
            ParsedPromptRow(
                row_number=len(rows) + 1,
                text=cell(raw_row, "text") or "",
                market_code=cell(raw_row, "market_code") or None,
                topic=cell(raw_row, "topic") or None,
                is_active=_parse_is_active(cell(raw_row, "is_active")),
            )
        )
    if not rows:
        raise PromptImportError("import_no_rows_found")
    return rows


def parse_json(file: bytes) -> list[ParsedPromptRow]:
    """Parse an uploaded JSON file into `ParsedPromptRow` objects (design decision 7).

    Expects a JSON array of objects with the same keys as the CSV/XLSX columns (`text`
    required; `market_code`/`topic`/`is_active` optional) — `import_parse_failed` if the root
    isn't an array or an element isn't an object, rather than a raw exception.
    """
    try:
        data = json.loads(file.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptImportError("import_parse_failed") from exc

    if not isinstance(data, list) or not data:
        raise PromptImportError("import_no_rows_found" if data == [] else "import_parse_failed")

    rows: list[ParsedPromptRow] = []
    for item in data:
        if not isinstance(item, dict):
            raise PromptImportError("import_parse_failed")
        market_code = item.get("market_code")
        topic = item.get("topic")
        is_active = item.get("is_active")
        rows.append(
            ParsedPromptRow(
                row_number=len(rows) + 1,
                text=str(item.get("text") or "").strip(),
                market_code=str(market_code).strip() or None if market_code is not None else None,
                topic=str(topic).strip() or None if topic is not None else None,
                is_active=is_active if isinstance(is_active, bool) else _parse_is_active(
                    str(is_active) if is_active is not None else None
                ),
            )
        )
    return rows


def validate_and_check_duplicates(
    rows: list[ParsedPromptRow], db: Session, prompt_set_id: int, default_market_id: int
) -> list[ParsedPromptRow]:
    """Resolve each row's market and flag duplicates/errors in place, returning the same list
    (design decisions 8, 10).

    A row without its own `market_code` falls back to `default_market_id` (the market chosen
    once in the upload form); a row with a `market_code` that doesn't match any known `Market`
    is flagged `"error"` rather than silently falling back to the default — the market on file
    is trusted over the default when both are present, but never silently dropped when it's
    wrong. Duplicate detection compares normalized (whitespace-collapsed, case-folded) text
    against both the current-version prompts already in this prompt set (scoped to the same
    market) and every earlier row in this same batch with the same market — what to *do* with a
    duplicate (skip it, import anyway) is left to the caller's preview screen (BIM-T5), not
    decided here.
    """
    market_cache: dict[str, Market | None] = {}

    def resolve_market(code: str) -> Market | None:
        if code not in market_cache:
            market_cache[code] = db.scalar(select(Market).where(Market.code == code))
        return market_cache[code]

    existing_texts_by_market: dict[int, set[str]] = {}

    def existing_texts(market_id: int) -> set[str]:
        if market_id not in existing_texts_by_market:
            texts = db.scalars(
                select(Prompt.text).where(
                    Prompt.prompt_set_id == prompt_set_id,
                    Prompt.market_id == market_id,
                    Prompt.is_current_version.is_(True),
                )
            ).all()
            existing_texts_by_market[market_id] = {_normalize_text(t) for t in texts}
        return existing_texts_by_market[market_id]

    seen_in_batch: dict[int, set[str]] = {}

    for row in rows:
        if not row.text:
            row.status = "error"
            row.error_message = "Prompt text is required."
            continue

        if row.market_code:
            market = resolve_market(row.market_code)
            if market is None:
                row.status = "error"
                row.error_message = f"Unknown market code '{row.market_code}'."
                continue
            row.market_id = market.id
        else:
            row.market_id = default_market_id

        normalized = _normalize_text(row.text)
        batch_seen = seen_in_batch.setdefault(row.market_id, set())
        if normalized in existing_texts(row.market_id) or normalized in batch_seen:
            row.status = "duplicate"
        else:
            row.status = "new"
        batch_seen.add(normalized)

    return rows
