"""CSV/XLSX/JSON parsing and duplicate detection for bulk prompt import
(docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T4).

Parsing (`parse_csv`/`parse_xlsx`/`parse_json`) never touches the database — each returns
`ParsedPromptRow` objects with only file-derived fields set (`market_code` is the raw string
from the file, not yet resolved to a `Market` id). `validate_and_check_duplicates` is the one
function that talks to the database, resolving market codes and flagging duplicates against
both existing prompts and other rows in the same batch — kept separate so parsing stays
unit-testable without a database session (design decisions 7-10).

This module has no `Request`/`t()` access, so it never builds user-facing text itself.
File-level problems raise `PromptImportError` with an English `error_code`; the calling route
turns that into `t(f"errors.{error_code}")`. Per-row problems set `ParsedPromptRow.error_code`
(plus `error_context` for placeholders) rather than a hardcoded message string — the template
resolves `t('prompt_import.row_error_' ~ error_code)` itself, the same "backend produces a code,
the request layer localizes it" split, just applied per-row instead of per-file (code-review
fix, 2026-09-14: the original version of this module set a raw English `error_message` here and
the template printed it verbatim, bypassing `t()` entirely — a real AI_INSTRUCTIONS.md §3
violation this closes). `error_message` still exists alongside `error_code`, but only as an
English technical detail for logs/tests, mirroring `Run.error_message`'s role — never rendered
to a user directly anymore.

`MAX_IMPORT_FILE_BYTES` is enforced by the caller before this module ever sees the bytes
(unchanged). `MAX_IMPORT_ROWS`, previously checked by the caller only after a parser returned
its full row list, is now enforced by each parser inside its own row loop instead via the shared
`_append_row` helper — a file with far more rows than the cap is worth is rejected as soon as the
cap is crossed, not after fully parsing every row (code-review fix, 2026-09-14: superseded design
decision 13's "limits are enforced by that same caller, not here" for row count specifically; the
byte-size limit is still caller-only, since a parser never sees more bytes than it's handed).
This "reject early" benefit is real for `parse_csv` (a lazy `csv.reader` generator) and
`parse_xlsx` (lazy `iter_rows` in `read_only` mode), which only consume as many rows as the cap
allows before raising — but NOT for `parse_json` (code-review finding, 2026-09-15): `json.loads`
must fully deserialize the entire array into memory in one call before the row loop and its cap
check ever run, so for JSON the check only bounds how many `ParsedPromptRow` objects get built,
not how much parsing work already happened. Left as-is rather than switching to a streaming JSON
parser (e.g. `ijson`) — a new dependency for a cost that `MAX_IMPORT_FILE_BYTES`'s 2MB ceiling
already bounds to a non-issue in practice.
"""

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Callable, Literal

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
    leaves a row at the `"new"` default (except the parser's own field-count-mismatch error,
    which sets `status="error"` directly — see `parse_csv`/`parse_xlsx`).

    An `"error"` row carries both `error_code` (an i18n key suffix — the caller/template
    resolves `t('prompt_import.row_error_' ~ error_code).format(**error_context)`) and
    `error_message` (the same information in plain English, kept only for logs/tests, never
    shown to a user directly — see the module docstring for why this split exists).
    """

    row_number: int
    text: str
    market_code: str | None = None
    topic: str | None = None
    is_active: bool = True
    status: RowStatus = "new"
    error_message: str | None = None
    error_code: str | None = None
    error_context: dict[str, str | int] = field(default_factory=dict)
    market_id: int | None = None


def normalize_prompt_text(text: str) -> str:
    """Collapse whitespace and case-fold, so duplicate detection matches on meaning, not exact
    bytes. Public (not `_`-prefixed) because `import_prompts_confirm`
    (app/routers/prompt_sets.py) reuses it to re-check duplicates against the database's current
    state at confirm time, not just here at preview time (code-review fix, 2026-09-14).
    """
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


def _append_row(rows: list[ParsedPromptRow], row: ParsedPromptRow) -> None:
    """Append one parsed row and enforce `MAX_IMPORT_ROWS`, shared by all three parsers
    (code-review fix, 2026-09-15: the append-then-check-the-cap pair was copy-pasted verbatim
    into `parse_csv`/`parse_xlsx`/`parse_json` — a single call site means the cap logic can't
    drift out of sync between formats).
    """
    rows.append(row)
    if len(rows) > MAX_IMPORT_ROWS:
        raise PromptImportError("import_too_many_rows")


def parse_csv(file: bytes) -> list[ParsedPromptRow]:
    """Parse an uploaded CSV file into `ParsedPromptRow` objects (design decision 7).

    Expects a header row with a `text` column (required) plus optional `market_code`/`topic`/
    `is_active` columns, matched case-insensitively. Tries `utf-8-sig` first (handles a BOM some
    spreadsheet tools add), falling back to `cp1252` (common for CSVs exported by German-locale
    Excel) — a file in neither encoding raises `import_parse_failed` rather than silently
    mangling accented characters (design decision 9). A fully blank line anywhere in the file is
    skipped, not treated as a row with an empty (error) `text` — the same "blank rows don't
    count" behavior `parse_xlsx` needs for its own format.

    The delimiter is auto-detected (design decision 15) rather than assumed to be a comma —
    German-locale Excel commonly exports "CSV" with a semicolon instead (comma is the decimal
    separator there). A row with MORE fields than the header (design decision 16, e.g. an
    unquoted comma/semicolon inside a `text` value throwing off the split) is flagged
    `status="error"` with the mismatch explained, instead of being silently split into the wrong
    columns — this is what a hand-typed, incorrectly-quoted CSV row used to do. A row with FEWER
    fields than the header is accepted, not errored (code-review fix, 2026-09-14: the original
    version of this check used `!=`, which also rejected a row that's short only because
    optional trailing `market_code`/`topic`/`is_active` columns were omitted — a previously-valid
    input class under the old `csv.DictReader`-based parser, which filled missing trailing keys
    with `None`). `cell()` below already handles a short row correctly via its own
    `idx < len(raw_row)` bounds check, so only the too-many-fields case needs flagging.
    """
    try:
        text_content = file.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text_content = file.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise PromptImportError("import_parse_failed") from exc

    dialect = _detect_csv_dialect(text_content)
    reader = csv.reader(io.StringIO(text_content), dialect=dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise PromptImportError("import_no_rows_found")
    field_map = {(name or "").strip().lower(): idx for idx, name in enumerate(header)}
    if "text" not in field_map:
        raise PromptImportError("import_parse_failed")

    def cell(raw_row: list[str], key: str) -> str | None:
        if key not in field_map:
            return None
        idx = field_map[key]
        return raw_row[idx].strip() if idx < len(raw_row) else None

    rows: list[ParsedPromptRow] = []
    for raw_row in reader:
        if all((v or "").strip() == "" for v in raw_row):
            continue  # fully blank line — skip, not an error
        if len(raw_row) > len(header):
            _append_row(
                rows,
                ParsedPromptRow(
                    row_number=len(rows) + 1,
                    text="",
                    status="error",
                    error_message=(
                        f"Row has {len(raw_row)} field(s) but the header has {len(header)} — "
                        "check for an unquoted comma/semicolon inside a text value."
                    ),
                    error_code="field_count_mismatch",
                    error_context={"actual": len(raw_row), "expected": len(header)},
                ),
            )
        else:
            _append_row(
                rows,
                ParsedPromptRow(
                    row_number=len(rows) + 1,
                    text=cell(raw_row, "text") or "",
                    market_code=cell(raw_row, "market_code") or None,
                    topic=cell(raw_row, "topic") or None,
                    is_active=_parse_is_active(cell(raw_row, "is_active")),
                ),
            )
    if not rows:
        raise PromptImportError("import_no_rows_found")
    return rows


def _detect_csv_dialect(text_content: str) -> type[csv.Dialect]:
    """Sniff the delimiter/quoting of an uploaded CSV from its first ~4096 characters
    (design decision 15).

    Restricted to `,`/`;`/tab candidates — `Sniffer` given free rein sometimes misreads a
    punctuation mark inside ordinary prose as the delimiter. Falls back to the standard
    comma-delimited `excel` dialect (today's behavior) when the sample is too ambiguous to
    call, rather than raising over what is, at worst, a return to the previous behavior.
    """
    try:
        return csv.Sniffer().sniff(text_content[:4096], delimiters=",;\t")
    except csv.Error:
        return csv.excel


def parse_xlsx(file: bytes) -> list[ParsedPromptRow]:
    """Parse an uploaded XLSX file into `ParsedPromptRow` objects (design decision 7).

    Reads the first sheet only; the first row is the header (same column names/matching as
    `parse_csv`), and a fully blank row is skipped wherever it appears rather than treated as
    the end of data or as an error. A row with real (non-empty) data in a column past the
    header's own width is flagged `status="error"`, mirroring `parse_csv`'s field-count-mismatch
    check (code-review fix, 2026-09-14: this format-shape check previously existed only for CSV,
    so the identical user mistake — a shifted/ragged row — silently produced wrong or dropped
    data in XLSX instead of being flagged). This checks for actual *values* past the header,
    not raw tuple length: `openpyxl.iter_rows` pads every row's tuple out to the sheet's used
    column width regardless of that row's real content — including the header row itself, so a
    boundary derived from `len(header)` is wrong whenever the offending row is also the sheet's
    widest row (the common real case: a user typed exactly one extra value into an unused
    column, which is what makes the sheet that wide in the first place — the header row then
    gets padded to that same width on read, silently hiding the mismatch). The boundary is
    instead the highest column index any header name actually occupies, plus one — robust to
    both kinds of padding, and to a blank/skipped header cell followed by more named columns.
    A row with FEWER cells than the header is accepted, not errored — same reasoning as
    `parse_csv`: `cell()`'s own `idx < len(raw_row)` bounds check already handles a short row
    correctly (missing optional trailing columns become `None`).
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
    header_width = max(field_map.values(), default=-1) + 1

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
        extra_cells = raw_row[header_width:]
        has_stray_data = any(v is not None and str(v).strip() != "" for v in extra_cells)
        if has_stray_data:
            _append_row(
                rows,
                ParsedPromptRow(
                    row_number=len(rows) + 1,
                    text="",
                    status="error",
                    error_message=(
                        f"Row has data in {len(extra_cells)} column(s) past the header's "
                        f"{header_width} — check for a value in the wrong column."
                    ),
                    error_code="field_count_mismatch",
                    error_context={"actual": header_width + len(extra_cells), "expected": header_width},
                ),
            )
        else:
            _append_row(
                rows,
                ParsedPromptRow(
                    row_number=len(rows) + 1,
                    text=cell(raw_row, "text") or "",
                    market_code=cell(raw_row, "market_code") or None,
                    topic=cell(raw_row, "topic") or None,
                    is_active=_parse_is_active(cell(raw_row, "is_active")),
                ),
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
        _append_row(
            rows,
            ParsedPromptRow(
                row_number=len(rows) + 1,
                text=str(item.get("text") or "").strip(),
                market_code=str(market_code).strip() or None if market_code is not None else None,
                topic=str(topic).strip() or None if topic is not None else None,
                is_active=is_active if isinstance(is_active, bool) else _parse_is_active(
                    str(is_active) if is_active is not None else None
                ),
            ),
        )
    return rows


def build_existing_texts_lookup(db: Session, prompt_set_id: int) -> Callable[[int], set[str]]:
    """Return a `market_id -> {normalized existing prompt texts}` lookup, memoized per market_id.

    Shared by `validate_and_check_duplicates` (preview-time) and `import_prompts_confirm`
    (app/routers/prompt_sets.py, confirm-time) — both used to hand-roll the identical cache dict
    plus query (code-review fix, 2026-09-15), which risked the two duplicate checks silently
    drifting apart if only one copy was ever updated.
    """
    cache: dict[int, set[str]] = {}

    def existing_texts(market_id: int) -> set[str]:
        if market_id not in cache:
            texts = db.scalars(
                select(Prompt.text).where(
                    Prompt.prompt_set_id == prompt_set_id,
                    Prompt.market_id == market_id,
                    Prompt.is_current_version.is_(True),
                )
            ).all()
            cache[market_id] = {normalize_prompt_text(t) for t in texts}
        return cache[market_id]

    return existing_texts


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
    decided here. A row the parser already flagged `"error"` (BIM-T8's mismatched-field-count
    check) is left untouched — its `text` is meaningless (spliced from the wrong columns), so
    there's nothing to validate, and re-processing it here would overwrite that specific
    diagnostic with the generic "Prompt text is required." message.
    """
    market_cache: dict[str, Market | None] = {}

    def resolve_market(code: str) -> Market | None:
        if code not in market_cache:
            market_cache[code] = db.scalar(select(Market).where(Market.code == code))
        return market_cache[code]

    existing_texts = build_existing_texts_lookup(db, prompt_set_id)

    seen_in_batch: dict[int, set[str]] = {}

    for row in rows:
        if row.status == "error":
            continue  # already flagged by the parser (e.g. mismatched field count) — leave as-is

        if not row.text:
            row.status = "error"
            row.error_message = "Prompt text is required."
            row.error_code = "text_required"
            continue

        if row.market_code:
            market = resolve_market(row.market_code)
            if market is None:
                row.status = "error"
                row.error_message = f"Unknown market code '{row.market_code}'."
                row.error_code = "unknown_market_code"
                row.error_context = {"market_code": row.market_code}
                continue
            row.market_id = market.id
        else:
            row.market_id = default_market_id

        normalized = normalize_prompt_text(row.text)
        batch_seen = seen_in_batch.setdefault(row.market_id, set())
        if normalized in existing_texts(row.market_id) or normalized in batch_seen:
            row.status = "duplicate"
        else:
            row.status = "new"
        batch_seen.add(normalized)

    return rows
