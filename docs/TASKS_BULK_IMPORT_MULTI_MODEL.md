# SignalMap — Tasks: Bulk prompt import + Multi-model run

## Status: ✅ Done — PR #12, merged 2026-09-15, released in v1.0.0

## v1.0 | Září 2026
## Branch: feature/signalmap-bulk-import-multi-model
## Task ID prefix: BIM

> Fáze 1–6, hardening, export, ChatGPT/persona/pricing a local-time-display jsou hotové a
> smergnuté (viz `docs/TASKS.md`). Tenhle dokument bundluje dvě nezávislé funkce do jedné branch
> (odsouhlaseno s uživatelem 2026-09-14) — ani jedna nezávisí na druhé, obě jsou dost malé, že
> nestojí za oddělenou granularitu, jakou měly fáze 2–4:
>
> 1. **Multi-model run pro jeden prompt** (BIM-T1–T3) — checkboxy místo jednoho `<select>` u
>    modelu na run-trigger formuláři (`app/templates/prompts/detail.html`), spustí vybrané
>    modely paralelně. Reuse existující `POST /prompts/{id}/runs` route beze změny byznys
>    logiky uvnitř `trigger_run`.
> 2. **Hromadný import promptů** (BIM-T4–T7) — CSV/XLSX/JSON upload do existujícího prompt
>    setu, s preview krokem před uložením (kontrola duplicit, per-řádkový trh). Dnes se prompty
>    přidávají jen jednotlivě přes formulář (`create_prompt`, `app/routers/prompt_sets.py:153-179`).
> 3. **CSV robustnost + stažitelná šablona** (BIM-T8–T9, přidáno 2026-09-14 po ručním testu
>    BIM-T5) — ruční test odhalil, že ručně psané CSV s neuvozeným polem obsahujícím čárku
>    (`"Wie wird die Marke Skoda Auto..., insbesondere..., Zuverlässigkeit..."`) appka tiše
>    rozparsovala do špatných sloupců místo aby to nahlásila jako chybu — `market_code` skončil
>    s kouskem věty. Kriticky probráno v konverzaci, návrh (delimiter sniffing, kontrola počtu
>    polí na řádek, stažitelná šablona) odsouhlasen beze změn.
>
> Vědomě mimo scope: "spustit všechny prompty najednou" (`docs/ROADMAP.md`, "Study" koncept) —
> čeká na Scheduler infrastrukturu (fronta/worker běžící na pozadí), kterou appka dnes nemá
> (ověřeno: žádný `BackgroundTasks`/Celery/arq nikde v `app/`) a která by se jinak musela stavět
> dvakrát nezávisle — jednou tady, jednou u Scheduleru (`docs/ROADMAP.md` bod 5). Design proposal
> a kritické zhodnocení obou částí proběhlo v konverzaci 2026-09-14, včetně `AskUserQuestion` k
> rozsahu importu (hybrid: default trh v UI + volitelný `market_code` sloupec v souboru) a k
> execution enginu batch-runu (rozhodnuto: čeká na Scheduler, není součást týhle branch).

---

## ⚠️ Schema flagy (AI_INSTRUCTIONS.md §4)

**Tahle branch NEPŘIDÁVÁ žádnou novou tabulku ani sloupec.** Obě featury reuse existující
`Prompt`/`Run`/`AIModel` modely beze změny schématu:
- Bulk import zapisuje jen nové `Prompt` řádky přes existující sloupce (`text`, `market_id`,
  `topic`, `is_active`) — stejná cesta jako dnešní jednotlivý `create_prompt`, vždy verze 1.
- Multi-model run volá existující `POST /prompts/{id}/runs` N-krát, jednou na model — žádná
  nová route, žádný nový sloupec na `Run`.

Jedno volitelné rozšíření bylo v konverzaci zmíněné a **vědomě vynechané** z týhle branch:
`ai_models.is_default` (bool) pro předzaškrtnutí "rozumného" modelu v multi-select UI místo
prázdného výběru — stejný vzor jako existující `Persona.is_default`. Nebylo uživatelem
odsouhlaseno, takže v1 nic nepředzaškrtne. Pokud se bude chtít později, jde o samostatnou malou
migraci + úpravu `/ai-models` formuláře, nezávislou na zbytku týhle branch — nepřidávat mlčky.

---

## Design decisions (rozhodnuto před psaním kódu, konverzace 2026-09-14)

1. **Multi-model: žádná nová route.** UI odpaluje N paralelních requestů na existující
   `POST /prompts/{id}/runs`, jeden `model_id` na request — `trigger_run` samo o sobě se
   byznysově nemění, jen se volá vícekrát z klienta.
2. **Multi-model: duplicate-run guard rozšířený z `prompt_id` na `(prompt_id, model_id)`.**
   Dnešní kontrola (`runs.py:246-250`) blokuje druhý běh na *jakémkoliv* modelu, dokud první
   neskončí — s paralelními požadavky na různé modely by se navzájem falešně blokovaly. Pořád
   musí blokovat dvojklik na *stejný* model.
