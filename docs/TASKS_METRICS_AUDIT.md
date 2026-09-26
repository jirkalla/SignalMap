# SignalMap — Tasks: Metrics Audit Fixes

## v1.0 | Září 2026
## Branch: feature/signalmap-metrics-audit-fixes
## Task ID prefix: MAF

Status: navrženo v konverzaci 2026-09-25, po průřezovém review výpočtů na
`/dashboard`, `/ops` a `/schedules` (master `3fded1f`, v1.1.0).

Přehled výpočtů a všech 19 nálezů (F1–F19) se scénáři je v artifactu
„SignalMap Metric Lineage" (https://claude.ai/artifact/LrrCLgVmTQokCCUZ7Wdinh,
privátní). Tenhle soubor z něj přebírá čísla nálezů, závažnost, náročnost
a navržené řešení. Nálezy vznikly **čtením kódu**, proti DB ani na běžící
aplikaci ověřené nejsou — proto MAF-T0 jako první krok.

**Goal:** každé číslo na třech interních stránkách buď počítá to, co jeho
popisek tvrdí, nebo to uživateli řekne (neúplná cena, chybějící týden,
jiný jmenovatel). Scheduler se nespouští dřív, než uživatel čeká, a
nezasekne se po retry.

**Jedna větev, pořadí podle triage** (vlny z artifactu):

- **Vlna 1** (T1–T11) — bez změny schématu, bez metodického rozhodnutí.
- **Vlna 2** (T12–T17) — nejdřív rozhodnutí uživatele (T12), pak kód.
- **Vlna 3** (T18–T20) — až po ověření proti fakturám / datům z T0.
- **Dokumentace a nasazení** (T21–T23).

Větev je velká (≈ 20 commitů). Pokud se po vlně 1 ukáže, že review PR
bude nepřehledné, je legitimní vlnu 1 zmergovat samostatně a vlny 2–3
dodělat na navazující větvi — rozhoduje uživatel po T11.

---

## Nálezy — přehled

| ID | Nález | Oblast | Závažnost | Náročnost | Task |
|----|-------|--------|-----------|-----------|------|
| F1 | Retried queue item zůstane navždy `leased` | scheduler | Medium | S | T2 |
| F2 | Nový rozvrh se spustí hned, ne v příští termín | scheduler | High | S | T1 |
| F3 | Náhled v editaci rozvrhu začíná od původního `starts_on` | scheduler | Medium | S | T3 |
| F4 | U prompt rozvrhů je každá položka vlastní „okno" v historii a health stripu | scheduler | Medium | M | T11 |
| F5 | Transport retry = další Run → Ops počítá pokusy, ne výsledky | ops | Medium | M (migrace) | T17 |
| F6 | Celková cena tiše vynechává runy bez ceny | ops | High | M | T8 |
| F7 | Prázdné týdny se kreslí jako 0 (rate, SoV, position) | dashboard | High | M | T9 |
| F8 | „Position" znamená v grafu pořadí, v tabulce offset ve znacích | dashboard | Low | S | T7 |
| F9 | „Cited in %" dělí i runy modelů bez web search | dashboard | Medium | S | T4 |
| F10 | Own-domain rate míchá runy analyzované před nastavením domény | dashboard | Medium | L | T20 |
| F11 | Průměrná latence zahrnuje chybové runy | ops | Low | S | T6 |
| F12 | Pozdě přidaní konkurenti a přejmenování zkreslují tabulku entit | dashboard | Low | S→M | T16 |
| F13 | Share of voice = průměr poměrů po runech | dashboard | Medium | S | T13 |
| F14 | Position jen přes runy, kde je klient zmíněn | dashboard | Medium | S | T14 |
| F15 | Gemini thinking tokeny se neceníme (ověřit) | cost | High, pokud se potvrdí | S + M | T18 |
| F16 | Poplatky za web search nejsou v cenovém modelu | cost | High, pokud jsou podstatné | L (migrace) | T19 |
| F17 | Jednotka citace se liší podle providera | dashboard | Medium | M | T15 |
| F18 | Denní limit runů není vidět v náhledu rozvrhu | scheduler | Medium | S | T10 |
| F19 | Limit hloubky fronty počítá jen `queued` | scheduler | Low | S | T5 |

