# SignalMap — Claude Code Session Prompts: Bulk Prompt Import + Multi-Model Run

## Status: ✅ Done — PR #12, merged 2026-09-15, released in v1.0.0

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. Branch `feature/signalmap-bulk-import-multi-model` už existuje (založena 2026-09-14).
##    `git checkout feature/signalmap-bulk-import-multi-model` na začátku každé session.
## 2. Devět kódových promptů (BIM-1..BIM-9). DOPORUČENÉ POŘADÍ: BIM-1→2→3 (multi-model run),
##    pak BIM-4→5→8→9→6→7 (bulk import, BIM-8/9 přidané 2026-09-14 po bugu nalezeném při
##    ručním testu BIM-5) — viz "Pořadí" v docs/TASKS_BULK_IMPORT_MULTI_MODEL.md.
##    Skupiny na sobě nezávisí, uvnitř skupiny je pořadí vynucené (BIM-2 potřebuje guard z BIM-1,
##    BIM-5 potřebuje service z BIM-4, BIM-8 rozšiřuje parse_csv z BIM-4/5, BIM-9 potřebuje
##    formulář z BIM-5, BIM-6 potřebuje formulář z BIM-5).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu). Agent NIKDY nespouští
##    git commit/push sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl.
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_BULK_IMPORT_MULTI_MODEL.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-14: docs/TASKS_BULK_IMPORT_MULTI_MODEL.md —
##    přečti si konkrétní task ID před psaním kódu, ideálně celý soubor před BIM-1.
## 9. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md krátkou poznámku/odkaz na tenhle
##    branch a do docs/ROADMAP.md poznámku u "Hromadný import promptů"/"Study" (viz Completion
##    Checklist v TASKS_BULK_IMPORT_MULTI_MODEL.md) — teprve po tom, co uživatel v prohlížeči
##    potvrdí, že obě featury fungují (AI_INSTRUCTIONS.md §7).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-bulk-import-multi-model.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_BULK_IMPORT_MULTI_MODEL.md — CELÉ, hlavně design decisions 1-14

KONTEXT: appka spouští prompty proti AI providerům jednotlivě (jeden prompt, jeden model,
jeden request). Tahle branch přidává dvě nezávislé věci: (a) možnost zaškrtnout víc modelů
najednou a spustit je paralelně pro jeden prompt, (b) hromadný CSV/XLSX/JSON import promptů
do prompt setu místo přidávání po jednom. "Spustit všechny prompty najednou" NENÍ součást
téhle branch — čeká na Scheduler infrastrukturu (docs/ROADMAP.md bod 5), appka dnes nemá
žádný task queue / BackgroundTasks mechanismus.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný JS framework kromě
vanilla JS bloků v base.html), Docker Compose. Backend kód anglicky vč. komentářů, UI texty
přes t() mechanismus v app/i18n/{en,de}.json.

KRITICKÁ PRAVIDLA:
- Žádná nová DB tabulka ani sloupec v týhle branch (viz "⚠️ Schema flagy" v
  TASKS_BULK_IMPORT_MULTI_MODEL.md) — obě featury reuse existující Prompt/Run/AIModel modely.
- Multi-model run NEPŘIDÁVÁ novou route — jen víc paralelních volání existující
  POST /prompts/{id}/runs.
- Bulk import preview→confirm putuje jako indexovaná pole běžného Jinja formuláře
  (rows-0-text, rows-0-market_id, ...), ne jako skrytý JSON blob ani nová tabulka/session.
- Nikdy `git commit` ani `git push` bez tvého výslovného potvrzení — i po tom, co agent sám
  navrhne commit message, čeká na "ano, commitni" než cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message. Nikdy nespouštěj
git add/commit/push sám bez výslovného pokynu — a to i tehdy, když jsi zprávu sám navrhl
v předchozí větě.
```

---
---

## PROMPT BIM-1 — Duplicate-run guard: rozšíření na (prompt_id, model_id)

```
Task: Prompt BIM-1 — scope the pending-run guard to prompt+model

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T1 CELÝ, hlavně design decision 2.

1. app/routers/runs.py, trigger_run — rozšiř WHERE klauzuli pending-run kontroly
   (dnes jen Run.prompt_id == prompt_id) o Run.model_id == model_id. model_id je dostupné
   hned z Form(...) na začátku funkce, kontrola zůstává na svém dnešním místě.
