# SignalMap — Tasks: Scheduler

## v1.1 | Září 2026
## Branch: feature/signalmap-scheduler
## Task ID prefix: SCH

Status: navrženo v konverzaci (2026-09-16), čeká na vlastní branch.

**v1.1 (2026-09-18)** — revize po zpětné vazbě od Philipa (mail 2026-09-18:
"standard should be let it run 3 days in a row, then once a week or once a
month" a požadavek na srovnávací běh proti peecu). Přibyly design decisions
31–35, task T5b, tři sloupce ukončení na `run_schedules`, `persona_ids`
místo `persona_id`, dva sloupce na `clients` a rozšíření indexu z migrace
0024. Decision 24 tím mění závěr, ne důvody. Realizuje
`docs/ROADMAP.md` §5 (Scheduler) — automatické, opakované spouštění runů
(`FR-9`, ve fázi 1 explicitně mimo scope).

Obě prerekvizity, které si roadmapa sama stanovila, jsou splněné: #1 auth
(PR #8, smergnuto 2026-09-12) a #4 ops dashboard (PR #13) — takže je vidět,
kdo runy spouští a co stojí, dřív než je začne spouštět stroj.
`app/services/ops_dashboard.py` už pseudo-uživatele "Scheduler"
(`trigger_type='scheduled'` + `triggered_by_user_id IS NULL`) umí agregovat;
tahle větev ho poprvé naplní reálnými daty.

**Poznámka k pořadí vůči deploy hardeningu (#2/#3):** plánovač potřebuje
nepřetržitě běžící stroj. Do doby, než appka poběží na serveru, poběží na
vývojářově PC s vypnutým uspáváním — proto je v návrhu heartbeat a stavový
pruh "Plánovač neodpovídá" (design decision 19): monitoring, který při
vypnutém stroji vypadá stejně jako prázdná fronta, aktivně lže.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Tři oddělené vrstvy, `Run` se nerozšiřuje**: pravidlo opakování
   (`run_schedules`) → konkrétní výskyt (`run_queue`) → evidence (`Run` +
   `RawResponse` + `Citation`). Stejný rozpad má Airflow (DAG → DagRun →
   TaskInstance) i GitHub Actions (workflow → workflow_run → job). Míchat
   plán do `Run` by znamenalo psát plánovací sloupce do řádku, který je
   zároveň evidencí — appka má dnes zdokumentovaný dvoufázový životní cyklus
   runu (`app/models/run.py`) a ten zůstává beze změny: `Run` vzniká až při
   skutečném spuštění.
2. **Fronta v Postgresu přes `FOR UPDATE SKIP LOCKED`**, žádný Celery, Redis
   ani jiný broker. Stejný vzor jako Oban (Elixir), GoodJob a SolidQueue
   (od Rails 8 výchozí, dřív Redis), River (Go), procrastinate (Python).
   Drží NFR-8 portabilitu — appka pořád potřebuje jen Postgres a Python.
3. **Worker je samostatná Compose služba ze STEJNÉHO image**, spuštěná
   `command: ["python", "-m", "app.worker"]`. Nesmí spouštět
   `alembic upgrade head` — to dělá `CMD` v `dockerfile` pro službu `app` a
   dva souběžné `alembic upgrade` proti jedné databázi je závod o migrační
   zámek. `depends_on: app: condition: service_healthy` zaručí, že worker
   startuje až po doběhnutí migrací.
4. **Exekuce runu se vytáhne do `app/services/run_execution.py`** a volá ji
   jak HTTP router, tak worker — žádná duplikace. Dnes je celá (adapter →
   `Run`/`RawResponse`/`Citation`/`SearchQuery` → analysis skills) v těle
   `trigger_run` (`app/routers/runs.py`, ~200 řádků).
5. **Ruční run zůstává v této větvi synchronní** (varianta C z konverzace).
   `run_queue` má `schedule_id` nullable a sloupec `source`
   (`'schedule'`/`'manual'`/`'batch'`), takže ruční položky umí přijmout —
   router je zatím nezakládá. Přepnutí ručního runu na frontu (stav
   "ve frontě/běží" + HTMX polling na detailu runu) je samostatný pozdější
   úkol, ne součást téhle větve: měnit zároveň plánování i tok, který
   kolegové denně používají, jsou dvě rizika v jedné dávce.
6. **Rozvrh míří na lineage promptu (`root_prompt_id`), ne na konkrétní
   verzi.** Editace promptu vytváří nový řádek s `version + 1`
   (`app/routers/prompts.py`); rozvrh napíchnutý na `prompt_id` by navždy
   spouštěl starý text. Konkrétní aktuální verzi resolvuje ticker při
   zařazení do fronty (stejný `WHERE id = root_id OR root_prompt_id =
   root_id` vzor jako `app/services/export.py`) a do fronty ukládá už
   konkrétní `prompt_id`, aby evidence zůstala přesná.
7. **Časová zóna: ukládá se pravidlo v lokálním čase, ne okamžik v UTC.**
   `time_of_day` (TIME) + `days_of_week` + `timezone` (IANA identifikátor,
   nikdy offset — offset se dvakrát ročně mění). `next_run_at` (TIMESTAMPTZ)
   je pouze **odvozená cache** pro indexovaný `WHERE next_run_at <= now()`.
   Důsledek, ověřený v kontejneru: 6:00 Europe/Prague = 04:00 UTC v létě,
   05:00 UTC v zimě — uživatel vidí vždy 6:00 a vždy to tak i proběhne.
8. **DST hrany jsou pojmenované rozhodnutí, ne emergentní chování:**
   - dvojznačný čas (podzim, 02:00–02:59 nastane dvakrát) → **`fold=0`**,
     tedy první výskyt. Bez toho by vznikly dva různé `scheduled_for`, tedy
     dva legitimní řádky a dvakrát zaplacený run — unique constraint
     (decision 12) by to nezachytil.
   - neexistující čas (jaro, hodina se přeskočí) → první platný okamžik po
     skoku. Ověřeno: Python nevyhodí výjimku, tiše posune; musí to být
     zapsané chování a vysvětlené v UI.
   - měsíční rozvrh na 31. → ořez na poslední den měsíce (únor 28./29.),
     nikoli přeskočení měsíce.
9. **`next_run_at` se počítá z termínu okna, NIKDY z `now()`.** Jinak by
   každý run rozvrh o pár minut posunul a za rok by "6:00" byla půl osmé.
10. **`compute_next_run_at(schedule, *, after: datetime)` je čistá funkce** —
    žádné `now()` uvnitř, žádná DB. Jediný způsob, jak tabulkově otestovat
    chování na jaře/na podzim, aniž bys čekal půl roku.
11. **Zmeškaná okna se NEDOHÁNĚJÍ** (Airflow `catchup=False`, k8s CronJob
    `startingDeadlineSeconds`). Lhůta `SCHEDULER_GRACE_PERIOD_MINUTES`
    (default 360 = 6 h): okno po termínu do lhůty se vykoná pozdě s původním
    `scheduled_for`, starší se označí `skipped`. Kontroluje se **na obou
    místech** — při zařazení (worker byl mimo provoz, položka vůbec
    nevznikla) i při výběru z fronty (worker běžel, ale nestíhal). Dva
    důvody: (a) dohánění po víkendovém výpadku vygeneruje neplánovanou dávku
    placených volání; (b) pondělní run vykonaný ve středu není opožděný
    pondělní run, je to středeční run se špatným datem — a evidence se
    nikdy nepřepisuje (NFR-6), takže by lhala natrvalo.
12. **Idempotence zařazení: unikátní klíč na řádku fronty.** Ticker
    může běžet dvakrát, spadnout mezi insertem a přepočtem `next_run_at`,
    restartovat se — druhý insert neprojde. Jediná spolehlivá obrana proti
    "zaplatili jsme run dvakrát" na straně plánování.
    **Upřesněno ve v1.1:** klíč je
    `(schedule_id, scheduled_for, prompt_id, model_id, persona_id)`, ne jen
    dvojice. Se set-level rozvrhem (decision 32) a multi-select modely i
    personami (decision 34) připadá na jedno okno N položek, ne jedna —
    dvousloupcový klíč by je vzájemně vyřadil a z 225 runů by prošel jeden.
13. **Idempotence vykonání: `run_id` se zapíše na řádek fronty PŘED voláním
    adaptéru a commitne.** Položka, která už `run_id` má, se nikdy nevolá
    znovu — místo toho ji převezme rekonciliace: `Run` ve stavu `pending`
    starší než 30 minut se označí `error` s `error_message` ve smyslu
    "worker interrupted; provider call outcome unknown". Řeší to případ
    "worker zabit uprostřed 15sekundového volání providera": lease vyprší,
    položka by se jinak vzala znovu a run by se zaplatil dvakrát. Skutečné
    exactly-once by vyžadovalo idempotency key na straně providera (žádný
    ze tří ho pro generování nemá) nebo durable-execution runtime typu
    Temporal — pro tenhle objem nesmysl. Přiznaná nejistota je správnější
    než tiché dvojí zaplacení.
14. **Lease s expirací, ne prostý zámek.** Worker si položku pronajme
    (`leased_by`, `leased_until`, default 15 min). Zabitý worker tak
    nenechá položku uvíznout navždy ve stavu "běží".
15. **Dry-run (shadow mode) od začátku: `SCHEDULER_DRY_RUN`.** Ticker
    normálně plánuje a zařazuje, executor položky označí
    `skipped (dry run)` a **nezavolá providera**. Plánovač je první část
    appky, která utrácí peníze bez člověka u klávesnice — týden nanečisto
    před ostrým během je nejlevnější pojistka v celé větvi. Zůstává zapnutý,
    dokud není hotové SCH-10 (stropy).
16. **Kill switch `SCHEDULER_ENABLED`** (default true) — vypne tickerovou
    smyčku, executor běží dál. Nutné ve chvíli, kdy appka poběží zároveň na
    PC i na serveru: dvě instance nad jednou databází by plánovaly každá po
    svém. Unique constraint (decision 12) by je ochránil, ale explicitní
    vypínač je lepší než spoléhat na pojistku.
17. **Kolize s `idx_runs_one_pending_per_prompt_model`** (migrace 0024):
    když má prompt+model rozjetý `pending` run, položka se vrátí do fronty
    s odkladem a stavem `deferred`, **ne `error`**. Ekvivalent
    `concurrencyPolicy: Forbid` u k8s CronJob.
18. **Neaktivní prompt nebo model v okamžiku vykonání → `skipped` s důvodem,
    ne `error`.** `trigger_run` obojí kontroluje a vrací 409; worker nemá
    komu 409 vrátit a deaktivace je legitimní administrativní akce, ne
    porucha.
19. **Heartbeat + healthcheck bez HTTP.** Worker neposlouchá na portu, takže
    Docker healthcheck kontroluje stáří touchfile (`/tmp/worker-alive`),
    který worker přepisuje v každé iteraci; do DB (`worker_heartbeats`)
    zapisuje stav pro UI. Bez toho by `/schedules` při vypnutém workeru
    vypadal stejně jako při prázdné frontě.
20. **Priorita: JEDNO celé číslo uložené na řádku fronty**, spočítané jako
    `client.priority * CLIENT_PRIORITY_WEIGHT + schedule.priority` (obě
    výchozí 100, rozsah 0–999). `CLIENT_PRIORITY_WEIGHT = 1000` je
    **konstanta v kódu, nikdy nastavení v `/settings`** — není to provozní
    parametr, ale definice sémantiky priority; jeho změna by změnila význam
    všech čísel, která uživatelé zadali, a rozdělila frontu na nesrovnatelně
    ohodnocené položky. Řazení `ORDER BY priority DESC, scheduled_for ASC`
    nad jedním indexovaným sloupcem, bez joinu. Priorita se fixuje při
    zařazení — pozdější změna priority klienta ovlivní jen nově zařazené
    položky.
21. **Atribuce: `Run.triggered_by_user_id = NULL` + `trigger_type =
    'scheduled'`.** Přesně dvojice, kterou `app/services/ops_dashboard.py`
    už klasifikuje jako pseudo-uživatele "Scheduler" a umí ji odlišit od
    historických runů bez atribuce. Odpovědnost za rozvrh se nese jinudy:
    `run_queue.schedule_id` → `run_schedules.created_by_user_id`, a detail
    runu zobrazí "Spuštěno plánovačem · rozvrh vytvořil X". Přiřadit run
    člověku, který rozvrh založil, by ve `/ops` smíchalo útratu vyvolanou
    kliknutím s útratou, která běží sama.
22. **`can_schedule(user)` vzniká hned, sloupec `users.can_schedule` ne.**
    V1 vrací `user.role in ("admin", "editor")`. Každá šablona i router volá
    výhradně tuhle funkci, nikde `role in (...)` natvrdo — přesně pravidlo,
    které si `docs/ROADMAP.md` zapsal po code review u `can_edit()`.
    Pozdější přechod na per-user příznak je pak jedna migrace, jeden
    checkbox v `/users` a jeden řádek v těle funkce; žádné volající místo se
    nemění. Sloupec teď nepřidávat — dokud ho nikdo nepotřebuje, je to mrtvé
    pole, které musí obsloužit každý formulář uživatele.
23. **Deaktivace uživatele pozastaví jeho aktivní rozvrhy** (`is_active =
    false`, důvod `owner_deactivated`) a vyvolá oznámení. Reaktivace
    uživatele je **neobnoví** — obnovení je vědomé rozhodnutí, ne vedlejší
    efekt. Uživatelé se nikdy nemažou, jen deaktivují
    (`app/routers/users.py`), takže FK je bezpečné.
24. **`target_type` (`'prompt'` | `'prompt_set'`) je ve schématu od začátku,
    UI v1 nabízí jen `prompt`.** Set-level rozvrh je to, co kolegové budou
    chtít ("jeď celého Knaufa týdně"), ale znamená kombinatorický rozstřel
    (40 promptů × 3 modely = 120 placených runů z jednoho kliknutí).
    Bezpečným ho dělá povinný **odhad ceny ve formuláři**, ne kód pro
    rozstřel — proto se schéma připraví teď a UI se doplní hned poté.
    **AKTUALIZOVÁNO ve v1.1 → decision 32: set-level je v UI od v1.**
    Rozbor rizika výš platí beze změny; mění se závěr, protože pojistky,
    které ho dělají bezpečným, jsou součástí téže větve.
25. **`notification_outbox` + `notify()` + kanály jako
    `app/notifications/<channel>.py`** — stejný vzor jako
    `app/adapters/<provider>.py`: přidat e-mail znamená přidat soubor, ne
    přepsat volající místa. Dnes se událost zapíše a zobrazí in-app; až
    přibude SMTP, nový kanál outbox vyprázdní a **nic z mezidobí se
    neztratí**. Chybové události se doručují jako **denní souhrn** — 40
    e-mailů při výpadku providera si lidi odfiltrují do koše a pak jim
    unikne i ten důležitý.
    **Doplněno v v1.1:** `.env.example` dostane SMTP klíče (host, port,
    uživatel, from-adresa) prázdné už teď, ať je zdokumentované, co bude
    potřeba — adresy příjemců už existují (`users.email`). A až kanál
    přibude, **nahromaděná historie se nedoručuje**: položky starší než pár
    dní se označí `suppressed`, jinak první e-mail po zapnutí SMTP bude tři
    měsíce starých oznámení.
26. **Stropy se vynucují v `run_execution.py`, ne ve workeru**: denní limit
    runů na klienta, hloubka fronty na klienta, souběh na providera. Kdyby
    je vynucoval jen worker, obejde je ruční trigger a limit nic
    negarantuje.
27. **`run_queue` se nemaže a slouží zároveň jako historie.** Výběr z fronty
    drží rychlý **partial index** `WHERE status = 'queued'` — indexuje jen
    čekající položky (jednotky až stovky), historie ho nezvětšuje. Při 600
    runech denně je to ~220 tisíc řádků ročně, což je pro Postgres nic;
    mazání by navíc šlo proti duchu NFR-6.
28. **Pravidlo se zobrazuje v zóně rozvrhu, události v zóně prohlížeče.**
    Výjimka z `docs/TASKS_LOCAL_TIME.md`, a to vědomá: kdyby se pravidlo
    přepočítalo do zóny prohlížeče, kolega v Londýně by u téhož rozvrhu
    viděl "05:00" a šel by to opravit. Pravidlo je text dohody, ne okamžik
    (stejně to řeší Google Calendar). Konkrétní příští termín se vedle toho
    zobrazí normálně v zóně prohlížeče.
29. **Náhled příštích termínů + odhad ceny přímo ve formuláři rozvrhu.**
    Pět řádků kódu (volá čistou funkci z decision 10), a eliminuje celou
    kategorii nedorozumění kolem zón a dnů v týdnu ještě před uložením.
30. **Žádná nová závislost.** `zoneinfo` je ve standardní knihovně a
    ověřeně funguje ve vašem `python:3.14-slim` image včetně
    `Europe/Prague`. Pokud se v průběhu ukáže, že je potřeba APScheduler,
    Celery, dateutil nebo cokoliv dalšího — ZASTAV a zeptej se.

### Doplněno ve v1.1 (2026-09-18)

31. **Každý rozvrh má povinné ukončení — "donekonečna" nejde uložit.**
    V databázi `CHECK (num_nonnulls(ends_on, max_occurrences) = 1)`: přesně
    jedna z obou podmínek konce musí být vyplněná. Důvod (uživatel,
    2026-09-18): rozvrhů bude přibývat a zapomenutý věčný rozvrh utrácí
    tiše dál, aniž si toho kdokoliv všimne.
    Nejzazší `ends_on` se řídí **frekvencí, ne pevným počtem měsíců** —
    strop měří "kolik runů proběhne, aniž se na to někdo podívá", a ten
    musí u denního i měsíčního rozvrhu vyjít zhruba nastejno (~30 runů):

    | frekvence | nejzazší `ends_on` | ≈ runů |
    |---|---|---|
    | `daily`   | 30 dní     | ~30 |
    | `weekly`  | 6 měsíců   | ~26 (3× týdně ~78) |
    | `monthly` | 12 měsíců  | 12 |

    Pojistky proti opačnému riziku (sběr dat tiše skončí a nikdo si
    nevšimne): oznámení `schedule.expiring_soon` 7 dní / 3 výskyty předem,
    akce **"prodloužit"** na `/schedules` (posune `ends_on` o další období
    jedním kliknutím), a doběhlý rozvrh **zůstává v seznamu** se stavem
    `inactive_reason='completed'` — nemizí.
    Důsledek: **`clients.is_active` se nepřidává** (zvažováno a zamítnuto
    2026-09-18). Povinné ukončení pokrývá i případ "klient utichl", zatímco
    nový stav klienta by si vyžádal průřez šesti obrazovkami a otázku "kam
    se poděl Knauf" u každé z nich. Archivace klientů je samostatné téma,
    ne vedlejší efekt plánovače.

32. **Set-level rozvrh je v UI od v1** — mění závěr decision 24. Důvod:
    první reálný požadavek na plánovač je srovnávací běh proti peecu (25
    promptů, jeden trh, jeden jazyk, denně 7 dní). S prompt-level rozvrhy
    by to znamenalo 25 rozvrhů založit a 25 pohlídat — to nikdo dělat
    nebude, takže by plánovač minul svůj první use case.
    Bezpečným ho nedělá to, že UI tu volbu nenabídne, ale: povinný odhad
    **počtu runů i ceny** před uložením (decision 29), `batch_id` na
    položkách fronty, strop hloubky fronty a denní kvóta (decision 33).

33. **Dva různé stropy, každý na jinou práci.**
    (a) **Denní limit runů na klienta** — tvrdý, blokuje, vynucuje se
    v `run_execution.py` (decision 26), počítá se deterministicky.
    (b) **Měsíční finanční hranice na klienta** (`clients.monthly_budget_usd`,
    NULL = žádná) — jen vyvolá `budget.threshold_exceeded`, **neblokuje.**
    Skutečnou cenu runu známe až po něm (závisí na počtu output tokenů),
    takže zastavovat na odhadu by shodilo běžící benchmark kvůli
    nepřesnosti. Měna je USD — stejně jako `price_per_unit_usd`
    (`app/models/provider.py`) a celý `app/services/cost.py`.

34. **Jeden rozvrh smí mířit na víc modelů i víc person.** `model_ids` bylo
    pole od začátku (původní "v1 UI vybírá právě jeden" se ruší —
    porovnání napříč modely je jádro produktu). `persona_id` se ze stejného
    důvodu mění na **`persona_ids`**: Philip chce tytéž prompty přes
    několik person a `Run.persona_id` je na řádku runu, takže evidence je
    rozliší a jdou porovnat.
    Násobení je tím pádem trojí — prompty × modely × persony. 25 × 3 × 3 =
    **225 runů z jednoho okna.** Proto odhad ve formuláři ukazuje **dvě
    čísla**: za jedno okno a za celý rozvrh do jeho konce. To druhé je
    vůbec spočitatelné jen proto, že konec je povinný (decision 31).

35. **`idx_runs_one_pending_per_prompt_model` se rozšiřuje na
    `(prompt_id, model_id, persona_id, market_id)`.** Index vznikl
    v migraci 0024 jako TOCTOU pojistka proti dvojkliku na tlačítko, tedy
    proti "nespouštěj dvakrát totéž naráz". S decision 34 by ale tři
    persony nad týmž promptem a modelem byly tři **legitimní** runy, které
    si navzájem překážejí: druhý a třetí by se podle decision 17 odkládaly
    s backoffem 1/5/25 min, přestože jde o tři různé věci. Rozšířením se
    původní záměr zachovává a jen se doplní definice "téhož". Zásah do
    existujícího constraintu je vědomý a hlášený podle
    `AI_INSTRUCTIONS.md` §4.

### Doplněno 2026-09-21 (po dokončení T5b, v konverzaci)

36. **Dva různé rozvrhy mířící na tentýž prompt+model+personu se
    navzájem nekontrolují — jen se na to upozorní při ukládání, nikdy se
    to nezablokuje.** Objevilo se jako reálný scénář: rozvrh na jeden
    prompt a rozvrh na celý set, který ten prompt obsahuje, se stejným
    modelem/personou ale jiným časem — obě proběhnou nezávisle a bez
    dry-runu by se to zaplatilo dvakrát. Pětisloupcová unikátnost
    (decision 12) i kolizní odklad (decision 17) tohle nezachytí, protože
    obojí je navázané na `schedule_id`, resp. na skutečnou souběžnost —
    dva rozvrhy 15 minut od sebe se nikdy nepotkají jako `pending` run.
    Srovnání s podobnými nástroji (Airflow, k8s CronJob, GitHub Actions):
    žádný z nich nedetekuje sémantický překryv mezi nezávislými
    plánovanými jednotkami automaticky — buď to řeší explicitní
    deklarací (GitHub Actions `concurrency: group:`), nebo to neřeší
    vůbec a nechávají to na provozovateli. Automatické **zablokování**
    by navíc bylo špatně — překryv může být záměrný (např. porovnání
    dvou různých cadencí). Proto: **upozornit, nikdy neblokovat** — stejná
    filozofie jako odhad ceny (decision 29). Realizuje T5c (kontrola při
    uložení) a rozšiřuje T6 (trvalé zobrazení na monitorovací stránce).

### Doplněno 2026-09-21 (po prvním nasazení T6, v konverzaci)

37. **Historie na `/schedules` dostává "health strip" — řádek barevných
    dlaždic (poslední ~10 oken) na rozvrh, seskupený podle klienta,
    zdraví klienti defaultně sbalení.** Vzniklo z konkrétní zpětné vazby,
    že textový součet ("0 done · 0 error · 2 skipped") se čte pomalu a
    nedává rychlou odpověď na "stíhám to pro všechny klienty?". Vzor je
    zavedený u monitorovacích nástrojů (healthchecks.io, UptimeRobot,
    GitHub Actions' workflow health) — barevná dlaždice na okno
    (zelená/červená/šedá) se čte bez čtení textu. Klasifikace jednoho
    okna je all-or-nothing jako u CI buildu: `error_count > 0` →
    `error`, jinak `done_count > 0` → `done`, jinak `skipped_count > 0`
    → `skipped`, jinak `cancelled`. Klient s alespoň jedním `error`
    dlaždicí v posledních ~10 oknech je defaultně rozbalený; jinak
    sbalený na "✓ Klient · vše v pořádku, N rozvrhů". Strip je *triage*
    vrstva nad existující timeline (T6/SCH-6b), ne její náhrada — klik
    do rozbaleného klienta pořád vede k plnému seznamu dávek pod ním.
    Jedna nová SQL agregace s window funkcí (`ROW_NUMBER() OVER
    (PARTITION BY schedule_id ORDER BY scheduled_for DESC)`), žádná
    smyčka v Pythonu — stejná disciplína jako zbytek T6.

---

## Nové schéma (migrace 0029)

> **Číslo migrace:** `docs/TASKS_PRE_SCHEDULER.md` (krátká větev, která jde
> vědomě před plánovačem) si bere 0029. Pokud je smergnutá dřív — a to je
> záměr — je tahle migrace **0030**. Čísla níž v textu neměň zpětně, vyřeš to
> při psaní SCH-0.

Čtyři nové tabulky, tři sloupce na `clients` a úprava jednoho existujícího
indexu. Nic z toho není v `schema_phase1.sql` — odsouhlaseno v konverzacích
2026-09-16 (tabulky) a 2026-09-18 (sloupce ukončení, `persona_ids`,
`daily_run_limit`, `monthly_budget_usd`, rozšíření indexu z 0024) podle
`AI_INSTRUCTIONS.md` §4.

```
run_schedules                 -- pravidlo opakování
  id
  client_id            FK clients            -- denormalizované: priorita, kvóty, budoucí scoping
  target_type          varchar(20)           -- 'prompt' | 'prompt_set'  (decision 24)
  target_id            int                   -- root_prompt_id nebo prompt_set_id (decision 6)
  model_ids            int[]                 -- které modely; multi-select (decision 34)
  market_id            FK markets NULL       -- NULL = vlastní trh promptu
  persona_ids          int[]                 -- které persony; multi-select (decision 34)
  frequency            varchar(10)           -- 'daily' | 'weekly' | 'monthly'
  days_of_week         smallint[]            -- weekly; 2x týdně = {1,4}
  day_of_month         smallint              -- monthly; 31 se ořízne (decision 8)
  time_of_day          time                  -- lokální nástěnný čas
  timezone             varchar(64)           -- IANA, v1 vždy 'Europe/Prague'
  starts_on            date                  -- první den platnosti (default dnes)
  ends_on              date NULL             -- konec kalendářem (decision 31)
  max_occurrences      int NULL              -- nebo konec počtem výskytů (decision 31)
  occurrences_count    int default 0         -- kolik už proběhlo; proti max_occurrences
  priority             int  default 100
  is_active            bool default true
  inactive_reason      varchar(30) NULL      -- 'owner_deactivated' | 'user' | 'completed'
  next_run_at          timestamptz NULL      -- odvozená cache (decision 7); NULL = doběhlo
  last_enqueued_at     timestamptz NULL
  created_by_user_id   FK users
  updated_by_user_id   FK users NULL
  created_at, updated_at
  CHECK (num_nonnulls(ends_on, max_occurrences) = 1)          -- decision 31
  INDEX (next_run_at) WHERE is_active

run_queue                     -- konkrétní výskyt + historie (decision 27)
  id
  schedule_id          FK run_schedules NULL -- NULL = ruční/dávkové (decision 5)
  source               varchar(10)           -- 'schedule' | 'manual' | 'batch'
  batch_id             uuid NULL             -- pro set-level rozstřel (decision 32)
  client_id, prompt_id, model_id, market_id, persona_id   -- vše resolvované
  scheduled_for        timestamptz           -- termín okna
  priority             int                   -- fixované při zařazení (decision 20)
  status               varchar(12)           -- queued|leased|done|error|skipped|deferred|cancelled
  skip_reason          varchar(40) NULL      -- worker_down|grace_expired|dry_run|inactive_prompt|...
  attempts             int default 0
  last_error           text NULL
  leased_by            varchar(64) NULL
  leased_until         timestamptz NULL
  run_id               FK runs NULL          -- zapsáno PŘED voláním adaptéru (decision 13)
  queued_at, started_at, finished_at
  UNIQUE (schedule_id, scheduled_for,
          prompt_id, model_id, persona_id)                   -- decision 12 + 32
  INDEX (priority DESC, scheduled_for) WHERE status='queued' -- decision 27
  INDEX (schedule_id, scheduled_for DESC)                    -- stránkovaná historie

worker_heartbeats             -- živost plánovače (decision 19)
  worker_name          varchar(64) PK
  last_seen_at         timestamptz
  version              varchar(40) NULL
  dry_run              bool

notification_outbox           -- decision 25
  id
  event_type           varchar(40)  -- schedule.run_failed | schedule.window_skipped |
                                    -- schedule.owner_deactivated | worker.stale | quota.exceeded |
                                    -- schedule.expiring_soon (d31) | budget.threshold_exceeded (d33)
  payload              jsonb
  recipient_user_id    FK users NULL -- NULL = všichni admini
  status               varchar(12)  -- pending|sent|failed|suppressed
  attempts             int default 0
  last_error           text NULL
  created_at, sent_at

clients
  + priority           int default 100       -- decision 20
  + daily_run_limit    int NULL              -- tvrdý denní strop; NULL = hodnota z configu
  + monthly_budget_usd numeric(10,2) NULL    -- měkká hranice, jen oznámí (decision 33)

runs                          -- existující tabulka, jen úprava indexu
  ~ idx_runs_one_pending_per_prompt_model
      (prompt_id, model_id)  ->  (prompt_id, model_id, persona_id, market_id)
      WHERE status = 'pending'                                -- decision 35
```

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T0 | Migrace 0029 — čtyři tabulky, sloupce na `clients`, indexy, rozšíření indexu z 0024 | ⏳ |
| T1 | `compute_next_run_at()` jako čistá funkce + tabulkové DST testy + konec rozvrhu | ⏳ |
| T2 | `app/services/run_execution.py` — vytažení exekuce, beze změny chování | ⏳ |
| T3 | `app/worker.py` — ticker, executor, lease, heartbeat, rekonciliace, dry-run | ⏳ |
| T4 | `worker` služba v Compose + env + healthcheck | ⏳ |
| T5 | `can_schedule()` + CRUD rozvrhů (prompt-level) s povinným koncem a náhledem termínů i ceny | ⏳ |
| T5b | Set-level rozvrh (`target_type='prompt_set'`) + rozstřel přes `batch_id` | ⏳ |
| T5c | Upozornění na překryv rozvrhů (stejný prompt+model+persona, jiný rozvrh) při uložení | ⏳ |
| T6 | `/schedules` — rozvrhy / fronta / historie + stav workeru + trvalé upozornění na překryv | ⏳ |
| T7 | Dead letter: opakování chyb, hromadné akce | ⏳ |
| T8 | `notification_outbox` + `notify()` + in-app oznámení (vč. vypršení rozvrhu a rozpočtu) | ✅ |
| T9 | Pozastavení rozvrhů při deaktivaci uživatele | ⏳ |
| T10 | Stropy: denní limit na klienta, měsíční rozpočet, hloubka fronty, souběh na providera | ⏳ |
| T11 | Závěrečný průchod: i18n kompletnost, responsive, testy, docs | ⏳ |

Pořadí je vynucené: T1 a T2 jsou nezávislé stavební kameny, které T3
potřebuje oba; T4 bez T3 nemá co spouštět; T5 zakládá data, která T6
zobrazuje; T5b staví na hotovém formuláři z T5 a jen mění cíl rozvrhu;
T7–T10 rozšiřují hotový základ.

**`SCHEDULER_DRY_RUN` zůstává zapnutý až do dokončení T10.** Ostrý provoz se
zapíná jedním env varem teprve tehdy, když jsou hotové stropy — do té doby
se plánuje nanečisto a na `/schedules` se kontroluje, co by se bylo stalo
(decision 15).

i18n klíče (DE/EN) a responsive kontrola jsou součástí **každého** UI tasku,
ne odložené na konec — T11 je jen závěrečné ověření úplnosti.

---

## T0 — Migrace 0029

**Target:** nová `alembic/versions/0029_scheduler.py`, nové
`app/models/schedule.py`, `app/models/notification.py`, úprava
`app/models/client.py`, `app/models/__init__.py`

1. Alembic migrace podle schématu výše — čtyři tabulky, tři sloupce na
   `clients`, všechny tři indexy na `run_queue` a jeden na `run_schedules`.
   Downgrade musí být kompletní (drop v opačném pořadí kvůli FK).
1b. `CHECK (num_nonnulls(ends_on, max_occurrences) = 1)` na `run_schedules`
   (decision 31) — povinné ukončení se vynucuje v databázi, ne jen ve
   formuláři. Formulář se dá obejít, constraint ne.
1c. **Rozšířit existující `idx_runs_one_pending_per_prompt_model`** z
   `(prompt_id, model_id)` na `(prompt_id, model_id, persona_id, market_id)`
   (decision 35) — drop + create v migraci, a stejná změna v
   `__table_args__` v `app/models/run.py`. Je to zásah do pojistky
   z migrace 0024: v docstringu u indexu vysvětli PROČ se mění
   (multi-persona rozvrhy, decision 34), ať to při příštím čtení nevypadá
   jako oslabení ochrany.
2. SQLAlchemy modely `RunSchedule`, `RunQueueItem`, `WorkerHeartbeat`,
   `NotificationOutbox` — včetně `__table_args__` s indexy a unique
   constraintem, aby `Base.metadata.create_all()` (co používá testovací
   sada) vytvořil totéž co migrace. Přesně ten precedent, který si
   `app/models/run.py` zapsal u `idx_runs_one_pending_per_prompt_model`.
3. Docstringy na modelech vysvětlující vztah tří vrstev (decision 1) a proč
   `target_id` míří na lineage promptu (decision 6).
4. Žádná business logika v téhle fázi — jen schéma.

**Done when:** `alembic upgrade head` i `downgrade -1` projdou proti reálné
databázi; `pytest` zelený (existující sada nesmí spadnout na nových
modelech); `\d run_queue` v psql ukazuje partial index.

**Expected commit:** `feat(infra): add scheduler, run queue and notification tables`

---

## T1 — `compute_next_run_at()` + DST testy

**Target:** nová `app/services/scheduling.py`, nový
`tests/test_scheduling_recurrence.py`

1. `compute_next_run_at(schedule, *, after: datetime) -> datetime` — čistá
   funkce, žádné `now()`, žádná DB (decision 10). Vstupem je termín, od
   kterého se hledá další okno; výstupem timestamp v UTC.
2. Implementace přes `zoneinfo` (decision 30) podle decisions 7–9:
   lokální nástěnný čas → `fold=0` → převod na UTC. Neexistující čas na
   jaře → první platný okamžik po skoku (detekce round-tripem
   local→UTC→local). Měsíční den nad délku měsíce → poslední den měsíce.
3. Tabulkové testy, minimálně: běžný týdenní případ (Po+Čt 6:00); přechod
   na zimní čas (26. 10. 2026 — 6:00 CEST = 04:00 UTC vs. 6:00 CET = 05:00
   UTC); dvojznačný čas 02:30 na podzim (musí vyjít JEDEN okamžik,
   `fold=0`); neexistující 02:30 na jaře; denní rozvrh přes půlnoc roku;
   měsíční 31. v únoru; rozvrh, jehož `days_of_week` neobsahuje dnešek.
4. Test na drift (decision 9): opakované volání s `after` = předchozí
   výsledek musí dát přesně stejný lokální čas, ne posunutý.
5. **Konec rozvrhu (decision 31):** funkce vrací `None`, když je další
   termín po `ends_on` nebo když `occurrences_count >= max_occurrences`.
   `None` je pro ticker signál "rozvrh doběhl" → `is_active = false`,
   `inactive_reason = 'completed'`, `next_run_at = NULL`. Testy na obě
   podmínky zvlášť i na hranu (termín přesně v den `ends_on` se ještě
   vykoná).
6. Pomocná funkce `max_end_date(frequency, *, starts_on)` pro formulář —
   vrací nejzazší přípustné `ends_on` podle tabulky v decision 31 (denně
   30 dní, týdně 6 měsíců, měsíčně 12 měsíců). Taky čistá, taky bez
   `now()`.

**Done when:** `pytest tests/test_scheduling_recurrence.py -v` zelený;
všechny DST případy pokryté explicitním očekávaným UTC timestampem, ne
vypočítaným z téže funkce.

**Expected commit:** `feat(scheduler): add timezone-aware recurrence calculation`

---

## T2 — `run_execution.py`

**Target:** nová `app/services/run_execution.py`, úprava
`app/routers/runs.py`, úprava/doplnění `tests/test_runs.py`

**Tenhle task nemění žádné chování** — je to čistý refaktor, jehož jediným
viditelným výsledkem je, že všechny existující testy pořád procházejí.

1. Vytáhnout z `trigger_run` (`app/routers/runs.py`) do
   `execute_run(db, *, prompt, model, market, persona, trigger_type,
   triggered_by_user_id, run_id=None) -> Run`: sestavení `request_payload`,
   vložení `pending` `Run`, volání adaptéru, zápis `RawResponse`/
   `Citation`/`SearchQuery`, spuštění analysis skills, měření latence,
   ošetření chyby (FR-16).
2. Router zůstane tenký: parsuje `Form`, ověří 409 případy (neaktivní
   prompt/model, běžící `pending` run, `IntegrityError` backstop) a zavolá
   `execute_run`. Všechny dnešní docstringy a komentáře, co vysvětlují
   proč (TOCTOU race, `expire_on_commit=False`, best-effort analysis),
   musí jít s kódem — ne zmizet při přesunu.
3. `_run_active_analysis_skills` a `_build_system_instruction` se přesouvají
   také, pokud je router sám jinak nepotřebuje.
4. Rozhraní musí být volatelné **bez `Request`** (worker žádný nemá) —
   chybové hlášky pro uživatele (`t(...)`) zůstávají v routeru, služba
   vrací/vyhazuje neutrální výjimky.

**Done when:** `pytest` zelený beze změny očekávání v existujících testech;
ruční run z prohlížeče funguje přesně jako před refaktorem (včetně 409 při
dvojkliku a HX-Redirect cesty).

**Expected commit:** `refactor(runs): extract run execution into a shared service`

---

## T3 — `app/worker.py`

**Target:** nová `app/worker.py`, nová `app/services/queue.py`, nový
`tests/test_worker_queue.py`

1. `app/services/queue.py` — operace nad frontou, testovatelné bez procesu:
   - `enqueue_due_schedules(db, *, now)` — ticker: rozvrhy s
     `next_run_at <= now`, resolve aktuální verze promptu (decision 6),
     rozstřel podle `target_type`/`model_ids`, výpočet priority
     (decision 20), insert s ošetřením unique constraintu (decision 12),
     přepočet `next_run_at` z **termínu okna** (decision 9), zápis
     `skipped`/`worker_down` řádků pro okna nad lhůtu (decision 11, strop
     30 řádků na rozvrh, pak jeden souhrnný).
   - `claim_next(db, *, worker_name, now)` — `SELECT ... WHERE
     status='queued' AND scheduled_for <= now ORDER BY priority DESC,
     scheduled_for ASC LIMIT 1 FOR UPDATE SKIP LOCKED`, nastavení lease.
   - `release_expired_leases(db, *, now)`.
   - `reconcile_interrupted_runs(db, *, now)` — decision 13.
2. `app/worker.py` — smyčka: heartbeat (DB + touchfile, decision 19),
   `enqueue_due_schedules` 1×/min (přeskočí se při `SCHEDULER_ENABLED=false`),
   `claim_next` + vykonání, `sleep(5)`. Graceful shutdown na `SIGTERM`:
   dokončit rozdělanou položku, uvolnit lease, skončit.
3. Vykonání položky: kontrola lhůty (decision 11), kontrola aktivity
   promptu/modelu (decision 18), kontrola kolize s `pending` runem
   (decision 17 → `deferred` + odklad), zápis `run_id` **před** voláním
   (decision 13), volání `execute_run` z T2, retry politika (3 pokusy,
   exponenciální odklad 1/5/25 min, jen transportní chyby — chyba od
   providera po odpovědi je terminální).
4. `SCHEDULER_DRY_RUN` (decision 15): položka se označí `skipped` /
   `dry_run`, adapter se nezavolá, `Run` nevznikne.
5. Nové konfigurační klíče v `app/config.py`: `scheduler_enabled`,
   `scheduler_dry_run`, `scheduler_grace_period_minutes` (360),
   `scheduler_lease_minutes` (15), `worker_name`.
6. Testy: `claim_next` respektuje prioritu i `SKIP LOCKED`; vypršelá lease
   se vrátí; položka s `run_id` se nikdy nevezme znovu; okno po lhůtě →
   `skipped`; dry-run nevolá adapter (mock).

**Done when:** `pytest tests/test_worker_queue.py -v` zelený; worker
spuštěný lokálně (`python -m app.worker` uvnitř kontejneru) zapisuje
heartbeat a v dry-runu zpracuje ručně vložený rozvrh, aniž by zavolal
providera.

**Expected commit:** `feat(scheduler): add queue worker with ticker, leases and reconciliation`

---

## T4 — `worker` služba v Compose

**Target:** `docker-compose.yaml`, `.env.example`, `README.md` (provozní
odstavec), případně `dockerfile` (pojmenování image)

1. Pojmenovat image u služby `app` (`image: signalmap-app` vedle jejího
   `build:`) a službu `worker` postavit na `image: signalmap-app` **bez
   vlastního `build:`** — jeden build, zaručeně identický kód v obou
   kontejnerech.
2. `worker` služba: `command: ["python", "-m", "app.worker"]`, `init: true`,
   `depends_on: app: condition: service_healthy` (decision 3),
   `restart: unless-stopped`, `stop_grace_period: 120s` (volání providera
   trvá 5–28 s, worker musí stihnout dokončit rozdělanou položku).
3. Env: `DATABASE_URL`, `SECRET_KEY` (**povinné i pro worker** — v
   `app/config.py` je `secret_key: str` bez defaultu, takže jakýkoliv import
   `get_settings()` bez něj spadne při startu, i když worker žádné cookies
   nevydává), všechny tři API klíče, `ENVIRONMENT`, `SCHEDULER_ENABLED`,
   `SCHEDULER_DRY_RUN`.
4. Healthcheck přes stáří touchfile (decision 19), ne HTTP.
5. `.env.example` doplnit o nové klíče s komentářem, co dělají.
6. Caddy se nemění — worker zvenku dostupný není a být nemá.

**Done when:** `docker compose up -d --build` nastartuje tři služby (čtyři
s Caddy); `docker compose logs worker` ukazuje heartbeat; `docker ps` hlásí
worker jako `healthy`; `docker compose stop worker` doběhne bez SIGKILL.

**Expected commit:** `chore(infra): add scheduler worker service to Compose`

---

## T5 — `can_schedule()` + CRUD rozvrhů

**Target:** `app/templating.py`, nová `app/routers/schedules.py`, nové
`app/templates/schedules/form.html`, úprava `app/templates/prompts/detail.html`,
`app/templates/clients/detail.html`, `app/i18n/en.json`, `app/i18n/de.json`,
registrace routeru v `app/main.py`

1. `can_schedule(user)` v `app/templating.py` vedle `can_edit()`
   (decision 22) + registrace do `templates.env.globals`. Router používá
   `require_role("admin", "editor")` — **UI skrytí nikdy nenahrazuje
   serverovou kontrolu** (`docs/TASKS_PHASE6.md` design decision 4).
2. CRUD: `GET/POST /schedules/new?prompt_id=`, `POST /schedules/{id}`,
   `POST /schedules/{id}/toggle`, `POST /schedules/{id}/delete`. Plain form
   POST → 303, HTMX → `HX-Redirect` (vzor z `trigger_run`).
3. Formulář (v T5 jen `target_type='prompt'`; set-level přidá T5b):
   frekvence (denně / týdně + checkboxy dnů / měsíčně + den), čas,
   **modely a persony jako multi-select** (decision 34), trh, priorita.
   Reuse maker z `app/templates/partials/macros.html`. Pole `timezone` se
   **nezobrazuje** (v1 pevně `Europe/Prague`, decision 7), ale ukládá se.
3b. **Povinné ukončení (decision 31):** přepínač "konec datem" / "konec
   počtem opakování", jedno z obou musí být vyplněné — validace v routeru
   přes `AppError`, ne jen `required` v HTML. Výchozí hodnota i horní mez
   `ends_on` se berou z `max_end_date()` (T1) a mění se se zvolenou
   frekvencí. Uživateli se vysvětlí proč ("rozvrh bez konce by utrácel i
   poté, co si na něj nikdo vzpomene") — jinak to vypadá jako šikana
   formuláře.
4. **Náhled příštích pěti termínů** a **odhad ceny** živě ve formuláři
   (decision 29) — náhled volá `compute_next_run_at` z T1, cena
   `estimate_run_cost` / cost components (PR #14). Odhad ukazuje **dvě
   čísla**: za jedno okno a za celý rozvrh do konce (decision 34), spolu
   s počtem runů — při více modelech a personách se násobí. HTMX partial,
   žádný nový JS framework.
5. Seznam rozvrhů jako partial na detailu promptu a detailu klienta:
   frekvence, příští spuštění, **konec rozvrhu**, poslední výsledek,
   přepínač aktivní, filtr podle vlastníka rozvrhu.
   Pravidlo se zobrazuje v zóně rozvrhu s popiskem, konkrétní termín v zóně
   prohlížeče (decision 28).
6. Delete s `data-confirm="{{ t('schedules.delete_confirm') }}"` přes
   sdílený delegovaný listener v `base.html` — nikdy `onsubmit="return
   confirm(...)"` (rozbije se na apostrofu v překladu).
7. i18n klíče DE/EN pro všechno nové; responsive ~375 / 768 / desktop.

**Done when:** rozvrh se dá založit, upravit, pozastavit i smazat z
prohlížeče; náhled termínů odpovídá tomu, co spočítá T1; viewer dostane 403
na všech `/schedules*` routách; responsive ověřené na všech třech šířkách.

**Expected commit:** `feat(scheduler): add schedule CRUD with occurrence and cost preview`

---

## T5b — Set-level rozvrh

**Target:** `app/routers/schedules.py`, `app/services/queue.py`,
`app/templates/schedules/form.html`, `app/templates/prompt_sets/detail.html`,
i18n, testy

Realizuje decision 32. Staví na hotovém formuláři z T5 — mění se cíl
rozvrhu a rozstřel při zařazení, ne celý tok.

1. `target_type='prompt_set'` ve formuláři: volba "celý prompt set" na
   detailu prompt setu (`GET /schedules/new?prompt_set_id=`). `target_id`
   je `prompt_sets.id`; jednotlivé prompty se resolvují **až při zařazení**
   (decision 6), takže prompt přidaný do setu příští týden se do rozvrhu
   zahrne sám.
2. `enqueue_due_schedules` (T3) pro set-level rozstřelí okno na **aktivní
   prompty setu × `model_ids` × `persona_ids`** a všem položkám dá společné
   `batch_id` (uuid). Neaktivní prompty se přeskočí bez chyby
   (decision 18) — set je živý seznam, ne zmražený.
3. Unique constraint `(schedule_id, scheduled_for)` z decision 12 na
   set-level **nestačí** — položek na jedno okno je N. Idempotence je tedy
   `(schedule_id, scheduled_for, prompt_id, model_id, persona_id)`; v T0 to
   znamená unique přes pět sloupců, ne dva. **Ověř, že to migrace opravdu
   má** — bez toho by restart tickeru uprostřed rozstřelu založil dávku
   podruhé a zaplatila by se dvakrát.
4. Odhad ve formuláři (T5 bod 4) musí pro set-level počítat přes všechny
   prompty setu: "25 promptů × 3 modely × 3 persony = 225 runů na okno,
   7 oken, odhadem $X celkem". Tohle číslo je jediná ochrana, kterou
   uživatel uvidí **před** uložením.
5. Strop hloubky fronty na klienta (T10) se kontroluje **před** rozstřelem,
   ne po něm — jinak se 225 položek vloží a teprve pak zjistí, že se
   nevešly.
6. Na `/schedules` (T6) zobrazit dávku jako jeden řádek s rozpadem podle
   `batch_id`, ne 225 samostatných řádků historie.
7. Testy: rozstřel dá přesně N × M × P položek se shodným `batch_id`;
   opakované zařazení téhož okna nevytvoří duplicitu; neaktivní prompt
   v setu se přeskočí; prompt přidaný do setu po založení rozvrhu se
   v dalším okně zahrne.

**Done when:** rozvrh na celý prompt set se dá založit z detailu setu,
formulář před uložením ukáže počet runů i odhad ceny za celý rozvrh, a
v dry-runu vznikne správný počet položek s jedním `batch_id`.

**Expected commit:** `feat(scheduler): add prompt-set level schedules`

---

## T5c — Upozornění na překryv rozvrhů

Realizuje decision 36. Malý task, staví na `_resolve_target` z T5/T5b —
žádná nová tabulka ani sloupec.

**Target:** rozšíření `app/routers/schedules.py`
(`find_overlapping_schedules`, zapojení do `preview_occurrences`), nové
skryté pole `schedule_id` ve `app/templates/schedules/form.html`, nová
sekce v `app/templates/schedules/_occurrence_preview.html`, i18n

1. `find_overlapping_schedules(db, *, client_id, exclude_schedule_id,
   prompt_ids, model_ids, persona_ids)` — najde jiné **aktivní** rozvrhy
   stejného klienta, jejichž rozbalený cíl (`_resolve_target`) sdílí
   **současně** alespoň jeden prompt, jeden model a jednu personu s nově
   ukládaným rozvrhem. Shoda jen v jednom rozměru (např. stejný model,
   jiný prompt) není překryv. Rozsah je **jeden klient** — napříč klienty
   překryv nedává smysl.
2. `GET /schedules/preview` (T5) o tenhle výsledek rozšířit — potřebuje
   navíc `schedule_id` (prázdné při zakládání, vyplněné při editaci, aby
   se rozvrh při editaci nesrovnával sám se sebou) a `client_id`.
3. Zobrazení v `_occurrence_preview.html`: jantarový box, oddělený od
   odhadu ceny, s odkazem na overlapující rozvrh a tím, co konkrétně sdílí
   (počet promptů, který model, která persona). **Nic neblokuje** — jen
   informuje, dřív než uživatel klikne Save (decision 36).
4. Testy: dva aktivní rozvrhy se shodným promptem+modelem+personou →
   detekováno; shoda jen v modelu (jiný prompt) → nedetekováno; editace
   rozvrhu sama se sebou → nedetekováno; pozastavený (`is_active=False`)
   překrývající se rozvrh → nedetekováno (neběží, není co hlásit).

**Done when:** založení rozvrhu na prompt, který je zároveň v aktivně
naplánovaném prompt setu se stejným modelem/personou, zobrazí varování
před uložením; uložení samotné není ničím blokované.

**Expected commit:** `feat(scheduler): warn about overlapping schedules at save time`

---

## T6 — `/schedules` monitoring

**Target:** nová `app/templates/schedules/index.html`, rozšíření
`app/routers/schedules.py`, nová `app/services/schedule_monitor.py`,
odkaz v `app/templates/base.html`, i18n

**Doplněno po prvním nasazení:** search + filtr chipy na Rozvrhách a
Historii, seskupení Rozvrh podle klienta, živé auto-refreshování Fronty
přes HTMX, a **health strip na Historii** (decision 37) — barevné
dlaždice posledních ~10 oken na rozvrh, seskupené podle klienta,
problémoví klienti rozbalení, zdraví sbalení.

1. Stránka se třemi pohledy (stejný trojlístek jako GitHub Actions a
   Airflow): **Rozvrhy** (všechna pravidla, příští běh, poslední výsledek,
   pauza, **odznak "překrývá se s N dalšími" přes `find_overlapping_schedules`
   z T5c, spočítaný jednou pro všechny rozvrhy stránky najednou, ne
   N+1 dotazů**), **Fronta** (co čeká a běží, v pořadí podle priority —
   tady je priorita konečně vidět), **Historie** (posledních N oken: kdy,
   co, výsledek, latence, cena, odkaz na `Run`; filtr "jen chyby").
2. **Stavový pruh workeru** nahoře z `worker_heartbeats` (decision 19):
   `Plánovač běží · poslední signál před 8 s` / `Plánovač neodpovídá 3 h`.
   V dry-runu výrazný odlišný pruh, aby nikdo omylem nečekal reálné runy.
3. Metrika **stáří nejstarší čekající položky** — první ukazatel, který
   řekne "nestíháme", dřív než hloubka fronty.
4. Agregace v SQL, nikdy Python smyčka nad řádky fronty (stejná disciplína
   jako `app/services/ops_dashboard.py`). Historie stránkovaná přes index
   `(schedule_id, scheduled_for DESC)`.
5. Detail runu (`app/templates/runs/detail.html`) doplnit o "Spuštěno
   plánovačem · rozvrh vytvořil X · zobrazit rozvrh" pro runy se
   `trigger_type='scheduled'` (decision 21).
6. Nav odkaz jen pro admina/editora přes `can_schedule()`, ne inline
   `role in (...)`.
7. Jinja2 + HTMX, **žádný Vue ostrůvek** — jsou to tabulky a stavové
   odznaky, ne sdílený client-side stav (`TASKS_PHASE4.md` design
   decision 1 není splněný).

**Done when:** `/schedules` ukazuje všechny tři pohledy na reálných datech
z dry-runu; stavový pruh správně přepne na "neodpovídá" po zastavení
workeru; viewer 403; responsive ověřené.

**Expected commit:** `feat(scheduler): add schedules monitoring page`

---

## T7 — Dead letter a ruční zásah

**Target:** `app/routers/schedules.py`, `app/services/queue.py`,
`app/templates/schedules/index.html`, i18n, testy

1. `POST /schedules/queue/{id}/retry` — založí novou položku fronty ze
   staré (nová `scheduled_for = now()`, `source` zachován, odkaz na
   původní položku v payloadu). Nikdy nemění původní řádek na `queued`
   zpět — historie se nepřepisuje.
2. Hromadná akce "Zkusit znovu všechny chyby" nad aktuálním filtrem
   (např. "všech 12 chyb z dnešní noci").
3. `POST /schedules/queue/{id}/cancel` pro čekající položku.
4. Guard: opakování respektuje stropy z T10 i dry-run.
5. Testy: retry vytvoří přesně jednu novou položku; cancel nefunguje na
   `leased` položku; hromadná akce nesáhne mimo filtr.

**Done when:** chybu z historie jde jedním kliknutím zopakovat; původní
záznam zůstane nedotčený.

**Expected commit:** `feat(scheduler): add queue retry and cancel actions`

---

## T8 — Oznámení

**Target:** nová `app/services/notifications.py`, nový balíček
`app/notifications/` (`base.py`, `inapp.py`), `app/routers/schedules.py`,
šablony, i18n, testy

1. `notify(db, event_type, payload, *, recipient_user_id=None)` — zapíše
   řádek do `notification_outbox` a zaloguje (decision 25).
2. Balíček kanálů podle vzoru `app/adapters/` — `base.py` definuje rozhraní
   `send(notification) -> None`, `inapp.py` je jediná dnešní implementace
   (označí řádek `sent`, obsah se čte z DB). **SMTP kanál se v této větvi
   nepíše** — jen se nechá místo, kam přibude soubor.
2b. `.env.example` dostane prázdné SMTP klíče (`SMTP_HOST`, `SMTP_PORT`,
   `SMTP_USER`, `SMTP_FROM`) s komentářem, že kanál zatím neexistuje — ať
   je v den zapínání jasné, co bude potřeba. Do rozhraní v `base.py`
   zapsat, že se při zapnutí doručovatele **nevyprazdňuje nahromaděná
   historie** (položky starší než pár dní → `suppressed`, decision 25).
3. Události zapojit na místa vzniku: `schedule.run_failed` (po vyčerpání
   pokusů), `schedule.window_skipped` (souhrnně za jeden průchod tickeru),
   `worker.stale` (heartbeat starší než 30 min — detekuje ho webová
   aplikace při zobrazení, ne mrtvý worker sám o sobě),
   `quota.exceeded` (T10), `schedule.owner_deactivated` (T9),
   **`schedule.expiring_soon`** (7 dní nebo 3 výskyty před koncem,
   decision 31 — pojistka proti tomu, aby sběr dat tiše skončil) a
   **`budget.threshold_exceeded`** (měsíční rozpočet klienta, decision 33;
   **neblokuje**, jen upozorní).
4. In-app zobrazení: odznak s počtem nepřečtených na `/schedules` + seznam.
5. Testy: `notify` zapíše řádek; opakované selhání téhož rozvrhu
   negeneruje řádek na každý pokus, jen na finální selhání.

**Done when:** simulované selhání runu ve frontě vyrobí oznámení viditelné
na `/schedules`; outbox drží řádky i bez doručovacího kanálu.

**Expected commit:** `feat(scheduler): add notification outbox with in-app channel`

---

## T9 — Deaktivace uživatele pozastaví jeho rozvrhy

**Target:** `app/routers/users.py`, `app/services/schedule_monitor.py`
(nebo `schedules.py`), šablony, i18n, testy

1. Při přepnutí `user.is_active` na False: jeho aktivní rozvrhy →
   `is_active = false`, `inactive_reason = 'owner_deactivated'`, a jedno
   oznámení `schedule.owner_deactivated` adminům s počtem (decision 23).
2. Reaktivace uživatele rozvrhy **neobnoví** — na `/schedules` se zobrazí
   upozornění s akcí "převzít" (admin si je přepíše na sebe přes
   `updated_by_user_id`) nebo "obnovit".
3. Test: deaktivace uživatele s dvěma aktivními rozvrhy je oba pozastaví a
   vyrobí právě jedno oznámení; reaktivace je nezapne.

**Done when:** scénář z testu projde i ručně v prohlížeči.

**Expected commit:** `feat(scheduler): pause schedules when their owner is deactivated`

---

## T10 — Stropy

**Target:** `app/services/run_execution.py`, `app/services/queue.py`,
`app/config.py`, `app/routers/clients.py` + šablona (pole `priority` a denní
limit), i18n, testy

1. **Denní limit runů na klienta** — počítadlo runů za posledních 24 h;
   při překročení se položka označí `skipped` / `quota_exceeded` a vyvolá
   `quota.exceeded`. Vynuceno v `run_execution.py`, takže platí i pro ruční
   trigger (decision 26). Limit jako sloupec na klientovi, výchozí hodnota
   z configu.
1b. **Měsíční finanční hranice na klienta** (`clients.monthly_budget_usd`,
   decision 33) — při překročení se vyvolá `budget.threshold_exceeded` a
   **nic se nezastaví**. Počítá se ze skutečných cen už proběhlých runů
   (`app/services/cost.py`), ne z odhadu. Jedno oznámení za měsíc a
   klienta, ne za každý run nad hranicí.
2. **Hloubka fronty na klienta** (default 500 čekajících) — další zařazení
   se odmítne s oznámením místo neomezeného růstu. U set-level rozvrhu se
   kontroluje **před** rozstřelem (T5b bod 5).
3. **Souběh na providera** — semafor v workeru (default 1, konfigurovatelné
   na 2–3). Kapacitní výpočet pro dokumentaci: run ~15 s → jeden worker
   sekvenčně ~240 runů/h → set-level rozvrh 120 runů ≈ 30 min, denní dávka
   600 runů ≈ 2,5 h; jeden worker tedy stačí, souběh je rezerva.
4. Admin UI na detailu klienta: priorita (`clients.priority`), denní limit
   runů (`clients.daily_run_limit`) a měsíční rozpočet v USD
   (`clients.monthly_budget_usd`). Prázdné pole = beze změny chování
   (limit z configu, rozpočet žádný).
5. Testy: 21. run při limitu 20 → `skipped`/`quota_exceeded` + oznámení;
   ruční trigger při vyčerpaném limitu → 409 se srozumitelnou hláškou přes
   `AppError`, ne 500.

**Done when:** limity fungují na obou cestách (worker i ruční trigger).
**Teprve po tomhle tasku je bezpečné vypnout `SCHEDULER_DRY_RUN`.**

**Expected commit:** `feat(scheduler): enforce per-client run quotas and concurrency limits`

---

## T11 — Závěrečný průchod

**Target:** `app/i18n/en.json`, `app/i18n/de.json`, šablony, `tests/`,
`docs/REQUIREMENTS.md`

1. i18n úplnost — projít všechny nové šablony, ověřit, že žádná
   uživatelská prosa není natvrdo v šabloně (`AI_INSTRUCTIONS.md` §3).
2. Responsive ~375 / 768 / desktop na `/schedules` (všechny tři pohledy),
   formuláři rozvrhu a seznamech na detailu promptu i klienta — skutečně
   v prohlížeči, ne "mělo by fungovat".
3. Doplnit `docs/REQUIREMENTS.md`: FR-9 přestává být "mimo scope", nové NFR
   pro plánovanou útratu (stropy, dry-run) a pro hranici přístupu
   k `/schedules`.
4. Celý `pytest` zelený.

**Done when:** vše výše ověřené a odškrtnuté s uživatelem.

**Expected commit:** `docs(requirements): record scheduler behaviour and quota limits`

---

## Co tahle větev VĚDOMĚ nedělá

- **Ruční run přes frontu** (varianta B) — schéma je připravené
  (decision 5), přepnutí je samostatný pozdější úkol.
- **Archivaci klientů (`clients.is_active`)** — zvažováno a zamítnuto
  2026-09-18 (decision 31). Povinné ukončení rozvrhu pokrývá případ
  "klient utichl"; nový stav klienta je průřez šesti obrazovkami a patří
  do vlastní větve.
- **"Study"** (dávka promptů × modelů) — `run_queue.batch_id` a
  `source='batch'` jsou přípravou, ne implementací.
- **SMTP kanál oznámení** — outbox a rozhraní kanálu ano, doručovatel ne
  (appka nemá SMTP server).
- **`users.can_schedule`** — jen funkce `can_schedule()`, sloupec až bude
  potřeba (decision 22).
- **Fair-share řazení fronty** — dnešní lexikografická priorita stačí;
  změna bude jeden `ORDER BY`, ne migrace (decision 20).

---

## Completion Checklist (až je branch hotová a smergnutá)

- Doplnit do `docs/TASKS.md` odkaz na tuhle branch (stejný vzor jako
  ostatní položky).
- Upravit `docs/ROADMAP.md` — přepnout stav #5 na hotovo, poznamenat, co
  z toho plyne pro "Study" koncept a pro #14 (Client-view portál).
- Opravit v `docs/ROADMAP.md` tabulku `## Stav`: #4 a #11 jsou od PR #13 a
  PR #15 smergnuté, tabulka je pořád hlásí jako "čeká na merge".
- Aktualizovat `schema_phase1.sql` nebo založit jeho ekvivalent pro tuhle
  fázi, aby zůstal autoritativním popisem schématu.
- Zvážit záznam do `docs/REQUIREMENTS.md` o tom, že plánovač je první část
  appky, která utrácí bez interakce uživatele — a jaké pojistky to má.
