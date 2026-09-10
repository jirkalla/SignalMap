"""Direct unit tests for MentionVisibilityRunner (docs/TASKS_PHASE3.md P3-T3/P3-T5) — no HTTP/DB,
just in-memory Client/ClientAlias/Citation instances, since the runner's `run()` only reads
attributes off them.
"""

from app.analysis.mention_visibility import MentionVisibilityRunner
from app.models import Citation, Client, ClientAlias

runner = MentionVisibilityRunner()


def _client(name: str, domain: str | None = None, aliases: list[str] | None = None) -> Client:
    c = Client(name=name, slug="test", domain=domain)
    c.aliases = [ClientAlias(alias=a) for a in (aliases or [])]
    return c


def test_no_mention():
    result = runner.run("This text talks about some other company entirely.", [], _client("Acme Corporation"))
    assert result["text_mentioned"] is False
    assert result["mention_count"] == 0
    assert result["first_mention_position"] is None
    assert result["matched_terms"] == []
    assert result["match_spans"] == []


def test_mention_by_name():
    result = runner.run("Acme Corporation is a leading provider of widgets.", [], _client("Acme Corporation"))
    assert result["text_mentioned"] is True
    assert result["mention_count"] == 1
    assert result["matched_terms"] == ["Acme Corporation"]
    assert result["match_spans"] == [[0, len("Acme Corporation")]]


def test_mention_by_alias_only():
    client = _client("Acme Corporation", aliases=["Acme"])
    result = runner.run("Acme is a leading provider of widgets.", [], client)
    assert result["text_mentioned"] is True
    assert result["matched_terms"] == ["Acme"]
    assert result["match_spans"] == [[0, 4]]


def test_multiple_mentions_count_and_positions():
    client = _client("Acme Corporation", aliases=["Acme"])
    text = "Acme Corporation is great. Acme is great too."
    result = runner.run(text, [], client)
    assert result["mention_count"] == 2
    assert result["first_mention_position"] == 0
    assert result["match_spans"] == [[0, 16], [27, 31]]
    assert text[0:16] == "Acme Corporation"
    assert text[27:31] == "Acme"


def test_word_boundary_rejects_substring_inside_another_word():
    result = runner.run("Pinnacme Solutions has nothing to do with this.", [], _client("Acme"))
    assert result["text_mentioned"] is False
    assert result["mention_count"] == 0


def test_rendered_text_none_is_handled_without_error():
    result = runner.run(None, [], _client("Acme Corporation"))
    assert result["text_mentioned"] is False
    assert result["mention_count"] == 0
    assert result["first_mention_position"] is None
    assert result["match_spans"] == []


def test_client_domain_none_means_never_cited():
    citations = [Citation(source_domain="acme.com")]
    result = runner.run(None, citations, _client("Acme", domain=None))
    assert result["cited"] is False
    assert result["cited_domains"] == []


def test_citation_domain_matches_exactly():
    citations = [Citation(source_domain="acme.com")]
    result = runner.run(None, citations, _client("Acme", domain="acme.com"))
    assert result["cited"] is True
    assert result["cited_domains"] == ["acme.com"]


def test_citation_domain_matches_as_subdomain():
    citations = [Citation(source_domain="blog.acme.com")]
    result = runner.run(None, citations, _client("Acme", domain="acme.com"))
    assert result["cited"] is True
    assert result["cited_domains"] == ["blog.acme.com"]


def test_citation_on_a_different_domain_does_not_match():
    citations = [Citation(source_domain="other.com")]
    result = runner.run(None, citations, _client("Acme", domain="acme.com"))
    assert result["cited"] is False
    assert result["cited_domains"] == []


def test_match_spans_correspond_to_real_positions_and_lengths_in_text():
    # "Acme-like" also counts as a match: '-' is not a \w character, so it's a
    # word boundary like any other — same as re.IGNORECASE, this follows from
    # the regex in design decision 5, not a special case coded for this test.
    # "Acmematic" must NOT match — 'a' directly attached is not a boundary.
    client = _client("Acme Corporation", aliases=["Acme"])
    text = "Before. Acme Corporation is here. Acme-like tools exist. Acmematic doesn't count."
    result = runner.run(text, [], client)

    for start, end in result["match_spans"]:
        assert text[start:end] in ("Acme Corporation", "Acme")
    assert result["mention_count"] == 2
    assert result["matched_terms"] == ["Acme Corporation", "Acme"]
    assert "Acmematic" not in [text[start:end] for start, end in result["match_spans"]]


def test_matched_terms_never_claims_more_than_match_spans_can_show():
    # Regression: an alias overlapping the client name at a different start
    # position ("CD EF" starting inside "AB CD") used to survive into
    # matched_terms even when its span got dropped by overlap resolution,
    # desyncing matched_terms from match_spans/mention_count.
    client = _client("AB CD", aliases=["CD EF"])
    result = runner.run("AB CD EF is great.", [], client)

    assert len(result["matched_terms"]) == len(result["match_spans"])
    for term, (start, end) in zip(result["matched_terms"], result["match_spans"], strict=True):
        assert "AB CD EF is great."[start:end] == term
