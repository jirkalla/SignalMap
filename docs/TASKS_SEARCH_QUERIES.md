# SignalMap — Tasks: Search Query Capture (from existing raw_payload)

## v1.1 | Září 2026
## Branch: feature/signalmap-search-queries
## Task ID prefix: SQ

> Fáze 1 (`docs/TASKS.md`), fáze 2 (`docs/TASKS_PHASE2.md`) a Runs export
> (`docs/TASKS_EXPORT.md`) jsou hotové a smergnuté. Tenhle dokument pokrývá
> vytažení a zobrazení vyhledávacích dotazů, které providery (Gemini,
> Anthropic) při groundingu skutečně vygenerují — data, která `raw_payload`
> už dnes obsahuje (je to nedotčený dump celé odpovědi, FR-10), jen se z něj
> neparsují. Motivace a zdroj rozhodnutí: `/findings` záznamy z 2026-09-10
> ("What Peec AI's actual output data reveals..."). Není to jedna z pěti
> roadmap fází ze skillu `signalmap-conventions` ani rozšíření existující
> fáze — je to hlubší parsing dat, která adaptery už dostávají.
>
> **v1.1 korekce (2026-09-10), po kritickém přezkumu:** dřívější rámování
> jako "levný quick win, udělat hned" bylo nepřesné ve dvou bodech —
> rozsah práce je srovnatelný s `EX-T1` (2 modely, 1 migrace, 2 adaptery,
> router, šablona, i18n, testy), ne triviální dopisek; a **žádná časová
> tíseň neexistuje**, protože `raw_payload` zůstává netknuté navždy — data
> jdou zpětně doplnit (backfill) jednorázovým skriptem nad historickými
> runy kdykoliv později, i po měsících. Návrh tabulky (design decisions
> níže) po přezkumu obstál beze změny — jde jen o to, kdy tuhle práci
> zařadit, ne jestli je technicky v pořádku. Viz "Kritický přezkum" sekce
> pod schema flagem.

---

## ⚠️ Schema flag (AI_INSTRUCTIONS.md §4 — potvrdit před SQ-T1)

Tahle práce potřebuje **novou tabulku nad rámec `schema_phase1.sql`** —
`search_queries`. Návrh níže je záměrně 1:1 okopírovaný ze stávajícího
`Citation` modelu (stejný FK/cascade/position vzor, žádné paralelní
pojmenování) — to je návrh, ne odsouhlasené rozhodnutí. **Nezačínej SQ-T1,
dokud tenhle návrh někdo výslovně nepotvrdí** — technický přezkum níže
potvrzuje, že návrh je bezpečný, ale rozhodnutí *kdy* to zařadit je čistě
na uživateli, ne něco, co lze odvodit z toho, že design obstál.

Alternativa, kterou jsem zvážil a zamítl: JSON sloupec (`search_queries
JSONB`) přímo na `raw_responses` místo nové tabulky. Zamítnuto — ne kvůli
horší agregaci (JSONB se dá dotazovat, `jsonb_array_elements_text` atd.,
to není blokující), ale kvůli **konzistenci vzoru**: projekt už řeší
přesně tenhle problém (seznam položek patřících k jedné raw response) u
`Citation`, a `signalmap-conventions` skill explicitně říká nevynalézat
paralelní pojmenování/vzor pro totéž. Dvě různá řešení stejného problému
v jedné codebase by byla nekonzistence bez důvodu, ne úspora.

### Kritický přezkum (2026-09-10)

Provedeno na výslovnou žádost, po prvním schválení návrhu — cílem bylo
nerubberstampovat vlastní dřívější doporučení. Závěry:

1. **Boolean flag (`has_search_queries`) není potřeba — ověřeno, ne jen
   odhadnuto.** `_map_citations` (`app/adapters/google.py`) vrací
   `has_citations=False` i když `grounding_metadata` existuje, ale
   `grounding_chunks` je prázdné — tedy i dnešní citace nerozlišují
   "metadata chybí" od "metadata přítomná, ale prázdná", obojí sbalí do
   `False`. Prázdný seznam u `search_queries` nese přesně stejnou
   informační hodnotu, jakou dnes nese `has_citations` — přidat boolean
   by bylo přesnější, než co má i vlastní vzor, který kopírujeme, tedy
   over-engineering nad rámec předlohy.
