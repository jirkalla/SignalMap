# SignalMap — Claude Code Session Prompts: Runs Export (CSV/XLSX/JSON)

## Status: ✅ Done — PR #3, merged 2026-09-10, released in v1.0.0

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-runs-export (z aktuálního master)
## 2. Pět kódových promptů (EX-1 až EX-5), POŘADÍ VYNUCENÉ — viz docs/TASKS_EXPORT.md
##    "Task Index" pro odůvodnění (EX-2/3/4 volají service z EX-1; EX-5 testuje všechno).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit provádíš ty,
##    ne agent — agent NIKDY nespouští git commit/push sám bez výslovného potvrzení, a to i
##    přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_EXPORT.md přepni řádek daného task ID v tabulce "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-10: docs/TASKS_EXPORT.md — přečti si
##    konkrétní task ID před psaním kódu, ideálně celý soubor před EX-1. Vizuální návrh
##    (mockupy tlačítek, file-structure diagramy): Export Design artifact, odkaz v
##    docs/TASKS_EXPORT.md hlavičce.
## 9. EX-1 obsahuje krok "ověř aktuální stabilní verzi openpyxl" — NEPŘEBÍREJ číslo verze z
##    paměti/tréninkových dat, ověř ho v době implementace.
## 10. Až je větev hotová a smergnutá: v docs/TASKS.md přepnout "Runs export" sekci ze
##     "planned, not started" na hotovo s odkazem na tuhle větev (viz Completion Checklist v
##     TASKS_EXPORT.md).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-runs-export.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_EXPORT.md — CELÉ, hlavně design decisions 1-10

KONTEXT: Fáze 1 (docs/TASKS.md) a fáze 2 (docs/TASKS_PHASE2.md) jsou
hotové a smergnuté do master. Tahle větev přidává export runů do
CSV/XLSX/JSON na třech úrovních — jeden run, všechny runy promptu,
všechny runy klienta — rozšíření Task 6 (run list & detail), ne jedna
z pěti roadmap fází. Vizuální návrh (mockupy, file-structure diagramy):
Export Design artifact, odkaz v docs/TASKS_EXPORT.md hlavičce.

KRITICKÉ:
- Aktuální stabilní verze openpyxl se MUSÍ ověřit v době psaní kódu
  (EX-1) — neuhaduj/nepřebírej z paměti.
- Router zůstává tenký — VEŠKERÁ query/serializační logika žije v
  app/services/export.py (EX-1), ne rozesetá po routerech. Router jen
  parsuje query params, volá service, vrací Response.
- XLSX NIKDY nedostává volné soubory vstrčené do zip kontejneru zvenku
  (na rozdíl od CSV, kde je ZIP export legitimní) — Excel při otevření
  ohlásí poškozený balíček. raw_payload v XLSX jde do samostatného listu
  `RawPayload` jako JSON text, s truncate na EXCEL_CELL_CHAR_LIMIT
  (32767 znaků), nikdy jako výjimka za běhu.
- Export odkazy v šablonách jsou obyčejné <a href>, NIKDY hx-get/HTMX —
  stažení souboru potřebuje skutečnou navigaci prohlížeče.
- Prompt-scope export: default = jen verze z URL (Run.prompt_id ==
  prompt_id), `?versions=all` = celá lineage přes root_prompt_id. Nikdy
  neobrať tenhle default (viz design decision 5 — "export toho, co
  vidíš" je principle of least surprise).
- eager loading (selectinload) na Run.raw_response/citations/prompt/
  model/market v každém runs_for_* helperu — žádný N+1 na desítkách/
  stovkách runů.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný
JavaScript framework), Alembic migrace, Docker Compose. Backend kód
anglicky vč. komentářů/error_code, UI texty (tlačítka, ne obsah
exportu) přes t() mechanismus v app/i18n/{en,de}.json — nikdy natvrdo
v šabloně, oba jazyky v jednom commitu. Sloupce/hlavičky/názvy souborů
UVNITŘ exportovaných dat: pevně anglicky, i18n se na ně nevztahuje
(design decision 7).

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation) — export je čte, NIKDY
  nezapisuje/nemaže.