2. Uprav docstring trigger_run (popis guardu) tak, aby odpovídal novému chování — "blocks...
   the same prompt AND model combination", ne jen "the same prompt".

Po dokončení:
1. docker compose up -d --build — appka nastartuje bez chyby
2. pytest — stávající test duplicitního běhu (stejný model dvakrát) musí projít beze změny
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
fix(runs): scope the pending-run guard to prompt+model, not prompt alone
```

### DONE — commit 688bf98

---
---

## PROMPT BIM-2 — Multi-model checkboxy + paralelní spuštění (UI)

```
Task: Prompt BIM-2 — multi-model checkboxes + parallel fetch on the run-trigger form

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T2 CELÝ, hlavně design decisions
1, 3, 4, 5, 6.
Prerekvizita: BIM-1 hotový (jinak paralelní test níže narazí na falešný 409).

1. app/templates/prompts/detail.html — nahraď dnešní <select name="model_id"> (s <optgroup>
   podle providera, data z model_groups) checkboxy <input type="checkbox" name="model_ids"
   value="{{ model.id }}">, pořád seskupené podle providera. market_id a persona_id zůstávají
   single <select>.
2. Vanilla JS (stejné umístění/styl jako stávající cost-badge/button-disable blok) na submit
   formuláře:
   - preventDefault()
   - posbírej zaškrtnuté model_ids; nula zaškrtnutých → inline chyba
     (t('prompt.select_at_least_one_model')), žádný request
   - Promise.all paralelních fetch('/prompts/{{ prompt.id }}/runs', {method: 'POST', body:
     FormData s jedním model_id + sdíleným market_id/persona_id}) pro každý zaškrtnutý model
   - inline stav vedle každého checkboxu (⏳ → ✅/❌ podle výsledku HTTP requestu)
   - po vyřešení všech fetch → window.location.reload()
3. Tlačítko: text podle počtu zaškrtnutých modelů přes i18n placeholder {count}
   (t('prompt.trigger_runs_count')), zablokované po dobu čekání na všechny fetch.
4. i18n (EN+DE, jeden commit): prompt.trigger_runs_count, prompt.select_at_least_one_model,
   prompt.select_all_models (volitelný "vybrat vše" link/checkbox).

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči: prompt se 2+ aktivními modely, zaškrtnout 2-3, spustit → paralelně (celkový
   čas ≈ nejpomalejší model, ne součet), historie runů ukáže všechny nové běhy
3. Zaškrtnout jen jeden model → chová se jako dnešní single-run
4. Nezaškrtnout nic, kliknout run → inline chyba, žádný request v Network tabu
5. Ověřit na ~640px/~1024px/desktop šířce
6. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(prompts): allow selecting multiple models and running them in parallel
```

### DONE — commit eaad7ab

---
---

## PROMPT BIM-3 — Testy: multi-model run

```
Task: Prompt BIM-3 — tests for the prompt+model scoped pending-run guard

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T3 CELÝ.
Prerekvizita: BIM-1, BIM-2 hotové.

1. tests/test_runs.py — nový test: dva různé modely, oba POST /prompts/{id}/runs s odlišným
   model_id, zatímco první je ještě pending (vytvořeno přímo přes ORM) → oba projdou (žádný
   409). Druhý test: stejný model podruhé, zatímco první pending → pořád 409 (no-regression).
2. Docstring poznamenává, že paralelní JS orchestrace (BIM-2) sama o sobě testem nepokrytá —
   ověřuje se ručně v prohlížeči.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové)
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover pending-run guard scoped to prompt+model combination
```

### DONE — commit f7db94f (bundled with BIM-4's initial prompt_import.py in the same commit — see chat log 2026-09-14)

---
---

## PROMPT BIM-4 — Bulk import: parsing/validace service

```
Task: Prompt BIM-4 — CSV/XLSX/JSON parsing + duplicate-detection service

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T4 CELÝ, hlavně design decisions
7, 8, 9, 10, 13.

1. Nový app/services/prompt_import.py — dataclass ParsedPromptRow (row_number, text,
   market_code, topic, is_active, status: Literal["new","duplicate","error"], error_message).