2. **Žádná časová tíseň.** `raw_payload` je kompletní, netknutý dump
   (FR-10) — search queries u každého historického runu tam už leží.
   Odložení týhle práce o měsíc neztratí žádná data; jde kdykoliv doplnit
   zpětně jednorázovým skriptem nad existujícím `raw_payload`. Argument
   "udělejme to teď, než přijdeme o data" neplatí.
3. **Rozsah práce je podceněný v dřívějším rámování.** Sahá do 2 modelů,
   1 migrace, 2 adapterů, routeru, šablony, i18n (2 jazyky), testů —
   srovnatelné s `EX-T1` (největší jednotlivý task exportu), ne "malý
   quick win". Nemění to, jestli se má práce udělat, jen jak o ní mluvit.
4. **Reverzibilita zůstává vysoká.** Aditivní schema změna, žádná jiná
   tabulka na `search_queries` nezávisí — `down` migrace prostě tabulku
   dropne. Nízké riziko špatného rozhodnutí i kdyby se later ukázalo, že
   se data nevyužívají.

**Verdikt:** návrh tabulky (design decisions níže) obstál beze změny.
Otevřená otázka není technická, je to priorita/timing vůči roadmapě
(`docs/TASKS.md` "After phase 1" — first analysis skill, dashboard) —
to je produktové rozhodnutí uživatele, ne něco, co tenhle dokument nebo
agent má rozhodnout sám.

---

## Design decisions (návrh — viz flag výše)

1. **Nová tabulka `search_queries`, přesná kopie `Citation` vzoru**
   (`app/models/run.py`):
   ```python
   class SearchQuery(Base):
       """One search query the provider issued while grounding a raw response."""

       __tablename__ = "search_queries"

       id: Mapped[int] = mapped_column(primary_key=True)
       raw_response_id: Mapped[int] = mapped_column(
           ForeignKey("raw_responses.id", ondelete="CASCADE"), nullable=False
       )
       query_text: Mapped[str] = mapped_column(Text, nullable=False)
       query_position: Mapped[int | None] = mapped_column(Integer)

       raw_response: Mapped["RawResponse"] = relationship(back_populates="search_queries")
   ```
   Plus `RawResponse.search_queries` relationship (`cascade="all, delete-orphan"`,
   stejně jako `RawResponse.citations`). Žádný `has_search_queries` boolean
   flag jako u `has_citations` — viz "Kritický přezkum" bod 1 výše:
   `has_citations` samo dnes nerozlišuje "metadata chybí" od "metadata
   prázdná" (obojí je `False`), takže prázdný seznam u `search_queries`
   nese stejnou informační hodnotu, ne méně.
2. **Migrace `0010_search_queries.py`** — `CREATE TABLE search_queries`
   podle modelu výše, `down_revision` navazuje na `0009_anthropic_provider_and_model_columns`
   (aktuální head k datu psaní tohohle dokumentu — ověř `alembic heads`
   před vytvořením migrace, kdyby mezitím přibylo něco dalšího).
3. **Adapter vrstva — `RawResponsePayload` (`app/adapters/base.py`) dostane
   nové pole** `search_queries: list[str] = field(default_factory=list)`,
   stejný vzor jako `citations: list[AdapterCitation]`. Pořadí v seznamu =
   pořadí, ve kterém provider dotazy vygeneroval; **žádná deduplikace** — 
   opakovaný dotaz se počítá dvakrát (relevantní pro budoucí frekvenční
   analýzu v dashboard fázi, stejně jako Peecův `query-fanouts` export
   počítá "Occurrences").
4. **Google adapter (`app/adapters/google.py`)** — nová `_map_search_queries(candidate)`
   vedle `_map_citations`, čte `grounding_metadata.web_search_queries`.
   **Ověřeno 2026-09-10** přímo proti nainstalovanému `google-genai==2.22.0`
   v běžícím kontejneru (`GroundingMetadata.model_fields["web_search_queries"]`):
   `list[str] | None`, popis "Web search queries for the following-up web
   search." — stejný `grounding_metadata` objekt, ze kterého `_map_citations`
   už dnes čte `grounding_chunks`/`grounding_supports`, tedy prokazatelně
   naplněný v reálném provozu. Použij přímo, žádné další ověřování
   nepotřeba. (Vedlejší zjištění: `ai.google.dev/gemini-api/docs/google-search`
   obsahuje i jinak vyhlížející příklad — krok `"google_search_call"` s
   `"arguments": {"queries": [...]}` — to je jiný, pravděpodobně agentic/
   "thinking" tool-call formát, ne tvar, který vrací klasické
   `generate_content` volání s `tools=[GoogleSearch()]`, které tenhle
   adapter používá. Neignoruj to úplně, ale nemění to implementaci níže.)
