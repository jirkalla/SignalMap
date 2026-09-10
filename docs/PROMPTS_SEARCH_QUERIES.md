# SignalMap — Claude Code Session Prompts: Search Query Capture

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. NEZAKLÁDEJ větev, dokud schema návrh v docs/TASKS_SEARCH_QUERIES.md
##    (sekce "⚠️ Schema flag") není výslovně potvrzený — je to nová tabulka
##    nad rámec schema_phase1.sql (AI_INSTRUCTIONS.md §4).
## 2. git checkout -b feature/signalmap-search-queries (z aktuálního master)
## 3. Čtyři povinné kódové prompty (SQ-1 až SQ-4), POŘADÍ VYNUCENÉ — viz
##    docs/TASKS_SEARCH_QUERIES.md "Task Index" pro odůvodnění. SQ-5 je
##    volitelný (Export integrace) — potvrdit zvlášť, sahá do už hotové
##    Export práce.
## 4. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 5. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická
##    kontrola daného promptu) než jdeš na další.
## 6. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 7. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_SEARCH_QUERIES.md přepni řádek daného task ID v
##       tabulce "Task Index" z ⏳ na ✅.
## 8. Nikdy nekombinuj dva prompty do jedné session.
## 9. Kompletní zdůvodnění vč. design decisions 1-9 (9 = cross-provider
##    ověření OpenAI/Perplexity, i když zatím nepostavené): docs/TASKS_SEARCH_QUERIES.md —
##    přečti si konkrétní task ID před psaním kódu, ideálně celý soubor
##    před SQ-1.
## 10. Pole/tvary pro SQ-2 jsou už ověřené (2026-09-10, viz design decisions
##     4-5 v docs/TASKS_SEARCH_QUERIES.md) — přímo proti nainstalovanému
##     google-genai SDK v kontejneru a proti Anthropic oficiální dokumentaci
##     + SDK. Žádné další ověřování před psaním kódu není potřeba.
## 11. Až je větev hotová a smergnutá: v docs/TASKS.md přidat novou sekci
##     "Search query capture" s odkazem na tuhle větev.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-search-queries.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_SEARCH_QUERIES.md — CELÉ, hlavně "⚠️ Schema flag" sekci a
   design decisions 1-9

KONTEXT: Fáze 1, fáze 2 a Runs export jsou hotové a smergnuté do master.
Tahle větev přidává zachycení vyhledávacích dotazů, které Gemini/Anthropic
při groundingu skutečně vygenerují — data, která `raw_payload` už dnes
obsahuje (nedotčený dump celé odpovědi), jen se z něj neparsují. Motivace:
/findings záznamy z 2026-09-10 o Peec.ai datech. Není to jedna z pěti
roadmap fází.

KRITICKÉ:
- Tahle práce zavádí NOVOU TABULKU (search_queries) nad rámec
  schema_phase1.sql. To je schema flag podle AI_INSTRUCTIONS.md §4 —
  pokud návrh v docs/TASKS_SEARCH_QUERIES.md ještě nebyl výslovně
  potvrzený, ZASTAV a zeptej se, než založíš migraci.
- Pole/tvary pro SQ-2 jsou už ověřené (2026-09-10): Gemini
  `grounding_metadata.web_search_queries` (`list[str] | None`, ověřeno
  přímo proti nainstalovanému `google-genai==2.22.0` v kontejneru) a
  Anthropic `server_tool_use` blok s `name="web_search"`, `input.query`
  (ověřeno proti SDK i oficiální dokumentaci s příkladem odpovědi). Použij
  přímo, žádné další ověřování.
- Nová tabulka `search_queries` kopíruje přesně vzor existující `Citation`
  tabulky (FK s ondelete=CASCADE na raw_responses, position sloupec) —
  žádné paralelní/jiné pojmenování.
- Zobrazení na run detailu je čistě per-run (stejná úroveň jako Citations
  sekce dnes) — ŽÁDNÁ agregace/leaderboard napříč runy v týhle práci,
  to je pozdější dashboard-fáze práce.
- Export integrace (SQ-5) je VOLITELNÁ a sahá do už hotové Export práce —
  nezačínej ji bez zvláštního potvrzení, i kdyby SQ-1 až SQ-4 proběhly
  hladce.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný
