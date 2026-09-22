# SignalMap — Claude Code Session Prompts: Scheduler

## v1.2 | Září 2026
##
## v1.2 (2026-09-21) — přibyl prompt SCH-5c (upozornění na překryv
## rozvrhů), objevil se v konverzaci při SCH-5b jako reálný scénář: dva
## nezávislé rozvrhy (jeden na prompt, jeden na set obsahující ten samý
## prompt) se stejným modelem/personou se nijak nekontrolují a obě
## proběhnou. Design decision 36 v TASKS_SCHEDULER.md.
##
## v1.1 (2026-09-18) — revize po zpětné vazbě od Philipa. Přibyl prompt
## SCH-5b (set-level rozvrh) a do SCH-0/1/5/8/10 povinné ukončení rozvrhu,
## multi-select modelů a person, měsíční rozpočet klienta a rozšíření
## indexu z migrace 0024. Design decisions 31-35 v TASKS_SCHEDULER.md.
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-scheduler (z aktuálního master)
## 2. Čtrnáct kódových promptů (SCH-0 až SCH-11 včetně SCH-5b a SCH-5c),
##    POŘADÍ VYNUCENÉ — viz
##    docs/TASKS_SCHEDULER.md "Task Index" pro odůvodnění (SCH-1 a SCH-2 jsou
##    nezávislé stavební kameny, které SCH-3 potřebuje oba; SCH-4 bez SCH-3
##    nemá co spouštět; SCH-5 zakládá data, která SCH-6 zobrazuje).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická
##    kontrola daného promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_SCHEDULER.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-30: docs/TASKS_SCHEDULER.md
##    — přečti si konkrétní task ID před psaním kódu, ideálně celý soubor
##    před SCH-0.
## 9. ŽÁDNÁ NOVÁ ZÁVISLOST (design decision 30). Fronta je Postgres +
##    FOR UPDATE SKIP LOCKED, rekurence je zoneinfo ze standardní knihovny.
##    Pokud se během implementace zdá, že je potřeba APScheduler, Celery,
##    Redis, dateutil nebo croniter — ZASTAV a zeptej se. To by byl scope
##    creep mimo to, co bylo odsouhlaseno.
## 10. PENÍZE. Tahle větev je první část appky, která volá placené API bez
##     člověka u klávesnice. Dvě idempotence (decisions 12 a 13) a dry-run
##     (decision 15) nejsou "nice to have" — jsou důvod, proč je bezpečné to
##     vůbec pustit. Pokud by kterýkoliv krok znamenal je obejít, ZASTAV.
## 10b. ČÍSLO MIGRACE. docs/TASKS_PRE_SCHEDULER.md si bere 0029 a jde před
##     touhle větví. Pokud je smergnutá dřív, je plánovačova migrace 0030 —
##     ne 0029, jak je psáno v SCH-0 a v TASKS dokumentu. Ověř to při SCH-0.
## 11. SCHEDULER_DRY_RUN zůstává zapnutý až do dokončení SCH-10 (stropy).
##     Ostrý provoz zapíná uživatel, ne agent.
## 12. ŽÁDNÝ ROZVRH BEZ KONCE (decision 31). `CHECK (num_nonnulls(ends_on,
##     max_occurrences) = 1)` je v databázi, ne jen ve formuláři. Pokud by
##     kterýkoliv krok znamenal ho obejít nebo zavést "nekonečný" rozvrh —
##     ZASTAV. Zapomenutý věčný rozvrh utrácí tiše dál.
## 13. Evidence se nikdy nepřepisuje (NFR-6). Run/RawResponse/Citation se
##     jen vkládají; historie ve frontě (skipped/error řádky) se taky
##     nepřepisuje — opakování vytváří NOVOU položku, ne úpravu staré.
## 14. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na
##     tuhle větev a upravit docs/ROADMAP.md §5 (viz Completion Checklist
##     v TASKS_SCHEDULER.md).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-scheduler.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_SCHEDULER.md — CELÉ, hlavně design decisions 1-30

KONTEXT: Fáze 1-6 (docs/TASKS.md) a navazující branche (export, bulk import,
ChatGPT/persony/pricing, local time fix, cost components, ops dashboard,
Gemini citace) jsou hotové a smergnuté do master. Tahle větev realizuje
ROADMAP.md §5 — Scheduler: automatické, opakované spouštění runů.

ARCHITEKTURA VE TŘECH VRSTVÁCH (design decision 1):
  run_schedules  — pravidlo opakování ("každé Po a Čt v 6:00")
  run_queue      — konkrétní výskyt a zároveň historie ("pondělí 6:00")
  Run + RawResponse + Citation — evidence, beze změny