- Každá route funkce dostane docstring; každý netriviální Query()
  parametr description=....
- Nikdy git commit ani git push bez tvého výslovného potvrzení — i po
  tom, co agent sám navrhne commit message, čeká na "ano, commitni" než
  cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message.
Nikdy nespouštěj git add/commit/push sám bez výslovného pokynu — a to
i tehdy, když jsi zprávu sám navrhl v předchozí větě.
```

---
---

## PROMPT EX-1 — Export service foundation

```
Task: Prompt EX-1 — export service (queries + csv/xlsx/json writers)

Přečti docs/TASKS_EXPORT.md úkol EX-T1 CELÝ, hlavně design decisions
2, 3, 4, 9 (formáty, content tiers, XLSX raw_payload jako samostatný
list ne volný soubor v zipu, eager loading).

Než přidáš openpyxl do requirements.txt: ověř aktuální stabilní verzi
(web search / PyPI) — nepřebírej číslo z paměti.

1. requirements.txt — přidej pinnutou aktuální stabilní verzi openpyxl.
2. Nový app/services/export.py:
   - runs_for_run(db, run_id) -> list[Run]
   - runs_for_prompt(db, prompt_id, *, all_versions=False) -> list[Run]
     — all_versions=False filtruje Run.prompt_id == prompt_id;
     all_versions=True najde lineage (root_id/root_prompt_id, stejná
     logika jako _version_history v app/routers/prompts.py:53-60) a
     filtruje Run.prompt_id.in_(lineage_ids).
   - runs_for_client(db, client_id) -> list[Run] — join Run.prompt ->
     Prompt.prompt_set -> PromptSet.client_id == client_id.
   - Všechny tři se selectinload řetězcem: Run.raw_response ->
     RawResponse.citations, Run.prompt, Run.model -> AIModel.provider,
     Run.market. Žádný lazy-load v serializéru.
   - ExportContent literal: "answer" | "raw" | "full".
   - build_filename(scope, identifier, content, ext) -> str — schéma
     signalmap_{scope}_{identifier}[_{content}]_{YYYYMMDD}.{ext}
     (content suffix jen když != "answer").
   - build_csv_zip(runs, content) -> bytes — io.BytesIO + zipfile.
     ZipFile, csv.DictWriter pro runs.csv (id, prompt_id,
     prompt_version, is_current_version, client_name, prompt_set_name,
     prompt_text, market_code, provider_code, model_name, status,
     trigger_type, started_at, finished_at, latency_ms, error_message,
     rendered_text, has_citations, token_usage jako JSON text) a
     citations.csv (run_id, source_url, source_title, source_domain,
     citation_position, cited_answer_span) — header-only když run(y)
     nemají citace. content in ("raw", "full") přidá raw_<run_id>.json
     (pretty-printed raw_payload) do ZIPu per run s RawResponse.
   - build_xlsx(runs, content) -> bytes — openpyxl.Workbook(), list
     Runs (stejné sloupce jako runs.csv), list Citations (stejné
     sloupce jako citations.csv), a při content in ("raw", "full") list
     RawPayload (run_id, raw_payload_json). EXCEL_CELL_CHAR_LIMIT = 32767
     pojmenovaná konstanta — text nad limit se ořízne na
     EXCEL_CELL_CHAR_LIMIT - len(suffix) znaků + "... [truncated — use
     format=json for full payload]", žádná výjimka za běhu.
   - build_json(runs, content) -> bytes — vnořené pole objektů;
     raw_payload klíč jen při content in ("raw", "full"); rendered_text/
     citace jen při content in ("answer", "full").
   - Docstring na každé veřejné funkci.