---

## Design decisions (rozhodnuto před psaním kódu)

1. **T0 před vším ostatním.** Nález, který T0 vyvrátí, se neopravuje —
   jeho task se v Task Indexu označí ❌ s jednou větou proč. Nález, který
   T0 nedokáže ověřit z dat (F2, F3, F4 jsou čistě logika), se opravuje
   s regresním testem, který chybu nejdřív reprodukuje.
2. **Evidence se nepřepisuje (NFR-6).** Žádný task nemění existující
   `runs`, `raw_responses`, `citations`, `analysis_results`. F10 se řeší
   novými řádky `analysis_results`, ne `UPDATE`.
3. **Žádná nová závislost.** Testy času (F2, F3) jdou přes čisté funkce
   s explicitním `now`, stejně jako `tests/test_scheduling_recurrence.py`
   — ne přes `freezegun`.
4. **F1: rozšířit uvolnění leasu, `run_id` nemazat.** Mazání by ztratilo
   vazbu queue item → neúspěšný Run v historii. Lease se uvolní, když je
   `run_id IS NULL` **nebo** navázaný Run už není `pending`.
5. **F2 + F3 sdílí jeden helper** v `app/services/scheduling.py`
   (`first_occurrence_after(schedule, *, now)` nebo podobně) — začátek
   hledání = `max(now, starts_on 00:00 v timezone rozvrhu)`. Router ho volá
   při vytvoření i v náhledu, aby se dvě místa nemohla znovu rozejít.
6. **F4: opravit seskupení, ne jen zápis.** Klíč okna v
   `history_batches` a `schedule_health` =
   `COALESCE(batch_id, schedule_id + scheduled_for, 'item-' + id)`. Tím se
   opraví i existující historie bez zásahu do dat. Nové okno s fan-outem
   > 1 navíc dostane `batch_id` vždy (ne jen prompt set).
7. **F6: počet runů bez ceny, ne odhad.** Neúplná suma se neukazuje jako
   úplná a ani se nedopočítává odhadem — k sumě přibude
   `unpriced_runs_count` (run má `raw_response`, ale cena je NULL).
8. **F7: `null` jen pro poměrové metriky.** `runs` a `citations` vrací za
   prázdný týden dál 0 (to je pravdivá hodnota), `own_rate`,
   `share_of_voice` a `position` vrací `null` a graf kreslí mezeru. Mění se
   API kontrakt → docstring endpointu i `TimeseriesResponse`.
9. **Otevřená rozhodnutí (F5, F12 krok 2, F13, F14, F16, F17) se
   zapisují v T12** jako design decisions 12+ do tohoto souboru, dřív než
   se napíše kód vlny 2. Doporučení z artifactu:
   - F13 → souhrnný poměr Σ zmínek klienta / Σ všech zmínek
   - F14 → definice zůstává, vedle position se vždy ukazuje pokrytí
   - F17 → nová metrika „cited sources" = distinct (run, normalizovaná
     doména), dnešní počet citací jako sekundární
   - F5 → nullable `runs.queue_item_id` (migrace), Ops ukáže „výsledek" i
     „pokusy"
   - F12 krok 2 → `entity_id` ve výstupu skillu, nová `skill_version`
   - F16 → jen pokud T0 ukáže podstatný podíl na fakturách
10. **Každá migrace se hlásí před napsáním** (`AI_INSTRUCTIONS.md` §3/§4).
    V téhle větvi to jsou nejvýš dvě: T17 (F5) a T19 (F16).
11. **Release.** Viditelné změny + možná migrace → pravděpodobně MINOR
    (v1.2.0) podle `docs/DEPLOYMENT.md` §0. Bump verze a tag dělá
    uživatel.

---

## Task Index