Plánovač (ticker) rozhoduje KDY a zapisuje do fronty. Vykonavatel
(executor) bere z fronty a volá providera. Obojí běží v jednom novém
procesu `app/worker.py`, ve vlastní Compose službě ze STEJNÉHO image.

KRITICKÉ:
- Žádná nová závislost. Fronta = Postgres FOR UPDATE SKIP LOCKED, žádný
  Celery/Redis/broker. Rekurence = zoneinfo ze standardní knihovny
  (ověřeno, že v python:3.14-slim funguje včetně Europe/Prague).
- Dvě idempotence, obě chrání peníze: UNIQUE (schedule_id, scheduled_for)
  proti dvojímu zařazení (decision 12), a run_id zapsané na řádek fronty
  PŘED voláním adaptéru proti dvojímu zaplacení po pádu workeru
  (decision 13).
- Zmeškaná okna se NEDOHÁNĚJÍ (decision 11) — lhůta 6 h, pak skipped.
- next_run_at se počítá z termínu okna, NIKDY z now() (decision 9) — jinak
  rozvrh driftuje.
- Časová zóna: ukládá se pravidlo v lokálním čase + IANA zóna, ne hotový
  UTC okamžik (decision 7). fold=0 pro dvojznačný čas na podzim
  (decision 8) — jinak dva zaplacené runy.
- Exekuce runu je sdílená mezi routerem a workerem přes
  app/services/run_execution.py — žádná duplikace (decision 4).
- Ruční run zůstává v této větvi synchronní (decision 5). Fronta ho umí
  přijmout, ale router ho tam zatím nedává.
- Atribuce: Run.triggered_by_user_id = NULL + trigger_type='scheduled'
  (decision 21) — přesně to, co app/services/ops_dashboard.py už umí
  klasifikovat jako pseudo-uživatele "Scheduler". NEMĚNIT to tak, aby se
  run přiřadil člověku, co rozvrh založil.
- SCHEDULER_DRY_RUN je zapnutý po celou dobu vývoje větve.
- Každý rozvrh má POVINNÝ konec: datum (`ends_on`) nebo počet opakování
  (`max_occurrences`), přesně jedno z nich, vynuceno CHECK constraintem
  (decision 31). Nejzazší datum se řídí frekvencí: denně 30 dní, týdně
  6 měsíců, měsíčně 12 měsíců.
- Rozvrh míří na víc modelů i víc person (`model_ids`, `persona_ids` —
  decision 34) a od SCH-5b i na celý prompt set (decision 32). Násobení je
  proto trojí a odhad ve formuláři je jediná ochrana před překvapením.
- `clients.is_active` se NEPŘIDÁVÁ (zvažováno a zamítnuto 2026-09-18).
```

---
---

## SCH-0 — Migrace 0029: schéma plánovače

Viz `docs/TASKS_SCHEDULER.md` T0 pro plný seznam cílových souborů a kroků,
a sekci "Nové schéma (migrace 0029)" pro přesný tvar tabulek. Shrnutí:
čtyři nové tabulky (`run_schedules`, `run_queue`, `worker_heartbeats`,
`notification_outbox`), tři sloupce na `clients` (`priority`,
`daily_run_limit`, `monthly_budget_usd`), tři indexy na frontě včetně
partial indexu pro výběr.

**Ve v1.1 navíc:** sloupce ukončení na `run_schedules` (`starts_on`,
`ends_on`, `max_occurrences`, `occurrences_count`) s CHECK constraintem
(decision 31), `persona_ids` jako pole místo `persona_id` (decision 34),
unikátní klíč fronty přes PĚT sloupců
`(schedule_id, scheduled_for, prompt_id, model_id, persona_id)` místo dvou
(decision 12 ve znění v1.1 — jinak by z 225 položek jednoho okna prošla
jedna), a **rozšíření existujícího** `idx_runs_one_pending_per_prompt_model`
na `(prompt_id, model_id, persona_id, market_id)` (decision 35).

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T0 v TASKS dokumentu) a počkej na potvrzení, než
začneš psát kód.

**Kritické:** indexy a unique constraint musí být i v `__table_args__` na
ORM modelech, ne jen v migraci — testovací sada staví schéma přes
`Base.metadata.create_all()`, takže by jinak testovala jinou databázi, než
jaká běží v produkci. Přesně ten precedent, který si `app/models/run.py`
zapsal u `idx_runs_one_pending_per_prompt_model`.

**Po dokončení:**
1. `docker compose up -d --build`
2. `docker compose exec app alembic upgrade head`, pak `downgrade -1`, pak
   znovu `upgrade head` — obojí musí projít bez ruční opravy.
3. V psql: `\d run_queue` — ověřit partial index `WHERE status = 'queued'`
   a pětisloupcový unique constraint. `\d runs` — ověřit, že rozšířený
   `idx_runs_one_pending_per_prompt_model` má čtyři sloupce a pořád má
   `WHERE status = 'pending'`.
3b. `\d run_schedules` — ověřit CHECK na ukončení: pokus vložit rozvrh
   s `ends_on IS NULL AND max_occurrences IS NULL` musí selhat, stejně tak
   s oběma vyplněnými.
4. `pytest -v` (existující sada nesmí spadnout na nových modelech)
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(infra): add scheduler, run queue and notification tables
```