5. **Anthropic adapter (`app/adapters/anthropic.py`)** — nová
   `_map_search_queries(content)` vedle `_map_citations`, projde
   `response.content` bloky s `type == "server_tool_use"` a
   `name == "web_search"`, vytáhne `block.input.get("query")` v pořadí
   výskytu (na rozdíl od citací, které jsou na `text` blocích — search
   queries jsou na `server_tool_use` blocích, které v odpovědi předchází
   odpovídající `web_search_tool_result` blok). **Ověřeno 2026-09-10**
   dvěma zdroji, shoda: (a) `anthropic==1.4.0` SDK — `ServerToolUseBlock`
   má `type`, `name`, `input: Dict[str, object]` (input netypováno hlouběji,
   proto `.get("query")`, ne `["query"]`); (b) oficiální dokumentace
   (`platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool`)
   ukazuje přesný příklad odpovědi s `{"type": "server_tool_use", "name":
   "web_search", "input": {"query": "..."}}`. Použij přímo.
6. **Persistence — `app/routers/runs.py` (run-trigger success path, vedle
   existujícího `Citation` vytváření okolo řádku 232-243)** — po
   `db.flush()` (které dnes existuje kvůli `raw_response.id` pro citace)
   přidej smyčku vytvářející `SearchQuery` řádky ze `payload.search_queries`,
   `query_position` = index v seznamu.
7. **Zobrazení — `run_detail` (`app/routers/runs.py:260-284`) + `runs/detail.html`** —
   nová query `select(SearchQuery).where(SearchQuery.raw_response_id ==
   raw_response.id).order_by(SearchQuery.query_position)`, předaná do
   šablony jako `search_queries` context var (stejný vzor jako `citations`
   dnes). V šabloně nová sekce hned vedle existující "Citations" sekce
   (`runs/detail.html:73-85`), stejný vizuální vzor (nadpis + seznam),
   i18n klíče `run.search_queries_label` / `run.search_queries_empty`
   (EN+DE, jeden commit).
8. **Žádná agregace/leaderboard v tomhle dokumentu.** Zobrazení je čistě
   per-run (stejná úroveň jako citace dnes). Doménová/napříč-runy agregace
   ("top queries", frekvence) je dashboard-fáze práce, zmíněná ve
   findings jako samostatný, pozdější krok — nemíchat do týhle práce.
