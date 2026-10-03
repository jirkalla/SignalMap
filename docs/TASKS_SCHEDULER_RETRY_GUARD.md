# SignalMap — Tasks: Scheduler Retry Guard (v1.4.1)

## v1.0 | Říjen 2026
## Branch: feature/signalmap-scheduler-retry-guard
## Task ID prefix: RG
## Cílová verze: v1.4.1 (viz design decision 1 — k potvrzení)

Status: navrženo 2026-10-03, po incidentu klienta DPE (viz „Proč"). Sedm
úkolů, **bez migrace**. Staví na `feature/signalmap-scheduler-ops` (v1.4.0):
využívá `daily_quota_usage`, `skipped_summary`, `SKIP_REASONS` a banner
přeskočených runů z SO-T5.

**Goal:** (1) plán, který se nevejde do denního limitu klienta, se pozná
**před** tím, než přeskočí runy; (2) přeskočené nebo chybné položky se dají
v History **vybrat** a znovu zařadit bez ručního SQL; (3) před zařazením
je vidět **náhled** — kolik se zařadí, kolik se přeskočí, vliv na limit
a odhad ceny.

---

## Proč

2026-10-02 založil Philip klienta DPE s plánem na celý prompt set (230
runů za okno). Klient neměl vlastní `daily_run_limit`, platil výchozí
`SCHEDULER_DEFAULT_DAILY_RUN_LIMIT=50` (`app/config.py:95`). Worker
zpracoval 50 položek a 180 jich označil `skipped` / `quota_exceeded`
(`app/worker.py:273`). Stav je terminální, sám se nespustí znovu. Nikde
v UI nebylo před uložením plánu vidět, že se nevejde; a po události šlo
položky znovu zařadit jen po jedné (tlačítko Retry), nebo hromadně jen
`error` — `skipped` ne. Opraveno ručním SQL na produkci (vložení 180
`run_queue` řádků s `retry_of_id`).

Tři mezery, tři vrstvy řešení:

| Vrstva | Mezera | Úkoly |
|---|---|---|
| Prevence | plán se nevejde do limitu a nikdo to neřekne | T1, T2 |
| Výběr | hromadný retry jen podle textového filtru, jen `error` | T3, T4 |
| Potvrzení | `confirm()` s textem bez čísel | T5 |

---

## Předpoklad

`feature/signalmap-scheduler-ops` je mergnutá do `master` a **v1.4.0 je
nasazená** (T5 z ní dodává `daily_quota_usage`, `SKIP_REASONS`, banner).
Tahle větev vzniká z aktuálního `master` po tomto nasazení.

---

## Výchozí stav (kód k 2026-10-03)

**Kvóta** — `check_daily_quota` / `daily_quota_usage`
(`app/services/run_execution.py:60`, `:89`): počítá `Run` řádky klienta
s `started_at` v posledních **24 h (klouzavé okno, ne kalendářní den)**,
všechny stavy včetně `error` a `pending`. Limit = `clients.daily_run_limit`
nebo `scheduler_default_daily_run_limit` (50). Fronta se do počtu **nezapočítává**,
dokud z ní worker `Run` nevytvoří.

**Náhled plánu** — `GET /schedules/preview` (`app/routers/schedules.py:756`)
ukazuje dalších pět výskytů, `run_count_per_window`
(= prompty × modely × persony), odhad ceny (`_estimate_window_cost`,
`average_historical_cost` v `app/services/cost.py:544`) a překryvy.
**Nezná limit klienta.** `create_schedule` (`:492`) limit nekontroluje.

**Retry** — `create_retry` (`app/services/queue.py:419`) vloží nový řádek
s `retry_of_id`, `scheduled_for=now`, `status='queued'`; `RETRYABLE_STATUSES
= ("error", "skipped", "cancelled")`. `retry_all_errors` (`:468`) bere jen
`status='error'` podle textového filtru `q` a **nekontroluje, jestli už
retry existuje** — dvojí kliknutí zařadí a zaplatí položku dvakrát.
Route `POST /schedules/queue/retry-errors` (`schedules.py:1277`),
tlačítko jen při `status == 'errors'` (`schedules/index.html:365`).

**History** — `history_batches` (`schedule_monitor.py:352`) stránkuje
**dávky** (`batch_id`), ne položky; filtr `status` je all/errors/skipped,
`q` hledá v textu promptu a názvu klienta. Položky dávky se vykreslují
v rozbalené tabulce (`index.html:~395`), 225řádková dávka je jedna
stránková položka. `skip_reason` už je přeložený (`skip_reason_labels`).

**`SKIP_REASONS`** (`app/models/schedule.py:129`): `grace_expired`,
`inactive_prompt`, `inactive_model`, `quota_exceeded`, `dry_run`,
`worker_down`, `queue_depth_exceeded`.

**Index** — `run_queue.retry_of_id` je cizí klíč **bez indexu**
(migrace `0031`).

---

## Design decisions

1. **Verze — k potvrzení před T7.** Zadání říká v1.4.1 (PATCH). Podle
   `docs/DEPLOYMENT.md` §0 je ale „uživatel uvidí něco nového" → **MINOR**,
   a `docs/ROADMAP.md` už rezervuje v1.5.0 pro vydání 3 (market-locale-names)
   a v1.6.0 pro vydání 4. Tři možnosti: (a) v1.4.1 podle zadání, vědomá
   odchylka od tabulky v §0 (důvod: oprava provozního incidentu); (b) v1.5.0
   a posunout vydání 3 a 4; (c) rozdělit — prevence T1–T2 jako v1.4.1,
   výběr a potvrzení T3–T5 do vydání 4. Plán je napsaný pro (a); rozhodnutí
   a bump verze dělá uživatel (AI_INSTRUCTIONS §4). Před T7 se rozhodne a
   zapíše sem.
2. **Prevence je upozornění, nikdy blokace.** Uložení plánu se nezakáže —
   limit se dá zvýšit později, plán může být záměrně větší než limit
   (rozložený přes víc dní jinými plány). Upozornění říká číslo a nabízí
   odkaz na úpravu limitu klienta.
3. **Projektované zatížení klienta za 24 h.** Každý aktivní plán se
   spustí nejvýše jednou za 24 h (denní/týdenní/měsíční, jeden
   `time_of_day`), takže zatížení = součet `prompty × modely × persony`
   přes **všechny aktivní plány klienta** + (v náhledu) plán, který se právě
   edituje/vytváří. Prompty podle `_target_prompts` (prompt set = aktivní
   prompty v setu, žije — roste s množinou). Konzervativní: překryvy se
   neodečítají. Ruční běhy a retry se do projekce nepočítají (není co
   předvídat), ale do využití kvóty ano.
4. **Dvě místa upozornění.** (a) Živý náhled ve formuláři plánu
   (`_occurrence_preview.html`) — upozornění vidí ten, kdo plán tvoří.
   (b) Trvalý štítek v hlavičce skupiny klienta na `/schedules` — chytí
   i pozdější změny (zvětšený prompt set, snížený limit), které formulář
   nikdy neuvidí. Obě čtou **jednu** službu (`app/services/schedule_load.py`),
   stejný princip jako `daily_quota_usage` (zobrazení se nemůže rozejít s tím,
   co se vynucuje).
5. **Retryovatelné položky — jedna definice v kódu.**
   `RETRYABLE_SKIP_REASONS = ("quota_exceeded", "grace_expired",
   "worker_down", "queue_depth_exceeded")` vedle `SKIP_REASONS`.
   **Ne** `inactive_prompt`, `inactive_model` (retry by se zase přeskočil,
   dokud někdo prompt/model neaktivuje) a `dry_run` (záměr). `error` a
   `cancelled` vždy. `grace_expired` je retryovatelné, ale potvrzení u něj
   výslovně upozorní, že se evidence uloží pod dnešním datem, ne pod
   původním oknem (stejně se chová dnešní tlačítko Retry).
6. **Idempotence: retryovat lze jen konec řetězu.** Položka, která už má
   potomka (`EXISTS run_queue r WHERE r.retry_of_id = item.id`), není
   retryovatelná — zobrazí se „znovu zařazeno" s odkazem na potomka. Pokud
   potomek selhal, retryuje se **potomek** (je to nový řádek v History),
   ne původní. Tím zmizí dvojí platba z dvojího kliknutí. Platí pro
   všechny cesty včetně jednotlivého tlačítka Retry.
7. **Souběh dvou potvrzení.** Provedení retry zamkne vybrané řádky
   (`SELECT … FOR UPDATE`) a až pak ověří, že nemají potomka — dvě
   současná potvrzení téhož výběru nemohou obě vložit.
8. **Výběr je jen to, co je na obrazovce.** Žádné „vybrat všech N
   odpovídajících" mimo stránku — skrytý filtr byl původní problém.
   Checkboxy jsou u položek v rozbalené dávce; tlačítko „Vybrat
   retryovatelné" u dávky a nahoře „…na této stránce". Strop 500 položek
   na jeden požadavek (`MAX_RETRY_SELECTION`); při překročení srozumitelná
   chyba „vyber méně" (AppError). Výběr zůstává jen v prohlížeči (vanilla
   JS, delegovaný listener jako `data-confirm` v `base.html`, žádný
   framework).
