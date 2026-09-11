"""Direct unit tests for CompetitiveVisibilityRunner (docs/TASKS_PHASE5.md P5-T5) — no HTTP/DB,
just in-memory Client/ClientAlias/TrackedEntity/TrackedEntityAlias/Citation instances, since the
runner's `run()` only reads attributes off them.
"""

from app.analysis.competitive_visibility import CompetitiveVisibilityRunner
from app.models import Citation, Client, ClientAlias, TrackedEntity, TrackedEntityAlias

runner = CompetitiveVisibilityRunner()


def _client(
    name: str,
    domain: str | None = None,
    aliases: list[str] | None = None,
    tracked: list[TrackedEntity] | None = None,
) -> Client:
    c = Client(name=name, slug="test", domain=domain)
    c.aliases = [ClientAlias(alias=a) for a in (aliases or [])]
    c.tracked_entities = tracked or []
    return c


def _entity(name: str, domain: str | None = None, aliases: list[str] | None = None) -> TrackedEntity:
    e = TrackedEntity(name=name, domain=domain)
    e.aliases = [TrackedEntityAlias(alias=a) for a in (aliases or [])]
    return e


def test_no_tracked_entities_still_produces_a_result_for_the_client_alone():
    client = _client("Acme")
    result = runner.run("Acme is mentioned here.", [], client)
    assert result["entities"] == [
        {
            "name": "Acme",
            "is_own_client": True,
            "mentioned": True,
            "mention_count": 1,
            "first_position": 0,
            "cited": False,
            "cited_domains": [],
        }
    ]
    assert result["share_of_voice"] == 1.0
    assert result["position"] == 1


def test_no_entity_mentioned_gives_none_share_of_voice_and_position():
    client = _client("Acme", tracked=[_entity("Globex"), _entity("Initech")])
    result = runner.run("This text is about neither company.", [], client)
    assert result["share_of_voice"] is None
    assert result["position"] is None
    assert all(e["mention_count"] == 0 for e in result["entities"])


def test_own_client_mentioned_first_gets_position_one():
    client = _client("Acme", tracked=[_entity("Globex"), _entity("Initech")])
    result = runner.run("Acme leads, then Globex, then Initech.", [], client)
    assert result["position"] == 1
    assert result["share_of_voice"] == round(1 / 3, 4)


def test_own_client_mentioned_last_gets_last_position():
    client = _client("Acme", tracked=[_entity("Globex"), _entity("Initech")])
    result = runner.run("Globex leads, then Initech, then Acme.", [], client)
    assert result["position"] == 3


def test_own_client_not_mentioned_gives_none_position_but_real_share_of_voice():
    client = _client("Acme", tracked=[_entity("Globex"), _entity("Initech")])
    result = runner.run("Globex and Initech lead this segment.", [], client)
    assert result["position"] is None
    assert result["share_of_voice"] == 0.0


def test_tiebreak_on_identical_first_position_is_alphabetical_by_name():
    # Both "Zeta Corp" (own client, via alias "Shared") and "Alpha Corp" (tracked entity, also
    # via alias "Shared") match the same word at the same start position — a real, if
    # deliberately contrived, way two different entities can tie on first_position.
    client = _client("Zeta Corp", aliases=["Shared"], tracked=[_entity("Alpha Corp", aliases=["Shared"])])
    result = runner.run("Shared is mentioned here.", [], client)
    mentioned = {e["name"] for e in result["entities"] if e["mention_count"] > 0}
    assert mentioned == {"Zeta Corp", "Alpha Corp"}
    assert result["position"] == 2  # "Alpha Corp" sorts before "Zeta Corp" alphabetically


def test_cited_and_cited_domains_are_computed_per_entity():
    citations = [Citation(source_domain="globex.com"), Citation(source_domain="unrelated.com")]
    client = _client("Acme", domain="acme.com", tracked=[_entity("Globex", domain="globex.com")])
    result = runner.run("Acme and Globex both appear.", citations, client)
    own = next(e for e in result["entities"] if e["is_own_client"])
    competitor = next(e for e in result["entities"] if not e["is_own_client"])
    assert own["cited"] is False
    assert own["cited_domains"] == []
    assert competitor["cited"] is True
    assert competitor["cited_domains"] == ["globex.com"]


def test_rendered_text_none_is_handled_without_error():
    client = _client("Acme", tracked=[_entity("Globex")])
    result = runner.run(None, [], client)
    assert result["share_of_voice"] is None
    assert result["position"] is None
    assert all(e["mention_count"] == 0 and e["first_position"] is None for e in result["entities"])