### DONE — commit 4be2d07

---

## SCH-1 — Rekurence: `compute_next_run_at()` + DST testy

Viz `docs/TASKS_SCHEDULER.md` T1. Shrnutí: `app/services/scheduling.py`
s jedinou čistou funkcí `compute_next_run_at(schedule, *, after: datetime)`,
a tabulkové testy v `tests/test_scheduling_recurrence.py`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické (design decisions 7-10):**
- Funkce **nesmí volat `now()`** ani sáhnout do DB. Čas vstupuje výhradně
  parametrem `after`. Bez toho je scheduler netestovatelný a chyba v něm se
  projeví jednou za půl roku jako záhada.
- `next_run_at` se počítá **z termínu okna**, ne z okamžiku skutečného
  spuštění — jinak se rozvrh každým během posune o pár minut.
- Dvojznačný čas na podzim → **`fold=0`**. Ověřené chování: 2026-10-25
  02:30 Europe/Prague dá s `fold=0` 00:30 UTC, s `fold=1` 01:30 UTC. Bez
  pevného `fold=0` by vznikly dva různé `scheduled_for`, tedy dva
  legitimní řádky fronty a dvakrát zaplacený run.
- Neexistující čas na jaře → první platný okamžik po skoku. Ověřené
  chování: 2027-03-28 02:30 Europe/Prague Python **nevyhodí výjimku**, tiše
  vrátí 01:30 UTC (= 03:30 lokálně). Detekuj to round-tripem
  local → UTC → local a normalizuj vědomě, nespoléhej na náhodu.
- Měsíční rozvrh na den nad délku měsíce → ořez na poslední den, ne
  přeskočení měsíce.
- **Konec rozvrhu (decision 31):** funkce vrací `None`, když by další
  termín byl po `ends_on` nebo když `occurrences_count >= max_occurrences`.
  `None` znamená pro ticker "rozvrh doběhl" → `is_active = false`,
  `inactive_reason = 'completed'`, `next_run_at = NULL`. Termín přesně
  v den `ends_on` se ještě vykoná.
- Přidej i `max_end_date(frequency, *, starts_on)` pro formulář (denně
  30 dní, týdně 6 měsíců, měsíčně 12 měsíců) — taky čistá funkce.

Referenční ověřená čísla pro testy (Europe/Prague):
```
2026-09-21 06:00 CEST -> 04:00 UTC
2026-10-23 06:00 CEST -> 04:00 UTC
2026-10-26 06:00 CET  -> 05:00 UTC
2027-03-26 06:00 CET  -> 05:00 UTC
2027-03-30 06:00 CEST -> 04:00 UTC
```

Očekávané hodnoty v testech piš **natvrdo**, nikdy je nepočítej stejnou
funkcí, kterou testuješ.

**Po dokončení:**
1. `pytest tests/test_scheduling_recurrence.py -v`
2. Ověřit, že v `app/services/scheduling.py` není ani jedno volání
   `datetime.now()` / `utcnow()`.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add timezone-aware recurrence calculation
```

### DONE — commit 4ccba86

---

## SCH-2 — Vytažení exekuce do `run_execution.py`

Viz `docs/TASKS_SCHEDULER.md` T2. Shrnutí: přesun exekuční logiky z
`trigger_run` (`app/routers/runs.py`, ~200 řádků) do nové
`app/services/run_execution.py`, volatelné bez `Request`.

Nejdřív navrhni CO uděláš + PROČ, včetně přesné signatury `execute_run`,
a počkej na potvrzení.

**Kritické:** tenhle prompt **nemění žádné chování**. Je to čistý refaktor
a jeho jediným viditelným výsledkem je, že všechny existující testy pořád
procházejí beze změny očekávání. Pokud by sis myslel, že je při té
příležitosti vhodné něco opravit nebo zlepšit — NE. Zapiš to a řekni mi to,
opravíme to jinde.

Dvě věci, které se při přesunu snadno rozbijí:
- Komentáře a docstringy, které vysvětlují PROČ (TOCTOU race a
  `IntegrityError` backstop na migraci 0024, `db.expire_on_commit = False`
  před commitem, best-effort chování analysis skills) musí jít **s kódem**,
  ne zmizet. Vznikly z code review a popisují netriviální rozhodnutí.
- Služba nesmí potřebovat `Request` ani `t()` — worker žádný request nemá.
  Lokalizované chybové hlášky zůstávají v routeru.

**Po dokončení:**
1. `docker compose up -d --build`
2. `pytest -v` — celá sada zelená, **bez úprav existujících očekávání**.
3. V prohlížeči ruční run: úspěšný běh, redirect na detail runu, a dvojklik
   na tlačítko → 409 "run already pending" jako dřív. Použij klienta a
   prompt podle mého zadání a model `gemini-3.1-flash-lite`.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
refactor(runs): extract run execution into a shared service
```