2. parse_csv(file: bytes) -> list[ParsedPromptRow] — csv.DictReader nad
   io.StringIO(file.decode("utf-8-sig")); UnicodeDecodeError → zkus cp1252, jinak
   strukturovaná chyba (doporuč UTF-8/XLSX). Sloupce case-insensitive: text povinný,
   market_code/topic/is_active volitelné.
3. parse_xlsx(file: bytes) -> list[ParsedPromptRow] — openpyxl.load_workbook(io.BytesIO(file),
   read_only=True, data_only=True), první list, první řádek = hlavička, přeskoč plně prázdné
   řádky kdekoliv.
4. parse_json(file: bytes) -> list[ParsedPromptRow] — json.loads, očekává pole objektů;
   strukturovaná chyba, pokud kořen není pole nebo prvek není objekt.
5. validate_and_check_duplicates(rows, db: Session, prompt_set_id: int, default_market_id: int)
   -> list[ParsedPromptRow]: doplň market_code chybějící → default_market_id; neznámý
   market_code → error; prázdný text po strip() → error; normalizovaný text
   (" ".join(text.split()).casefold()) proti existujícím current-version Prompt řádkům ve
   stejném (prompt_set_id, market_id) a proti ostatním řádkům stejného batche se stejným
   market_id → duplicate; jinak new.
6. Konstanty MAX_IMPORT_ROWS = 500, MAX_IMPORT_FILE_BYTES = 2_000_000 (kontroluje volající
   route v BIM-5, ne tenhle modul).

Po dokončení:
1. Ruční ověření (ad-hoc python -c, nebo počkej na BIM-5 UI) — CSV/XLSX/JSON se stejným
   obsahem dají stejný výsledek
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(prompts): add CSV/XLSX/JSON parsing and duplicate-detection service for bulk import
```

### DONE — commit f7db94f (bundled with BIM-3's test — see chat log 2026-09-14)

---
---

## PROMPT BIM-5 — Bulk import: upload formulář + preview route/šablona

```
Task: Prompt BIM-5 — bulk-import upload form + validation preview screen

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T5 CELÝ, hlavně design decisions
8, 12, 13, 14.
Prerekvizita: BIM-4 hotový.

1. app/routers/prompt_sets.py, GET /prompt-sets/{prompt_set_id}/prompts/import — formulář:
   file upload (accept=".csv,.xlsx,.json"), select "výchozí trh" (market_options(db)).
2. POST /prompt-sets/{prompt_set_id}/prompts/import/preview (file: UploadFile = File(...),
   default_market_id: int = Form(...)):
   - kontrola velikosti/přípony před parsováním, počtu řádků po naparsování
     (MAX_IMPORT_FILE_BYTES/MAX_IMPORT_ROWS) → strukturovaná AppError, ne raw 500
   - podle přípony zavolej parse_csv/parse_xlsx/parse_json (BIM-4), pak
     validate_and_check_duplicates
   - render nové app/templates/prompt_sets/import_preview.html — ŽÁDNÝ zápis do DB v tomhle
     kroku
3. import_preview.html — tabulka: row_number, zkrácený text (title tooltip s plným zněním),
   editovatelný select_field pro market, text_field pro topic, checkbox_field pro is_active,
   badge stavu (new/duplicate/error, reuse pill_badge makra). checkbox_field "Importovat" na
   začátku řádku — pre-checked pro new, pre-unchecked pro duplicate, disabled pro error.
   Souhrn nad tabulkou: "{new} nových, {duplicate} duplicitních, {error} chyb". Formulář
   POSTuje na .../import/confirm (BIM-6), indexovaná pole rows-{i}-text (hidden),
   rows-{i}-market_id, rows-{i}-topic, rows-{i}-is_active, rows-{i}-include.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči: CSV s 5 řádky (2 s market_code, 3 bez) → preview ukáže správný trh u všech
   (explicitní i doplněný default), správné duplicate flagy proti existujícím promptům