| ID | Name | Nález | Status |
|----|------|-------|--------|
| T0 | Ověření nálezů proti DB a fakturám (bez kódu) | všechny | ⏳ |
| T1 | První termín rozvrhu počítat od „teď" | F2 | ⏳ |
| T2 | Uvolnit lease i po retry | F1 | ⏳ |
| T3 | Náhled v editaci od „teď" | F3 | ⏳ |
| T4 | Jmenovatel „cited in %" jen search modely | F9 | ⏳ |
| T5 | Limit hloubky fronty počítá deferred + leased | F19 | ⏳ |
| T6 | Latence jen z úspěšných runů | F11 | ⏳ |
| T7 | Přejmenovat sloupec „first position" | F8 | ⏳ |
| T8 | Počet runů bez ceny v Ops a v budget notifikaci | F6 | ⏳ |
| T9 | Mezery místo nul v týdenním grafu + osa position | F7 | ⏳ |
| T10 | Denní limit v náhledu rozvrhu | F18 | ⏳ |
| T11 | Seskupení historie a health stripu po oknech | F4 | ⏳ |
| T12 | Rozhodnutí vlny 2 (bez kódu) | F5, F12, F13, F14, F16, F17 | ⏳ |
| T13 | Share of voice jako souhrnný poměr | F13 | ⏳ |
| T14 | Pokrytí vedle position | F14 | ⏳ |
| T15 | Metrika „cited sources" | F17 | ⏳ |
| T16 | Tabulka entit: jmenovatel + entity_id | F12 | ⏳ |
| T17 | Runs → queue item vazba, Ops „výsledek vs. pokusy" | F5 | ⏳ |
| T18 | Gemini thinking tokeny v ceně | F15 | ⏳ |
| T19 | Poplatky za web search v cenovém modelu | F16 | ⏳ |
| T20 | Backfill analýz + čtení posledního výsledku | F10 | ⏳ |
| T21 | CHANGELOG | — | ⏳ |
| T22 | Definice metrik v REQUIREMENTS.md | — | ⏳ |
| T23 | Nasazení a end-of-branch docs | — | ⏳ |

---

## T0 — Ověření nálezů (bez kódu)

**Target:** žádné soubory kromě tohoto (výsledky do tabulky níže)

Read-only dotazy proti lokální DB (případně produkční kopii — rozhodne
uživatel), výsledek zapsat sem:

| Nález | Jak ověřit | Výsledek |
|-------|-----------|----------|
| F1 | `run_queue` se `status='leased'` a `leased_until < now()` | |
| F5 | počet `runs` se `trigger_type='scheduled'` na jeden `run_queue` item (přes čas + prompt + model) | |
| F6 | runy s `raw_responses`, jejichž cena z `run_cost_sql_expr` je NULL, podle modelu | |
| F9 | podíl runů na modelech se `supports_web_search=false` na klienta | |
| F10 | klienti, jejichž runy mají `cited=false` všude před nějakým datem a `true` po něm; historie změny `clients.domain` (pokud existuje audit) | |
| F12 | entity, jejichž jméno se objevuje v `analysis_results.output→entities` jen v části runů | |
| F15 | SUM(`thoughts_token_count`) vs. SUM(`candidates_token_count`) u Gemini; porovnat spočítanou cenu pár dní s Google billingem (**uživatel**) | |
| F16 | počet vyhledávání na run (`search_queries`) × aktuální ceník vyhledávání providerů; porovnat s měsíční fakturou (**uživatel**) | |
| F17 | citace na run podle providera (průměr, medián) | |
| F18 | klienti, jejichž aktivní rozvrhy mají v jednom okně víc runů než jejich denní limit | |
| F19 | klienti s `deferred` + `leased` > 0 | |

F2, F3, F4, F7, F8, F11, F13, F14 se z dat ověřit nedají nebo nemusí
(logika / zobrazení) — v jejich tasku se chyba nejdřív reprodukuje testem.

**Done when:** každý řádek má výsledek „potvrzeno" / „vyvráceno" /
„nelze ověřit z dat" a vyvrácené tasky jsou v Task Indexu ❌.

**Expected commit:** `docs(metrics-audit): record verification of findings`

---

## T1 — První termín rozvrhu od „teď" (F2)

**Target:** `app/services/scheduling.py`, `app/routers/schedules.py`
(`create_schedule`, `schedules.py:572`), `tests/test_scheduling_recurrence.py`,
`tests/test_schedules.py`