9. **Dvoukrokové potvrzení bez uloženého stavu na serveru.**
   `POST /schedules/queue/retry/preview` (vybraná ID) → stránka s náhledem;
   `POST /schedules/queue/retry/confirm` (stejná ID jako hidden inputy) →
   provede. Confirm vše **přepočítá znovu** — náhledu se nevěří (mezi
   kroky se mohla změnit kvóta nebo stav položek).
10. **Náhled je kvóta-vědomý.** Pro každého dotčeného klienta:
    `využito` (`daily_quota_usage`) + `rozpracováno` (jeho položky
    `queued`/`leased`/`deferred` — worker je ještě nepočítá) + `zařazuji`
    vs. `limit`. Když součet překročí limit, náhled to řekne předem:
    „X z N by se zase přeskočilo", s volbami: zvýšit limit (odkaz na
    `/clients/{id}/edit`), nebo „zařadit jen prvních K, co se vejde".
    Bez této kontroly by retry po nízkém limitu jen produkoval další
    `skipped` řádky.
11. **Odhad ceny znovu používá `average_historical_cost`** (design
    decisions 29/32/34 z vydání 1 plánovače) — součet přes (prompt,
    model) kombinace, bez historie = vynecháno a zobrazeno „X z N kombinací
    má historii", stejně jako `_estimate_window_cost`. Společná pomocná
    funkce, ne kopie.