### DONE — commit eef393a

---

## SCH-3 — Worker: ticker, fronta, lease, rekonciliace

Viz `docs/TASKS_SCHEDULER.md` T3. Shrnutí: `app/services/queue.py`
(operace nad frontou, testovatelné bez procesu) + `app/worker.py` (smyčka)
+ `tests/test_worker_queue.py`. Nové config klíče v `app/config.py`.

Nejdřív navrhni CO uděláš + PROČ, včetně přesného SQL pro `claim_next` a
seznamu stavů, do kterých se položka může dostat, a počkej na potvrzení —
tohle je nejvíc nový kód v celé větvi a stojí za to potvrdit kontrakt před
psaním.

**Kritické — tohle jsou místa, kde chyba stojí peníze:**
1. `run_id` se zapíše na řádek fronty a **commitne PŘED** voláním adaptéru
   (decision 13). Položka, která už `run_id` má, se **nikdy** nevolá znovu.
2. Rekonciliace: `Run` ve stavu `pending` starší než 30 min → `error`
   s hláškou ve smyslu "worker interrupted; provider call outcome unknown".
   Běží při startu workeru a pak periodicky. Přiznaná nejistota je
   správnější než tiché dvojí zaplacení.
3. `SCHEDULER_DRY_RUN=true` → položka `skipped`/`dry_run`, **adapter se
   nezavolá**, `Run` nevznikne. Ověř to testem s mockem, ne očima.
4. Lhůta pro zmeškaná okna se kontroluje na OBOU místech — při zařazení
   i při výběru z fronty (decision 11).
5. Kolize s běžícím `pending` runem → `deferred` + odklad, **ne `error`**
   (decision 17). Neaktivní prompt/model → `skipped`, ne `error`
   (decision 18).
6. Retry jen pro transportní chyby a 429 (3 pokusy, odklad 1/5/25 min).
   Chyba, kterou provider vrátil po odpovědi, je terminální — `Run` se
   stejně uloží se `status='error'` a evidence se nezahazuje.

**Po dokončení:**
1. `pytest tests/test_worker_queue.py -v`
2. `docker compose exec app python -m app.worker` (dočasně, ručně) —
   ověřit heartbeat v logu a v tabulce `worker_heartbeats`.
3. Vložit ručně jeden rozvrh do `run_schedules` s `next_run_at` v minulosti
   a ověřit v dry-runu: vznikne položka fronty, dostane stav
   `skipped`/`dry_run`, **nevznikne `Run`** a nevolal se provider.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add queue worker with ticker, leases and reconciliation
```

### DONE — commit e6dbb1a

---

## SCH-4 — `worker` služba v Compose

Viz `docs/TASKS_SCHEDULER.md` T4. Shrnutí: nová služba `worker` v
`docker-compose.yaml` ze stejného image jako `app`, `.env.example`,
provozní odstavec v `README.md`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické — tři věci, které jinak stojí jeden zbytečný debug cyklus:**
1. Worker **nesmí spouštět `alembic upgrade head`**. `CMD` v `dockerfile`
   ho spouští pro službu `app`; dva souběžné `alembic upgrade` proti jedné
   databázi je závod o migrační zámek. Proto `command:` přepisuje `CMD` a
   `depends_on: app: condition: service_healthy` zaručí, že worker startuje
   až po doběhnutí migrací.
2. `SECRET_KEY` je pro worker **povinné**, i když nevydává žádné cookies —
   v `app/config.py` je `secret_key: str` bez defaultu, takže jakýkoliv
   import `get_settings()` (a ten worker udělá přes `app.database`) bez něj
   spadne při startu.
3. Healthcheck nemůže být HTTP — worker neposlouchá na portu. Použij stáří
   touchfile, který worker přepisuje v každé iteraci smyčky.

Dál: `image:` pojmenovat u `app` a worker ať ho jen použije bez vlastního
`build:` (jeden build, identický kód v obou kontejnerech);
`stop_grace_period: 120s` (volání providera trvá 5–28 s a worker musí
stihnout dokončit rozdělanou položku); `init: true` kvůli doručení SIGTERM.
Caddy se nemění — worker zvenku dostupný není a být nemá.

**Po dokončení:**
1. `docker compose up -d --build`
2. `docker compose ps` — worker `healthy`
3. `docker compose logs -f worker` — heartbeat
4. `docker compose stop worker` — doběhne bez SIGKILL (sleduj čas)
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
chore(infra): add scheduler worker service to Compose
```

