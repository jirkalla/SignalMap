"""Unit tests for `derive_claim` (docs/TASKS_CITATION_VERIFICATION.md T1, design decisions 4-5).

Real-payload fixtures (tests/fixtures/claims/run_{108,311,422}.json) were pulled from the dev DB
2026-09-28 (`rendered_text`, the relevant `raw_payload`, and the stored `citations` rows for
each run), stripped of `encrypted_content`/`encrypted_index` blobs `derive_claim` never reads —
no network, no DB access, nothing paid, per AI_INSTRUCTIONS.md. The synthetic-payload tests below
cover the defensive branches those three real runs don't happen to exercise (a reshaped
Anthropic payload, offsets missing entirely, a Gemini segment that isn't findable).
"""

import json
import logging
from pathlib import Path

import pytest

from app.models.run import Citation
from app.services.claims import DerivedClaim, derive_claim

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "claims"


def _load_fixture(run_id: int) -> dict:
    return json.loads((FIXTURES_DIR / f"run_{run_id}.json").read_text(encoding="utf-8"))


def _citation(row: dict) -> Citation:
    """Build a bare (unsaved, no session) Citation row from one fixture citation dict."""
    return Citation(
        citation_position=row["citation_position"],
        source_url=row["source_url"],
        source_title=row["source_title"],
        source_domain=row["source_domain"],
        cited_answer_span=row["cited_answer_span"],
        answer_span_start=row["answer_span_start"],
        answer_span_end=row["answer_span_end"],
        source_passage=row["source_passage"],
    )


def _derive(fixture: dict, position: int) -> DerivedClaim | None:
    citation = next(row for row in fixture["citations"] if row["citation_position"] == position)
    return derive_claim(
        fixture["provider_code"], _citation(citation), fixture["rendered_text"], fixture["raw_payload"]
    )


# --- Anthropic: block the citation is attached to (run 108) -----------------------------------


def test_anthropic_derives_the_full_text_block_a_citation_is_attached_to():
    result = _derive(_load_fixture(108), 0)
    assert result.method == "anthropic_block"
    assert result.text == (
        "Leichtbau ist eine zukunftsträchtige Schlüsseltechnologie mit Relevanz für zahlreiche "
        "Branchen: In der Automobil- und Luftfahrtindustrie sowie der Bauwirtschaft bis hin zu "
        "Maschinenbau und Medizintechnik werden neue Materialien wie Verbundwerkstoffe, "
        "Aluminiumlegierungen oder innovative Kunststoffe entwickelt, die leicht, stark und "
        "langlebig sind."
    )
    assert result.start == 204
    assert result.end == 553


def test_anthropic_two_citations_on_the_same_block_derive_the_same_claim():
    """Run 108 attaches two citation rows (positions 1 and 2) to one block — both must derive

    the identical claim, not one winning and the other losing its match.
    """
    fixture = _load_fixture(108)
    first = _derive(fixture, 1)
    second = _derive(fixture, 2)
    assert first == second
    assert first.text == (
        "Durch den Einsatz von Leichtbauprinzipien in der Baubranche und vor allem "
        "CO2-speicherndes Holz können wertvolle Ressourcen gespart werden."
    )


def test_anthropic_derived_span_matches_rendered_text():
    """`rendered_text[start:end]` must equal `text` for every real citation in run 108 — the

    offsets are what T9's highlighting will slice by, so a silent off-by-one would misplace
    every highlight without any test here ever failing on `text` alone.
    """
    fixture = _load_fixture(108)
    for row in fixture["citations"]:
        result = _derive(fixture, row["citation_position"])
        assert fixture["rendered_text"][result.start : result.end] == result.text


def test_anthropic_returns_none_and_logs_when_citation_order_does_not_match_the_payload(caplog):
    """Design decision 4 point 2: a payload citation at the matched position whose `url` disagrees

    with the DB row's `source_url` must never be guessed at — return None and log instead.
    """
    raw_payload = {
        "content": [
            {"type": "text", "text": "Claim text.", "citations": [{"url": "https://real-source.example/"}]},
        ]
    }
    citation = Citation(citation_position=0, source_url="https://different-source.example/")
    with caplog.at_level(logging.WARNING):
        result = derive_claim("anthropic", citation, "Claim text.", raw_payload)
    assert result is None
    assert "url mismatch" in caplog.text


def test_anthropic_returns_none_when_citation_position_is_out_of_range():
    raw_payload = {"content": [{"type": "text", "text": "Claim.", "citations": [{"url": "https://x.example/"}]}]}
    citation = Citation(citation_position=5, source_url="https://x.example/")
    assert derive_claim("anthropic", citation, "Claim.", raw_payload) is None


# --- OpenAI: sentence before the link marker (run 311) ----------------------------------------