12. **Původní tlačítko „Retry all errors" a route
    `POST /schedules/queue/retry-errors` se ruší**, včetně
    `retry_all_errors`, překladů `schedules.history_retry_all_*` a testu.
    Nahrazuje je výběr + náhled (chyby = filtr „Errors" → „Vybrat
    retryovatelné" → „Retry selected"). Je to interní formulářová akce
    bez vnějších volajících; podle §0 by „zrušení URL" bylo MAJOR, ale
    tahle URL se nikdy nenavigovala — vědomě zapsat do CHANGELOG
    (`### Removed`).
13. **Filtr podle důvodu v History.** Ke stávajícím `status` filtrům
    (all/errors/skipped) přibude `reason` (jedna hodnota z `SKIP_REASONS`
    nebo prázdné), který jde jen s `status=skipped`; chipy s počtem za
    24 h. Zdroj čísel: `skipped_summary` (SO-T5).
14. **Bez migrace.** `retry_of_id` zůstává bez indexu — `EXISTS` dotaz
    na potomky běží nad `run_queue` jen pro ID aktuální stránky (≤ 500).
    V T7 změřit na produkci (`EXPLAIN ANALYZE`); když > 200 ms, index
    jde jako samostatná migrace v další větvi, ne tady.
15. **Evidence se nikdy nepřepisuje.** Retry = nový řádek (NFR-6), původní
    `skipped`/`error` zůstává. Kdo retry spustil: do logu (`user_id`,
    počet, klienti); sloupec pro to nepřidáváme (bez migrace).
16. **Prevence nemění `check_daily_quota`.** Vynucení v workeru zůstává
    jediná pravda; T1–T2 jen zobrazují projekci.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Služba projektovaného zatížení + upozornění v náhledu plánu | ⏳ |
| T2 | Trvalý štítek přetížení klienta na `/schedules` | ⏳ |
| T3 | Retry služba: idempotence, retryovatelnost, náhled | ⏳ |
| T4 | History: výběr položek, filtr důvodu, lišta akce | ⏳ |
| T5 | Potvrzení s náhledem (kvóta, cena) + zrušení „Retry all errors" | ⏳ |
| T6 | Dokumentace + CHANGELOG | ⏳ |
| T7 | Nasazení (v1.4.1) a měření | ⏳ |

---

## T1 — Služba projektovaného zatížení + upozornění v náhledu plánu

**Target:** nový `app/services/schedule_load.py`, `app/routers/schedules.py`
(`preview_occurrences`), `app/templates/schedules/_occurrence_preview.html`,
`app/i18n/de.json`, `app/i18n/en.json`, `tests/test_schedule_load.py`,
`tests/test_schedules.py`

