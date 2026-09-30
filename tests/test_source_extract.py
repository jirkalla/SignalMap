"""Unit tests for extract_html/extract_pdf (docs/TASKS_CITATION_VERIFICATION.md T4).

Fixtures in tests/fixtures/sources/: simple.html, accordion.html, radware.html are hand-written
(no real network fetch — this module never makes one); sample.pdf/blank.pdf were generated
locally with a minimal hand-built PDF writer and their expected extracted text verified directly
against the pinned pypdf==6.19.0 before being committed as fixtures, so the expectations below
are not guessed.
"""

from pathlib import Path

from app.services.source_extract import extract_html, extract_pdf

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sources"


def _html(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _pdf(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _locations_are_consistent(result) -> bool:
    """Every location's `text[start:end]` must be non-empty, ordered, and non-overlapping —

    the invariant T8's quote_match (later) relies on to slice `result.text` by these offsets.
    """
    previous_end = 0
    for loc in result.locations:
        if loc["start"] < previous_end or loc["start"] >= loc["end"]:
            return False
        if result.text[loc["start"] : loc["end"]] == "":
            return False
        previous_end = loc["end"]
    return True


# --- extract_html -------------------------------------------------------------------------


def test_extract_html_skips_script_and_style_content():
    result = extract_html(_html("simple.html"))
    assert "should-never-appear-in-extracted-text" not in result.text
    assert "color: red" not in result.text


def test_extract_html_tracks_heading_breadcrumb():
    result = extract_html(_html("simple.html"))
    by_text = {loc["start"]: loc for loc in result.locations}
    headings_seen = [tuple(loc["headings"]) for loc in result.locations]
    assert ("Leichtbau in BW",) in headings_seen
    assert ("Leichtbau in BW", "Mobilität") in headings_seen
    assert _locations_are_consistent(result)


def test_extract_html_closed_details_is_collapsed_with_summary_as_title():
    """docs/TASKS_CITATION_VERIFICATION.md design decision 10 — a closed <details> is collapsed,

    its <summary> text is itself visible (not collapsed), and becomes the collapsed_title for
    the rest of the details' content.
    """
    result = extract_html(_html("accordion.html"))

    summary_locations = [loc for loc in result.locations if result.text[loc["start"] : loc["end"]].strip() == "Bauwesen"]
    assert len(summary_locations) == 1
    assert summary_locations[0]["collapsed"] is False

    body_locations = [
        loc
        for loc in result.locations
        if "Durch den Einsatz von Leichtbauprinzipien" in result.text[loc["start"] : loc["end"]]
    ]
    assert len(body_locations) == 1
    assert body_locations[0]["collapsed"] is True
    assert body_locations[0]["collapsed_title"] == "Bauwesen"


def test_extract_html_hidden_element_resolves_title_via_aria_labelledby():
    result = extract_html(_html("accordion.html"))
    hidden_locations = [
        loc for loc in result.locations if "Elektromobilität" in result.text[loc["start"] : loc["end"]]
    ]
    assert len(hidden_locations) == 1
    assert hidden_locations[0]["collapsed"] is True
    assert hidden_locations[0]["collapsed_title"] == "Mobilität"


def test_extract_html_locations_cover_text_without_overlap():
    for name in ("simple.html", "accordion.html", "radware.html"):
        result = extract_html(_html(name))
        assert _locations_are_consistent(result), name


def test_extract_html_never_raises_on_malformed_markup():
    # Mismatched/stray end tags, an unclosed <div> — HTMLParser is lenient; this extractor's
    # own tag-stack handling must be too.
    html = "<html><body><div><p>Text</span></div></p><span>tail</html>"
    result = extract_html(html)
    assert "Text" in result.text
    assert "tail" in result.text


# --- extract_pdf ----------------------------------------------------------------------------


def test_extract_pdf_merges_hyphenated_linebreak():
    result = extract_pdf(_pdf("sample.pdf"))
    assert result is not None
    assert "Technologien" in result.text
    assert "Techno-\nlogien" not in result.text


def test_extract_pdf_records_page_starts_for_every_page():
    result = extract_pdf(_pdf("sample.pdf"))
    assert result is not None
    assert len(result.page_starts) == 2
    assert result.page_starts[0] == 0
    assert result.text[result.page_starts[1] :].startswith("Zweite Seite Inhalt.")


def test_extract_pdf_returns_none_when_no_text_layer():
    assert extract_pdf(_pdf("blank.pdf")) is None