3. **Multi-model: `market_id`/`persona_id` zůstávají single-select, platí pro všechny zaškrtnuté
   modely v jednom triggeru.** Žádný důvod měnit trh/personu per model v jednom kliknutí — jiná
   kombinace = samostatné spuštění, jako dnes.
4. **Multi-model: paralelní `fetch()`, ne sekvenční.** Jde o max. 3–4 modely (ne desítky jako u
   budoucího batch-runu), takže riziko zahlcení rate limitů providerů je zanedbatelné — celková
   čekací doba = nejpomalejší model, ne součet.
5. **Multi-model: plain JS `fetch()` smyčka, ne zapojení htmx.** htmx.org je v `base.html`
   načtené, ale dnes ho žádný formulář reálně nepoužívá — `HX-Redirect` větev v `trigger_run`
   (`runs.py:392-396`) je mrtvý kód (žádný `hx-post`/`hx-boost` nikde v `app/templates`).
   Zavádět první reálné použití htmx jako vedlejší efekt týhle featury by zbytečně rozšiřovalo
   rozsah změny; `fetch()` smyčka je izolovanější.
6. **Multi-model: po dokončení všech requestů — reload stránky promptu, žádná nová šablona.**
   Historie runů na `prompts/detail.html` už dnes řadí `started_at desc` a ukáže všechny nově
   vzniklé runy automaticky.
7. **Import: header-driven formát, ne pozicí.** CSV/XLSX sloupce `text,market_code,topic,
   is_active` (case-insensitive hlavička), JSON pole objektů se stejnými klíči. Anglicky natvrdo
   — datový formát, ne UI text (stejná disciplína jako `RUN_COLUMNS` v `app/services/export.py`).
8. **Import: hybridní výchozí trh.** Formulář nad uploadem má "výchozí trh" select. Řádek/objekt
   s vlastním `market_code` ho přebije, řádek bez něj dostane výchozí trh. Kombinuje jednoduchý
   case (jeden trh pro celý soubor) s pokročilým (mix trhů v jednom souboru).
9. **Import: kódování — `utf-8-sig` primárně, jasná chyba místo tichého poškození textu.**
   Německé Excel exporty běžně nejsou čisté UTF-8 (cp1252/BOM) — u DE/CZ přehlásek reálné
   riziko. Při selhání dekódování: strukturovaná chyba doporučující uložit jako UTF-8 nebo
   použít XLSX (tam kódování není nejednoznačné).
10. **Import: duplicity — normalizovaný text (trim + casefold) v rámci `(prompt_set_id,
    market_id)`.** Porovnává se proti (a) existujícím current-version promptům ve stejném
    prompt setu+marketu, (b) ostatním řádkům ve stejném nahrávaném souboru (chytí omylem
    zkopírovaný řádek). Jiný market = jiný klíč, i při identickém textu (může být záměrné
    srovnání).
11. **Import: duplicita se v preview přednastaví jako NEzahrnutá (skip), ne jako chyba.**
    Uživatel ji může ručně zaškrtnout, pokud je záměrná (např. opakovaná otázka kvůli ověření
    konzistence odpovědi). `error` řádky (neplatný market_code, prázdný text) se zahrnout nedají
    vůbec — checkbox je disabled.
12. **Import: preview→confirm bez nové tabulky/session/cache.** Data mezi kroky putují jako
    indexovaná pole běžného Jinja formuláře (`rows-0-text`, `rows-0-market_id`, ...), reuse
    stávajících `select_field`/`text_field`/`checkbox_field` maker z `partials/macros.html` —
    žádný skrytý JSON blob, žádná nová infrastruktura. Confirm route **znovu ověří** `market_id`
    server-side, nikdy neslepě nedůvěřuje resubmitu.
13. **Import: limity na řádky/velikost souboru.** `MAX_IMPORT_ROWS = 500`,
    `MAX_IMPORT_FILE_BYTES = 2_000_000` — kontrola před plným naparsováním, strukturovaná
    `AppError`, ne pád na velkém souboru.
14. **Import: nová route žije v `app/routers/prompt_sets.py`, ne v novém routeru.** Stejné místo
    jako dnešní `create_prompt` — import je jen alternativní způsob vytvoření promptů ve
    stejném prompt setu, žádný nový doménový router.
15. **CSV: detekce oddělovače přes `csv.Sniffer`, omezená na `,;\t`.** Appka dnes natvrdo
    předpokládá čárku — reálné riziko hlavně u německého Excelu, kde lokalizovaný export
    "CSV (Comma delimited)" kvůli desetinné čárce často reálně píše středníkem. Kandidátní
    množina oddělovačů je záměrně omezená na `,`/`;`/tab (ne cokoliv, co Sniffer uhodne z
    volného textu) — Sniffer nad německou/anglickou prózou bez omezení někdy uhodne nesmyslný
    znak. Selhání detekce (nejednoznačný vzorek) → tichý fallback na `,` (dnešní chování),
    nikdy tvrdá chyba jen kvůli sniffingu.