3. Soubor s neplatným market_code → řádek označený jako chyba, zbytek uploadu neovlivněn
4. Poškozený/nesprávný formát souboru → srozumitelná chyba na formuláři, ne 500
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(prompts): add bulk-import upload form and validation preview screen
```

### DONE — commit 3de2571 (bundled with BIM-8/BIM-9 in the same commit — see chat log 2026-09-14)

---
---

## PROMPT BIM-8 — CSV: delimiter sniffing + kontrola počtu polí na řádek

```
Task: Prompt BIM-8 — detect CSV delimiter and flag rows with a mismatched field count

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T8 CELÝ, hlavně design decisions 15, 16.
KONTEXT: ruční test BIM-5 odhalil bug — CSV s neuvozenou čárkou v text poli se tiše
rozparsovalo do špatných sloupců místo aby appka nahlásila chybu.

1. app/services/prompt_import.py — nová _detect_dialect(text_content: str) -> csv.Dialect:
   csv.Sniffer().sniff(sample, delimiters=",;\t") nad prvními ~4096 znaky; csv.Error
   (nejednoznačný vzorek) → fallback na csv.excel.
2. parse_csv — přejít z csv.DictReader na csv.reader(io.StringIO(text_content),
   dialect=dialect) + ruční field_map = {header[i].strip().lower(): i}.
3. Za každý neblank řádek porovnej len(raw_row) == len(header). Neshoda →
   ParsedPromptRow(row_number=..., text="", status="error", error_message=f"Row has
   {len(raw_row)} field(s) but the header has {len(header)} — check for an unquoted
   comma/semicolon inside a text value."), continue (nevolat cell() helper na nekonzistentní
   řádek).
4. validate_and_check_duplicates — na začátku smyčky přidat "if row.status == 'error':
   continue", jinak větev "if not row.text" přepíše konkrétní hlášku obecnou "Prompt text is
   required."
5. tests/test_prompt_import.py (pokud ještě neexistuje, založ) — regresní testy: CSV se
   středníkem se naparsuje správně; CSV s neuvozenou čárkou (víc polí než hlavička) → error
   s hláškou o počtu polí.

Po dokončení:
1. docker compose up -d --build
2. Ruční ověření: CSV se středníkem jako oddělovačem → naparsuje se správně; CSV s neuvozenou
   čárkou v text poli → status="error" s hláškou o počtu polí, ostatní řádky nedotčené
3. pytest — všechny testy zelené (staré i nové)
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
fix(prompts): detect CSV delimiter and flag rows with a mismatched field count
```

### DONE — code in commit 3de2571; tests/test_prompt_import.py (step 5) written 2026-09-14, not yet committed

---
---

## PROMPT BIM-9 — Stažitelná import šablona (CSV + XLSX + JSON)

```
Task: Prompt BIM-9 — add a downloadable CSV/XLSX/JSON template for bulk import

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T9 CELÝ, hlavně design decision 17.
Prerekvizita: BIM-5 hotový.

1. app/routers/prompt_sets.py — konstanty _TEMPLATE_HEADER = ["text", "market_code", "topic",
   "is_active"], _TEMPLATE_EXAMPLE_ROW (CSV/XLSX, string hodnoty) a _TEMPLATE_EXAMPLE_JSON_ROW
   (stejný obsah se skutečnými JSON typy — is_active: True, ne string "true").
2. GET /prompt-sets/{prompt_set_id}/prompts/import/template (format: Literal["csv", "xlsx",
   "json"] = Query("csv"), dependencies=_editor_or_admin) — csv.writer/openpyxl.Workbook/
   json.dumps postaví hlavičku + ukázkový řádek do paměti, vrátí jako Response s
   Content-Disposition: attachment. Žádná perzistence.
3. app/templates/prompt_sets/import.html — reuse export_button_group('/prompt-sets/' ~ id ~
   '/prompts/import/template', t('prompt.export_csv'), t('prompt.export_xlsx'),
   t('prompt.export_json')) vedle file inputu — stejný vzor jako export runů, žádné nové i18n
   klíče pro popisky tlačítek (jen jeden nový úvodní label prompt_import.download_template_label).
4. tests/test_prompt_import.py — round-trip test: stažená šablona (všechny tři formáty) se dá
   zpátky naparsovat přes parse_csv/parse_xlsx/parse_json beze změny.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči: stáhnout CSV, XLSX i JSON šablonu, nahrát je zpátky appce → naparsuje se bez
   chyby, ukázkový řádek se objeví v preview jako "New"