Po dokončení:
1. docker compose exec app python — zavolej runs_for_run/build_csv_zip/
   build_xlsx/build_json na existujícím run ID z DB, ověř že bytes
   vzniknou bez chyby a mají očekávaný obsah (rozbal ZIP, otevři XLSX
   přes openpyxl.load_workbook, json.loads na JSON výstup).
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(export): add export service — run/prompt/client queries, csv/xlsx/json writers
```

### DONE — commit 19a2b5f

---
---

## PROMPT EX-2 — Run-scope export endpoint + UI

```
Task: Prompt EX-2 — single-run export endpoint + UI buttons

Přečti docs/TASKS_EXPORT.md úkol EX-T2 CELÝ.
Prerekvizita: EX-1 hotový.

1. app/routers/runs.py — GET /runs/{run_id}/export: Query() params
   format (csv/xlsx/json, default json) a content (answer/raw/full,
   default answer), description= na obou. 404 přes existující AppError
   vzor když run neexistuje. Zavolej runs_for_run + odpovídající
   build_*, vrať Response se správným media_type (application/zip /
   application/vnd.openxmlformats-officedocument.spreadsheetml.sheet /
   application/json) a Content-Disposition: attachment; filename="..."
   (build_filename("run", str(run_id), content, ext)). Docstring na
   route funkci.
2. app/templates/runs/detail.html — skupina tří <a href="/runs/{{
   run.id }}/export?format=csv">/xlsx/json odkazů (export-group vzhled
   z Export Design artifactu, NE hx-get). Umísti vedle status badge na
   vrcholu detailu.
3. i18n (EN+DE, jeden commit) — run.export_csv, run.export_xlsx,
   run.export_json label klíče.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči na existujícím run detailu: klik na CSV → stáhne se
   .zip s runs.csv + citations.csv se správnými daty. XLSX → otevře se
   bez "oprava souboru" dialogu, listy Runs/Citations sedí. JSON →
   validní JSON, jeden objekt v poli.
3. Zkus ?content=raw a ?content=full ručně přes URL — raw JSON
   přítomný/nepřítomný podle tieru, XLSX RawPayload list jen u
   raw/full.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): add single-run export (csv/xlsx/json) with content-tier selection
```

### DONE — commit 3f9a9a4

---
---

## PROMPT EX-3 — Prompt-scope export endpoint + UI

```
Task: Prompt EX-3 — per-prompt runs export + UI

Přečti docs/TASKS_EXPORT.md úkol EX-T3 CELÝ, hlavně design decision 5
(verzování — default = jen verze z URL, ?versions=all = celá lineage).
Prerekvizita: EX-1, EX-2 hotové.

1. app/routers/runs.py — GET /prompts/{prompt_id}/runs/export: stejné
   format/content params jako EX-2, plus versions (current/all,
   default current). Importuj _get_prompt_or_404 z
   app/routers/prompts.py pro 404 (stejný vzor jako trigger_run). Volej
   runs_for_prompt(db, prompt_id, all_versions=(versions == "all")).
   Prázdný seznam runů → pořád validní (prázdné) CSV/XLSX/JSON, ne
   chyba.
2. app/templates/prompts/detail.html — stejná tříčlenná skupina odkazů
   jako EX-2, plus checkbox/toggle "include all versions"
   (?versions=all) vedle nich — jen pokud _version_history(prompt)
   vrátí víc než jednu verzi.
3. i18n (EN+DE) — prompt.export_csv/xlsx/json,
   prompt.export_all_versions_label.

Po dokončení:
1. docker compose up -d --build
2. Na promptu s víc runy: export bez versions=all obsahuje jen runy
   aktuální verze; s versions=all (na promptu s editační historií)
   obsahuje runy napříč verzemi, každý řádek má správný
   prompt_version/is_current_version.
3. Export promptu bez jediného runu → validní prázdný soubor, ne
   500/chyba.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(prompts): add per-prompt runs export with optional full-version-history scope