9. **Cross-provider ověření (2026-09-10)** — než byl návrh tabulky
   považovaný za hotový, ověřil jsem i zbylé dva providery zmíněné ve
   skillu (`signalmap-conventions`, "later Perplexity/OpenAI") proti
   oficiální dokumentaci, i když zatím nemají adapter:
   - **OpenAI (Responses API)** — `output[].action.query`, kde
     `output[].type == "web_search_call"` a `action.type == "search"`.
     Jeden dotaz na položku, víc položek za odpověď (stejný vzor jako
     Anthropic). Důležité: `action` může být i `open_page`/`find_in_page`
     bez `query` vůbec — dokumentace sama píše, že search akce "usually
     but not always" dotaz obsahuje. Budoucí `_map_search_queries` musí
     filtrovat na `action.type == "search"` A ošetřit chybějící `query`
     stejně jako Anthropic adapter dnes ošetřuje chybějící `input`.
   - **Perplexity (Sonar)** — **API dotazy vůbec nevrací.** Response
     schema má jen `citations`, `search_results`, `related_questions` a
     `usage.num_search_queries` (počet, ne text). Tohle není "zatím
     nepostavené", je to strukturální limit API — Perplexity adapter,
     až vznikne, bude mít `search_queries` trvale prázdné, ne dočasně.
     Nevyžaduje to změnu tabulky (prázdný seznam už reprezentuje "žádná
     data" správně), ale stojí za poznámku v budoucím `perplexity.py`
     docstringu, ať se to nikdo nesnaží "opravit".

   Závěr: návrh tabulky (design decision 1) pokrývá i budoucí OpenAI
   adapter beze změny. Perplexity je permanentní výjimka na úrovni
   adapteru, ne na úrovni schématu.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| SQ-T1 | Schema: `SearchQuery` model + migrace `0010` | ⏳ |
| SQ-T2 | Adapter vrstva: `RawResponsePayload.search_queries` + mapping v obou adapterech | ⏳ |
| SQ-T3 | Persistence + zobrazení na run detailu | ⏳ |
| SQ-T4 | Testy: pytest pokrytí (mapping funkce + perzistence/zobrazení) | ⏳ |
| SQ-T5 (volitelné) | Export integrace — search queries do CSV/XLSX/JSON | ⏳ |

Pořadí vynucené: SQ-T2 potřebuje model z SQ-T1 (perzistence v SQ-T2 zatím
ne, jen datový tvar). SQ-T3 potřebuje SQ-T1 (tabulka) i SQ-T2 (adapter
pole). SQ-T4 testuje všechno předchozí najednou. SQ-T5 je nezávislé na
SQ-T4, ale logicky navazuje na SQ-T3 (nejdřív musí něco existovat, než se
to dá exportovat) — a je označené jako volitelné, protože sahá do už
hotové a smergnuté Export práce; **potvrdit zvlášť, než se do něj pustíš.**

---

## SQ-T1 — Schema: model + migrace

**Target:** `app/models/run.py`, nová `alembic/versions/0010_search_queries.py`

Prerekvizita: schema návrh výše potvrzený uživatelem (viz flag na začátku
dokumentu).

1. `app/models/run.py` — nová třída `SearchQuery` (přesný tvar viz design
   decision 1), umístěná hned za `Citation`. Přidej `search_queries`
   relationship na `RawResponse` (`cascade="all, delete-orphan"`, stejně
   jako `citations`).
2. `alembic heads` — ověř aktuální head revizi (očekáváno `0009_anthropic_provider_and_model_columns`,
   ověř že mezitím nepřibylo nic dalšího).
3. Nová migrace `0010_search_queries.py` — `CREATE TABLE search_queries`
   (`id` serial PK, `raw_response_id` FK → `raw_responses.id` s
   `ondelete=CASCADE`, `query_text` text not null, `query_position`
   integer nullable), `down_revision` = zjištěný head. `downgrade()` dropne
   tabulku.

Po dokončení:
1. `docker compose exec app alembic upgrade head` — proběhne bez chyby.
2. `docker compose exec app python -c "from app.models.run import SearchQuery; print(SearchQuery.__table__)"` —
   potvrdí, že model a tabulka sedí.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add search_queries table for provider grounding search queries
```

---

## SQ-T2 — Adapter vrstva

**Target:** `app/adapters/base.py`, `app/adapters/google.py`, `app/adapters/anthropic.py`

Prerekvizita: SQ-T1 hotový (jen kvůli konzistenci názvů — tenhle task sám
o sobě do DB nic neukládá).

Pole/tvary jsou už ověřené (design decisions 4-5, 2026-09-10) — žádné
další ověřování před psaním kódu není potřeba.

1. `app/adapters/base.py` — `RawResponsePayload` dostane nové pole
   `search_queries: list[str] = field(default_factory=list)`, hned za
   `citations`. Docstring update.
2. `app/adapters/google.py` — nová `_map_search_queries(candidate) -> list[str]`
   vedle `_map_citations`, čte `getattr(metadata, "web_search_queries",
   None) or []` (design decision 4). Zavolej ji v `run()` vedle
   `_map_citations`, výsledek dej do `RawResponsePayload.search_queries`.
3. `app/adapters/anthropic.py` — nová `_map_search_queries(content) -> list[str]`
   vedle `_map_citations`, projde bloky `type == "server_tool_use"` a
   `name == "web_search"`, vytáhne `(getattr(block, "input", None) or {}).get("query")`
   (design decision 5). Zavolej ji v `run()`, výsledek do
   `RawResponsePayload.search_queries`.

Po dokončení:
1. `docker compose exec app python` — spusť reálný run přes oba adaptery
   (Gemini i Anthropic) na existujícím promptu s groundingem/web_search
   aktivním, ověř že `payload.search_queries` je neprázdný seznam
   stringů, ne prázdný list (pokud model při daném promptu vyhledával —
   pokud ne, ověř aspoň, že kód neshodí s chybou na prázdné/chybějící poli).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(adapters): extract search queries from Gemini grounding and Anthropic web_search blocks
```

---

## SQ-T3 — Persistence + zobrazení

**Target:** `app/routers/runs.py`, `app/templates/runs/detail.html`, `app/i18n/en.json`, `app/i18n/de.json`

Prerekvizita: SQ-T1, SQ-T2 hotové.

1. `app/routers/runs.py` — v run-trigger success path (vedle existujícího
   `Citation` vytváření po `db.flush()`, řádky ~232-243): smyčka
   `for position, query_text in enumerate(payload.search_queries):` která
   vytvoří `SearchQuery(raw_response_id=raw_response.id, query_text=query_text,
   query_position=position)` a `db.add(...)`.
2. `run_detail` (řádky 260-284) — nová query `select(SearchQuery).where(
   SearchQuery.raw_response_id == raw_response.id).order_by(
   SearchQuery.query_position)` (prázdný seznam, když `raw_response` je
   `None`, stejný vzor jako `citations` dnes). Přidej `"search_queries":
   search_queries` do template contextu.
3. `app/templates/runs/detail.html` — nová sekce hned za existující
   Citations sekcí (řádky 73-85), stejný vizuální vzor (nadpis přes
   `t('run.search_queries_label')`, seznam přes `{% for q in
   search_queries %}` zobrazující `q.query_text` a `q.query_position`,
   prázdný stav přes `t('run.search_queries_empty')`).
4. i18n (EN+DE, jeden commit) — `run.search_queries_label`,
   `run.search_queries_empty`.

Po dokončení:
1. `docker compose up -d --build`
2. Spusť nový run na promptu, u kterého SQ-T2 ověřilo neprázdné
   `search_queries` — na run detailu se zobrazí seznam dotazů ve správném
   pořadí, pod Citations sekcí.
3. Run bez search queries (nebo starý run z před touto změnou) → zobrazí
   `run.search_queries_empty`, ne chybu.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): persist and display provider search queries on run detail
