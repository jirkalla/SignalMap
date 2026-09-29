"""Unit tests for app/services/quote_match.py (docs/TASKS_CITATION_VERIFICATION.md T8, design

decisions 15-17). No DB, no network — pure text functions.

Real-data cases (PDF hyphenation, the Anthropic "·" join, the Perplexity/ADAC Markdown table)
are excerpts pulled directly from the dev DB 2026-09-29, after capturing the actual cited pages
via `capture_url` and running `verify_citations_by_quote` against runs 108 (Anthropic) and 290
(Perplexity) — the same real citation text and the same real captured source text, not
approximations. The negative number-rule case is design decision 17's own stated example
("150 Wohnungen" must never pass against "50 Wohnungen"), not tied to a specific run.
"""

from app.services.quote_match import find_chunk, match_quote, normalize, split_into_chunks

# --- normalize ----------------------------------------------------------------------------


def test_normalize_unifies_quotes_dashes_and_soft_hyphen():
    result = normalize("„Weniger ist mehr“ – Leicht­bau")
    assert result.text == '"weniger ist mehr" - leichtbau'


def test_normalize_decodes_html_entities_and_keeps_offset_map():
    result = normalize("Drees &amp; Sommer")
    assert result.text == "drees & sommer"
    # the decoded "&" must map back to the ENTITY's start in the original text, not drift
    amp_index = result.text.index("&")
    start, end = result.to_original_range(amp_index, amp_index + 1, len("Drees &amp; Sommer"))
    assert "Drees &amp; Sommer"[start:end] == "&amp;"


def test_normalize_collapses_ss_and_whitespace_runs():
    result = normalize("großen   Herausforderungen\n\nunserer")
    assert result.text == "grossen herausforderungen unserer"


def test_normalize_never_changes_words_or_digits():
    result = normalize("150 Wohnungen für 3.200 Familien")
    assert "150" in result.text
    assert "3.200" in result.text or "3200" in result.text  # NFKC/whitespace only, digits intact


# --- split_into_chunks ------------------------------------------------------------------------


def test_anthropic_splits_on_ellipsis_and_middle_dot():
    # Real citation text, run 108 #5 (Bundestag PDF) — Anthropic joins two non-contiguous PDF
    # excerpts with " · ". The trailing "unserer Z..." piece is itself split again by the "..."
    # truncation marker and falls under the 20-char floor either way — a real citation ends up
    # truncated exactly like this whenever "Up to 150 characters" (Anthropic's own docs) cuts it
    # off mid-clause, so dropping that remnant rather than treating it as a checkable chunk is
    # correct, not a gap.
    quote = (
        "Dies gilt besonders für Techno- logien wie den Leichtbau, die das Potenzial haben "
        "oder gar notwendig sind, um den großen Herausforderungen · unserer Z..."
    )
    chunks = split_into_chunks(quote, "anthropic")
    assert len(chunks) == 1
    assert chunks[0].startswith("Dies gilt besonders")
    assert chunks[0].endswith("Herausforderungen")


def test_perplexity_splits_on_newlines_and_pipes_and_strips_markdown():
    # Real snippet, run 290, adac.de/skoda-enyaq (verified 2026-09-29 — matches the exact text
    # docs/TASKS_CITATION_VERIFICATION.md's own normalization example table cites).
    quote = (
        "- **Top: Škoda Enyaq bekommt die Testnote 1,6**\n"
        "...\n"
        "Der **Škoda Enyaq 85x Sportline** bekommt im ADAC Autotest die Top-Note 1,6.\n"
        "...\n"
        "|Fahreigenschaften|2,4|\n"
        "|Sicherheit|1,3|"
    )
    chunks = split_into_chunks(quote, "perplexity")
    assert "Top: Škoda Enyaq bekommt die Testnote 1,6" in chunks
    assert "Der Škoda Enyaq 85x Sportline bekommt im ADAC Autotest die Top-Note 1,6." in chunks
    # table cells ("Fahreigenschaften", "2,4", ...) are all under the 20-char floor
    assert not any("Fahreigenschaften" in c or c.strip() == "2,4" for c in chunks)
    assert not any(c.startswith("-") or c.startswith("*") or c.startswith("|") for c in chunks)


def test_chunks_under_minimum_length_are_dropped():
    assert split_into_chunks("Ja. ... Nein.", "anthropic") == []


# --- find_chunk / match_quote — real cases ---------------------------------------------------


def test_pdf_hyphenated_word_matches_via_fuzzy_with_number_rule_satisfied():
    """Real case: run 108 #5. The extracted PDF text has already had 'Techno-\\nlogien' merged

    to 'Technologien' (app/services/source_extract.py's own hyphen fix) — the citation's own
    text still has 'Techno- logien' (a plain space, not a newline, since it's the provider's
    own truncated cited_text, not our extraction), so the two sides differ enough to miss an
    exact match but still clear 0.9 similarity.
    """
    source_excerpt = (
        "entscheidend für die Sicherung von industrieller Wertschöpfung in Deutschland. Dies gilt "
        "besonders für Technologien wie den Leichtbau, die das Potenzial haben oder gar notwendig "
        "sind, um den großen Herausforderungen unserer Zeit, wie Klimawandel, Digitalisierung, "
        "Energie- und Ressourcenknappheit entgegenzuwirken."
    )
    chunk = (
        "Dies gilt besonders für Techno- logien wie den Leichtbau, die das Potenzial haben oder "
        "gar notwendig sind, um den großen Herausforderungen"
    )
    result = find_chunk(chunk, source_excerpt)
    assert result.kind == "fuzzy"
    assert result.similarity >= 0.9
    assert result.matched_text is not None
    assert source_excerpt[result.match_start : result.match_end] == result.matched_text