### DONE — commit bacc764

---

## SCH-5 — `can_schedule()` + CRUD rozvrhů

Viz `docs/TASKS_SCHEDULER.md` T5. Shrnutí: `can_schedule()` v
`app/templating.py`, `app/routers/schedules.py`,
`app/templates/schedules/form.html`, seznam rozvrhů jako partial na detailu
promptu a klienta, i18n DE/EN.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- `can_schedule(user)` je **jediné místo**, kde se rozhoduje "kdo smí
  plánovat" (decision 22). V1 vrací `user.role in ("admin", "editor")`.
  Nikde v šablonách ani routerech nesmí být `role in (...)` natvrdo — až
  přibude `users.can_schedule`, mění se jedna funkce, ne dvacet míst.
  **Sloupec `users.can_schedule` teď NEPŘIDÁVEJ.**
- Skrytí tlačítka v UI **nenahrazuje** serverovou kontrolu — router má
  `require_role("admin", "editor")` nezávisle.
- Pole `timezone` se ve formuláři **nezobrazuje**, ale ukládá
  (`Europe/Prague`) — decision 7.
- **Konec rozvrhu je povinný** (decision 31): přepínač "konec datem" /
  "konec počtem opakování", validace v routeru přes `AppError`, ne jen
  `required` v HTML. Horní mez data podle frekvence z `max_end_date()`
  (SCH-1). Do UI napiš PROČ — jinak to vypadá jako šikana formuláře.
- Modely i persony jsou **multi-select** (decision 34), ne jedna hodnota.
- V SCH-5 jen `target_type='prompt'`. Set-level je samostatný prompt
  SCH-5b hned za tímhle — nedělej obojí v jedné session.
- Delete guard: `data-confirm="{{ t('schedules.delete_confirm') }}"` přes
  sdílený delegovaný listener v `base.html`, **nikdy**
  `onsubmit="return confirm('...')"` — rozbije se na apostrofu v překladu.
- Náhled příštích pěti termínů + odhad ceny přímo ve formuláři
  (decision 29), jako HTMX partial. Žádný nový JS framework. Odhad má
  **dvě čísla i počet runů**: za jedno okno a za celý rozvrh do konce
  (decision 34) — to druhé jde spočítat právě proto, že konec je povinný.

**Po dokončení:**
1. `docker compose up -d --build`
2. V prohlížeči: založit rozvrh na promptu, ověřit že náhled termínů
   odpovídá pravidlu (zkontroluj i "2× týdně"); upravit; pozastavit;
   smazat (s potvrzením).
2b. Zkusit uložit rozvrh bez konce → musí to odmítnout se srozumitelnou
   hláškou (ne 500). Založit rozvrh "denně, 3 opakování" a ověřit, že
   náhled ukáže právě tři termíny a pak konec.
3. Přihlásit se jako viewer → 403 na `/schedules/new` i na POST routách.
4. Responsive ~375 / 768 / desktop na formuláři i na seznamu.
5. `pytest -v`
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add schedule CRUD with occurrence and cost preview
```

### DONE — commit 19da629

---

## SCH-5b — Set-level rozvrh (celý prompt set)

Viz `docs/TASKS_SCHEDULER.md` T5b a decision 32. Shrnutí: volba "celý
prompt set" ve formuláři rozvrhu, rozstřel okna na prompty × modely ×
persony se společným `batch_id`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické — tohle je prompt, kde se z jednoho kliknutí stanou stovky
placených volání:**
1. Prompty setu se resolvují **až při zařazení** (decision 6), ne při
   založení rozvrhu — prompt přidaný do setu příští týden se zahrne sám.
   Neaktivní prompt se přeskočí bez chyby (decision 18).
2. Idempotence je pětisloupcová
   `(schedule_id, scheduled_for, prompt_id, model_id, persona_id)`.
   **Ověř, že to migrace z SCH-0 opravdu takhle má** — s dvousloupcovým
   klíčem by z 225 položek okna prošla jedna, a při restartu tickeru
   uprostřed rozstřelu by se dávka založila podruhé.
3. Odhad ve formuláři musí ukázat celé násobení: "25 promptů × 3 modely ×
   3 persony = 225 runů na okno, 7 oken, odhadem $X". Je to jediná
   ochrana, kterou uživatel uvidí **před** uložením.
4. Strop hloubky fronty (SCH-10) se kontroluje **před** rozstřelem, ne po
   něm — jinak se 225 položek vloží a teprve pak se zjistí, že se nevešly.
5. Na `/schedules` je dávka **jeden řádek s rozpadem** podle `batch_id`,
   ne 225 samostatných řádků historie.

**Po dokončení:**
1. `pytest -v` — vč. testu, že rozstřel dá přesně N × M × P položek se
   shodným `batch_id` a že opakované zařazení téhož okna nic nezduplikuje.
2. V prohlížeči (pořád v dry-runu): založit rozvrh na prompt setu podle
   mého zadání, ověřit počet položek ve frontě proti číslu z odhadu.
3. Přidat prompt do setu a ověřit, že se v dalším okně zahrne.
4. Responsive ~375 / 768 / desktop na formuláři se set-level volbou.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add prompt-set level schedules
```

