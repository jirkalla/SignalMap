# SignalMap — Tasks: Runs Export (CSV / XLSX / JSON)

## v1.0 | Září 2026
## Branch: feature/signalmap-runs-export
## Task ID prefix: EX

> Fáze 1 (`docs/TASKS.md`) a fáze 2 (`docs/TASKS_PHASE2.md`) jsou hotové a smergnuté. Tenhle
> dokument pokrývá export runů do CSV/XLSX/JSON na třech úrovních (jeden run, všechny runy
> promptu, všechny runy klienta) — rozšíření Task 6 (run list & detail), ne jedna z pěti
> roadmap fází ze skillu `signalmap-conventions`. Design byl probraný a odsouhlasený s
> uživatelem před psaním tohohle dokumentu — plný vizuální návrh:
> [Export Design artifact](https://claude.ai/code/artifact/c6e05821-fbb5-4ad7-8440-613db84ba022).
>
> `docs/TASKS.md` má souhrnný záznam design rozhodnutí ("Runs export" sekce) — tenhle soubor je
> jeho rozpracování do konkrétních implementačních kroků.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Tři scope, tři endpointy, jeden sdílený service.** `GET /runs/{run_id}/export`,
   `GET /prompts/{prompt_id}/runs/export`, `GET /clients/{client_id}/runs/export` — všechny tři
   v `app/routers/runs.py` (export je runs-doména bez ohledu na to, ze které stránky se spouští).
   Router zůstává tenký (parsuje query params, zavolá service, vrátí Response) — veškerá logika
   (query, serializace) žije v novém `app/services/export.py`, aby se nic neduplikovalo mezi
   třemi endpointy.
2. **Formáty** (`?format=csv|xlsx|json`, default `json`): `csv` = ZIP s `runs.csv` +
   `citations.csv`, propojené přes `run_id`, stejný tvar na všech třech scope. `xlsx` = jeden
   workbook, listy `Runs` + `Citations` (openpyxl — nová pinnutá závislost, ověř aktuální
   stabilní verzi k datu implementace, stejný přístup jako u `anthropic` SDK v P2-T4). `json` =
   vnořené pole, plná věrnost.
3. **Content tiers** (`?content=answer|raw|full`, default `answer`): `answer` = rendered_text +
   citace + metadata runu + token_usage (jediná úroveň, kterou CSV/XLSX vykreslují jako
   skutečné tabulky). `raw` = nedotčený `raw_payload` bez rendered_text/citací. `full` = obojí.
   Žádný nový per-provider normalizer pro "vyčištění" raw_payload — Gemini `usage_metadata` a
   Anthropic `usage` nesdílí názvy polí, sjednocení by byl vlastní feature, ne detail exportu.
4. **Jak `raw`/`full` vypadá podle formátu — liší se, protože XLSX není ZIP jako CSV export:**
   - **JSON** — `raw_payload` je prostě klíč v objektu runu, žádné omezení délky.
   - **CSV** — `raw_<run_id>.json` jako samostatný soubor v ZIPu, vedle `runs.csv`/
     `citations.csv`. Bezpečné, protože export už tak jako tak ZIP je.
   - **XLSX — korekce oproti původnímu zápisu v `docs/TASKS.md`:** žádné volné soubory uvnitř
     `.xlsx` (je to sice taky ZIP kontejner, ale cizí soubory mimo OOXML manifest → Excel při
     otevření nahlásí poškození/opravu balíčku). Místo toho třetí list `RawPayload` — sloupce
     `run_id`, `raw_payload_json` (JSON text). Excel má limit ~32 767 znaků na buňku — text nad
     limit se ořízne na `EXCEL_CELL_CHAR_LIMIT - len(suffix)` znaků a připojí se
     `"... [truncated — use format=json for full payload]"`, žádná výjimka za běhu.
5. **Verzování promptu.** `GET /prompts/{prompt_id}/runs/export` defaultně exportuje jen tu
   verzi promptu, co je v URL (`Run.prompt_id == prompt_id`) — stejný scope, jaký
   `prompt_detail` už dnes zobrazuje (`app/routers/prompts.py:73-75`, "export toho, co vidíš").
   `?versions=all` přepne na celou lineage přes `root_prompt_id`/`is_current_version`, stejnou
   logiku jako `_version_history()` (`app/routers/prompts.py:53`). Každý řádek exportu nese
   `prompt_version` a `is_current_version`, aby smíchané verze zůstaly jednoznačné. Export
   klienta žádný přepínač nepotřebuje — přirozeně prochází všechny prompt sety/prompty/verze.
6. **Název souboru:** `signalmap_{scope}_{identifier}[_{content}]_{YYYYMMDD}.{ext}` — `scope` =
   `run`/`prompt`/`client`, `identifier` = run id / prompt id / **client slug** (ne číselné id),
   `content` suffix jen když není default `answer` (`_raw`, `_full`), datum = UTC datum
   vygenerování. Přípona vždy odpovídá skutečnému souboru na disku (`.zip` pro CSV bundle,
   nikdy `.csv` — stahuje se zip, ne holé CSV).
7. **Hlavičky sloupců a názvy souborů: pevně anglicky.** Datový formát pro další zpracování
   (Excel, skripty), ne UI text — nepodléhá §3 LANGUAGE pravidlu pro UI, žádné i18n klíče.
8. **Bez stránkování/limitu na "all runs" scope.** Interní nástroj, dnešní objemy dat jsou malé
   — řešit, až to bude reálný problém, ne předem.
9. **Eager loading, ne N+1.** `runs_for_*` helpery v `app/services/export.py` použijí
   `selectinload(Run.raw_response).selectinload(RawResponse.citations)` plus
   `selectinload(Run.prompt)`, `selectinload(Run.model).selectinload(AIModel.provider)`,
   `selectinload(Run.market)` — export stovek runů nesmí znamenat stovky extra dotazů.
10. **Export odkazy jsou obyčejné `<a href>`, ne HTMX.** Stažení souboru potřebuje skutečnou
    navigaci prohlížeče; `hx-get` by response s `Content-Disposition: attachment` jen ticho
    swapnul do DOM, ne stáhnul. Tři tlačítka (CSV/XLSX/JSON) jako jedna skupina, stejný vzor na
    všech třech stránkách — viz mockupy v Export Design artifactu.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| EX-T1 | Export service foundation (`app/services/export.py`) + openpyxl dependency | ✅ |
| EX-T2 | Run-scope export endpoint + UI tlačítka na detailu runu | ✅ |
| EX-T3 | Prompt-scope export endpoint (+ `?versions=all`) + UI tlačítka na detailu promptu | ✅ |
| EX-T4 | Client-scope export endpoint + UI tlačítka na detailu klienta | ✅ |
| EX-T5 | Testy: pytest sada pro export (scope × format × content) | ✅ |
| EX-6 | Code review pass (security/stability/DRY) + fixes — viz sekce níže | ✅ |

Pořadí je vynucené: EX-T2/T3/T4 volají service z EX-T1, který musí existovat a fungovat první
(design decision 1). EX-T2/T3/T4 jsou navzájem nezávislé, ale drží se v pořadí run → prompt →
client, protože to je rostoucí složitost dotazu (přímý `run_id` lookup → jeden prompt/verze →
join přes celý client). EX-T5 testuje všechno předchozí najednou, takže jde poslední.

---

## EX-T1 — Export service foundation

**Target:** nový `app/services/export.py`, `requirements.txt`

1. `requirements.txt` — přidej pinnutou aktuální stabilní verzi `openpyxl` (ověř k datu
   implementace, stejný přístup jako u `anthropic` SDK v P2-T4).
2. `app/services/export.py`:
   - `runs_for_run(db, run_id) -> list[Run]` — jeden run nebo prázdný seznam (404 řeší router).
   - `runs_for_prompt(db, prompt_id, *, all_versions=False) -> list[Run]` — `all_versions=False`
     (default) filtruje `Run.prompt_id == prompt_id`; `all_versions=True` nejdřív najde lineage
     `root_id`/`root_prompt_id` (stejná logika jako `_version_history` v
     `app/routers/prompts.py:53-60`, sem přenesená/znovupoužitá, ne duplikovaná), pak
     `Run.prompt_id.in_(lineage_ids)`.
   - `runs_for_client(db, client_id) -> list[Run]` — join `Run.prompt` → `Prompt.prompt_set` →
     `PromptSet.client_id == client_id`.
   - Všechny tři s `selectinload` řetězcem z design decision 9 — žádný lazy-load v serializéru.
   - `ExportContent` literál/enum: `"answer" | "raw" | "full"`.
   - `build_filename(scope: str, identifier: str, content: str, ext: str) -> str` — implementuje
     schéma z design decision 6.
   - `build_csv_zip(runs: list[Run], content: ExportContent) -> bytes` — `io.BytesIO` +
     `zipfile.ZipFile`, `csv.DictWriter` pro `runs.csv` (id, prompt_id, prompt_version,
     is_current_version, client_name, prompt_set_name, prompt_text, market_code, provider_code,
     model_name, status, trigger_type, started_at, finished_at, latency_ms, error_message,
     rendered_text, has_citations, token_usage jako JSON text) a `citations.csv` (run_id,
     source_url, source_title, source_domain, citation_position, cited_answer_span) — prázdný s
     jen hlavičkou, když run(y) nemají citace. `content in ("raw", "full")` přidá
     `raw_<run_id>.json` (pretty-printed `raw_payload`) do ZIPu per run s `RawResponse`.
   - `build_xlsx(runs: list[Run], content: ExportContent) -> bytes` — `openpyxl.Workbook()`,
     list `Runs` (stejné sloupce jako `runs.csv`), list `Citations` (stejné sloupce jako
     `citations.csv`), a při `content in ("raw", "full")` list `RawPayload` (`run_id`,
     `raw_payload_json`, s truncate pravidlem z design decision 4). `EXCEL_CELL_CHAR_LIMIT = 32767`
     jako pojmenovaná konstanta, ne magic number.
   - `build_json(runs: list[Run], content: ExportContent) -> bytes` — vnořené pole objektů;
     `raw_payload` klíč přítomný jen při `content in ("raw", "full")`; `rendered_text`/citace
     přítomné jen při `content in ("answer", "full")`.
   - Docstring na každé veřejné funkci (jaký tvar vrací, kdy je seznam prázdný).

Po dokončení:
1. Rychlé ruční ověření BEZ nové routy (Python shell v kontejneru, `docker compose exec app
   python`): zavolej `runs_for_run`/`build_csv_zip`/`build_xlsx`/`build_json` na existujícím
   run ID z DB (z fáze 1/2 testování), zkontroluj že ZIP/XLSX/JSON bytes vzniknou bez chyby a
   mají očekávaný obsah (rozbal ZIP, otevři XLSX, `json.loads` na JSON výstup).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(export): add export service — run/prompt/client queries, csv/xlsx/json writers
```

---

## EX-T2 — Run-scope export endpoint + UI

**Target:** `app/routers/runs.py`, `app/templates/runs/detail.html`

Prerekvizita: EX-T1 hotový.

1. `app/routers/runs.py` — `GET /runs/{run_id}/export`: parsuje `format` (`csv`/`xlsx`/`json`,
   default `json`) a `content` (`answer`/`raw`/`full`, default `answer`) query params (Pydantic/
   FastAPI `Query(...)` s `description=...`), 404 přes existující `AppError` vzor když run
   neexistuje, zavolá `runs_for_run` + odpovídající `build_*`, vrátí `Response` se správným
   `media_type` (`application/zip` / `application/vnd.openxmlformats-officedocument.
   spreadsheetml.sheet` / `application/json`) a `Content-Disposition: attachment; filename="..."`
   (`build_filename("run", str(run_id), content, ext)`). Docstring na route funkci.
2. `app/templates/runs/detail.html` — skupina tří `<a href="/runs/{{ run.id }}/export?format=
   csv">`/`xlsx`/`json` odkazů (styl podle mockupu v Export Design artifactu — `export-group`
   vzhled, ne HTMX). Umísti vedle existujícího status badge/metadat na vrcholu detailu.