def test_openai_derives_the_sentence_before_the_marker():
    result = _derive(_load_fixture(311), 0)
    assert result.method == "openai_before_marker"
    assert result.text == (
        "Der Bund sieht darin eine Schlüsseltechnologie für Klimaschutz, Rohstoffsicherung und "
        "Innovation – ausdrücklich auch für leichte Aufstockungen und Nachverdichtung in dicht "
        "bebauten Städten."
    )


def test_openai_two_consecutive_markers_to_the_same_domain_derive_distinct_claims():
    """Run 311 cites bundeswirtschaftsministerium.de twice back to back (positions 0 and 1) —

    each must derive its own preceding sentence, not the same span twice.
    """
    fixture = _load_fixture(311)
    first = _derive(fixture, 0)
    second = _derive(fixture, 1)
    assert first.text != second.text
    assert second.text == "Das ist besonders für Quartiere in Berlin, Hamburg, München oder dem Rhein-Ruhr-Gebiet relevant."
    assert first.end <= second.start


def test_openai_derives_a_clean_sentence_from_inside_a_markdown_table_cell():
    """Run 311's position 6 sits inside a markdown table cell — the derived claim must not carry

    the leading `|` cell delimiter or bold markers along with it.
    """
    result = _derive(_load_fixture(311), 6)
    assert result.text == (
        "ZÜBLIN Timber bietet Entwicklung, Produktion und Montage aus einer Hand; Brüninghoff "
        "verbindet Holz, Beton, Metall und Hybridbau; Rubner zählt sich zu den führenden "
        "Ingenieurholzbauunternehmen Europas."
    )
    assert not result.text.startswith("|")


def test_openai_derived_span_matches_rendered_text():
    fixture = _load_fixture(311)
    for row in fixture["citations"]:
        result = _derive(fixture, row["citation_position"])
        assert fixture["rendered_text"][result.start : result.end] == result.text


def test_openai_returns_none_when_offsets_are_missing():
    citation = Citation(citation_position=0, answer_span_start=None, answer_span_end=None)
    assert derive_claim("openai", citation, "Some rendered text.", {}) is None


# --- Gemini: segment expanded to the full sentence / list item (run 422) ----------------------


def test_gemini_fragment_expands_to_the_full_list_item():
    """The exact case docs/TASKS_CITATION_VERIFICATION.md T1 calls out: the raw grounding

    segment is the torn fragment "Carbonbeton), um den Materialeinsatz zu optimieren" — it must
    widen to the complete list item, not stay truncated, and must not get fooled by the "z. B."
    abbreviation inside it into stopping at "B.".
    """
    result = _derive(_load_fixture(422), 21)
    assert result.method == "gemini_sentence"
    assert result.text == (
        "Große Baukonzerne wie **Max Bögl**, **Züblin (Strabag-Gruppe)** oder **Hochtief** "
        "investieren verstärkt in Modulbauweisen, Hybridbauweisen und innovative "
        "Betontechnologien (z. B. Carbonbeton), um den Materialeinsatz zu optimieren."
    )


def test_gemini_several_citations_on_the_same_segment_derive_the_same_claim():
    """Run 422 backs that one list item with three separate sources (positions 21-23) — every

    one of them must expand to the identical full sentence.
    """
    fixture = _load_fixture(422)
    results = {_derive(fixture, position).text for position in (21, 22, 23)}
    assert len(results) == 1


def test_gemini_derived_span_matches_rendered_text():
    fixture = _load_fixture(422)
    for row in fixture["citations"]:
        result = _derive(fixture, row["citation_position"])
        assert fixture["rendered_text"][result.start : result.end] == result.text


def test_gemini_returns_none_and_logs_when_segment_is_not_found_in_rendered_text(caplog):
    citation = Citation(citation_position=0, cited_answer_span="text that is not in the answer")
    with caplog.at_level(logging.WARNING):
        result = derive_claim("google_gemini", citation, "The actual rendered answer.", {})
    assert result is None
    assert "not found" in caplog.text


def test_gemini_returns_none_when_there_is_no_segment_at_all():
    citation = Citation(citation_position=0, cited_answer_span=None)
    assert derive_claim("google_gemini", citation, "Some rendered text.", {}) is None


# --- Providers with no claim/answer-span link at all -------------------------------------------


@pytest.mark.parametrize("provider_code", ["perplexity", "xai", "deepseek"])
def test_providers_without_an_answer_span_link_derive_nothing(provider_code):
    """Perplexity, xAI and DeepSeek never link a source to a specific answer segment

    (docs/TASKS_CITATION_VERIFICATION.md design decision 4 point 5) — derive_claim must say so
    plainly (None), not fabricate a guess from whatever fields happen to be populated.
    """
    citation = Citation(citation_position=0, source_url="https://x.example/")
    assert derive_claim(provider_code, citation, "Some rendered text.", {}) is None


def test_derive_claim_returns_none_when_rendered_text_is_empty():
    citation = Citation(citation_position=0)
    assert derive_claim("anthropic", citation, "", {"content": []}) is None
    assert derive_claim("anthropic", citation, None, {"content": []}) is None