1. Čistý helper v `scheduling.py` (design decision 5): začátek hledání =
   `max(now, datetime.combine(starts_on, time.min, tz=schedule.timezone))`.
2. `create_schedule` ho volá s `datetime.now(timezone.utc)` místo
   `starts_on 00:00 UTC`.
3. Testy: vytvořeno 14:00 Prague, denní 09:00 → zítra 09:00; vytvořeno
   07:00, denní 09:00 → dnes 09:00; denní 01:00 Prague → první den se
   nepřeskočí (dnešní chování přes půlnoc UTC ho přeskakuje).

**Done when:** testy projdou, celá sada projde.

**Expected commit:** `fix(schedules): start the first occurrence search from now`

---

## T2 — Uvolnit lease i po retry (F1)

**Target:** `app/services/queue.py` (`release_expired_leases`, `:355`),
`tests/test_worker_queue.py`

1. Podmínka uvolnění: `leased_until < now` AND (`run_id IS NULL` OR
   navázaný `Run.status != 'pending'`) — design decision 4.
2. Docstring: proč navázaný terminální Run znamená, že adaptér pro
   *aktuální* pokus ještě nebyl volán.
3. Test: item s `run_id` na `error` Run, `leased`, lease vypršel →
   `release_expired_leases` ho vrátí do `queued`. Item s `run_id` na
   `pending` Run se nesmí uvolnit (zůstává na `reconcile_interrupted_runs`).

**Done when:** oba testy projdou; pokud T0 našel zaseknuté položky, návod
na jejich jednorázové uvolnění je v tomhle tasku (dotaz, spouští uživatel).

**Expected commit:** `fix(runs): release expired leases left behind by a retried item`

---

## T3 — Náhled v editaci od „teď" (F3)

**Target:** `app/routers/schedules.py` (`preview_occurrences`, `:825`),
`tests/test_schedules.py`

1. Náhled simuluje od helperu z T1 (`max(now, starts_on)`), ne od
   `starts_on 00:00 UTC`.
2. Test: rozvrh se `starts_on` před 60 dny, weekly, `ends_on` za 30 dní →
   první položka náhledu je v budoucnu a počet zbývajících výskytů ≈ 4,
   ne ≈ 13.

**Expected commit:** `fix(schedules): preview occurrences from now when editing`

---

## T4 — „Cited in %" jen ze search modelů (F9)

**Target:** `app/services/dashboard.py` (`domain_league_rows`, `:317`, `:361`),
`tests/test_dashboard.py`

1. Jmenovatel `run_coverage_pct` = počet runů v scope na modelech se
   `supports_web_search=true`. Filtr převzít ze stejného místa jako
   `_mention_visibility_base_query`, ne napsat znovu.
2. Test: 2 runy Gemini (jeden cituje doménu) + 2 runy DeepSeek → 50 %, ne 25 %.

**Expected commit:** `fix(dashboard): exclude non-search models from domain coverage`

---

## T5 — Limit hloubky fronty (F19)

**Target:** `app/services/queue.py` (`:165`), `tests/test_worker_queue.py`

`status IN ('queued','deferred','leased')`. Test na klienta s 490 deferred
+ fan-out 20 → okno se přeskočí jako `queue_depth_exceeded`.

**Expected commit:** `fix(runs): count deferred and leased items toward queue depth`

---

## T6 — Latence jen z úspěšných runů (F11)

**Target:** `app/services/ops_dashboard.py` (`ops_summary` `:179`,
`prompt_model_comparison_rows` `:534`), popisek v `app/i18n/{en,de}.json`
(`ops.kpi_latency_sub`), `tests/test_ops_dashboard.py`

`AVG(latency_ms) FILTER (WHERE status='success')`; popisek „across
successful runs" / DE ekvivalent.

**Expected commit:** `fix(ops): average latency over successful runs only`

---

## T7 — Sloupec „first position" (F8)

**Target:** `app/i18n/{en,de}.json`, `app/templates/dashboard/index.html`

