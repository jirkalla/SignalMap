"""Bulk prompt import: parsing/validation/duplicates/confirm flow (BIM-T4/T5/T6) plus the CSV
robustness and downloadable template layered on top (BIM-T8/BIM-T9) — docs/TASKS_BULK_IMPORT_
MULTI_MODEL.md. Founded in BIM-T8; this file is extended, not recreated, by BIM-T7.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Market, Prompt
from app.services import prompt_import
from app.services.prompt_import import (
    ParsedPromptRow,
    PromptImportError,
    parse_csv,
    parse_json,
    parse_xlsx,
    validate_and_check_duplicates,
)


def test_parse_csv_detects_semicolon_delimiter():
    """German-locale Excel commonly exports "CSV" with a semicolon instead of a comma (comma is
    the decimal separator there) — the sniffer must pick that up rather than treating the whole
    line as one field.
    """
    content = "text;market_code;topic;is_active\nHello world?;de-DE;Greeting;true\n".encode("utf-8-sig")
    rows = parse_csv(content)
    assert len(rows) == 1
    assert rows[0].text == "Hello world?"
    assert rows[0].market_code == "de-DE"
    assert rows[0].topic == "Greeting"
    assert rows[0].is_active is True
    assert rows[0].status == "new"


def test_parse_csv_flags_mismatched_field_count_as_error():
    """An unquoted comma inside `text` used to silently split into the wrong columns; it must
    now be flagged as an error naming the mismatch, and leave other rows in the file untouched.
    """
    content = (
        "text,market_code,topic,is_active\n"
        "Broken, row, with, unquoted commas,de-DE,Topic,true\n"
        "A normal row?,de-DE,Topic,true\n"
    ).encode("utf-8-sig")
    rows = parse_csv(content)
    assert len(rows) == 2
    assert rows[0].status == "error"
    assert "field(s)" in rows[0].error_message
    assert rows[1].status == "new"
    assert rows[1].text == "A normal row?"


def test_parse_csv_raises_on_undecodable_bytes():
    """Neither utf-8-sig nor cp1252 can decode this — a structured error, not a raw traceback."""
    with pytest.raises(PromptImportError) as exc_info:
        parse_csv(bytes([0x81]))
    assert exc_info.value.error_code == "import_parse_failed"


def test_validate_and_check_duplicates_does_not_overwrite_parser_error(
    db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_BULK_IMPORT_MULTI_MODEL.md design decision 16 — a row the parser already
    flagged (mismatched field count) must be left untouched by validate_and_check_duplicates,
    not overwritten with the generic "Prompt text is required." message.
    """
    content = "text,market_code,topic,is_active\nBroken, row,de-DE,Topic,true\n".encode("utf-8-sig")
    rows = parse_csv(content)
    assert rows[0].status == "error"
    original_message = rows[0].error_message

    validate_and_check_duplicates(rows, db_session, sample_prompt.prompt_set_id, seed["market"].id)

    assert rows[0].status == "error"
    assert rows[0].error_message == original_message