```

### DONE — commit 5e74fe5 (plus unrelated fix commit 128ee1d landed in between — see docs/TASKS_EXPORT.md EX-6 for the delete-lineage bug it fixed)

---
---

## PROMPT EX-4 — Client-scope export endpoint + UI

```
Task: Prompt EX-4 — whole-client runs export + UI

Přečti docs/TASKS_EXPORT.md úkol EX-T4 CELÝ.
Prerekvizita: EX-1, EX-2 hotové.

1. app/routers/runs.py — GET /clients/{client_id}/runs/export: stejné
   format/content params, žádný versions param (client scope zahrnuje
   všechno automaticky). 404 přes existující client lookup vzor
   (app/routers/clients.py). Volej runs_for_client.
2. app/templates/clients/detail.html — stejná tříčlenná skupina odkazů
   u nadpisu detailu klienta.
3. i18n (EN+DE) — client.export_csv/xlsx/json.

Po dokončení:
1. docker compose up -d --build
2. Na klientovi s runy napříč víc prompt sety/prompty (i napříč
   providery, pokud fáze 2 data existují): export obsahuje runy ze
   všech, ne jen z jednoho prompt setu.
3. Export klienta bez jediného runu → validní prázdný soubor.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(clients): add whole-client runs export across all prompt sets
```

### DONE — commit f4db340

---
---

## PROMPT EX-5 — Testy: pytest sada pro export

```
Task: Prompt EX-5 — export test suite

Přečti docs/TASKS_EXPORT.md úkol EX-T5 CELÝ.
Prerekvizita: EX-1 až EX-4 hotové.

1. Nový tests/test_export.py, na existující conftest.py fixture
   (seed data + FakeAdapter runy, stejný vzor jako tests/test_runs.py):
   - Run-scope: format × content matice (9 kombinací) vrací 200 se
     správným Content-Type/Content-Disposition; CSV rozbalitelný ZIP;
     XLSX otevřitelný openpyxl.load_workbook; JSON json.loads-itelný.
     404 na neexistující run_id.
   - Prompt-scope: versions=current vs versions=all vrací různý počet
     řádků na promptu se dvěma verzemi (fixture založí edit → novou
     verzi, jako existující versioning test). Prázdný prompt (bez
     runů) → validní prázdný export, ne 500.
   - Client-scope: runy napříč dvěma prompt sety obě v exportu;
     prázdný klient → validní prázdný export.
   - raw/full content: RawPayload list přítomný v XLSX jen u těchto
     tierů; truncate chování ověřené syntetickým run s uměle velkým
     raw_payload (>32767 znaků po serializaci) — ověř, že řádek
     obsahuje truncate suffix.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové)
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover runs export across scope/format/content-tier matrix
```

### DONE — commit 21885fb

---
---

## EX-6 — Code review pass (post-hoc, not a numbered PROMPT)

Not part of the original 5-prompt sequence — run after EX-1–EX-5 were all done and committed, on
user request ("proveď code review, zaměř se na security, stabilitu, DRY"). 5 parallel review
angles against the full branch diff, 7 verified findings, 6 fixed (CSV formula injection, XLSX
crash on control characters, delete-lineage duplication + missing tests + O(n) round-trips,
3x duplicated export-route boilerplate, 3x duplicated template markup, duplicated run→dict
mapping), 1 confirmed as an already-accepted tradeoff (no pagination on client export). Full
detail: `docs/TASKS_EXPORT.md` "EX-6" section. Also updated: `app/templates/help.html` (new
"Exporting runs" section, EN+DE) — the user asked for in-app docs to be checked/updated
alongside the code fix. A `/findings` entry on the two security fixes was drafted and then
removed at the user's explicit instruction ("jen já rozhoduju co dát do findings") — `/findings`
entries are the user's call, not something to add unprompted even when the content fits its
stated scope.

**Expected commit** (one commit for the whole review-and-fix pass, not split per finding):
```
fix(export): sanitize CSV/XLSX output against formula injection and illegal characters; dedupe export routes/templates/delete-lineage logic
```
