# SignalMap — Claude Code Session Prompts: Scheduler

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-scheduler (z aktuálního master)
## 2. Dvanáct kódových promptů (SCH-0 až SCH-11), POŘADÍ VYNUCENÉ — viz
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
## 11. SCHEDULER_DRY_RUN zůstává zapnutý až do dokončení SCH-10 (stropy).
##     Ostrý provoz zapíná uživatel, ne agent.
## 12. Evidence se nikdy nepřepisuje (NFR-6). Run/RawResponse/Citation se
##     jen vkládají; historie ve frontě (skipped/error řádky) se taky
##     nepřepisuje — opakování vytváří NOVOU položku, ne úpravu staré.
## 13. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na
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
```

---
---

## SCH-0 — Migrace 0029: schéma plánovače

Viz `docs/TASKS_SCHEDULER.md` T0 pro plný seznam cílových souborů a kroků,
a sekci "Nové schéma (migrace 0029)" pro přesný tvar tabulek. Shrnutí:
čtyři nové tabulky (`run_schedules`, `run_queue`, `worker_heartbeats`,
`notification_outbox`), sloupec `clients.priority`, tři indexy na frontě
včetně partial indexu pro výběr.

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
   a unique constraint na `(schedule_id, scheduled_for)`.
4. `pytest -v` (existující sada nesmí spadnout na nových modelech)
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(infra): add scheduler, run queue and notification tables
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

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

### (sem dopiš DONE — commit {hash} až bude hotovo)

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

### (sem dopiš DONE — commit {hash} až bude hotovo)

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

### (sem dopiš DONE — commit {hash} až bude hotovo)

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

### (sem dopiš DONE — commit {hash} až bude hotovo)

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
- V1 jen `target_type='prompt'`. Set-level rozvrhy sem NEPATŘÍ, i když
  schéma je na ně připravené (decision 24).
- Delete guard: `data-confirm="{{ t('schedules.delete_confirm') }}"` přes
  sdílený delegovaný listener v `base.html`, **nikdy**
  `onsubmit="return confirm('...')"` — rozbije se na apostrofu v překladu.
- Náhled příštích pěti termínů + odhad ceny přímo ve formuláři
  (decision 29), jako HTMX partial. Žádný nový JS framework.

**Po dokončení:**
1. `docker compose up -d --build`
2. V prohlížeči: založit rozvrh na promptu, ověřit že náhled termínů
   odpovídá pravidlu (zkontroluj i "2× týdně"); upravit; pozastavit;
   smazat (s potvrzením).
3. Přihlásit se jako viewer → 403 na `/schedules/new` i na POST routách.
4. Responsive ~375 / 768 / desktop na formuláři i na seznamu.
5. `pytest -v`
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add schedule CRUD with occurrence and cost preview
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## SCH-6 — `/schedules`: rozvrhy, fronta, historie

Viz `docs/TASKS_SCHEDULER.md` T6. Shrnutí: monitorovací stránka se třemi
pohledy, stavový pruh workeru z heartbeatu, doplnění detailu runu o
atribuci plánovače.

Nejdřív navrhni CO uděláš + PROČ, včetně tvaru každého ze tří pohledů, a
počkej na potvrzení.

**Kritické:**
- **Jinja2 + HTMX, žádný Vue ostrůvek.** Jsou to tabulky a stavové
  odznaky, ne sdílený client-side stav — podmínka z `TASKS_PHASE4.md`
  design decision 1 není splněná.
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
3. `docker compose stop worker`, počkat, obnovit stránku → pruh musí
   přepnout na "neodpovídá". Pak `docker compose start worker`.
4. Viewer → 403. Responsive ~375 / 768 / desktop na všech třech pohledech.
5. `pytest -v`
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): add schedules monitoring page
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

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

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## SCH-8 — Oznámení (outbox + in-app kanál)

Viz `docs/TASKS_SCHEDULER.md` T8. Shrnutí: `app/services/notifications.py`
s `notify()`, balíček kanálů `app/notifications/` (`base.py`, `inapp.py`),
zapojení pěti událostí, in-app zobrazení na `/schedules`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Kanály jsou balíček podle vzoru `app/adapters/<provider>.py` — přidat
  e-mail musí být "přidat soubor", ne "přepsat volající místa"
  (decision 25).
- **SMTP kanál v této větvi NEPIŠ.** Appka nemá SMTP server. Smyslem
  outboxu je, že se do té doby nic neztratí — ne aby ses pokoušel doručovat.
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

### (sem dopiš DONE — commit {hash} až bude hotovo)

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

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## SCH-10 — Stropy: kvóty, hloubka fronty, souběh

Viz `docs/TASKS_SCHEDULER.md` T10. Shrnutí: denní limit runů na klienta,
strop hloubky fronty, semafor souběhu na providera, admin UI pro prioritu
a limit klienta.

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
3. `pytest -v`
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(scheduler): enforce per-client run quotas and concurrency limits
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

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

### (sem dopiš DONE — commit {hash} až bude hotovo)

---
---

## PO DOKONČENÍ VŠECH PROMPTŮ

1. Projít Completion Checklist v `docs/TASKS_SCHEDULER.md`.
2. Nechat větev běžet v dry-runu alespoň týden, než se vypne
   `SCHEDULER_DRY_RUN` — sledovat na `/schedules`, jestli plánovaná okna
   sedí s tím, co lidi čekali (decision 15).
3. Teprve pak ostrý provoz, a první den zkontrolovat `/ops` — řádek
   "Scheduler" na ose Uživatel musí sedět s počtem oken na `/schedules`.
