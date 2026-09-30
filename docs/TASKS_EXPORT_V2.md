# SignalMap — Tasks: Export v2 + History filtry (vydání 4)

## v1.0 | Září 2026
## Branch: feature/signalmap-export-v2
## Task ID prefix: EX2
## Cílová verze: v1.6.0 (MINOR) — společně s `feature/signalmap-metric-definitions`

Status: navrženo 2026-09-30 jako **vydání 4** z plánu vydání
(`docs/ROADMAP.md` „Plán vydání"). Pokrývá `docs/ROADMAP.md` #25 a z #21
bod „History: filtry + Retry all errors podle filtru". Osm úkolů, bez
migrace.

**Goal:** export za klienta i prompt set jde omezit rozsahem dat a stavem,
volitelně s raw odpovědí, a obsahuje personu, jazyk/zemi marketu, odhad
ceny, výsledky ověření citací a souhrnný list. History na `/schedules`
jde filtrovat podle klienta, modelu, důvodu a data a hromadný retry
respektuje filtr.

**Proč až po vydání 1 a 3:** export má obsahovat verdikty ověření
(spolehlivé až po vydání 1) a názvy jazyka/země marketu (vydání 3).

**Pořadí v rámci vydání 4:** `feature/signalmap-metric-definitions`
(`docs/TASKS_METRIC_DEFINITIONS.md`, #26) se smerguje první bez nasazení;
tahle větev z ní vychází (list „Definitions" v T4 bere texty z jejího
katalogu) a EX2-T8 nasadí obě jako v1.6.0.

Navazuje na `docs/TASKS_EXPORT.md` (design decisions 1–10 platí, pokud
níže není výslovně jinak).

---

## Výchozí stav (kód k 2026-09-30, master 7807f7a)

**Export** — `app/services/export.py`:
- `ExportContent = Literal["answer","raw","full"]`; CSV/XLSX obsahují
  odpověď vždy, `content` jen přidá raw (`raw` = `full`); JSON `raw` =
  metadata + `raw_payload` bez odpovědi.
- `RUN_COLUMNS` (19 sloupců) — **bez persony a ceny**; `CITATION_COLUMNS`
  (11) — **bez ověření**; `SEARCH_QUERY_COLUMNS` (3).
- `_RUN_EAGER_LOAD` nenačítá `Run.persona`.
- `runs_for_run` / `runs_for_prompt(all_versions)` / `runs_for_client` —
  **žádný filtr** data/stavu, **žádný `runs_for_prompt_set`**, bez limitu
  (`docs/TASKS_EXPORT.md` design decision 8).
- Buildery: `build_csv_zip` (ZIP: `runs.csv`, `citations.csv`,
  `search_queries.csv`, `raw_<id>.json`), `build_xlsx` (listy `Runs`,
  `Citations`, `SearchQueries`, `RawPayload` s ořezem na 32 767 znaků),
  `build_json` (pole run objektů).
- `build_filename(scope, identifier, content, ext)`.

**Routy** — `app/routers/runs.py:495-561`: `/runs/{id}/export`,
`/prompts/{id}/runs/export?versions=`, `/clients/{id}/runs/export`;
`_export_response` (:103). UI: makro `export_button_group` (tři `<a>`
odkazy, `macros.html:257`) na `runs/detail.html:12`,
`clients/detail.html:12`, `prompts/detail.html:292` (+ JS přepis odkazů
podle checkboxu verzí, :296). **`prompt_sets/detail.html` export nemá.**

**Ověření** — `CitationVerification` je append-only, „aktuální" = nejnovější
`created_at` per `citation_id`; helper `latest_verification_query()`
(`verification_display.py:97`, `DISTINCT ON`). Lidská hodnocení
`VerificationLabel` (append-only, `HUMAN_VERDICTS`). Zdroj přes
`CitationVerification.source_document_id` → `SourceDocument`
(`final_url`, `method`, `http_status`, `fetched_at`, `text_sha256` →
`SourceText`). `VERDICT_BUCKETS` (`verification_display.py:80`).

**Cena** — `estimate_run_cost(token_usage, model, prices)`
(`cost.py:245`) + `load_price_components` / `prices_at`; hromadný vzor
v `ops_dashboard.py:646-661`.

**Datumy** — `date_ranges.range_bounds("7d"|"30d"|"90d"|"quarter"|"all")`;
parser `YYYY-MM-DD` → inkluzivní UTC rozsah je privátní
`clients.py::_parse_verify_date_range` (:123). Časy v DB jsou UTC,
převod na lokální čas je jen zobrazovací (`docs/TASKS_LOCAL_TIME.md`).

**Chyby v prohlížeči** — `AppError` se pro HTML požadavek vykreslí
přes `error.html` (`app/errors.py:68`), pro `/api/` a JSON jako JSON.

**History** — `/schedules?view=history`: filtry jen `status`
(all/errors/skipped) a `q`; `retry_all_errors(db, search, now)`
(`queue.py:457`) bere jen `q`; `history_batches` seskupuje podle
`batch_id`. (Přeložený `skip_reason` přidá vydání 2.)

---

## Design decisions

1. **Filtry jako jeden objekt pro všechny scopy.** `ExportFilters`
   (dataclass: `date_from`, `date_to` jako UTC datetimy, `statuses`)
   se aplikuje v query helperech jednotně — `runs_for_client`,
   nový `runs_for_prompt_set`, i `runs_for_prompt` (zadarmo). Filtr
   podle `Run.started_at`. Parser dat se přesune z `clients.py` do
   `app/services/date_ranges.py` (`parse_date_range`) a použije se
   na obou místech.
2. **Datum = UTC den**, stejně jako retro-verify (`_parse_verify_date_range`).
   Hraniční runy kolem půlnoci lokálního času se můžou posunout o den —
   přijatelné, popsat v nápovědě formuláře. Presety 7d/30d/90d/quarter/all
   z `range_bounds`.
3. **Výchozí stav = jen `success`.** Export je pro analýzu odpovědí;
   chybové runy jsou volba („všechny stavy"). Scopy run a prompt
   (dnešní odkazy) zůstávají beze změny chování — výchozí filtr jen pro
   nový formulář (client, prompt set), aby staré odkazy/záložky dávaly
   stejný výsledek jako dřív.
4. **`content` se nemění**, UI jen přemapuje: checkbox „Zahrnout raw
   odpověď" → `content=full`, jinak `answer`. Hodnoty API zůstávají
   (zpětná kompatibilita odkazů).
5. **Nové sloupce se přidávají na konec** (`RUN_COLUMNS`, `CITATION_COLUMNS`)
   — aditivní změna, existující konzumenti podle pozice se nerozbijí.
   Runs: `persona_label`, `market_language`, `market_country` (názvy
   z vydání 3, fallback ISO), `cost_usd` (odhad, prázdné = neznámá cena).
   Kdo run spustil se **nepřidává** — ruční vs. plánovaný rozliší
   existující `trigger_type`, jméno uživatele je osobní údaj bez potřeby.
6. **Ověření citace = nejnovější verdikt, sloupce v `Citations`.**
   `verdict`, `verdict_bucket` (`VERDICT_BUCKETS`), `check_type`,
   `reason`, `similarity`, `matched_text`, `page_number`, `llm_reason`,
   `verified_at`; zdroj: `source_final_url`, `source_method`,
   `source_http_status`, `source_fetched_at`; lidské hodnocení:
   `human_verdict_latest`, `human_labels_count`. Celá historie verdiktů
   mimo rozsah (append-only tabulka zůstává zdrojem pravdy v appce).
   Načtení jedním `DISTINCT ON` dotazem pro všechna `citation_id` dávky
   (žádné N+1).
7. **Plný text zdroje jen s raw:** ZIP → `source_<sha256>.txt` (každý
   text jednou), JSON → `source_text` u citace. XLSX **nikdy** (limit
   buňky, velikost).
8. **Souhrn jen v XLSX a ZIP, ne v JSON.** List `Summary` / `summary.csv`:
   hlavička (klient, prompt set, rozsah dat, filtry, `generated_at`,
   verze appky, **Vision klienta** z vydání 1) + tabulka po modelech
   (runy, success, error, citace, rozpad `VERDICT_BUCKETS`, průměrná
   latence, součet ceny). Druhý list **`Definitions`** (ZIP
   `definitions.csv`): metriky použité v souhrnu a sloupce ověření —
   `name`, `what`, `how` z katalogu `app/metrics_catalog.py` (#26),
   v jazyce UI uživatele, který export stáhl. Texty v appce a v exportu
   tak nikdy nejsou v rozporu. JSON root zůstává **pole** — změna na objekt
   by rozbila konzumenty; JSON uživatel si souhrn spočítá.
9. **Strop velikosti:** `EXPORT_MAX_RUNS` (config, výchozí 5 000). Nad
   stropem `AppError("export_too_large", …, 409)` → v prohlížeči
   `error.html` s radou zúžit rozsah dat. Počet se ověří `COUNT(*)`
   **před** načtením runů. XLSX přes `openpyxl` `write_only=True`
   (paměť). Asynchronní export mimo rozsah.
10. **UI: formulář místo tří odkazů pro client a prompt set.** Nové makro
    `export_form(action_url, show_versions=false)` — GET formulář:
    preset rozsahu (select) + vlastní od–do (`<input type="date">`,
    zobrazí se při „vlastní"), stav (success / vše), checkbox raw, tři
    submit tlačítka `name="format"` (CSV/XLSX/JSON). Na detailu klienta
    a prompt setu sbalené pod tlačítkem „Export…" (`<details>`), ať
    nezabírá místo. Run a prompt detail dál `export_button_group`.
11. **Název souboru** dostane rozsah, když je filtr aktivní:
    `signalmap_client_knauf_2026-09-01_2026-09-30[_full]_20261015.xlsx`.
12. **History filtry:** přidat `client_id`, `model_id` (nebo provider),
    `skip_reason`, `date_from`/`date_to` (queued_at) k dnešním `status`
    a `q`. Filtry v URL (sdílitelné). `last_error` (zkrácený, celý
    v `title`) přímo v řádku. `retry_all_errors` přijme **stejný filtr
    objekt** jako výpis → „Retry all errors" retryuje přesně to, co je
    vidět (potvrzovací `data-confirm` s počtem položek).
13. **Bez migrace.** Verze MINOR.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Filtry exportu + `runs_for_prompt_set` + strop velikosti | ⏳ |
| T2 | Nové sloupce Runs (persona, jazyk/země, cena) | ⏳ |
| T3 | Ověření citací v exportu (+ text zdroje s raw) | ⏳ |
| T4 | Souhrnný list (XLSX + ZIP) | ⏳ |
| T5 | Routy a UI formulář exportu (klient, prompt set) | ⏳ |
| T6 | History filtry + retry podle filtru | ⏳ |
| T7 | Dokumentace + CHANGELOG | ⏳ |
| T8 | Nasazení v1.6.0 a ověření | ⏳ |

---

## T1 — Filtry exportu + `runs_for_prompt_set` + strop

**Target:** `app/services/export.py`, `app/services/date_ranges.py`,
`app/routers/clients.py` (přesun parseru), `app/config.py`, `tests/test_export.py`,
`tests/test_clients.py`

1. `parse_date_range` do `date_ranges.py` (přesun z `clients.py`,
   `AppError` zůstane v routeru — služba vrací `ValueError`), retro-verify
   přepojit, jeho testy beze změny.
2. `ExportFilters` + aplikace ve všech query helperech; nový
   `runs_for_prompt_set(db, prompt_set_id, filters)`.
3. `count_runs(...)` se stejnými filtry; `EXPORT_MAX_RUNS` v configu.
4. Testy: filtr dat (hranice dne inkluzivně), stav, prompt set scope
   (jen jeho prompty, všechny verze), count odpovídá výsledku, výchozí
   `ExportFilters()` = dnešní chování.

**Expected commit:** `feat(runs): filter exports by date range and status`

---

## T2 — Nové sloupce Runs

**Target:** `app/services/export.py`, `tests/test_export.py`

1. `_RUN_EAGER_LOAD` + `Run.persona`.
2. `persona_label`, `market_language`, `market_country`, `cost_usd` na
   konec `RUN_COLUMNS` (design decision 5); cena hromadně
   (`load_price_components` jednou pro dávku, `prices_at` per run).
3. JSON: stejná pole v objektu runu.
4. Testy: hodnoty ve všech třech formátech; free model → `0.0`,
   neznámá cena → prázdné; pořadí stávajících sloupců beze změny
   (test porovná prefix `RUN_COLUMNS` s dnešní n-ticí).

**Expected commit:** `feat(runs): add persona, market names and cost to exports`

---

## T3 — Ověření citací v exportu

**Target:** `app/services/export.py`, `app/services/verification_display.py`
(jen pokud je potřeba veřejný helper), `tests/test_export.py`

1. `_latest_verifications_by_citation(db, citation_ids)` —
   `latest_verification_query()` omezená na ID dávky, eager
   `source_document`; `_label_summary_by_citation` (nejnovější lidský
   verdikt + počet) jedním dotazem.
2. Sloupce z design decision 6 na konec `CITATION_COLUMNS`; JSON
   stejná pole u citace.
3. S raw: texty zdrojů (design decision 7) — ZIP soubory deduplikované
   podle sha256, JSON `source_text`.
4. Testy: citace bez verdiktu (prázdné), s více verdikty (nejnovější),
   s lidským hodnocením; ZIP obsahuje text jednou i pro dvě citace
   stejného zdroje; XLSX text neobsahuje; počet SQL dotazů nezávisí na
   počtu citací (vzor z `docs/TASKS_EXPORT.md` design decision 9).

**Expected commit:** `feat(runs): include citation verification results in exports`

---

## T4 — Souhrnný list

**Target:** `app/services/export.py`, `tests/test_export.py`

1. `build_summary(runs, filters, scope_info) -> SummaryData` (čistá
   funkce nad už načtenými daty — žádné další dotazy kromě ceny, která
   je z T2).
2. XLSX list `Summary` jako **první** list; ZIP `summary.csv`
   (dvě sekce: hlavička klíč/hodnota, pak tabulka po modelech — nebo dva
   soubory `summary.csv` + `summary_by_model.csv`, vybrat čitelnější
   a zdůvodnit).
3. Hlavička vč. Vision klienta (když existuje), verze appky
   (`app.__version__`), filtrů.
3a. List `Definitions` / `definitions.csv` z `METRICS` katalogu
   (design decision 8) — jen metriky, které export obsahuje; test, že
   texty jsou shodné s i18n.
4. `openpyxl` `write_only=True` pro celý XLSX builder (design decision 9)
   — ověřit, že ořez buněk a sanitizace fungují stejně.
5. Testy: čísla souhrnu sedí proti řádkům; prázdný export má souhrn
   s nulami; JSON souhrn nemá.

**Expected commit:** `feat(runs): add a summary sheet to client and prompt set exports`

---

## T5 — Routy a UI formulář exportu

**Target:** `app/routers/runs.py`, `app/templates/partials/macros.html`,
`app/templates/clients/detail.html`, `app/templates/prompt_sets/detail.html`,
`app/i18n/*.json`, `tests/test_runs.py` (nebo kde jsou testy export rout)

1. `GET /prompt-sets/{id}/runs/export` (editor/admin, 404 přes
   `_get_prompt_set_or_404` vzor), parametry `range`, `date_from`,
   `date_to`, `status`, `content`, `format` — `description=` u všech;
   totéž u `/clients/{id}/runs/export`. Docstringy pro `/docs`.
2. Strop (design decision 9) → `AppError("export_too_large")` s počtem
   a limitem v hlášce (DE/EN).
3. `build_filename` s rozsahem (design decision 11).
4. Makro `export_form` (design decision 10) + použití na detailu
   klienta a prompt setu.
5. Testy: prompt set route (200, obsah, 404 cizího ID), filtry z query,
   neplatné datum → 400, strop → 409, viewer → 403.
6. Prohlížeč ~375 / ~768 px / desktop: formulář sbalený/rozbalený,
   vlastní rozsah, stažení všech tří formátů; otevřít XLSX a ZIP
   a zkontrolovat listy/soubory (fixture klient Skoda Auto).

**Expected commit:** `feat(runs): add a filterable export form for clients and prompt sets`

---

## T6 — History filtry + retry podle filtru

**Target:** `app/services/schedule_monitor.py`, `app/services/queue.py`,
`app/routers/schedules.py`, `app/templates/schedules/index.html`,
`app/i18n/*.json`, `tests/test_schedule_monitor.py`, `tests/test_schedules.py`

1. `HistoryFilters` (design decision 12) — sdílený pro `history_batches`
   a `retry_all_errors`.
2. UI: filtry nad History (select klient, model, důvod; od–do; stav; `q`),
   v URL; `last_error` v řádku; „Retry all errors (N)" s `data-confirm`.
3. Testy: každý filtr zvlášť i kombinace; retry podle filtru retryuje
   jen odpovídající `error` položky; bez filtru chování jako dnes.
4. Prohlížeč na třech šířkách.

**Expected commit:** `feat(runs): filter schedule history and retry errors by filter`

---

## T7 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/TASKS_EXPORT.md` (poznámka: design
decision 8 „bez limitu" nahrazena stropem, odkaz sem), `docs/ROADMAP.md`
#25 a #21 (History), `app/templates/help.html` / i18n nápověda k exportu,
pokud existuje

1. `CHANGELOG.md` `[Unreleased]`:
   - Added: export za prompt set; rozsah dat, stav a raw volitelně ve
     formuláři; persona, jazyk/země marketu, odhad ceny, výsledky ověření
     citací a souhrnný list v exportu; filtry History a retry podle filtru.
   - Changed: export nad 5 000 runů je potřeba zúžit rozsahem dat.

**Expected commit:** `docs(docs): document export v2 and history filters`

---

## T8 — Nasazení v1.6.0 a ověření

**Target:** produkce; end-of-branch docs

1. Merge PR, bump **v1.6.0**, přesun `[Unreleased]`, tag — uživatel.
2. Nasazení podle `docs/DEPLOYMENT.md` 1–5; migrace žádné → rollback =
   návrat kódu.
3. Ověření: export Knaufu za posledních 7 dní ve všech třech formátech
   (bez raw i s raw) — čas odpovědi, velikost souboru, listy/soubory,
   verdikty odpovídají run detailu u 2–3 namátkových citací; export
   prompt setu; strop (dočasně `EXPORT_MAX_RUNS=10` jen lokálně, ne na
   produkci).
3a. Ověření metric definitions podle `docs/TASKS_METRIC_DEFINITIONS.md`
   „Ověření po nasazení" (tooltips na dashboardu/`/ops`, slovníček,
   list `Definitions` v XLSX).
4. History: filtr na klienta + chyby, „Retry all errors" jen po potvrzení
   uživatele (placené runy).
5. End-of-branch docs: `## Status: ...` v `*_EXPORT_V2.md`
   i `*_METRIC_DEFINITIONS.md`, dva řádky v `docs/00_INDEX.md`, `docs/ROADMAP.md` „Plán vydání" → vydání 4 ✅.

**Expected commit:** `docs(docs): record export v2 deploy and close the branch`

---

## Co tohle vydání vědomě nedělá

- **Asynchronní export / export na pozadí** — až bude strop reálně
  překážet.
- **Celá historie verdiktů** v exportu — jen nejnovější (design decision 6).
- **Souhrn v JSON** / změna rootu JSON (design decision 8).
- **Jméno uživatele, který run spustil** — osobní údaj bez potřeby.
- **Seskupení opakovaných pokusů v Runs, circuit breaker, kontrola před
  dávkou** (#21) — mimo plán vydání.
- **Plánovaný (automatický) export e-mailem** — appka nemá SMTP.