3. i18n — `run.export_csv`, `run.export_xlsx`, `run.export_json` label klíče (EN+DE, jeden
   commit) — jde o UI text tlačítka, ne o obsah souboru (design decision 7 platí jen pro obsah
   exportu samotného).

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči na existujícím run detailu: klik na CSV → stáhne se `.zip`, obsahuje
   `runs.csv` + `citations.csv` se správnými daty tohoto runu. XLSX → otevře se bez "oprava
   souboru" dialogu, listy `Runs`/`Citations` sedí. JSON → validní JSON, jeden objekt v poli.
3. Zkus `?content=raw` a `?content=full` ručně přes URL — raw JSON přítomný/nepřítomný podle
   tiéru, XLSX `RawPayload` list se objeví jen u raw/full.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): add single-run export (csv/xlsx/json) with content-tier selection
```

---

## EX-T3 — Prompt-scope export endpoint + UI

**Target:** `app/routers/runs.py` (nebo `app/routers/prompts.py` — viz krok 1),
`app/templates/prompts/detail.html`

Prerekvizita: EX-T1, EX-T2 hotové.

1. `GET /prompts/{prompt_id}/runs/export` — stejné `format`/`content` params jako EX-T2, plus
   `versions` (`current`/`all`, default `current`). Žije v `app/routers/runs.py` vedle EX-T2
   (design decision 1 — export je runs-doména), importuje `_get_prompt_or_404` z
   `app/routers/prompts.py` pro 404 (stejný vzor jako dnešní `trigger_run`). Volá
   `runs_for_prompt(db, prompt_id, all_versions=(versions == "all"))`. Prázdný seznam runů →
   pořád validní (prázdné) CSV/XLSX/JSON, ne chyba — prompt bez běhů je legitimní stav.
2. `app/templates/prompts/detail.html` — stejná tříčlenná skupina odkazů jako EX-T2, plus
   checkbox/toggle "include all versions" (`?versions=all`) vedle nich — jen pokud
   `_version_history(prompt)` vrátí víc než jednu verzi (žádný zbytečný ovládací prvek u
   promptu bez historie editací).
3. i18n — `prompt.export_csv/xlsx/json`, `prompt.export_all_versions_label` (EN+DE).

Po dokončení:
1. `docker compose up -d --build`
2. Na promptu s víc runy: export bez `versions=all` obsahuje jen runy aktuální verze; s
   `versions=all` (na promptu, který má editační historii z fáze 1 versioning testu) obsahuje
   runy napříč verzemi, každý řádek má správný `prompt_version`/`is_current_version`.
3. Export promptu bez jediného runu → validní prázdný soubor, ne 500/chyba.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(prompts): add per-prompt runs export with optional full-version-history scope
```