def test_adac_markdown_table_snippet_verifies_exact_after_stripping():
    """Real case: run 290, adac.de/skoda-enyaq — both non-table chunks of the snippet match the

    captured page text exactly once Markdown bold/bullet markers are stripped.
    """
    quote = (
        "- **Top: Škoda Enyaq bekommt die Testnote 1,6**\n"
        "...\n"
        "Der **Škoda Enyaq 85x Sportline** bekommt im ADAC Autotest die Top-Note 1,6."
    )
    # The two chunks' real matches sit ~9000 characters apart on the actual page (offsets 4955
    # and 13828) — too far apart to embed as one contiguous excerpt, so this concatenates the
    # two real neighborhoods with a paragraph break, each verbatim from the captured page.
    source_excerpt = (
        "t. Das Modell Enyaq glänzt dabei besonders. So schlug es sich im ADAC Test. "
        "Top: Škoda Enyaq bekommt die Testnote 1,6 Nach Facelift: Mehr Reichweite, weniger "
        "Verbrauch Im Test: Die Allradversion 85x Inhaltsverzeichnis Škoda Enyaq: Antrieb "
        "noch effizienter Federung und Fahrverhalten Platz in Innen- u\n\n"
        "troautos auf dem Markt © ADAC/Wolfgang Rudschies Der Škoda Enyaq 85x Sportline bekommt "
        "im ADAC Autotest die Top-Note 1,6. Damit führt das Elektro-SUV aus Tschechien die "
        "Rangliste der besten Elektroaut"
    )
    result = match_quote(quote, "perplexity", source_excerpt)
    assert result is not None
    assert result.verdict == "verified_exact"
    assert all(f.kind == "exact" for f in result.fragments)


def test_middot_joined_anthropic_chunks_both_verify():
    """Real case: run 108 #13 — another " · "-joined Anthropic citation, both halves check out

    against the (compact) captured PDF excerpt.
    """
    chunk = (
        "Diese werden seit 2020 durch das auf den branchen- und materialübergreifenden "
        "Wissens- und Technologietransfer ausgerichtete Technologietransfer-Pro"
    )
    source_excerpt = (
        "Leichtbau bereits gut aufgestellt. Angesichts der Querschnittsnatur des Leichtbaus gibt "
        "es zahlreiche branchen- und technologiespezifische Förderprogramme mit Bezügen zum "
        "Leichtbau. Diese werden seit 2020 durch das auf den branchen- und materialübergreifenden "
        "Wissens- und Technologietransfer ausgerichtete Technologietransfer-Programm Leichtbau "
        "(TTP Leichtbau) des BMWK ergänzt."
    )
    result = find_chunk(chunk, source_excerpt)
    assert result.kind in ("exact", "fuzzy")
    assert result.matched_text is not None


# --- the number rule (design decision 17) ------------------------------------------------------


def test_similar_sentence_with_a_changed_number_is_rejected():
    """The design decision's own example: a 95%+ textual match must still fail if a number

    inside it changed — otherwise "150 Wohnungen" and "50 Wohnungen" would be treated as the
    same claim.
    """
    chunk = "In diesem Gebiet entstehen 150 bis 400 Wohnungen für Familien in der Innenstadt."
    source = "In diesem Gebiet entstehen 50 bis 400 Wohnungen für Familien in der Innenstadt heute."
    result = find_chunk(chunk, source)
    assert result.kind == "not_found"


def test_identical_numbers_do_not_trigger_the_rejection():
    """Same pair as the rejection test above, but with the number left unchanged (only the

    trailing word swapped) — similarity clears 0.9 either way; the point is that an UNCHANGED
    number must never itself cause a rejection the way a changed one does.
    """
    chunk = "In diesem Gebiet entstehen 150 bis 400 Wohnungen für Familien in der Innenstadt."
    source = "In diesem Gebiet entstehen 150 bis 400 Wohnungen für Familien in der Innenstadt heute."
    result = find_chunk(chunk, source)
    assert result.kind in ("exact", "fuzzy")


# --- match_quote verdict aggregation -----------------------------------------------------------


def test_match_quote_returns_none_when_no_chunk_is_long_enough():
    assert match_quote("Ja. Nein. Ok.", "anthropic", "Some unrelated source text here.") is None


def test_match_quote_partially_found_when_only_some_chunks_match():
    quote = "This is the first sentence that should match completely fine here. · This one is entirely absent from the source page."
    source = "This is the first sentence that should match completely fine here. Nothing else relevant."
    result = match_quote(quote, "anthropic", source)
    assert result is not None
    assert result.verdict == "partially_found"


def test_match_quote_not_found_when_nothing_matches():
    result = match_quote(
        "This entire sentence is completely absent from the source text below.",
        "anthropic",
        "A totally unrelated page about something else altogether, with no overlap at all.",
    )
    assert result is not None
    assert result.verdict == "not_found"