### DONE — commit 9d8b6ec

---

## SCH-5c — Upozornění na překryv rozvrhů

Viz `docs/TASKS_SCHEDULER.md` T5c a design decision 36. Shrnutí: vznikl
z reálného scénáře objeveného při SCH-5b — rozvrh na prompt (54, 8:30)
a nezávislý rozvrh na prompt set (9, 8:45), který prompt 54 obsahuje,
se stejným modelem/personou nijak nekolidují a oba proběhnou; nic
existujícího to nehlídá (idempotence z SCH-0 chrání jen dvojí zařazení
TÉHOŽ okna, ne dvě různá okna se stejným obsahem). Řešení: `find_
overlapping_schedules(db, *, client_id, exclude_schedule_id, prompt_ids,
model_ids, persona_ids)` v `app/routers/schedules.py`, zapojené do
`GET /schedules/preview`, a nový amber warning blok v
`_occurrence_preview.html`.

Nejdřív navrhni CO uděláš + PROČ, včetně přesného SQL/ORM dotazu pro
`find_overlapping_schedules` a přesného tvaru warning bloku, a počkej na
potvrzení.

**Kritické:**
- **Warn, never block** (decision 36, stejná filozofie jako odhad ceny
  v decision 29). Překryv může být záměrný (např. vědomě chceš dvojí
  pokrytí kvůli výpadku providera) — appka o tom jen informuje, nikdy
  neodmítne uložení.
- Překryv = průnik na **prompt_id AND model_id AND persona_id** napříč
  jinými aktivními rozvrhy stejného klienta. Částečná shoda (jen model,
  jen prompt) se **nehlásí** — jinak by upozornění dostal skoro každý
  druhý rozvrh a lidi by ho začali ignorovat.
- Porovnávají se jen `is_active = true` rozvrhy jiné než ten upravovaný
  (`exclude_schedule_id`) — pozastavený rozvrh nekolidoval nikdy.
- Set-level rozvrh (SCH-5b) se do porovnání zapojuje přes **resolvnutý
  seznam promptů** (stejná funkce jako při zařazení do fronty), ne přes
  `prompt_set_id` — jinak by se rozvrh na set neporovnal s rozvrhem na
  jeden jeho prompt.
- Upozornění se počítá živě v `GET /schedules/preview` (stejný HTMX
  partial jako náhled termínů a ceny z SCH-5), ne při uložení — uživatel
  ho musí vidět **před** kliknutím na Uložit, ne až po chybové hlášce.

**Po dokončení:**
1. `pytest -v` — vč. testů: překryv detekován při shodě prompt+model+
   persona; nedetekován při částečné shodě; nedetekován při porovnání
   rozvrhu sama se sebou při editaci; nedetekován proti pozastavenému
   rozvrhu.
2. V prohlížeči: založit rozvrh na promptu 54 (8:30), pak založit rozvrh
   na prompt setu 9 obsahujícím prompt 54 (8:45, stejný model/personu) →
   amber upozornění se zobrazí v náhledu, uložení projde bez blokace.