---

## EX-T4 — Client-scope export endpoint + UI

**Target:** `app/routers/runs.py`, `app/templates/clients/detail.html`

Prerekvizita: EX-T1, EX-T2 hotové (EX-T3 nezávislé, ale logicky navazuje).

1. `GET /clients/{client_id}/runs/export` — stejné `format`/`content` params, žádný `versions`
   param (design decision 5 — client scope zahrnuje všechno automaticky). 404 přes existující
   client lookup vzor (`app/routers/clients.py`). Volá `runs_for_client`.
2. `app/templates/clients/detail.html` — stejná tříčlenná skupina odkazů, u nadpisu detailu
   klienta (mockup v Export Design artifactu — "widest scope" panel).
3. i18n — `client.export_csv/xlsx/json` (EN+DE).

Po dokončení:
1. `docker compose up -d --build`
2. Na klientovi s runy napříč víc prompt sety/prompty (i napříč providery, pokud fáze 2 data
   existují): export obsahuje runy ze všech, ne jen z jednoho prompt setu.
3. Export klienta bez jediného runu → validní prázdný soubor.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(clients): add whole-client runs export across all prompt sets
```

---

## EX-T5 — Testy: pytest sada pro export

**Target:** nový `tests/test_export.py`

Prerekvizita: EX-T1 až EX-T4 hotové.

1. `tests/test_export.py`, na existující `conftest.py` fixture (seed data + `FakeAdapter` runy,
   stejný vzor jako `tests/test_runs.py`):
   - Run-scope: `format` × `content` matice (9 kombinací) vrací 200 se správným
     `Content-Type`/`Content-Disposition`; CSV rozbalitelný ZIP; XLSX otevřitelný `openpyxl.
     load_workbook`; JSON `json.loads`-itelný. 404 na neexistující `run_id`.
   - Prompt-scope: `versions=current` vs `versions=all` vrací různý počet řádků na promptu se
     dvěma verzemi (fixture musí založit edit → novou verzi, jako existující verzovací test).
     Prázdný prompt (bez runů) → validní prázdný export, ne 500.
   - Client-scope: runy napříč dvěma prompt sety obě v exportu; prázdný klient → validní prázdný
     export.
   - `raw`/`full` content: `RawPayload` list přítomný v XLSX jen u těchto tierů; truncate
     chování ověřené syntetickým run s uměle velkým `raw_payload` (>32767 znaků po serializaci)
     — ověř, že řádek obsahuje truncate suffix a test neprocházel jen náhodou pod limitem.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover runs export across scope/format/content-tier matrix
```