Přejmenovat na „First mention (character)" / DE, tooltip s vysvětlením,
že jde o pozici ve znacích v odpovědi a že délky odpovědí se mezi
providery liší. Výpočet se nemění.

**Expected commit:** `fix(dashboard): clarify the first-mention column label`

---

## T8 — Runy bez ceny (F6)

**Target:** `app/services/ops_dashboard.py`, `app/routers/ops_dashboard.py`,
`app/templates/ops/index.html`, `app/services/cost.py`
(`client_month_to_date_spend`), `app/services/notifications.py`
(`check_budget_thresholds`), i18n, `tests/test_ops_dashboard.py`,
`tests/test_notifications.py`

1. `unpriced_runs_count` = `count(*) FILTER (WHERE RawResponse.id IS NOT
   NULL AND cost IS NULL)` do `OpsSummary` a do řádků provider / client /
   user / prompt set / prompt / model.
2. UI: pod dlaždicí ceny „+ N runs without a price" s odkazem na
   `/ai-models`; v tabulkách malá poznámka u částky.
3. Budget: `client_month_to_date_spend` vrací i počet; notifikace ho nese
   v payloadu a text říká, že útrata je spodní odhad.
4. Testy: model bez cenové komponenty → suma bez něj + count = počet jeho runů.

**Expected commit:** `feat(ops): show how many runs are missing a price`

---

## T9 — Mezery místo nul + osa position (F7)

**Target:** `app/services/dashboard.py` (`weekly_values`),
`app/routers/dashboard.py` (`WeekPoint`, `dashboard_timeseries`),
`app/templates/dashboard/index.html`, `tests/test_dashboard.py`

1. `WeekPoint.value: float | None`. Pro `own_rate`, `share_of_voice`,
   `position` prázdný týden → `None`; `runs`/`citations` dál 0
   (design decision 8). Docstring endpointu přepsat — dnes slibuje
   „never a gap".
2. Vue: čáru rozdělit na segmenty mezi `null`, plochu jen pod segmenty,
   hover přeskočí `null`; osa position obrácená (1 nahoře) nebo aspoň
   popisek „1 = named first".
3. Test API: týden bez runů vrací `null` pro rate, `0` pro runs.
4. Prohlížeč: ověřit na 640 / 1024 / desktop.

**Expected commit:** `fix(dashboard): leave gaps for weeks without data in rate charts`

---

## T10 — Denní limit v náhledu rozvrhu (F18)

**Target:** `app/routers/schedules.py` (`preview_occurrences`),
`app/templates/schedules/_occurrence_preview.html`, i18n,
`tests/test_schedules.py`

1. Náhled ukáže efektivní limit klienta (`daily_run_limit` nebo
   `SCHEDULER_DEFAULT_DAILY_RUN_LIMIT`) vedle „runs per window".
2. Amber varování, když runs per window > limit, a když součet runů za
   den ze všech aktivních rozvrhů klienta + tento > limit.
3. Test: set 25 × 2 modely × 1 persona, limit 50 → varování se ukáže až
   při součtu s dalším rozvrhem.

**Expected commit:** `feat(schedules): warn when a schedule exceeds the daily run limit`

---

## T11 — Seskupení po oknech (F4)

**Target:** `app/services/schedule_monitor.py` (`history_batches` `:226`,
`schedule_health` `:341`), `app/services/queue.py`
(`_enqueue_one_schedule` `:180`), `tests/test_schedule_monitor.py`,
`tests/test_worker_queue.py`

1. Klíč okna podle design decision 6 v obou funkcích (jeden sdílený výraz,
   ne dvě kopie).
2. `window_batch_id` pro každé okno s fan-outem > 1.
3. Testy: prompt rozvrh 2 modely × 2 persony, 3 okna → 3 řádky historie,
   3 dlaždice; retry zůstává vlastní dlaždicí.

**Done when:** testy projdou a `/schedules` Historie v prohlížeči ukazuje
jedno okno jako jeden řádek.

**Expected commit:** `fix(schedules): group history and health strip by schedule window`

**Checkpoint po T11:** uživatel rozhodne, jestli vlnu 1 mergovat hned
(pak T21 + T23 jen pro vlnu 1 a zbytek na navazující větvi).