3. Responsive ~375 / 768 / desktop na warning bloku.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): warn on overlapping schedule targets
```

### DONE — commit e57635b

---

## SCH-6 — `/schedules`: rozvrhy, fronta, historie

Viz `docs/TASKS_SCHEDULER.md` T6. Shrnutí: monitorovací stránka se třemi
pohledy, stavový pruh workeru z heartbeatu, doplnění detailu runu o
atribuci plánovače.

**Ve v1.2 navíc:** pohled "Rozvrhy" má u každého rozvrhu odznak "překrývá
se s N dalšími" z `find_overlapping_schedules` (SCH-5c, decision 36) —
stejné pravidlo, jen teď viditelné trvale, ne jen v náhledu při ukládání.

Nejdřív navrhni CO uděláš + PROČ, včetně tvaru každého ze tří pohledů, a
počkej na potvrzení.

**Kritické:**
- **Jinja2 + HTMX, žádný Vue ostrůvek.** Jsou to tabulky a stavové
  odznaky, ne sdílený client-side stav — podmínka z `TASKS_PHASE4.md`
  design decision 1 není splněná.
- Odznak překryvu se počítá **jednou pro všechny rozvrhy stránky**, ne
  voláním `find_overlapping_schedules` v cyklu (N+1 dotazů) — se
  stovkami rozvrhů klienta by to stránku znatelně zpomalilo.
- Stavový pruh workeru (decision 19) je důvod, proč heartbeat vůbec
  existuje: monitoring, který při vypnutém workeru vypadá stejně jako
  prázdná fronta, aktivně lže. Musí umět `Plánovač běží · poslední signál
  před 8 s` i `Plánovač neodpovídá 3 h 20 min`. V dry-runu výrazně odlišný
  pruh, aby nikdo omylem nečekal reálné runy.
- Agregace v SQL, nikdy Python smyčka nad řádky fronty — stejná disciplína
  jako `app/services/ops_dashboard.py`.
- Ukaž **stáří nejstarší čekající položky** — první ukazatel, který řekne
  "nestíháme", dřív než hloubka fronty.
- Na detailu runu doplnit "Spuštěno plánovačem · rozvrh vytvořil X" pro
  `trigger_type='scheduled'` (decision 21).

**Po dokončení:**
1. `docker compose up -d --build`
2. `/schedules` — všechny tři pohledy na datech z dry-runu.
2b. Ověřit odznak překryvu na dvou rozvrzích ze SCH-5c (prompt 54 a
   prompt set 9) a že se na stránce s víc rozvrhy negeneruje N+1 dotazů
   (zkontrolovat SQL log / počet dotazů).
3. `docker compose stop worker`, počkat, obnovit stránku → pruh musí
   přepnout na "neodpovídá". Pak `docker compose start worker`.
4. Viewer → 403. Responsive ~375 / 768 / desktop na všech třech pohledech.
5. `pytest -v`
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add schedules monitoring page
```

### DONE — commit 5046b27

---

## SCH-7 — Opakování chyb a ruční zásah

Viz `docs/TASKS_SCHEDULER.md` T7. Shrnutí: retry/cancel na položce fronty,
hromadná akce nad filtrem "jen chyby".

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** opakování vytváří **NOVOU položku fronty**, nikdy nepřepisuje
stav té staré zpátky na `queued`. Historie se nepřepisuje — stejné pravidlo
jako u evidence (NFR-6). Cancel smí sáhnout jen na `queued`, nikdy na
`leased` položku (tu má worker právě v ruce).

**Po dokončení:**
1. Vyrobit chybnou položku (např. dočasně neplatný API klíč v dry-run
   vypnutém stavu na testovacím klientovi — nebo mockem v testu).
2. Zopakovat ji z UI, ověřit že vznikla nová položka a stará zůstala.
3. `pytest -v`
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add queue retry and cancel actions
```

### DONE — commit 31aeef9

---

## SCH-8 — Oznámení (outbox + in-app kanál)

Viz `docs/TASKS_SCHEDULER.md` T8. Shrnutí: `app/services/notifications.py`
s `notify()`, balíček kanálů `app/notifications/` (`base.py`, `inapp.py`),
zapojení sedmi událostí, in-app zobrazení na `/schedules`.

**Ve v1.1 navíc dvě události:** `schedule.expiring_soon` (7 dní nebo
3 výskyty před koncem — pojistka proti tomu, aby sběr dat tiše skončil,
decision 31) a `budget.threshold_exceeded` (měsíční rozpočet klienta,
decision 33 — **neblokuje**, jen upozorní).

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Kanály jsou balíček podle vzoru `app/adapters/<provider>.py` — přidat
  e-mail musí být "přidat soubor", ne "přepsat volající místa"
  (decision 25).
- **SMTP kanál v této větvi NEPIŠ.** Appka nemá SMTP server. Smyslem
  outboxu je, že se do té doby nic neztratí — ne aby ses pokoušel doručovat.
  Do `.env.example` ale dej prázdné `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER`
  / `SMTP_FROM` s komentářem, že kanál zatím neexistuje, a do `base.py`
  zapiš, že se při zapnutí doručovatele **nahromaděná historie
  nedoručuje** (starší položky → `suppressed`).
- Chybové události se budou jednou posílat jako **denní souhrn**, ne po
  jedné (40 e-mailů při výpadku providera si lidi odfiltrují do koše).
  Zapiš to do rozhraní/dokumentace, i když dnes doručovatel neexistuje.
- `worker.stale` detekuje webová aplikace při zobrazení stránky, ne mrtvý
  worker sám o sobě — mrtvý proces o sobě nic neohlásí.

**Po dokončení:**
1. Vyvolat selhání runu ve frontě (mock) → oznámení viditelné na
   `/schedules`, řádek v `notification_outbox` se stavem `pending`/`sent`.
2. `pytest -v`
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add notification outbox with in-app channel
```