JavaScript framework), Alembic migrace, Docker Compose. Backend kód
anglicky vč. komentářů/error_code, UI texty přes t() mechanismus v
app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom
commitu.

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation, SearchQuery) — jednou
  vytvořené se nikdy nepřepisují/nemažou (kromě cascade delete při smazání
  rodiče). Nová verze/data = nový řádek.
- Router zůstává tenký — logika mapování dat žije v adapterech
  (app/adapters/), ne v routeru.
- Každá route funkce dostane docstring; každé netriviální pole popis.
- Nikdy git commit ani git push bez tvého výslovného potvrzení — i po
  tom, co agent sám navrhne commit message, čeká na "ano, commitni" než
  cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message.
Nikdy nespouštěj git add/commit/push sám bez výslovného pokynu — a to
i tehdy, když jsi zprávu sám navrhl v předchozí větě.
```

---
---

## PROMPT SQ-1 — Schema: model + migrace

```
Task: Prompt SQ-1 — search_queries tabulka + model + migrace

Přečti docs/TASKS_SEARCH_QUERIES.md úkol SQ-T1 CELÝ, hlavně "⚠️ Schema
flag" sekci a design decision 1-2.

ZASTAV a zeptej se, pokud schema návrh (nová tabulka search_queries,
1:1 podle Citation modelu) ještě nebyl výslovně potvrzený uživatelem v
téhle konverzaci.

1. app/models/run.py — nová třída SearchQuery hned za Citation:
   - id: Mapped[int] primary_key
   - raw_response_id: Mapped[int] ForeignKey("raw_responses.id",
     ondelete="CASCADE"), nullable=False
   - query_text: Mapped[str] Text, nullable=False
   - query_position: Mapped[int | None] Integer
   - raw_response: Mapped["RawResponse"] relationship(back_populates=
     "search_queries")
   Přidej search_queries relationship na RawResponse (cascade="all,
   delete-orphan", stejně jako citations).
2. alembic heads — ověř aktuální head revizi (očekáváno
   0009_anthropic_provider_and_model_columns).
3. Nová alembic/versions/0010_search_queries.py — CREATE TABLE
   search_queries (id serial PK, raw_response_id FK →
   raw_responses.id ondelete=CASCADE, query_text text not null,
   query_position integer nullable), down_revision = zjištěný head.
   downgrade() dropne tabulku.

Po dokončení:
1. docker compose exec app alembic upgrade head — bez chyby.
2. docker compose exec app python -c "from app.models.run import
   SearchQuery; print(SearchQuery.__table__)" — potvrdí tvar tabulky.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add search_queries table for provider grounding search queries
```

---
---

## PROMPT SQ-2 — Adapter vrstva

```
Task: Prompt SQ-2 — extrahovat search queries z Gemini a Anthropic odpovědí

Přečti docs/TASKS_SEARCH_QUERIES.md úkol SQ-T2 CELÝ, hlavně design
decision 3-5 — pole/tvary jsou už ověřené (2026-09-10), žádné další
ověřování před psaním kódu.
Prerekvizita: SQ-1 hotový.

1. app/adapters/base.py — RawResponsePayload dostane nové pole
   search_queries: list[str] = field(default_factory=list), hned za
   citations. Docstring update.
2. app/adapters/google.py — nová _map_search_queries(candidate) -> list[str]
   vedle _map_citations, čte getattr(metadata, "web_search_queries", None)
   or [] (design decision 4). Zavolej v run() vedle _map_citations.
3. app/adapters/anthropic.py — nová _map_search_queries(content) -> list[str]
   vedle _map_citations, projde bloky s type=="server_tool_use" a
   name=="web_search", vytáhne (getattr(block, "input", None) or {}).get("query")
   v pořadí výskytu (design decision 5). Zavolej v run() vedle _map_citations.

Po dokončení:
1. docker compose exec app python — spusť reálný run přes oba adaptery na
   existujícím promptu s aktivním groundingem/web_search, ověř že
   payload.search_queries je seznam stringů (neprázdný, pokud model
   vyhledával; každopádně bez chyby na chybějícím poli).
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(adapters): extract search queries from Gemini grounding and Anthropic web_search blocks
```

---
---

## PROMPT SQ-3 — Persistence + zobrazení

```
Task: Prompt SQ-3 — uložit a zobrazit search queries na run detailu

Přečti docs/TASKS_SEARCH_QUERIES.md úkol SQ-T3 CELÝ.
Prerekvizita: SQ-1, SQ-2 hotové.

1. app/routers/runs.py — v run-trigger success path, vedle existujícího
   Citation vytváření po db.flush() (řádky ~232-243): smyčka
   `for position, query_text in enumerate(payload.search_queries):`
   vytváří SearchQuery(raw_response_id=raw_response.id,
   query_text=query_text, query_position=position), db.add(...).
2. run_detail (řádky 260-284) — nová query select(SearchQuery).where(
   SearchQuery.raw_response_id == raw_response.id).order_by(
   SearchQuery.query_position) (prázdný seznam když raw_response is None,
   stejný vzor jako citations). Přidej "search_queries": search_queries
   do template contextu.
3. app/templates/runs/detail.html — nová sekce hned za existující
   Citations sekcí (řádky 73-85), stejný vizuální vzor: nadpis přes
   t('run.search_queries_label'), seznam přes {% for q in search_queries %}
   (zobraz q.query_text a q.query_position), prázdný stav přes
   t('run.search_queries_empty').
4. i18n (EN+DE, jeden commit) — run.search_queries_label,
   run.search_queries_empty.

Po dokončení:
1. docker compose up -d --build
2. Spusť nový run na promptu, u kterého SQ-2 ověřilo neprázdné
   search_queries — na run detailu se zobrazí seznam dotazů ve správném
   pořadí pod Citations sekcí.
3. Run bez search queries (nebo starý run z před touto změnou) → zobrazí
   run.search_queries_empty, ne chybu.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): persist and display provider search queries on run detail