---

## T12 — Rozhodnutí vlny 2 (bez kódu)

**Target:** tento soubor (design decisions 12+)

Projít s uživatelem body z design decision 9 a výsledky T0. Každé
rozhodnutí zapsat jako číslovanou design decision s krátkým „proč".
Pro F5 a F16 zároveň **nahlásit migraci** (tabulka, sloupec, typ,
nullable, index) a počkat na souhlas.

**Done when:** každý z F5, F12 krok 2, F13, F14, F16, F17 má zapsané
rozhodnutí „dělat takto" nebo „nedělat, protože".

**Expected commit:** `docs(metrics-audit): record wave 2 decisions`

---

## T13 — Share of voice jako souhrnný poměr (F13)

*Jen pokud T12 rozhodl pro změnu.*

**Target:** `app/services/dashboard.py` (`avg_share_of_voice`,
`weekly_values`), tooltip + i18n, `tests/test_dashboard.py`

Z `output→entities` sečíst `mention_count` klienta a všech entit za
scope (resp. týden), podělit. Runy bez jakékoli zmínky přispějí 0 do
obou součtů. Uložený per-run `share_of_voice` zůstává (evidence).

**Expected commit:** `feat(dashboard): compute share of voice as a pooled ratio`

---

## T14 — Pokrytí vedle position (F14)

**Target:** `app/services/dashboard.py` (`weekly_values` pro position),
`app/routers/dashboard.py`, `app/templates/dashboard/index.html`

`WeekPoint` dostane volitelné `coverage` (runy se zmínkou / analyzované
runy) pro metriku position; tooltip „rank 1.4 · in 12 of 40 answers".

**Expected commit:** `feat(dashboard): show mention coverage next to position`

---

## T15 — Metrika „cited sources" (F17)

*Podle T12.*

**Target:** `app/services/dashboard.py` (`citation_totals`,
`domain_league_rows`, `weekly_values`), router, šablona, i18n, testy

„Cited sources" = `count(DISTINCT (run_id, normalized_domain))`. KPI
dlaždice a řazení tabulky domén přejdou na ni; dnešní počet citací
zůstane jako sekundární údaj.

**Expected commit:** `feat(dashboard): count cited sources per run instead of raw citation rows`

---

## T16 — Tabulka entit (F12)

**Target:** `app/services/dashboard.py` (`entity_league_rows`),
`app/analysis/competitive_visibility.py` (krok 2), testy

1. Krok 1: jmenovatel `run_coverage_pct` = runy, kde je entita v poli
   `entities` (tj. byla v té době sledovaná).
2. Krok 2 (podle T12): `entity_id` ve výstupu, `skill_version` +1,
   seskupení podle `entity_id` s fallbackem na jméno pro staré výsledky.

**Expected commit:** `fix(dashboard): scope competitor coverage to runs where it was tracked`

---

## T17 — Vazba run → queue item (F5)

*Jen po schválení migrace v T12.*

**Target:** nová migrace v `alembic/versions/`, `app/models/run.py`,
`app/worker.py`, `app/services/ops_dashboard.py`, šablona Ops, testy

1. Nullable `runs.queue_item_id` FK na `run_queue.id`, index.
2. Worker ho vyplní při vytvoření Runu.
3. Ops: „success rate" počítá výsledek (poslední run na queue item +
   všechny ruční), vedle toho „attempts". Staré runy bez vazby se berou
   jako samostatné výsledky (poctivě uvedeno v tooltipu).

**Expected commit:** `feat(ops): distinguish final outcomes from retry attempts`

---

## T18 — Gemini thinking tokeny (F15)

*Jen pokud T0 potvrdil.*

**Target:** `app/services/cost.py` (`TokenUsageShape`, `GEMINI_SHAPE`,
`estimate_run_cost`, `run_cost_sql_expr`, `run_token_sql_expr`),
`tests/test_cost.py`

`TokenUsageShape.extra_output_keys` (Gemini: `thoughts_token_count`),
přičíst k výstupu v Python i SQL cestě — obě se staví ze stejného
`TOKEN_USAGE_SHAPES`, aby se nerozešly. Test: Python a SQL dávají stejnou
cenu pro payload s thinking tokeny. Cena starých runů se změní sama
(počítá se při čtení) — uvést v CHANGELOGu.