### DONE — commit b0f50ae

---

## SCH-9 — Deaktivace uživatele pozastaví jeho rozvrhy

Viz `docs/TASKS_SCHEDULER.md` T9. Shrnutí: napojení na existující toggle
v `app/routers/users.py`, `inactive_reason='owner_deactivated'`, oznámení,
akce "převzít" na `/schedules`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** reaktivace uživatele rozvrhy **neobnovuje** (decision 23).
Tiché pokračování v placených runech za člověka, který ve firmě není, je
horší varianta než tiché zastavení — ale tiché zapnutí zpátky je taky
špatně. Obnovení je vědomá akce admina.

**Po dokončení:**
1. V prohlížeči: založit dva rozvrhy pod testovacím editorem, deaktivovat
   ho, ověřit že jsou oba pozastavené a vzniklo **jedno** oznámení;
   reaktivovat ho a ověřit, že se rozvrhy **nezapnuly**.
2. `pytest -v`
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): pause schedules when their owner is deactivated
```

### DONE — commit 704f269

---

## SCH-10 — Stropy: kvóty, hloubka fronty, souběh

Viz `docs/TASKS_SCHEDULER.md` T10. Shrnutí: denní limit runů na klienta,
měsíční finanční hranice, strop hloubky fronty, semafor souběhu na
providera, admin UI pro prioritu a limity klienta.

**Dva stropy, každý na jinou práci (decision 33):** denní limit runů
**blokuje** a vynucuje se v `run_execution.py`; měsíční rozpočet
(`clients.monthly_budget_usd`) **neblokuje**, jen vyvolá
`budget.threshold_exceeded` — skutečnou cenu runu známe až po něm, takže
zastavovat na odhadu by shodilo běžící benchmark kvůli nepřesnosti.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** limity se vynucují v `app/services/run_execution.py`, ne ve
workeru (decision 26). Kdyby je vynucoval jen worker, obejde je ruční
trigger a limit nic negarantuje. Ruční trigger při vyčerpaném limitu musí
dostat 409 přes `AppError` se srozumitelnou hláškou, ne 500.

**Tohle je poslední task před ostrým provozem.** Po jeho dokončení a
ověření můžeš (ty, ne agent) vypnout `SCHEDULER_DRY_RUN`.

**Po dokončení:**
1. Nastavit testovacímu klientovi denní limit 2, spustit 3 runy → třetí
   `skipped`/`quota_exceeded` + oznámení.
2. Ruční trigger při vyčerpaném limitu → 409 se srozumitelnou hláškou.
2b. Nastavit testovacímu klientovi měsíční rozpočet pod jeho dosavadní
   útratu → vznikne **jedno** oznámení a runy **běží dál**.
3. `pytest -v`
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): enforce per-client run quotas and concurrency limits
```

### DONE — commit abc3f97

---

## SCH-11 — Závěrečný průchod: i18n, responsive, docs

Viz `docs/TASKS_SCHEDULER.md` T11. Shrnutí: kontrola úplnosti i18n,
responsive průchod v prohlížeči, doplnění `docs/REQUIREMENTS.md`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** responsive se **ověřuje v prohlížeči** na ~375 / 768 /
desktop, ne konstatuje. `AI_INSTRUCTIONS.md` §7 to vyžaduje explicitně.
Projdi: `/schedules` (všechny tři pohledy), formulář rozvrhu, seznamy
rozvrhů na detailu promptu i klienta, detail runu s atribucí plánovače.

`docs/REQUIREMENTS.md`: FR-9 přestává být "mimo scope"; doplnit NFR pro
plánovanou útratu (stropy, dry-run, dvě idempotence) a pro hranici
přístupu k `/schedules`. Ukaž mi diff, neměň to potichu.

**Po dokončení:**
1. Grep přes nové šablony na natvrdo psanou prosu mimo `t()`.
2. Responsive průchod, výsledek popiš konkrétně (co jsi kde viděl).
3. `pytest -v` — celá sada.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(requirements): record scheduler behaviour and quota limits
```

### DONE — commit 10ae617

---
---

## PO DOKONČENÍ VŠECH PROMPTŮ

1. Projít Completion Checklist v `docs/TASKS_SCHEDULER.md`.
2. Nechat větev běžet v dry-runu alespoň týden, než se vypne
   `SCHEDULER_DRY_RUN` — sledovat na `/schedules`, jestli plánovaná okna
   sedí s tím, co lidi čekali (decision 15).
3. Teprve pak ostrý provoz, a první den zkontrolovat `/ops` — řádek
   "Scheduler" na ose Uživatel musí sedět s počtem oken na `/schedules`.