```

---
---

## PROMPT SQ-4 — Testy

```
Task: Prompt SQ-4 — pytest pokrytí pro search query extrakci/perzistenci/zobrazení

Přečti docs/TASKS_SEARCH_QUERIES.md úkol SQ-T4 CELÝ.
Prerekvizita: SQ-1 až SQ-3 hotové.

1. Unit testy na _map_search_queries v obou adapterech (mock/fixture
   response objekt s grounding_metadata.web_search_queries resp.
   server_tool_use bloky) — ověř správné pořadí a že chybějící
   pole/blok vrátí prázdný seznam, ne chybu.
2. Test přes FakeAdapter (viz tests/fake_adapter.py) s payload_to_return
   obsahujícím search_queries — ověř že run-trigger vytvoří odpovídající
   SearchQuery řádky ve správném pořadí a že run_detail je zobrazí. Run
   bez search_queries → prázdný seznam na detailu, ne chyba.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover search query extraction, persistence, and display
```

---
---

## PROMPT SQ-5 (volitelné) — Export integrace

**Nespouštěj bez zvláštního potvrzení — sahá do už hotové Export práce.**

```
Task: Prompt SQ-5 — přidat search queries do run/prompt/client exportů

Přečti docs/TASKS_SEARCH_QUERIES.md úkol SQ-T5 CELÝ a
docs/TASKS_EXPORT.md design decision 9 (eager loading vzor).
Prerekvizita: SQ-1 až SQ-4 hotové. POTVRZENO uživatelem zvlášť (tenhle
task sahá do už smergnuté Export práce).

1. app/services/export.py — přidej search_queries jako čtvrtý
   list/sheet (SearchQueries v XLSX, search_queries.csv v ZIPu,
   search_queries klíč v JSON) na stejné content in ("answer", "full")
   úrovni jako citace, sloupce run_id, query_text, query_position.
   Přidej selectinload(RawResponse.search_queries) do runs_for_* helperů
   — žádný nový N+1.

Po dokončení:
1. Rozšiř tests/test_export.py o search_queries ve všech třech
   formátech, stejný vzor jako citace.
2. pytest — všechny testy zelené.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(export): include search queries in run/prompt/client exports
```