```

---

## SQ-T4 — Testy

**Target:** `tests/test_adapters.py` (nebo existující ekvivalent), `tests/test_runs.py`

Prerekvizita: SQ-T1 až SQ-T3 hotové.

1. Unit testy na `_map_search_queries` v obou adapterech (mock/fixture
   response objekt s `grounding_metadata.web_search_queries` resp.
   `server_tool_use` bloky) — ověř správné pořadí a že chybějící
   pole/blok vrátí prázdný seznam, ne chybu.
2. `tests/test_runs.py` (nebo nový soubor) — přes `FakeAdapter` s
   `payload_to_return` obsahujícím `search_queries`, ověř že run-trigger
   vytvoří odpovídající `SearchQuery` řádky ve správném pořadí a že
   `run_detail` je zobrazí. Run bez `search_queries` → prázdný seznam na
   detailu, ne chyba.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover search query extraction, persistence, and display
```

---

## SQ-T5 (volitelné) — Export integrace

**Potvrdit zvlášť před začátkem — sahá do už hotové/smergnuté Export práce.**

**Target:** `app/services/export.py`

Prerekvizita: SQ-T1 až SQ-T4 hotové.

Přidat `search_queries` jako čtvrtý list/sheet (`SearchQueries` v XLSX,
`search_queries.csv` v ZIPu, `search_queries` klíč v JSON) na stejné
`content in ("answer", "full")` úrovni jako citace, se sloupci `run_id`,
`query_text`, `query_position`. Vyžaduje `selectinload(RawResponse.search_queries)`
přidané do `runs_for_*` helperů (design decision 9 z `docs/TASKS_EXPORT.md`)
— žádný nový N+1.

Po dokončení: rozšířit `tests/test_export.py` o `search_queries` ve všech
třech formátech, stejný vzor jako citace. Implementation summary + navrhni
commit message (nespouštěj git).

**Expected commit:**
```
feat(export): include search queries in run/prompt/client exports
```

---

## Completion Checklist

- [ ] Schema návrh (design decisions výše) výslovně potvrzený před SQ-T1
- [ ] `search_queries` tabulka + migrace `0010`
- [ ] `RawResponsePayload.search_queries` + mapping v `google.py` a `anthropic.py`,
      SDK pole/tvar ověřené proti aktuální dokumentaci (ne z paměti)
- [ ] Run-trigger ukládá `SearchQuery` řádky, `run_detail` je zobrazuje
- [ ] i18n klíče `run.search_queries_label`/`run.search_queries_empty` (EN+DE)
- [ ] `pytest` sada zelená, pokrývá mapping + perzistenci + zobrazení
- [ ] Ověřeno v prohlížeči na ~640px/~1024px/desktop
- [ ] (Volitelné, SQ-T5) Export rozšířený o search queries
- [ ] `docs/TASKS.md` — nová sekce "Search query capture" po smergnutí