**Expected commit:** `fix(ops): price Gemini thinking tokens as output`

---

## T19 — Poplatky za web search (F16)

*Jen pokud T0 ukázal podstatný podíl a T12 schválil migraci.*

**Target:** migrace (CHECK constraint `component_type`), `app/models/provider.py`
(`COMPONENT_TYPES`), `app/services/cost.py`, admin cen (CC-3), testy

1. Komponenta `web_search`, jednotka `per_call`.
2. Počet vyhledávání na run: `search_queries` (nebo usage pole providera,
   kde existuje — ověřit v T0).
3. Cenový vzorec respektuje `unit` (dnes dělí vše 1M).
4. Ceny vyplní uživatel v `/ai-models`.

**Expected commit:** `feat(ops): include per-search fees in run cost`

---

## T20 — Backfill analýz (F10)

*Jen pokud T0 našel dotčené klienty; jinak ❌ a jen poznámka do T22.*

**Target:** `app/services/dashboard.py` (oba base query), nový admin
příkaz / skript, testy

1. **Nejdřív čtení:** base query vybírají jen poslední `analysis_results`
   na (raw_response, skill) — dnes předpokládají jeden řádek a backfill by
   bez toho počítal dvakrát. Samostatný commit, bez změny čísel.
2. Příkaz, který přes uložené `rendered_text` + `citations` spustí aktivní
   skilly a zapíše **nové** řádky (design decision 2). Spouští uživatel.

**Expected commits:**
`refactor(dashboard): read only the latest analysis result per response`
`feat(clients): add an analysis backfill command`

---

## T21 — CHANGELOG

**Target:** `CHANGELOG.md`

Pod `## [Unreleased]` odrážky do `### Fixed` / `### Changed` / `### Added`
za každý dokončený task s viditelnou změnou (T1–T20). Změna ceny starých
runů (T18, T19) a změna definice SoV / citací (T13, T15) výslovně.

**Expected commit:** `docs(changelog): record metrics audit fixes`

---

## T22 — Definice metrik v REQUIREMENTS.md

**Target:** `docs/REQUIREMENTS.md`

Krátká sekce „Definice metrik": runs, cited sources / citace,
own-domain rate (vč. vyřazení non-search modelů), share of voice,
position + pokrytí, cena (vč. toho, co cena neobsahuje). Ukázat diff,
nic tiše nepřepisovat (`AI_INSTRUCTIONS.md` §3).

**Expected commit:** `docs(requirements): define dashboard and ops metrics`

---

## T23 — Nasazení a end-of-branch docs

**Target:** produkce; `docs/TASKS_METRICS_AUDIT.md`,
`docs/PROMPTS_METRICS_AUDIT.md`, `docs/00_INDEX.md`

1. Merge, bump verze, přesun CHANGELOGu, tag — **uživatel**.
2. `docs/DEPLOYMENT.md` kapitoly 1–5. Pokud vznikly migrace (T17, T19),
   rollback je DB-aware podle runbooku.
3. Ověření po nasazení: vytvořit testovací rozvrh s časem dnes v minulosti
   → nespustí se (T1); `/ops` ukazuje počet runů bez ceny (T8); týdenní
   graf má mezery (T9).
4. `## Status: ...` v obou souborech, řádek v `docs/00_INDEX.md`.

**Expected commit:** `docs(metrics-audit): record deploy and close the branch`

---

## Co tahle větev vědomě nedělá

- **Nepřepisuje historii.** Chybové runy z retry, stará historie fronty
  ani uložené výsledky analýz se nemění (NFR-6).
- **Nemění, co se posílá providerům.** Parametry adaptérů jsou téma
  briefingu „Provider parameters", ne téhle větve.
- **Nepřidává průřezový review jako proces** — to je doporučení
  z konverzace 2026-09-25, ne kód.
- **Neimplementuje tasky vyvrácené v T0 ani rozhodnuté jako „nedělat"
  v T12.**
