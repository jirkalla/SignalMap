# SignalMap — Claude Code Session Prompts: Scheduler Retry Guard (v1.4.1)

## v1.0 | Říjen 2026
##
## JAK POUŽÍVAT:
## 0. NEJDŘÍV: feature/signalmap-scheduler-ops je mergnutá do master a
##    v1.4.0 je nasazená (docs/PROMPTS_SCHEDULER_OPS.md, SO-7). Tahle větev
##    staví na jejím SO-T5 (daily_quota_usage, skipped_summary, SKIP_REASONS).
## 1. git checkout -b feature/signalmap-scheduler-retry-guard (z master po
##    nasazení v1.4.0) — větev zakládáš ty nebo na tvůj výslovný pokyn.
## 2. Sedm promptů (RG-1 až RG-7). RG-2 staví na RG-1 (služba zatížení),
##    RG-4 a RG-5 na RG-3 (retry služba). RG-6 dokumentuje, RG-7 nasazuje.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent). Commit dokončit před dalším promptem.
## 5. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_SCHEDULER_RETRY_GUARD.md v "Task Index" ⏳ → ✅.
## 6. Zdůvodnění a design decisions 1-16: docs/TASKS_SCHEDULER_RETRY_GUARD.md.
## 7. Lokální testy běží v Dockeru — před RG-1 zapni Docker Desktop.
## 8. Verze (design decision 1) se rozhoduje před RG-7, ne dřív.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-scheduler-retry-guard
(cíl v1.4.1 — verze k potvrzení, viz design decision 1; staví na nasazené
v1.4.0).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_SCHEDULER_RETRY_GUARD.md — CELÉ, hlavně „Proč", „Výchozí
   stav" a design decisions 1-16
3. docs/TASKS_SCHEDULER_OPS.md — design decisions 9-10 (kvóta, přeskočené)
4. app/services/run_execution.py (check_daily_quota, daily_quota_usage),
   app/services/queue.py, app/services/schedule_monitor.py,
   app/routers/schedules.py (preview_occurrences, schedules_monitor,
   retry_*), app/templates/schedules/index.html

KONTEXT: Klient DPE dostal plán na celý prompt set (230 runů) bez vlastního
daily_run_limit; platil výchozí limit 50, 180 položek skončilo skipped /
quota_exceeded a šlo je znovu zařadit jen ručním SQL (hromadný retry bere
jen status=error a nekontroluje, jestli už retry existuje). Tahle větev
přidává tři vrstvy: (1) upozornění, že plán přesahuje limit klienta —
v náhledu plánu a trvalý štítek na /schedules; (2) výběr položek v History
a bezpečný retry (idempotentní); (3) potvrzení s náhledem — počty, vliv na
limit, odhad ceny.

KRITICKÉ:
- Žádná migrace (design decision 14). Žádný nový sloupec ani index.
- Evidence se nepřepisuje: retry = nový řádek `run_queue` s `retry_of_id`
  (NFR-6). Původní skipped/error zůstává.
- Retryovat lze jen konec řetězu (design decision 6); položka s potomkem
  se nikdy znovu nezařadí.
- Confirm vše přepočítá znovu a zamyká řádky (decision 7, 9) — náhledu
  se nevěří.
- check_daily_quota se NEMĚNÍ; vynucení v workeru zůstává jediná pravda
  (decision 16).
- Prevence upozorňuje, neblokuje (decision 2).
- Žádné placené runy navíc: lokálně SCHEDULER_DRY_RUN=true nebo
  FakeAdapter; testovací klient Skoda Auto, prompt 54,
  gemini-3.1-flash-lite. Nízký daily_run_limit testovacímu klientovi
  nastavit jen dočasně a vrátit zpět.
- UI texty přes t() v DE i EN; UI ověřit na ~375 / ~768 px / desktop.
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Bump verze, tag, git commit i git push dělám já (AI_INSTRUCTIONS.md §4, §8).
```

---

## RG-1 — Služba projektovaného zatížení + upozornění v náhledu plánu

```
Úkol RG-T1 z docs/TASKS_SCHEDULER_RETRY_GUARD.md (design decisions 2-4, 16).

1. Přečti preview_occurrences, _estimate_window_cost, queue._target_prompts
   a daily_quota_usage. Ukaž mi krátce: odkud vezmeš limit klienta (jedna
   definice s daily_quota_usage), jak spočítáš prompty plánu a jak
   z projekce vyloučíš editovaný plán. Počkej na souhlas.
2. Implementuj app/services/schedule_load.py a upozornění v náhledu
   (T1 body 1-3). Nic neblokuj.
3. Testy T1 bod 4, celá sada pytest.
4. Prohlížeč T1 bod 5 — screenshoty na třech šířkách.

Na konci: shrnutí, testy, screenshoty, navržený commit:
feat(runs): warn when a schedule exceeds the client's daily limit
```

---

## RG-2 — Trvalý štítek přetížení klienta na /schedules

```
Úkol RG-T2 z docs/TASKS_SCHEDULER_RETRY_GUARD.md (design decision 4).

1. Přečti _grouped_schedules_by_client a pohled „Schedules" v index.html.
2. Navrhni mi krátce, jak spočítáš projekci pro všechny klienty bez N+1
   dotazů (seskupení podle prompt setů). Počkej na souhlas.