def test_import_template_csv_roundtrips(authed_client: TestClient, sample_prompt: Prompt):
    """docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T9 — the downloaded CSV template is exactly
    what parse_csv expects, so it can never itself trigger the errors it's meant to prevent.
    """
    response = authed_client.get(f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/template?format=csv")
    assert response.status_code == 200
    rows = parse_csv(response.content)
    assert len(rows) == 1
    assert rows[0].text
    assert rows[0].status == "new"


def test_import_template_xlsx_roundtrips(authed_client: TestClient, sample_prompt: Prompt):
    response = authed_client.get(f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/template?format=xlsx")
    assert response.status_code == 200
    rows = parse_xlsx(response.content)
    assert len(rows) == 1
    assert rows[0].text
    assert rows[0].status == "new"


def test_import_template_json_roundtrips(authed_client: TestClient, sample_prompt: Prompt):
    response = authed_client.get(f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/template?format=json")
    assert response.status_code == 200
    rows = parse_json(response.content)
    assert len(rows) == 1
    assert rows[0].text
    assert rows[0].is_active is True
    assert rows[0].status == "new"


# ---------------------------------------------------------------------------
# BIM-T7 — parsing/validation/duplicates/confirm flow (BIM-T4/T5/T6)
# ---------------------------------------------------------------------------


def test_parse_csv_xlsx_json_agree_on_the_same_content():
    """docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T7 — the three formats are interchangeable
    inputs to the same pipeline, so identical content must parse to identical `ParsedPromptRow`
    data regardless of which one was uploaded.
    """
    csv_content = "text,market_code,topic,is_active\nHello world?,de-DE,Greeting,true\n".encode("utf-8-sig")

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["text", "market_code", "topic", "is_active"])
    sheet.append(["Hello world?", "de-DE", "Greeting", "true"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    xlsx_content = buffer.getvalue()

    json_content = json.dumps(
        [{"text": "Hello world?", "market_code": "de-DE", "topic": "Greeting", "is_active": True}]
    ).encode("utf-8")

    for rows in (parse_csv(csv_content), parse_xlsx(xlsx_content), parse_json(json_content)):
        assert len(rows) == 1
        assert rows[0].text == "Hello world?"
        assert rows[0].market_code == "de-DE"
        assert rows[0].topic == "Greeting"
        assert rows[0].is_active is True


def test_parse_csv_handles_bom_and_cp1252():
    """utf-8-sig (BOM) is tried first; cp1252 (common for German-locale Excel exports) is the
    fallback — neither should crash or mangle the accented character.
    """
    bom_rows = parse_csv("text\nCafé?\n".encode("utf-8-sig"))
    assert bom_rows[0].text == "Café?"

    cp1252_rows = parse_csv("text\nCafé?\n".encode("cp1252"))
    assert cp1252_rows[0].text == "Café?"


def test_validate_flags_unknown_market_code(db_session: Session, seed, sample_prompt: Prompt):
    rows = [ParsedPromptRow(row_number=1, text="Hello?", market_code="xx-XX")]
    validate_and_check_duplicates(rows, db_session, sample_prompt.prompt_set_id, seed["market"].id)
    assert rows[0].status == "error"


def test_validate_flags_empty_text(db_session: Session, seed, sample_prompt: Prompt):
    rows = [ParsedPromptRow(row_number=1, text="")]
    validate_and_check_duplicates(rows, db_session, sample_prompt.prompt_set_id, seed["market"].id)
    assert rows[0].status == "error"


def test_validate_fills_default_market_when_missing(db_session: Session, seed, sample_prompt: Prompt):
    rows = [ParsedPromptRow(row_number=1, text="Hello?", market_code=None)]
    validate_and_check_duplicates(rows, db_session, sample_prompt.prompt_set_id, seed["market"].id)
    assert rows[0].market_id == seed["market"].id
    assert rows[0].status == "new"


def test_validate_detects_duplicate_against_existing_prompt(db_session: Session, seed, sample_prompt: Prompt):
    """`sample_prompt` (conftest.py) is `"What is this test about?"` in `seed["market"]` —
    uploading the same text for the same market must be flagged, not silently created again.
    """
    rows = [ParsedPromptRow(row_number=1, text="What is this test about?", market_code=None)]
    validate_and_check_duplicates(rows, db_session, sample_prompt.prompt_set_id, seed["market"].id)
    assert rows[0].status == "duplicate"


def test_validate_same_text_different_market_is_new(db_session: Session, seed, sample_prompt: Prompt):
    """Market is part of the duplicate key (design decision 10) — the same question text
    targeting a different market is a legitimate, separate prompt, not a duplicate.
    """
    other_market = Market(code="de-DE", language="de", country="DE")
    db_session.add(other_market)
    db_session.commit()

    rows = [ParsedPromptRow(row_number=1, text="What is this test about?", market_code="de-DE")]
    validate_and_check_duplicates(rows, db_session, sample_prompt.prompt_set_id, seed["market"].id)
    assert rows[0].status == "new"


def test_validate_duplicate_within_same_batch(db_session: Session, seed, sample_prompt: Prompt):
    rows = [
        ParsedPromptRow(row_number=1, text="Brand new question?", market_code=None),
        ParsedPromptRow(row_number=2, text="Brand new question?", market_code=None),
    ]
    validate_and_check_duplicates(rows, db_session, sample_prompt.prompt_set_id, seed["market"].id)
    assert rows[0].status == "new"
    assert rows[1].status == "duplicate"


def test_bulk_import_confirm_saves_only_included_rows(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """End-to-end: upload -> preview -> confirm. Only the row submitted with `include=true`
    becomes a `Prompt`; the other (left unchecked, so its `include` field is simply absent from
    the form — matching what an unchecked HTML checkbox actually submits) does not.
    """
    csv_content = (
        "text,market_code,topic,is_active\n"
        "Brand new prompt one?,,Topic A,true\n"
        "Brand new prompt two?,,Topic B,true\n"
    ).encode("utf-8-sig")

    preview_response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/preview",
        data={"default_market_id": seed["market"].id},
        files={"file": ("prompts.csv", csv_content, "text/csv")},
    )
    assert preview_response.status_code == 200

    confirm_response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/confirm",
        data={
            "rows-0-text": "Brand new prompt one?",
            "rows-0-market_id": str(seed["market"].id),
            "rows-0-topic": "Topic A",
            "rows-0-is_active": "true",
            "rows-0-include": "true",
            "rows-1-text": "Brand new prompt two?",
            "rows-1-market_id": str(seed["market"].id),
            "rows-1-topic": "Topic B",
            # rows-1-include intentionally omitted — an unchecked checkbox submits nothing.
        },
        follow_redirects=False,
    )
    assert confirm_response.status_code == 303

    texts = set(
        db_session.scalars(
            select(Prompt.text).where(
                Prompt.prompt_set_id == sample_prompt.prompt_set_id, Prompt.is_current_version.is_(True)
            )
        ).all()
    )
    assert "Brand new prompt one?" in texts
    assert "Brand new prompt two?" not in texts


def test_bulk_import_confirm_never_saves_a_row_with_an_invalid_market_even_if_included(
    authed_client: TestClient, db_session: Session, sample_prompt: Prompt
):
    """Design decision 12 — the confirm route re-validates `market_id` itself rather than
    trusting the resubmitted form; a tampered/stale `market_id` must never create a `Prompt`,
    even with `include=true`.
    """
    response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/confirm",
        data={
            "rows-0-text": "Should never be saved",
            "rows-0-market_id": "999999",
            "rows-0-include": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert db_session.scalar(select(Prompt).where(Prompt.text == "Should never be saved")) is None


# ---------------------------------------------------------------------------
# Code-review fixes, 2026-09-14 — row-shape tolerance, row-count cap enforced
# in-loop, and confirm-time robustness (malformed fields, row cap, duplicate
# re-check against live DB state).
# ---------------------------------------------------------------------------


def test_parse_csv_accepts_short_row_missing_optional_trailing_columns():
    """A row that only fills the required `text` column (omitting the optional trailing
    `market_code`/`topic`/`is_active` columns) must be accepted, not flagged — this is the
    `csv.DictReader`-equivalent tolerance the field-count-mismatch check must preserve
    (code-review fix, 2026-09-14: the original `!=` comparison rejected this legitimate case).
    """
    content = "text,market_code,topic,is_active\nJust the text, nothing else?\n".encode("utf-8-sig")
    rows = parse_csv(content)
    assert len(rows) == 1
    assert rows[0].status != "error"


def test_parse_csv_enforces_row_cap_mid_parse(monkeypatch: pytest.MonkeyPatch):
    """`MAX_IMPORT_ROWS` is checked inside the parser's own loop, so a file far over the cap is
    rejected as soon as it's crossed rather than after fully parsing every row.
    """
    monkeypatch.setattr(prompt_import, "MAX_IMPORT_ROWS", 2)
    content = ("text\n" + "\n".join(f"Row {i}?" for i in range(5))).encode("utf-8-sig")
    with pytest.raises(PromptImportError) as exc_info:
        parse_csv(content)
    assert exc_info.value.error_code == "import_too_many_rows"


def test_parse_xlsx_enforces_row_cap_mid_parse(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(prompt_import, "MAX_IMPORT_ROWS", 2)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["text"])
    for i in range(5):
        sheet.append([f"Row {i}?"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    with pytest.raises(PromptImportError) as exc_info:
        parse_xlsx(buffer.getvalue())
    assert exc_info.value.error_code == "import_too_many_rows"


def test_parse_json_enforces_row_cap_mid_parse(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(prompt_import, "MAX_IMPORT_ROWS", 2)
    content = json.dumps([{"text": f"Row {i}?"} for i in range(5)]).encode("utf-8")
    with pytest.raises(PromptImportError) as exc_info:
        parse_json(content)
    assert exc_info.value.error_code == "import_too_many_rows"


def test_parse_xlsx_flags_stray_data_past_header_width():
    """A row with genuine data in a column past the header's own width is a shifted/ragged row,
    the same mistake `parse_csv`'s field-count-mismatch check already caught.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["text", "market_code"])
    sheet.append(["Ragged row?", "de-DE", "unexpected extra value"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    rows = parse_xlsx(buffer.getvalue())
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_code == "field_count_mismatch"


def test_parse_xlsx_ignores_blank_padding_past_header_width():
    """`openpyxl.iter_rows` pads every row tuple out to the sheet's used column width regardless
    of that row's real content — a sheet that's merely wider than its header (e.g. leftover
    formatting in an unused column) must not falsely flag every row as ragged.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["text", "market_code"])
    sheet.append(["Hello?", "de-DE"])
    sheet.append(["Bye?", "en-US", "", None, "   "])  # widens the sheet; trailing cells are blank
    buffer = io.BytesIO()
    workbook.save(buffer)
    rows = parse_xlsx(buffer.getvalue())
    assert len(rows) == 2
    assert all(row.status != "error" for row in rows)


def test_bulk_import_confirm_caps_row_count(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt, monkeypatch: pytest.MonkeyPatch
):
    """Posting directly to confirm (bypassing preview) with more rows than `MAX_IMPORT_ROWS`
    must be rejected, not silently create an unbounded number of `Prompt` rows in one request
    (code-review fix, 2026-09-14: this route previously had no cap of its own).
    """
    monkeypatch.setattr("app.routers.prompt_sets.MAX_IMPORT_ROWS", 1)
    response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/confirm",
        data={
            "rows-0-text": "Row zero?",
            "rows-0-market_id": str(seed["market"].id),
            "rows-0-include": "true",
            "rows-1-text": "Row one?",
            "rows-1-market_id": str(seed["market"].id),
            "rows-1-include": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "more than" in response.text  # errors.import_too_many_rows (HTML error page, not /api/*)
    assert db_session.scalar(select(Prompt).where(Prompt.text == "Row zero?")) is None
    assert db_session.scalar(select(Prompt).where(Prompt.text == "Row one?")) is None


def test_bulk_import_confirm_skips_malformed_row_index_without_crashing(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """A non-numeric `rows-{i}-*` index must be skipped like any other invalid row, not crash
    the whole request with an unhandled 500 (code-review fix, 2026-09-14) — and it must not
    prevent the other, well-formed rows in the same submission from importing.
    """
    response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/confirm",
        data={
            "rows-abc-text": "Should be ignored",
            "rows-abc-market_id": str(seed["market"].id),
            "rows-abc-include": "true",
            "rows-0-text": "Valid row despite the malformed sibling?",
            "rows-0-market_id": str(seed["market"].id),
            "rows-0-include": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    texts = set(db_session.scalars(select(Prompt.text).where(Prompt.prompt_set_id == sample_prompt.prompt_set_id)).all())
    assert "Valid row despite the malformed sibling?" in texts
    assert "Should be ignored" not in texts


def test_bulk_import_confirm_skips_row_that_became_duplicate_since_preview(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """A row that was `new` at preview time but has since been created (e.g. a concurrent
    import) must not create a second copy — confirm re-checks duplicates against the database's
    live state at commit time, not the stale preview snapshot (code-review fix, 2026-09-14).
    """
    db_session.add(
        Prompt(
            prompt_set_id=sample_prompt.prompt_set_id,
            text="Already imported by someone else?",
            market_id=seed["market"].id,
            is_active=True,
        )
    )
    db_session.commit()

    response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/confirm",
        data={
            "rows-0-text": "Already imported by someone else?",
            "rows-0-market_id": str(seed["market"].id),
            "rows-0-include": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    count = db_session.scalar(
        select(func.count()).select_from(Prompt).where(
            Prompt.prompt_set_id == sample_prompt.prompt_set_id,
            Prompt.text == "Already imported by someone else?",
            Prompt.is_current_version.is_(True),
        )
    )
    assert count == 1
