# SignalMap — Claude Code Session Prompts: Scheduler Ops (vydání 2)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 0. NEJDŘÍV větev feature/signalmap-capture-robustness (CR-1 až CR-4,
##    docs/PROMPTS_CAPTURE_ROBUSTNESS.md), pak feature/signalmap-worker-
##    throughput: WT-1 až WT-4 podle docs/PROMPTS_WORKER_THROUGHPUT.md; obě
##    se mergují do master BEZ nasazení. WT-5 (nasazení) se neprovádí
##    samostatně — je součástí SO-7.
## 1. git checkout -b feature/signalmap-scheduler-ops (z master po merge WT)
## 2. Sedm promptů (SO-1 až SO-7). SO-2 staví na SO-1 (jména), SO-4 na
##    SO-3 (kategorie). SO-5 je nezávislé. SO-6 dokumentuje, SO-7 nasazuje.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent). Commit dokončit před dalším promptem.
## 5. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_SCHEDULER_OPS.md v "Task Index" ⏳ → ✅.
## 6. Zdůvodnění a design decisions 1-13: docs/TASKS_SCHEDULER_OPS.md.
## 7. Lokální testy běží v Dockeru — před SO-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-scheduler-ops
(vydání 2, cíl v1.4.0 — nasazuje se společně s už smergnutou větví
worker-throughput).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_SCHEDULER_OPS.md — CELÉ, hlavně „Výchozí stav" a design
   decisions 1-13
3. docs/TASKS_WORKER_THROUGHPUT.md — design decisions 3 a 6 (repliky,
   heartbeat vlákno), už implementované
4. app/worker.py (celý), app/services/queue.py, app/services/schedule_monitor.py

KONTEXT: Na /schedules nejde poznat, který worker co dělá (jména jsou
hex hostname a v pruhu se nezobrazují). Vyčerpaný kredit u OpenAI
shodil dávku po 3 pokusech za 6 minut. Kvóta a přeskočené runy nejsou
v UI vidět. Tahle větev: jména worker-N, panel s aktuální prací,
kategorie chyb providera (billing čeká), sladěná retry politika,
kvóta/přeskočené runy v UI, popisek LLM judge na /ops.

KRITICKÉ:
- Žádná migrace (design decision 12).
- Klasifikace chyb v jedné službě (app/services/provider_errors.py),
  adaptéry se kvůli ní nemění.
- Každý pokus dál vytváří Run řádek (evidence, NFR-6).
- Lokální testy s víc workery jen s SCHEDULER_DRY_RUN=true nebo
  FakeAdapterem — žádné placené runy navíc.
- UI texty přes t() v DE i EN; UI ověřit na ~375 / ~768 px / desktop.
- Testovací fixture: Skoda Auto, prompt 54, gemini-3.1-flash-lite.
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Bump verze a tag dělá uživatel (AI_INSTRUCTIONS.md §4).
```

---

## SO-1 — Jméno workeru z čísla repliky + úklid heartbeatů

```
Úkol SO-T1 z docs/TASKS_SCHEDULER_OPS.md (design decisions 1-2).

1. NEJDŘÍV ověř mechanismus (T1 bod 1): se 4 replikami lokálně spusť
   v kontejneru workeru reverzní DNS lookup a ukaž mi výstup. Když
   nevrací jméno kontejneru, zastav se a navrhni zálohu z design
   decision 1 — nic neimplementuj bez mého souhlasu.
2. Implementuj T1 body 2-3.
3. Testy T1 bod 4, celá sada pytest.
4. Ověř T1 „Done when" (restart workeru nezmění jména, nepřibydou řádky).

Na konci: shrnutí, výstup ověření, navržený commit:
feat(runs): name workers after their compose replica
```

---

## SO-2 — Panel workerů na /schedules

```
Úkol SO-T2 z docs/TASKS_SCHEDULER_OPS.md (design decision 3).

1. Přečti schedules.py (worker bar ~1108, queue view), schedule_monitor.py
   a schedules/index.html (53-67, fronta 241-311).
2. Navrhni mi rozložení panelu (desktop + mobil) krátce slovy, než začneš.
3. Implementuj T2 body 1-3, testy T2 bod 4.
4. Prohlížeč: 4 workery, SCHEDULER_DRY_RUN=true, ruční „spustit teď"
   jednoho setu fixture klienta, screenshoty na ~375 / ~768 px / desktop.