---

## EX-6 — Code review pass (security / stability / DRY)

Ran after EX-T1–EX-T5 were done and committed, on user request — high-effort multi-angle review
(5 parallel finder passes: line-by-line, removed-behavior audit, cross-file tracer,
reuse/simplification/efficiency, altitude/conventions) against the full branch diff
(`master...HEAD`). 7 findings survived verification; 6 fixed on this branch, 1 confirmed as an
already-accepted tradeoff (no change).

**Fixed:**
1. **CSV formula injection** (security) — `rendered_text`/`prompt_text`/`error_message` can
   contain AI-provider output (which can itself echo scraped web content) starting with `=`,
   `+`, `-`, or `@`; Excel/LibreOffice reads that as a live formula on open. Fixed with
   `_csv_safe`/`_sanitize_csv_row` in `app/services/export.py` — a leading quote neutralizes it,
   applied only to CSV (XLSX/JSON aren't affected the same way). Regression test:
   `tests/test_export.py::test_run_export_csv_neutralizes_formula_injection`.
2. **XLSX crash on control characters** (correctness/stability) — any ASCII control character in
   provider text made `build_xlsx` raise `openpyxl.IllegalCharacterError`, surfacing as an
   unhandled 500 with no graceful degradation. Fixed with `_xlsx_safe` (strips via openpyxl's own
   `ILLEGAL_CHARACTERS_RE`), applied before every cell write. Regression test:
   `test_run_export_xlsx_strips_control_characters_instead_of_crashing`.
3. **Delete-lineage fix duplicated + untested + O(n) round-trips** — the `db.flush()`-per-row
   workaround for the missing `ondelete`/`relationship()` on `Prompt.root_prompt_id` (see the
   earlier bugfix, commit `128ee1d`) was hand-copied into both `app/routers/prompts.py` and
   `app/routers/prompt_sets.py`, had zero test coverage, and flushed once per row where one flush
   after all children (not per-row) is provably enough — verified empirically before landing.
   Consolidated into a shared `_delete_prompt_lineage(db, versions)` in `app/routers/prompts.py`,
   imported by `prompt_sets.py`; new `tests/test_prompts.py` covers both delete endpoints against
   a run-less multi-version lineage.
4. **Three export routes duplicated ~75-90 lines of Query()/Response boilerplate** — collapsed
   into a shared `_export_response(runs, scope, identifier, format, content)` helper plus two
   module-level `Query(...)` instances (`_EXPORT_FORMAT_QUERY`/`_EXPORT_CONTENT_QUERY`) reused
   across `export_run`/`export_prompt_runs`/`export_client_runs` in `app/routers/runs.py`.
5. **Export-button markup copy-pasted across 3 templates** — extracted an
   `export_button_group(base_url, label_csv, label_xlsx, label_json, id_prefix=none)` macro into
   `app/templates/partials/macros.html` (the project's established pattern for this, per
   `button`/`delete_button`/`toggle_button`), used by `runs/detail.html`, `prompts/detail.html`,
   `clients/detail.html`. `id_prefix` keeps the prompt page's "include all versions" JS toggle
   working unchanged.
6. **`_run_row` (CSV/XLSX) and `build_json` re-derived the same ~16-field run→dict mapping
   independently** — unified into a shared `_run_base_fields(run)` in `app/services/export.py`,
   called by both.

**Confirmed but not changed (already an accepted tradeoff):**
7. **No pagination/row limit on `runs_for_client`** — matches design decision 8 (`docs/TASKS_
   EXPORT.md` above): internal tool, current data volumes are small, revisit if it becomes a
   real problem. Flagged again here because it bears directly on the "stability" review request,
   not because it's new information.

Verification after every fix: full `pytest` suite green (41/41, up from 37 — 2 security
regression tests + 2 delete-fix regression tests), app imports cleanly
(`docker compose exec app python -c "from app.main import app"`), and a browser check on
`/prompts/2` confirmed the macro refactor didn't change the rendered page or break the
versions-toggle JS.

**In-app docs:** `/help` had no mention of the export feature at all — added an "Exporting
runs"/"Läufe exportieren" section (EN+DE) between the run-results and interface-language
sections. A `/findings` entry on the two security fixes was drafted and then removed at the
user's explicit instruction — `/findings` entries are the user's editorial call, not something
to add unprompted.

---

## Completion Checklist

- [x] `app/services/export.py` — query helpery + csv/xlsx/json writery, žádná N+1 dotazů
- [x] `openpyxl` pinnutá verze v `requirements.txt`
- [x] `/runs/{id}/export` funguje pro všechny 3 formáty × 3 content tiery
- [x] `/prompts/{id}/runs/export` respektuje `versions=current|all`, prázdný prompt exportuje
      validně
- [x] `/clients/{id}/runs/export` prochází všechny prompt sety klienta, prázdný klient exportuje
      validně
- [x] XLSX truncate chování u velkých `raw_payload` funguje bez výjimky
- [x] `pytest` sada zelená, pokrývá scope × format × content matici (41/41 po EX-6)
- [x] Ověřeno v prohlížeči na ~640px/~1024px/desktop na všech třech stránkách
- [x] Code review (EX-6): CSV/XLSX bezpečnostní opravy, DRY refaktoring, regresní testy pro
      delete fix — viz sekce výše
- [x] `docs/TASKS.md` — "Runs export" sekce přepnuta z "planned, not started" na hotovo,
      s odkazem na [PR #3](https://github.com/jirkalla/SignalMap/pull/3) (smergnuto 2026-09-10)