1. `projected_daily_runs(db, *, client_id, exclude_schedule_id=None,
   extra_runs=0) -> ProjectedLoad(runs, limit, over_by)` podle design
   decision 3. Počet promptů z `queue._target_prompts` (neduplikovat
   logiku), limit z `daily_quota_usage`-kompatibilní cesty (jedna definice
   limitu: klient → default z configu).
2. `preview_occurrences`: pokud `run_count_per_window` + zatížení ostatních
   plánů klienta > limit, přidat do kontextu `quota_warning`; partial ukáže
   žluté upozornění s čísly („Tento plán vytvoří 230 runů za 24 h, limit
   klienta DPE je 50 — 180 by se přeskočilo") a odkazem na
   `/clients/{id}/edit`. Nic nezakazuje (design decision 2).
3. Překlady DE/EN (`schedules.preview_quota_warning` …), test pokrytí
   překladů musí projít.
4. Testy: plán v limitu (bez upozornění); plán nad limitem; dva plány
   téhož klienta, jejichž součet přesáhne limit; editace plánu nezapočítá
   sám sebe dvakrát (`exclude_schedule_id`); prompt set s neaktivním
   promptem se nepočítá; klient s `daily_run_limit=NULL` použije default.
5. Prohlížeč (~375 / ~768 px / desktop): formulář nového plánu pro
   testovacího klienta s dočasně nízkým limitem (vrátit zpět).

**Done when:** testy projdou, screenshoty ukázané, bez migrace.

**Expected commit:** `feat(runs): warn when a schedule exceeds the client's daily limit`

---

## T2 — Trvalý štítek přetížení klienta na `/schedules`

**Target:** `app/routers/schedules.py` (`_grouped_schedules_by_client`,
`schedules_monitor`), `app/templates/schedules/index.html`,
`app/i18n/*.json`, `tests/test_schedules.py`

1. Pro každou skupinu klienta v pohledu „Schedules" spočítat
   `projected_daily_runs` **jedním průchodem** pro všechny klienty (žádné
   N+1 dotazy na prompty — pozor na `_target_prompts` v cyklu; seskupit
   dotazy po prompt setech).
2. Hlavička skupiny: štítek „Plánováno 230 / limit 50 za 24 h" žlutě při
   překročení, s odkazem na úpravu limitu. Bez překročení se nezobrazí nic
   (žádný šum).
3. Testy: skupina bez překročení bez štítku; s překročením se štítkem a
   čísly; počet SQL dotazů při 10 klientech neroste lineárně (stávající
   vzor v `tests/` pro počítání dotazů, pokud existuje; jinak aserce na
   jednu agregační funkci).
4. Prohlížeč na třech šířkách.

**Done when:** testy projdou, screenshoty.

**Expected commit:** `feat(runs): flag clients whose schedules exceed the daily limit`

---

## T3 — Retry služba: idempotence, retryovatelnost, náhled

**Target:** `app/models/schedule.py` (`RETRYABLE_SKIP_REASONS`),
`app/services/queue.py`, `app/services/schedule_monitor.py`
(anotace „znovu zařazeno" v `history_batches`), `app/services/cost.py`
(společná funkce pro odhad), `tests/test_worker_queue.py`,
`tests/test_schedule_monitor.py`

1. `RETRYABLE_SKIP_REASONS` podle design decision 5; test, že každá hodnota
   je v `SKIP_REASONS`.
2. `is_retryable(item, *, has_child)`: status + důvod + design decision 6.
3. `build_retry_plan(db, *, item_ids, now) -> RetryPlan`: načte položky,
   rozdělí na `to_queue`, `already_retried` (s ID potomka),
   `not_retryable` (s důvodem), pro každého klienta spočítá
   `využito / rozpracováno / zařazuji / limit` (design decision 10) a
   odhad ceny (decision 11). Čistě čtecí.
4. `execute_retry(db, *, item_ids, now, user_id, max_fit_per_client=None)
   -> RetryResult`: v jedné transakci `FOR UPDATE` na vybrané řádky
   (decision 7), znovu `build_retry_plan`, pak `create_retry` pro
   `to_queue`; `max_fit_per_client` omezí počet na klienta (volba „jen
   co se vejde"). Strop `MAX_RETRY_SELECTION=500` → `ValueError`.
5. `create_retry` (jednotlivé tlačítko) projde stejnou kontrolou potomka.
6. `history_batches` označí položky, které mají potomka
   (`retried_by_id`) jedním dotazem pro ID stránky.
7. Testy: skipped `quota_exceeded` je retryovatelné; `inactive_prompt` /
   `dry_run` ne; dvojí `execute_retry` téhož výběru vloží položky jen
   jednou; potomek, který selhal, je retryovatelný a původní ne;
   plán přes dva klienty počítá kvótu každého zvlášť; fronta
   (`queued`/`leased`/`deferred`) se do `rozpracováno` započítá; `FOR UPDATE`
   souběh (dvě session) nevloží duplicitu; strop 500.

**Done when:** testy projdou (nová služba bez UI), bez migrace.

**Expected commit:** `feat(runs): make queue retry idempotent and quota-aware`

---

## T4 — History: výběr položek, filtr důvodu, lišta akce

**Target:** `app/routers/schedules.py` (`schedules_monitor`: parametr
`reason`), `app/services/schedule_monitor.py` (filtr důvodu v
`history_batches`), `app/templates/schedules/index.html`,
`app/templates/base.html` (nebo malý partial pro JS výběru),
`app/i18n/*.json`, `tests/test_schedule_monitor.py`,
`tests/test_schedules.py`

1. Parametr `reason` (design decision 13) — platný jen se
   `status=skipped`, neplatná hodnota = ignorovat; chipy důvodů s počty
   z `skipped_summary`.
2. Tabulka položek v dávce: checkbox ve sloupci vlevo u retryovatelných
   položek; u položek s potomkem místo checkboxu „znovu zařazeno →"
   s odkazem; u neretryovatelných (`inactive_*`, `dry_run`, `done`)
   nic. Jednotlivé tlačítko Retry zůstává.
3. U dávky tlačítko „Vybrat retryovatelné (N)"; nahoře „Vybrat
   retryovatelné na této stránce". Lišta akce (zobrazí se při výběru):
   „Vybráno N" + „Zrušit výběr" + „Retry selected…" (odešle ID na
   `…/retry/preview`, T5). Vanilla JS, delegovaný listener, přístupné
   (popisky checkboxů, `aria-live` pro počet).
4. Mobil: tabulka dávky už má horizontální scroll; lišta akce na mobilu
   přilepená dole v toku (ne `position: fixed` mimo obsah) a tlačítka
   zalamovaná.
5. Testy: HTML obsahuje checkbox jen u retryovatelných; retried položka
   má odkaz místo checkboxu; filtr `reason` zúží dávky; `reason` bez
   `status=skipped` se ignoruje.
6. Prohlížeč na třech šířkách — výběr, odznačení, výběr celé dávky.

**Done when:** testy projdou, screenshoty, výběr funguje bez JS chyb
v konzoli.

**Expected commit:** `feat(runs): select history items to retry`

---

## T5 — Potvrzení s náhledem (kvóta, cena) + zrušení „Retry all errors"

**Target:** `app/routers/schedules.py` (dvě nové route, odstranění
`retry_all_errors_route`), `app/services/queue.py` (odstranění
`retry_all_errors`), nový `app/templates/schedules/retry_confirm.html`,
`app/i18n/*.json` (nové klíče, odstranění `history_retry_all_*`),
`tests/test_schedules.py`, `tests/test_worker_queue.py`

1. `POST /schedules/queue/retry/preview` (ID z formuláře) → stránka s
   `build_retry_plan`: počet k zařazení, rozpad podle důvodu, počet
   „už znovu zařazeno" a „nelze zařadit", pro každého klienta řádek
   „využito X · rozpracováno Y · zařazuji Z · limit L", odhad ceny a
   „X z N kombinací má historii". Hidden inputy s ID. U `grace_expired`
   upozornění na datum evidence (decision 5).
2. Když klient překračuje limit: červený blok „K z Z by se zase
   přeskočilo" a tři volby — „Zvýšit limit" (odkaz na
   `/clients/{id}/edit`), „Zařadit jen prvních K" (potvrzení s
   `max_fit_per_client`), „Zrušit". Tlačítko „Zařadit vše" zůstává, ale
   je vedle varování, ne místo něj.
3. `POST /schedules/queue/retry/confirm` → `execute_retry`, přesměrování
   `303` na `/schedules?view=queue` (nové položky jsou `queued`), flash
   s počtem. HTMX i plain POST stejně jako `trigger_run`.
4. Odstranit `retry_all_errors`, její route, tlačítko a překlady
   (decision 12); odpovídající test nahradit testem nové cesty.
5. Docstringy route (FastAPI `/docs`), `Form(..., description=...)`,
   chyby přes `AppError` (`retry_selection_empty`,
   `retry_selection_too_large`, překlady `errors.*`).
6. Testy: preview nic nezapíše do DB; confirm zařadí přesně
   `to_queue`; dvojí odeslání confirm nezařadí podruhé; přes limit →
   varování a `max_fit_per_client` zařadí jen K; prázdný výběr a výběr
   > 500 → AppError s překladem; role bez editor/admin dostane 403.
7. Prohlížeč na třech šířkách: preview s a bez překročení limitu,
   confirm, výsledek ve frontě. **Platí omezení z AI_INSTRUCTIONS:
   žádné placené runy navíc** — `SCHEDULER_DRY_RUN=true` nebo
   FakeAdapter, testovací klient Skoda Auto.

**Done when:** testy projdou, screenshoty, starý bulk retry neexistuje.

**Expected commit:** `feat(runs): confirm bulk retry with a quota and cost preview`

---

## T6 — Dokumentace + CHANGELOG

**Target:** `docs/REQUIREMENTS.md` (jen pokud popisuje retry/limit),
`docs/TASKS_SCHEDULER.md` (poznámka u T7 — původní bulk retry nahrazen),
`CHANGELOG.md`, `README.md` (pokud zmiňuje bulk retry), tento soubor

1. `CHANGELOG.md` pod `## [Unreleased]`: `### Added` — upozornění na
   překročení limitu v náhledu plánu a štítek na `/schedules`; výběr
   položek v History a hromadné zařazení s náhledem (počty, vliv na limit,
   odhad ceny); filtr přeskočených podle důvodu. `### Changed` —
   znovu zařazení je idempotentní (položka se neplatí dvakrát).
   `### Removed` — tlačítko „Retry all errors".
2. Zapsat rozhodnutí o verzi (decision 1) a výsledek měření `EXISTS`
   (decision 14), až bude.
3. **Ukázat diff všech dokumentů před zápisem.** Bez potvrzení uživatele
   nic neoznačovat jako hotové.

**Done when:** diff schválen.

**Expected commit:** `docs(docs): document the retry guard and bulk retry preview`

---

## T7 — Nasazení (v1.4.1) a měření

**Target:** `docs/DEPLOYMENT.md` (postup), `docs/00_INDEX.md`,
`## Status` v tomto souboru a v `PROMPTS_SCHEDULER_RETRY_GUARD.md`

1. Rozhodnout verzi (decision 1). Bump, přesun `[Unreleased]`, tag a push
   dělá uživatel.
2. Postup `docs/DEPLOYMENT.md` kap. 0–5; **bez migrace**, bez nové env
   proměnné. Prod: `/opt/signalmap`, `--scale worker=4` / `WORKER_REPLICAS=4`
   podle toho, co je v té době platné.
3. Ověření na produkci: (a) formulář plánu pro klienta s nízkým limitem
   ukáže upozornění; (b) `/schedules` štítek u přetíženého klienta;
   (c) History → filtr Skipped → výběr → náhled → **zrušit** (na produkci
   nic nezařazovat jen kvůli testu); (d) skutečný retry jen pokud nějaké
   přeskočené položky existují a uživatel to chce.
4. Měření `EXPLAIN ANALYZE` dotazu na potomky pro 500 ID (decision 14);
   výsledek zapsat sem.
5. End-of-branch docs: `## Status: ...` v obou souborech a řádek v
   `docs/00_INDEX.md` (`TASKS_VERSIONING.md` decision 20).

**Done when:** nasazeno, ověřeno, Status doplněn.

**Expected commit:** `docs(docs): record the retry guard deploy and close the branch`

---

## Co tohle vydání vědomě nedělá

- **Automatické znovuspuštění přeskočených při novém dni / zvýšení
  limitu** — mohlo by neočekávaně utratit peníze; retry zůstává vědomá
  akce uživatele s náhledem. Případně jako samostatná funkce později.
- **„Vybrat vše odpovídající" přes všechny stránky a filtry** — design
  decision 8.
- **Index na `run_queue.retry_of_id`** — jen pokud ho měření v T7
  ospravedlní (samostatná migrace).
- **Sloupec „kdo retry spustil"** — vyžaduje migraci; do logu stačí.
- **Změna chování `check_daily_quota` / klouzavého 24h okna** — mimo rozsah.
- **Circuit breaker, souběžnost podle providera, seskupení pokusů v Runs**
  — viz `docs/TASKS_SCHEDULER_OPS.md` „Co tohle vydání vědomě nedělá".
- **Změna výchozího limitu 50** — samostatné produktové rozhodnutí
  (nastavuje se per klient).