16. **CSV: kontrola počtu polí na řádek, ne tiché spolehnutí na `DictReader`.** Přesně tohle
    způsobilo bug nalezený při ručním testu BIM-T5 — neuvozené pole s čárkou uvnitř textu appku
    tiše rozházelo do špatných sloupců (`market_code` dostal kus věty), místo aby to nahlásila.
    `parse_csv` přechází z `csv.DictReader` na `csv.reader` + ruční mapování podle hlavičky,
    aby šlo `len(raw_row)` porovnat s `len(header)` explicitně — neshoda = `status="error"` s
    konkrétní hláškou ("Row has N field(s) but the header has M — check for an unquoted
    comma/semicolon"), ne tiché špatné namapování. `validate_and_check_duplicates` musí tenhle
    už-nastavený `status="error"` respektovat (přeskočit, nepřepsat obecnou "text required"
    hláškou) — řádky bez rozpoznatelného tvaru nemají žádný smysluplný `text` k validaci.
17. **Stažitelná šablona (CSV + XLSX + JSON) — prevence, ne jen detekce.** Nejlevnější oprava
    celé třídy bugů: nabídnout ke stažení správně naformátovaný soubor (stejná hlavička jako
    `parse_csv`/`parse_xlsx`/`parse_json` čekají, jeden ukázkový řádek), vygenerovaný přes
    `csv.writer`/`openpyxl`/`json.dumps` (stejné knihovny, co appka používá i pro export/parsing)
    — tedy zaručeně validní, ne ručně psaný text jako ten, co bug způsobil. JSON zahrnutý pro
    paritu s appčiným existujícím `export_button_group` vzorem (3 formáty vedle sebe), i když
    JSON žádnou delimiter/quoting nejednoznačnost jako CSV nemá. Žádná perzistence, čistě
    statický generovaný soubor za requestu.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| BIM-T1 | Duplicate-run guard: rozšíření na (prompt_id, model_id) | ✅ |
| BIM-T2 | Multi-model checkboxy + paralelní spuštění (UI) | ✅ |
| BIM-T3 | Testy: multi-model run | ✅ |
| BIM-T4 | Bulk import: parsing/validace service (CSV/XLSX/JSON) | ✅ |
| BIM-T5 | Bulk import: upload formulář + preview route/šablona | ✅ |
| BIM-T6 | Bulk import: confirm/commit route + i18n | ✅ |
| BIM-T7 | Testy: bulk import | ✅ |
| BIM-T8 | CSV: delimiter sniffing + kontrola počtu polí na řádek | ✅ |
| BIM-T9 | Stažitelná import šablona (CSV + XLSX + JSON) | ✅ |

Pořadí: BIM-T1→T2→T3 (guard fix musí být hotový dřív, než jde ověřit paralelní UI — jinak
falešné 409 mezi modely). BIM-T4→T5→T6→T7 (parsing/validace service dřív než UI, co ho volá;
confirm route dřív než testy celého flow). BIM-T8 (rozšiřuje `parse_csv` z BIM-T4) a BIM-T9
(nová route vedle BIM-T5's formuláře) přidány 2026-09-14 po ručním testu BIM-T5 — jdou po T4/T5,
nezávisle na T6/T7. Obě hlavní skupiny (multi-model / import) na sobě nezávisí, jdou v libovolném
pořadí vůči sobě navzájem — BIM-T1 klidně první jako nejmenší a nejrychlejší ověřitelný kus.

---

## BIM-T1 — Duplicate-run guard: rozšíření na (prompt_id, model_id)

**Target:** `app/routers/runs.py` (`trigger_run`)

Design decision 2. Dnešní kontrola (`runs.py:246-250`) blokuje druhý běh podle `prompt_id`
samotného — se 3 paralelními požadavky na 3 různé modely (BIM-T2) by se navzájem falešně
blokovaly.

1. `trigger_run` — rozšířit `WHERE` klauzuli o `Run.model_id == model_id`:
   ```python
   pending_run = db.scalar(
       select(Run.id).where(
           Run.prompt_id == prompt_id,
           Run.model_id == model_id,
           Run.status == "pending",
       ).limit(1)
   )
   ```
   `model_id` je dostupné hned z `Form(...)` na začátku funkce — kontrola může zůstat na svém
   dnešním místě (řádek 246, před lookupem `AIModel`), žádná změna pořadí kroků potřeba.
2. Docstring `trigger_run` (řádky 236-241) — upravit z "blocks... the same prompt" na "blocks...
   the same prompt **and model** combination".

Po dokončení:
1. `docker compose up -d --build`
2. `pytest` — stávající test duplicitního běhu (stejný model dvakrát) musí projít beze změny.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
fix(runs): scope the pending-run guard to prompt+model, not prompt alone
```

---

## BIM-T2 — Multi-model checkboxy + paralelní spuštění (UI)

**Target:** `app/templates/prompts/detail.html`, `app/i18n/en.json`, `app/i18n/de.json`

Design decisions 1, 3, 4, 5, 6. Prerekvizita: BIM-T1 hotový (jinak paralelní test v kroku 2 níže
narazí na falešný 409).

1. Run-trigger formulář — dnešní `<select name="model_id">` s `<optgroup>` podle providera
   (data z `_runnable_model_groups`, `app/routers/prompts.py:33-55`, beze změny) nahradit
   checkboxy `<input type="checkbox" name="model_ids" value="{{ model.id }}">`, pořád
   seskupené podle providera.
2. Vanilla JS (stejné umístění/styl jako stávající cost-badge/button-disable blok) — na submit:
   - `preventDefault()`.
   - Posbírat zaškrtnuté `model_ids`; nula zaškrtnutých → inline chyba
     (`t('prompt.select_at_least_one_model')`), žádný request.
   - Paralelně (`Promise.all`, ne sekvenčně) `fetch('/prompts/{{ prompt.id }}/runs', {method:
     'POST', body: FormData s jedním `model_id` + sdíleným `market_id`/`persona_id`})` pro
     každý zaškrtnutý model.
   - Inline stav vedle každého checkboxu: ⏳ → ✅/❌ podle výsledku HTTP requestu (ne podle
     `Run.status` — to se pozná až na run-detail stránce po reloadu).
   - Po vyřešení všech `fetch` → `window.location.reload()` (historie runů dole ukáže všechny
     nové běhy beze změny šablony).
3. Tlačítko — text podle počtu zaškrtnutých modelů (`t('prompt.trigger_runs_count')` s
   placeholderem `{count}`, ne JS string concat), zablokované po dobu čekání na všechny fetch.
4. i18n (EN+DE, jeden commit) — `prompt.trigger_runs_count`, `prompt.select_at_least_one_model`,
   `prompt.select_all_models` (volitelný "vybrat vše" link/checkbox).

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči: prompt se 2+ aktivními modely, zaškrtnout 2–3, spustit → spustí se paralelně
   (celkový čas ≈ nejpomalejší model, ne součet), historie runů ukáže všechny nové běhy.
3. Zaškrtnout jen jeden model → chová se jako dnešní single-run (regression check).
4. Nezaškrtnout nic, kliknout run → inline chyba, žádný request v Network tabu.
5. Ověřit na ~640px/~1024px/desktop šířce (checkboxy nesmí rozbít layout na mobilu).
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(prompts): allow selecting multiple models and running them in parallel
```

---

## BIM-T3 — Testy: multi-model run

**Target:** `tests/test_runs.py`

1. Regresní test guardu (BIM-T1): dva různé modely, oba `POST /prompts/{id}/runs` s odlišným
   `model_id`, zatímco první je ještě `pending` (vytvořeno přímo přes ORM) → oba projdou (žádný
   409). Stejný model podruhé, zatímco první `pending` → pořád 409.
2. Docstring testu poznamenává, že paralelní JS orchestrace (BIM-T2) sama o sobě testem
   nepokrytá (žádný headless-browser test v projektu) — ověřuje se ručně podle "Po dokončení"
   kroků BIM-T2.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover pending-run guard scoped to prompt+model combination
```

---

## BIM-T4 — Bulk import: parsing/validace service

**Target:** nový `app/services/prompt_import.py`

Design decisions 7, 8, 9, 10, 13.

1. Dataclass `ParsedPromptRow` — `row_number: int`, `text: str`, `market_code: str | None`,
   `topic: str | None`, `is_active: bool`, `status: Literal["new", "duplicate", "error"]`,
   `error_message: str | None`.
2. `parse_csv(file: bytes) -> list[ParsedPromptRow]` — `csv.DictReader` nad
   `io.StringIO(file.decode("utf-8-sig"))`; při `UnicodeDecodeError` zkusit `cp1252`, jinak
   strukturovaná chyba doporučující UTF-8/XLSX. Sloupce case-insensitive: `text` povinný,
   `market_code`/`topic`/`is_active` volitelné.
3. `parse_xlsx(file: bytes) -> list[ParsedPromptRow]` — `openpyxl.load_workbook(io.BytesIO(file),
   read_only=True, data_only=True)`, první list, první řádek = hlavička (stejné sloupce jako
   CSV), plně prázdné řádky kdekoliv se přeskakují.
4. `parse_json(file: bytes) -> list[ParsedPromptRow]` — `json.loads`, očekává pole objektů;
   strukturovaná chyba, pokud kořen není pole nebo prvek není objekt.
5. `validate_and_check_duplicates(rows, db: Session, prompt_set_id: int, default_market_id: int)
   -> list[ParsedPromptRow]`:
   - Řádek bez `market_code` → `default_market_id`; s `market_code` → lookup `Market.code`,
     nenalezeno → `status="error"`.
   - `text` prázdný po `strip()` → `status="error"`.
   - Normalizovaný text (`" ".join(text.split()).casefold()`) proti existujícím current-version
     `Prompt` řádkům ve stejném `(prompt_set_id, market_id)` a proti ostatním řádkům stejného
     batche se stejným market_id → `status="duplicate"`.
   - Jinak `status="new"`.
6. Konstanty `MAX_IMPORT_ROWS = 500`, `MAX_IMPORT_FILE_BYTES = 2_000_000` (kontroluje volající
   route v BIM-T5, ne tenhle modul).

Po dokončení:
1. Ruční ověření (ad-hoc `python -c` nebo počkat na BIM-T5 UI) — CSV/XLSX/JSON se stejným
   obsahem dají stejný výsledek.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(prompts): add CSV/XLSX/JSON parsing and duplicate-detection service for bulk import
```

---

## BIM-T5 — Bulk import: upload formulář + preview route/šablona

**Target:** `app/routers/prompt_sets.py`, nové `app/templates/prompt_sets/import.html`,
`app/templates/prompt_sets/import_preview.html`

Design decisions 8, 12, 13, 14. Prerekvizita: BIM-T4 hotový.

1. `GET /prompt-sets/{prompt_set_id}/prompts/import` — formulář: file upload (`accept=".csv,
   .xlsx,.json"`), select "výchozí trh" (`market_options(db)`).
2. `POST /prompt-sets/{prompt_set_id}/prompts/import/preview` (`file: UploadFile = File(...)`,
   `default_market_id: int = Form(...)`):
   - Kontrola velikosti/přípony před parsováním, počtu řádků po naparsování
     (`MAX_IMPORT_FILE_BYTES`/`MAX_IMPORT_ROWS`) → strukturovaná `AppError`, ne raw 500.
   - Podle přípony zavolat `parse_csv`/`parse_xlsx`/`parse_json` (BIM-T4), pak
     `validate_and_check_duplicates`.
   - Render `import_preview.html` — **žádný zápis do DB** v tomhle kroku.
3. `import_preview.html` — tabulka: `row_number`, zkrácený `text` (title tooltip s plným
   zněním), editovatelný `select_field` pro market, `text_field` pro topic, `checkbox_field`
   pro `is_active`, badge stavu (`new`/`duplicate`/`error`, reuse `pill_badge` makra).
   `checkbox_field` "Importovat" na začátku řádku — pre-checked pro `new`, pre-unchecked pro
   `duplicate`, disabled pro `error` (design decision 11). Souhrn nad tabulkou: "{new} nových,
   {duplicate} duplicitních, {error} chyb". Formulář POSTuje na `.../import/confirm` (BIM-T6),
   indexovaná pole `rows-{i}-text` (hidden), `rows-{i}-market_id`, `rows-{i}-topic`,
   `rows-{i}-is_active`, `rows-{i}-include`.

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči: CSV s 5 řádky (2 s `market_code`, 3 bez) → preview ukáže správný trh u všech
   (explicitní i doplněný default), správné duplicate flagy proti existujícím promptům.
3. Soubor s neplatným `market_code` → řádek označený jako chyba, zbytek uploadu neovlivněn.
4. Poškozený/nesprávný formát souboru → srozumitelná chyba na formuláři, ne 500.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(prompts): add bulk-import upload form and validation preview screen
```

---

## BIM-T6 — Bulk import: confirm/commit route + i18n

**Target:** `app/routers/prompt_sets.py`, `app/templates/prompt_sets/detail.html`,
`app/i18n/en.json`, `app/i18n/de.json`

Design decision 12. Prerekvizita: BIM-T5 hotový (pole formuláře musí odpovídat).

> **Poznámka (2026-09-14):** i18n klíče (krok 2) a odkaz "Bulk import" na
> `prompt_sets/detail.html` (krok 3) byly vytažené dopředu už do BIM-T5/rovnou po něm — bez
> i18n klíčů by se T5 šablony vůbec nevykreslily (chyba v původním rozdělení úkolů), odkaz byl
> přidán hned, jak chyběl při ručním testování. BIM-T6 tak teď pokrývá už jen krok 1.

1. `POST /prompt-sets/{prompt_set_id}/prompts/import/confirm` — parsuje indexovaná pole
   (`rows-{i}-*`) z `request.form()` (proměnný počet řádků, ne pevný seznam `Form(...)`
   parametrů), **znovu ověří** každé `market_id` (nikdy neslepě důvěřovat resubmitu), vytvoří
   `Prompt` řádek pro každý `include=true` řádek (stejná konstrukce jako `create_prompt`, vždy
   verze 1). Po commitu redirect na `/prompt-sets/{prompt_set_id}` se souhrnem počtu
   naimportovaných promptů.

Po dokončení:
1. `docker compose up -d --build`
2. Celý flow end-to-end: upload → preview → odškrtnout jeden duplicitní řádek → potvrdit →
   prompty se objeví v prompt setu se správným trhem/topicem/is_active, přeskočený duplicitní
   řádek chybí.
3. Ověřit na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(prompts): commit bulk-import preview selections as new prompts
```

---

## BIM-T7 — Testy: bulk import (parsing/validace/duplicity/confirm flow)

**Target:** `tests/test_prompt_import.py` (založený už v BIM-T8 — tenhle task ho rozšiřuje, ne
zakládá znovu)

> **Sladěno 2026-09-14:** původně tenhle task pokrýval i BIM-T8/BIM-T9 regresi (body 5-6 níže,
> teď smazané) — přesunuto přímo do jejich vlastních "Po dokončení" kroků (BIM-T8/T9 sekce
> níže), protože `PROMPTS_BULK_IMPORT_MULTI_MODEL.md`'s vlastní BIM-8/BIM-9 prompty už testy
> žádaly rovnou, ne až tady — dřívější verze tohohle dokumentu si protiřečila. BIM-T7 tak pokrývá
> jen to, co BIM-T8/T9 nezaložily: parsing/validace/duplicity (BIM-T4) a confirm flow (BIM-T6).

1. Parsing: CSV/XLSX/JSON se stejným obsahem dají stejná `ParsedPromptRow` data (round-trip).
   CSV s BOM/cp1252 se naparsuje bez pádu.
2. Validace: neznámý `market_code` → `error`; prázdný `text` → `error`; chybějící `market_code`
   → doplní se `default_market_id`.
3. Duplicity: stejný (normalizovaný) text jako existující current-version prompt ve stejném
   marketu → `duplicate`; jiný market, stejný text → `new` (market je součást klíče duplicity);
   dva stejné řádky v jednom souboru → druhý `duplicate`.
4. Integrace: upload → preview → confirm end-to-end přes test klienta — jen `include=true`
   řádky se uloží, `error` řádky se neuloží ani při vynuceném `include=true` na klientu
   (server-side re-validace, design decision 12).

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover bulk prompt import parsing, validation, and confirm flow
```

---

## BIM-T8 — CSV: delimiter sniffing + kontrola počtu polí na řádek

**Target:** `app/services/prompt_import.py` (`parse_csv`)

Design decisions 15, 16. Reaguje na reálný bug z ručního testu BIM-T5 (viz úvod dokumentu).

1. Nová `_detect_dialect(text_content: str) -> csv.Dialect` — `csv.Sniffer().sniff(sample,
   delimiters=",;\t")` nad prvními ~4096 znaky; `csv.Error` (nejednoznačný vzorek) → fallback
   na `csv.excel` (dnešní chování, čárka).
2. `parse_csv` přechází z `csv.DictReader` na `csv.reader(io.StringIO(text_content),
   dialect=dialect)` + ruční `field_map = {header[i].strip().lower(): i}` — potřeba pro krok 3,
   `DictReader`'s `restkey`/`restval` by neshodu tiše skryly.
3. Za každý řádek (po přeskočení fully-blank řádků, beze změny) porovnej `len(raw_row) ==
   len(header)`. Neshoda → `ParsedPromptRow(row_number=..., text="", status="error",
   error_message=f"Row has {len(raw_row)} field(s) but the header has {len(header)} — check
   for an unquoted comma/semicolon inside a text value.")`, `continue` (nezavolat `cell()`
   helper na nekonzistentní řádek).
4. `validate_and_check_duplicates` — na začátku smyčky přidat `if row.status == "error":
   continue` (design decision 16) — jinak `if not row.text` větev přepíše konkrétní hlášku o
   počtu polí obecnou "Prompt text is required."
5. Nový `tests/test_prompt_import.py` (pokud ještě neexistuje, založ) — regresní testy: CSV se
   středníkem se naparsuje správně (Sniffer); CSV s neuvozenou čárkou v textu (víc polí než
   hlavička) → `status="error"` s konkrétní hláškou o počtu polí, ne tiché špatné namapování.

Po dokončení:
1. `docker compose up -d --build`
2. Ruční ověření (`python -c` se skutečným `parse_csv`, nebo přes preview UI):
   - CSV se středníkem jako oddělovačem (`text;market_code;topic;is_active`) → naparsuje se
     správně, ne jako jeden sloupec.
   - CSV s neuvozenou čárkou v `text` poli (víc polí než hlavička) → `status="error"` s
     hláškou o počtu polí, `market_code`/`topic` ostatních řádků zůstávají nedotčené.
3. `pytest` — všechny testy zelené (staré i nové).
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
fix(prompts): detect CSV delimiter and flag rows with a mismatched field count
```

---

## BIM-T9 — Stažitelná import šablona (CSV + XLSX + JSON)

**Target:** `app/routers/prompt_sets.py`, `app/templates/prompt_sets/import.html`

Design decision 17. Rozšířeno 2026-09-14 (v konverzaci) o JSON a o vizuální sjednocení s
existujícím exportem — uživatel se zeptal "proč ne i JSON", odpověď: žádný důvod, appka už má
přesně tenhle vzor (`export_button_group` makro, 3 formáty vedle sebe) reuse místo vymýšlení
nového UI.

1. Konstanty `_TEMPLATE_HEADER = ["text", "market_code", "topic", "is_active"]`,
   `_TEMPLATE_EXAMPLE_ROW` (CSV/XLSX, string hodnoty) a `_TEMPLATE_EXAMPLE_JSON_ROW` (stejný
   obsah, ale se skutečnými JSON typy — `is_active: True`, ne string `"true"`).
2. `GET /prompt-sets/{prompt_set_id}/prompts/import/template` (`format: Literal["csv", "xlsx",
   "json"] = Query("csv")`, `dependencies=_editor_or_admin`) — `csv.writer`/`openpyxl.Workbook`/
   `json.dumps` postaví hlavičku + ukázkový řádek do paměti, vrátí jako `Response` s
   `Content-Disposition: attachment`. Žádná perzistence, žádný zápis do DB.
3. `import.html` — `export_button_group('/prompt-sets/' ~ id ~ '/prompts/import/template',
   t('prompt.export_csv'), t('prompt.export_xlsx'), t('prompt.export_json'))` vedle file inputu
   — stejné bordered tlačítkové skupiny jako u exportu runů, žádné nové i18n klíče pro popisky
   tlačítek (jen jeden nový úvodní label `prompt_import.download_template_label`).
4. `tests/test_prompt_import.py` (rozšiř soubor založený v BIM-T8) — round-trip test: stažená
   šablona (CSV, XLSX i JSON) se dá zpátky naparsovat přes `parse_csv`/`parse_xlsx`/`parse_json`
   beze změny.

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči: stáhnout CSV, XLSX i JSON šablonu, nahrát je zpátky appce → naparsuje se bez
   chyby, ukázkový řádek se objeví v preview jako "New".
3. `pytest` — všechny testy zelené (staré i nové).
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(prompts): add a downloadable CSV/XLSX/JSON template for bulk import
```

---

## Code-review remediation (2026-09-14 / 2026-09-15)

Po dokončení BIM-T1–T9 proběhla na téhle branch **dvě nezávislá kola** multi-agent code review
(`/code-review`, `--focus security,dry`), obě odsouhlasená a odpracovaná v konverzaci. Žádný
z těchto nálezů nemění schéma ani přidává novou route — jde o opravy v rámci existující
architektury BIM-T1–T9, proto dokumentované tady jako dodatek, ne jako nové BIM-T úkoly.

### Kolo 1 (2026-09-14) — 10 nálezů + 1 bug nalezený při psaní testů

| Nález | Oprava | Commit |
|---|---|---|
| Race condition: dva souběžné triggery na stejný prompt+model můžou oba projít SELECT kontrolou | Alembic migrace 0024 (partial unique index `idx_runs_one_pending_per_prompt_model`), zrcadlený na ORM úrovni (`app/models/run.py` `__table_args__`) — bez ORM zrcadlení by test DB (`Base.metadata.create_all`, ne Alembic) constraint neviděla | `efafef5` |
| `AIModel.is_active` nebyl na `trigger_run` serverově vynucený (jen UI filtr) | Přidána kontrola `if not model.is_active: raise AppError(...)` | `efafef5` |
| CSV kontrola počtu polí (`!=`) omylem odmítala i legitimně krátké řádky (chybějící volitelné sloupce) | Změněno na `>` — jen přebytečná pole se flagují | `c3c690d` |
| XLSX nemělo obdobu CSV's field-count-mismatch kontroly | Nová kontrola založená na skutečných hodnotách za hlavičkou, ne na syrové délce tuple (viz další řádek) | `c3c690d` |
| **Bonus bug nalezený při psaní testu k předchozímu řádku:** `openpyxl.iter_rows` doplňuje i hlavičkový řádek na šířku nejširšího řádku v listu — `len(header)` tak skryje shodu přesně u nejrealističtějšího případu (řádek se zaběhlou hodnotou je zároveň nejširší řádek) | Hranice teď počítaná z nejvyššího indexu, který skutečně zabírá pojmenovaný sloupec hlavičky (`header_width`), ne ze syrové (vycpané) délky | `c3c690d` |
| `MAX_IMPORT_ROWS` se kontroloval až po naparsování celého souboru | Kontrola přesunuta do smyčky každého parseru (později v kole 2 sloučena do jedné sdílené funkce) | `c3c690d` |
| Chybové hlášky u řádků (`error_message`) byly natvrdo anglicky, šablona je tiskla přímo — **AI_INSTRUCTIONS.md §3 porušení** | `error_code`/`error_context` na `ParsedPromptRow`, šablona resolvuje přes `t('prompt_import.row_error_' ~ error_code)` | `c3c690d` |
| `import_prompts_preview` četlo celý upload do paměti před kontrolou velikosti | `file.file.read(MAX_IMPORT_FILE_BYTES + 1)` — nikdy nenačte víc než 1 byte za limitem | `c3c690d` |
| `import_prompts_confirm` bez cap na počet řádků (šlo obejít preview přímým POSTem) | Stejný `MAX_IMPORT_ROWS` check jako preview | `c3c690d` |
| Duplicitní `_normalize_text` (privátní) používaná jen uvnitř modulu | Přejmenováno na veřejné `normalize_prompt_text`, reuse v `import_prompts_confirm` pro duplicate re-check | `c3c690d` |
| Locale-switch odkazy (`base.html`) stavěly `next` z `request.url.path` bezpodmínečně — 405 na kterékoliv z ~14 stránek renderovaných přímo z POST handleru (bulk-import preview mezi nimi) | `_locale_switch_next()` (`app/templating.py`) — na GET stránce beze změny, na POST-rendered stránce fallback na `Referer` hlavičku toho POST requestu, `/clients` jako poslední záchrana | `617b1a3` |

### Kolo 2 (2026-09-15) — 9 nálezů, 8 opraveno, 1 vědomě odloženo

| Nález | Oprava | Commit |
|---|---|---|
| `except IntegrityError` na `trigger_run` chytal i nesouvisející FK violation (např. smazaný model/market/persona v race) a hlásil to jako "run already pending", skutečná výjimka se nikam nelogovala | Rozlišeno podle `exc.orig.diag.constraint_name` — jen shoda s `idx_runs_one_pending_per_prompt_model` dá "already pending", cokoliv jiného se zaloguje a vrátí jako nový `run_creation_failed` | `35f5cdc` |
| Confirm-time duplicate re-check (kolo 1) tiše zahazoval i řádek, co uživatel **záměrně** zaškrtl navzdory `duplicate` statusu z preview (design decision 11) | Preview form teď posílá i původní `status` řádku (hidden pole); confirm rozlišuje "uživatel chtěl duplicitu záměrně" (importuje) od "stala se duplicitou až od preview" (přeskočí) | `35f5cdc` |
| `int(market_id_raw)` nemá horní limit — přetečené-ale-číselné `market_id` (např. zfalšovaný request) dosáhlo `db.get()` a spadlo na `DataError`, ne `ValueError`, což byl nezachycený 500 | `market_id` se validuje proti rozsahu Postgres int4 ještě před dotazem do DB | `35f5cdc` |
| Duplicitní `existing_texts_by_market`/`existing_texts()` closure — stejná v `prompt_import.py` i `prompt_sets.py` | Sloučeno do `build_existing_texts_lookup()` (`app/services/prompt_import.py`), používá ji preview i confirm | `35f5cdc` |
| `MAX_IMPORT_ROWS` kontrola kopírovaná 3× (jednou na parser) | Sloučeno do `_append_row()` | `35f5cdc` |
| Docstring `parse_json` tvrdil, že cap se kontroluje "dřív, než se celý soubor naparsuje" — u JSON to neplatí (`json.loads` musí přečíst celé pole najednou) | Opraven jen docstring — skutečná oprava (streamovací JSON parser) by byla neúměrná vzhledem k existujícímu 2MB limitu na velikost souboru | `35f5cdc` |
| Status pilulka (`new`/`duplicate`/`error`) v `import_preview.html` ručně duplikovala `status_badge` makro z `partials/macros.html` | Makro rozšířené o volitelný `label` parametr (zpětně kompatibilní), `import_preview.html` ho teď reuse | `35f5cdc` |
| `import_prompts_confirm` byl `async def` (kvůli `await request.form()`), ale dělal synchronní `Session` volání přímo na event loopu — jediná route v appce s tímhle vzorem, v rozporu s konvencí zdokumentovanou v `app/database.py` | Tělo přesunuté do nové `_confirm_import_rows()`, volané přes `run_in_threadpool` | `4de3753` |
| Locale-switch fallback (kolo 1) na `Referer` hlavičku není 100% spolehlivý (striktní Referrer-Policy, privacy rozšíření) | **Vědomě neopraveno** — i nespolehlivý fallback je striktně lepší než předchozí tvrdá 405 chyba; robustnější řešení (explicitní `?next=` parametr přes ~14 šablon) by bylo neúměrné vzhledem k tomu, jak okrajový je tenhle scénář | — |

Všech 177 testů (`pytest`) zelených po obou kolech, včetně nových testů na každý opravený nález.

---

## Completion Checklist

- [x] Duplicate-run guard rozšířený na (prompt_id, model_id) — různé modely běží souběžně,
      stejný model dvakrát pořád blokovaný
- [x] Multi-model checkboxy fungují, spouští paralelně, historie runů ukazuje všechny nové běhy
- [x] CSV/XLSX/JSON import — parsing, validace, duplicate detekce, preview, confirm
- [x] Bulk import respektuje limity (max řádků, max velikost souboru), kódovací edge case
      (cp1252/BOM) nerozbíjí text
- [x] CSV se středníkem i čárkou se naparsuje správně; řádek s neuvozenou čárkou v textu je
      `error` s konkrétní hláškou, ne tiché špatné namapování
- [x] Stažitelná CSV/XLSX šablona funguje a appka ji sama umí zpátky naimportovat
- [x] `pytest` sada zelená, pokrývá obě featury (177 testů, viz Code-review remediation výše)
- [ ] Ověřeno na ~640px/~1024px/desktop šířce (obě featury) — zatím jen desktop, mobil/tablet
      nebyl v týhle branch ještě ručně ověřen
- [x] `docs/TASKS.md` — poznámka, že tahle branch existuje a co pokrývá (odkaz na tenhle
      soubor) — hotovo po sloučení (PR #12, 2026-09-15), na výslovný pokyn uživatele
      (ne po plném browser walkthroughu, jak plán původně počítal — viz řádek výše)
- [x] `docs/ROADMAP.md` — poznámka u "Hromadný import promptů" / "Study" koncept, že import byl
      vytažen mimo pořadí a implementován; "Study" koncept samotný zůstává neimplementovaný,
      čeká na Scheduler — hotovo po sloučení, na výslovný pokyn uživatele