Na konci: shrnutí, testy, screenshoty, navržený commit:
feat(runs): show what each worker is processing on /schedules
```

---

## SO-3 — Kategorie chyb providera + chování workeru

```
Úkol SO-T3 z docs/TASKS_SCHEDULER_OPS.md (design decisions 4-5).

1. Přečti app/worker.py (_is_retryable_error, process_claimed_item),
   app/services/run_execution.py (jak se ukládá chyba) a v nainstalovaných
   SDK (openai, anthropic, google-genai) třídy výjimek a jak nesou
   status_code / body. Ukaž mi tabulku: SDK → výjimka → kde je kód
   a zpráva → navržená kategorie. Počkej na souhlas.
2. Implementuj T3 body 1-2.
3. Testy T3 body 3-4 s reálnými třídami výjimek.

Na konci: shrnutí, testy, navržený commit:
feat(runs): classify provider errors and defer billing failures
```

---

## SO-4 — Billing notifikace, retry politika, hloubka fronty

```
Úkol SO-T4 z docs/TASKS_SCHEDULER_OPS.md (design decisions 6-8).

1. Přečti app/services/notifications.py (vzor notify_worker_stale
   s cooldownem) a render notifikací v schedules.py.
2. Implementuj T4 body 1-3 vč. i18n DE/EN.
3. Testy T4 bod 4.

Na konci: shrnutí, testy, navržený commit:
feat(runs): notify once per provider when credit runs out
```

---

## SO-5 — Kvóta a přeskočené runy v UI, /ops judge popisek

```
Úkol SO-T5 z docs/TASKS_SCHEDULER_OPS.md (design decisions 9-11).

1. Přečti check_daily_quota, clients.py::client_detail + detail.html,
   schedule_monitor.history_batches (skip_reason na ~290),
   ops_dashboard verification_queue_snapshot a ops/index.html ~205.
2. Implementuj T5 body 1-4 vč. i18n a testu pokrytí překladů skip_reason.
3. Prohlížeč T5 bod 5 — nízký daily_run_limit testovacímu klientovi
   dočasně, pak vrátit. Screenshoty na třech šířkách.

Na konci: shrnutí, testy, screenshoty, navržený commit:
feat(clients): show daily quota and skipped runs
```

---

## SO-6 — Dokumentace + CHANGELOG

```
Úkol SO-T6 z docs/TASKS_SCHEDULER_OPS.md.

1. CHANGELOG.md [Unreleased] podle T6 bodu 1 (znění podle skutečné
   implementace; zkontroluj, že tam jsou i položky z WT).
2. docs/DEPLOYMENT.md podle T6 bodu 2 — hlavně zrušit ruční
   --scale worker=4 a doplnit WORKER_REPLICAS=4.
3. docs/ROADMAP.md #20, #21 (jen hotové body), #24;
   docs/TASKS_SCHEDULER.md poznámka k retry politice.
Ukaž diff PŘED zápisem.

Navržený commit: docs(docs): document scheduler ops changes
```

---

## SO-7 — Nasazení v1.4.0 (vč. WT-T5) a měření

```
Úkol SO-T7 z docs/TASKS_SCHEDULER_OPS.md + WT-T5 z
docs/TASKS_WORKER_THROUGHPUT.md.

Vede mě krok za krokem podle T7 a docs/DEPLOYMENT.md kapitol 0-5.
Příkazy na serveru spouštím já — ty je připravíš a vyhodnotíš výstup.

1. Připomeň kontrolu limitů v OpenAI konzoli (T7 krok 1).
2. Připomeň merge PR, bump v1.4.0, přesun [Unreleased], tag (dělám já).
3. Serverový .env: WORKER_REPLICAS=4, žádný WORKER_NAME. Nasazení bez
   --scale.
4. Ověření T7 krok 4.
5. Měření WT-T5 (SQL z docs/TASKS_WORKER_THROUGHPUT.md) po Knaufu —
   výsledek zapsat do obou TASKS dokumentů.
6. Za týden T7 krok 6 (kategorie chyb).
7. End-of-branch docs obou větví (T7 krok 7).
8. Připomeň mi aktualizovat memory o ručním --scale worker=4 (už neplatí).

Navržený commit na konci:
docs(docs): record scheduler ops deploy and close the branch
```
