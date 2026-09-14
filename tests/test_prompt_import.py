"""Bulk prompt import: CSV robustness and the downloadable template (docs/TASKS_BULK_IMPORT_
MULTI_MODEL.md BIM-T8/BIM-T9). Founded here per BIM-T8; BIM-T7 extends this same file with
parsing/validation/duplicate/confirm-flow coverage for BIM-T4/T5/T6.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Prompt
from app.services.prompt_import import (
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