3. Implementuj T2 body 1-2, testy T2 bod 3.
4. Prohlížeč na třech šířkách: skupina s překročením i bez.

Na konci: shrnutí, testy, screenshoty, navržený commit:
feat(runs): flag clients whose schedules exceed the daily limit
```

---

## RG-3 — Retry služba: idempotence, retryovatelnost, náhled

```
Úkol RG-T3 z docs/TASKS_SCHEDULER_RETRY_GUARD.md (design decisions 5-7, 10-11, 14-15).

1. Přečti create_retry, retry_all_errors, history_batches a
   average_historical_cost. Ukaž mi návrh datových struktur (RetryPlan,
   RetryResult) a SQL pro „má potomka" a „rozpracováno" — počkej na souhlas.
2. Implementuj T3 body 1-6. Jde jen o službu a anotaci v history_batches —
   žádné UI, žádné nové route.
3. Testy T3 bod 7 včetně souběhu dvou session; nové testy musí selhat
   na kódu před změnou tam, kde jde o idempotenci.
4. Celá sada pytest.

Na konci: shrnutí, testy, navržený commit:
feat(runs): make queue retry idempotent and quota-aware
```

---

## RG-4 — History: výběr položek, filtr důvodu, lišta akce

```
Úkol RG-T4 z docs/TASKS_SCHEDULER_RETRY_GUARD.md (design decisions 8, 13).

1. Přečti index.html (pohled History), schedules_monitor a
   delegovaný data-confirm listener v base.html. Navrhni mi rozložení
   (desktop + mobil) krátce slovy: checkboxy, „Vybrat retryovatelné",
   lišta akce, chipy důvodů. Počkej na souhlas.
2. Implementuj T4 body 1-4. Lišta akce odesílá ID na
   /schedules/queue/retry/preview — samotná route přijde v RG-5, do té
   doby tlačítko jen sestaví formulář (nebo skryj za TODO s odkazem na RG-5).
3. Testy T4 bod 5.
4. Prohlížeč T4 bod 6 — screenshoty, konzole bez chyb.

Na konci: shrnutí, testy, screenshoty, navržený commit:
feat(runs): select history items to retry
```

---

## RG-5 — Potvrzení s náhledem + zrušení „Retry all errors"

```
Úkol RG-T5 z docs/TASKS_SCHEDULER_RETRY_GUARD.md (design decisions 9-12).

1. Přečti trigger_run v app/routers/runs.py (reference pro HTMX/plain
   POST) a retry_*/cancel_* route v schedules.py. Ukaž mi návrh stránky
   náhledu (blok po klientech, varování nad limitem, tři volby) a seznam
   odstraňovaných kusů (route, retry_all_errors, překlady, test).
   Počkej na souhlas.
2. Implementuj T5 body 1-5.
3. Testy T5 bod 6.
4. Prohlížeč T5 bod 7 — jen DRY_RUN / FakeAdapter, žádné placené runy.

Na konci: shrnutí, testy, screenshoty, navržený commit:
feat(runs): confirm bulk retry with a quota and cost preview
```

---

## RG-6 — Dokumentace + CHANGELOG

```
Úkol RG-T6 z docs/TASKS_SCHEDULER_RETRY_GUARD.md.

1. Projdi docs/REQUIREMENTS.md, TASKS_SCHEDULER.md, README.md a najdi
   místa, která popisují bulk retry nebo denní limit.
2. Navrhni změny a UKAŽ MI DIFF všech dokumentů včetně CHANGELOG
   ([Unreleased]: Added / Changed / Removed). Nic nezapisuj před
   mým souhlasem a nic neoznačuj jako hotové, dokud nepotvrdím, že
   funkce funguje.
3. Zapiš rozhodnutí o verzi (design decision 1), pokud už padlo.

Na konci: shrnutí, navržený commit:
docs(docs): document the retry guard and bulk retry preview
```

---

## RG-7 — Nasazení (v1.4.1) a měření

```
Úkol RG-T7 z docs/TASKS_SCHEDULER_RETRY_GUARD.md.

Vede mě krok za krokem podle T7 a docs/DEPLOYMENT.md kapitol 0-5.
Příkazy na serveru spouštím já — ty je připravíš a vyhodnotíš výstup.

1. Nejdřív rozhodneme verzi (design decision 1: v1.4.1 / v1.5.0 / rozdělení)
   — připomeň mi pravidlo z DEPLOYMENT.md §0 a co je v ROADMAP rezervované.
2. Připomeň merge PR, bump verze, přesun [Unreleased], tag (dělám já).
3. Nasazení bez migrace a bez nové env proměnné; zkontroluj, jaký je
   aktuální počet workerů (WORKER_REPLICAS / --scale) a zachovej ho.
4. Ověření T7 krok 3 — na produkci nic nezařazovat jen kvůli testu,
   náhled vždy zrušit, pokud skutečný retry neschválím.
5. Měření EXPLAIN ANALYZE (T7 krok 4) — SQL připrav, výsledek zapíšu.
6. End-of-branch docs (T7 krok 5).

Navržený commit na konci:
docs(docs): record the retry guard deploy and close the branch
```