3. pytest — všechny testy zelené (staré i nové)
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(prompts): add a downloadable CSV/XLSX/JSON template for bulk import
```

### DONE — code in commit 3de2571; tests/test_prompt_import.py (step 4) written 2026-09-14, not yet committed

---
---

## PROMPT BIM-6 — Bulk import: confirm/commit route + i18n

```
Task: Prompt BIM-6 — commit bulk-import preview selections as new prompts

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T6 CELÝ, hlavně design decision 12.
Prerekvizita: BIM-5 hotový (pole formuláře musí odpovídat).

1. app/routers/prompt_sets.py, POST /prompt-sets/{prompt_set_id}/prompts/import/confirm —
   parsuj indexovaná pole (rows-{i}-*) z request.form() (proměnný počet řádků, ne pevný
   seznam Form(...) parametrů). ZNOVU ověř každé market_id (nikdy neslepě důvěřuj resubmitu).
   Vytvoř Prompt řádek pro každý include=true řádek (stejná konstrukce jako create_prompt,
   vždy verze 1). Po commitu redirect na /prompt-sets/{prompt_set_id} se souhrnem počtu
   naimportovaných promptů.
2. i18n (EN+DE, jeden commit) — prompt_import.* klíče: title, file_label,
   default_market_label, preview_summary, status_new/duplicate/error, include_label,
   confirm_button, a chyby import_file_too_large, import_too_many_rows, import_parse_failed,
   import_no_rows_found.
3. app/templates/prompt_sets/detail.html — odkaz "Hromadný import" vedle stávajícího
   formuláře "Přidat prompt".

Po dokončení:
1. docker compose up -d --build
2. Celý flow end-to-end: upload → preview → odškrtnout jeden duplicitní řádek → potvrdit →
   prompty se objeví v prompt setu se správným trhem/topicem/is_active, přeskočený duplicitní
   řádek chybí
3. Ověřit na ~640px/~1024px/desktop šířce
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(prompts): commit bulk-import preview selections as new prompts
```

### DONE — commit 7ed633a

---
---

## PROMPT BIM-7 — Testy: bulk import

```
Task: Prompt BIM-7 — cover bulk prompt import parsing, validation, and confirm flow

Přečti docs/TASKS_BULK_IMPORT_MULTI_MODEL.md úkol BIM-T7 CELÝ.
Prerekvizita: BIM-4, BIM-5, BIM-6 hotové.

1. Nový tests/test_prompt_import.py:
   - parsing: CSV/XLSX/JSON se stejným obsahem dají stejná ParsedPromptRow data (round-trip);
     CSV s BOM/cp1252 se naparsuje bez pádu
   - validace: neznámý market_code → error; prázdný text → error; chybějící market_code →
     doplní se default_market_id
   - duplicity: stejný (normalizovaný) text jako existující current-version prompt ve stejném
     marketu → duplicate; jiný market, stejný text → new; dva stejné řádky v jednom souboru →
     druhý duplicate
   - integrace: upload → preview → confirm end-to-end přes test klienta — jen include=true
     řádky se uloží, error řádky se neuloží ani při vynuceném include=true na klientu
     (server-side re-validace)

Po dokončení:
1. pytest — všechny testy zelené (staré i nové)
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover bulk prompt import parsing, validation, and confirm flow
```

### DONE — commit 4f6540b

---
---

## Po BIM-1..9: code-review remediation (mimo tento prompt-per-task workflow)

Po dokončení všech devíti promptů proběhla na branch dvě nezávislá kola `/code-review`
(2026-09-14 a 2026-09-15) přímo v konverzaci, ne přes samostatný BIM-N prompt — nálezy a opravy
jsou zdokumentované v `docs/TASKS_BULK_IMPORT_MULTI_MODEL.md`, sekce "Code-review remediation",
včetně commit hashů. BIM-8/BIM-9 nemají vlastní PROMPT sekce v tomhle souboru (byly odbavené
reaktivně po ručním testu BIM-5, ne přes připravený prompt) — jejich zdůvodnění a "Po dokončení"
kroky jsou přímo v `docs/TASKS_BULK_IMPORT_MULTI_MODEL.md`.

### DONE — written 2026-09-14, not yet committed
