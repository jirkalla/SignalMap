# SignalMap — Claude Code Session Prompts: Export v2 + History filtry (vydání 4)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 0. NEJDŘÍV větev feature/signalmap-metric-definitions (docs/PROMPTS_METRIC_DEFINITIONS.md,
##    MD-1 až MD-5), merge do master BEZ nasazení — nasadí se v EX2-8.
## 1. git checkout -b feature/signalmap-export-v2 (z master po merge metric-definitions)
## 2. Osm promptů (EX2-1 až EX2-8). EX2-1 → EX2-2 → EX2-3 → EX2-4
##    (služba exportu, staví na sobě) → EX2-5 (routy + UI). EX2-6
##    (History) je nezávislé. EX2-7 dokumentuje, EX2-8 nasazuje.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent). Commit dokončit před dalším promptem.
## 5. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_EXPORT_V2.md v "Task Index" ⏳ → ✅.
## 6. Zdůvodnění a design decisions 1-13: docs/TASKS_EXPORT_V2.md,
##    původní export: docs/TASKS_EXPORT.md.
## 7. Lokální testy běží v Dockeru — před EX2-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-export-v2
(vydání 4, cíl v1.6.0 — nasazuje se společně s už smergnutou
větví metric-definitions).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_EXPORT_V2.md — CELÉ, hlavně „Výchozí stav" a design
   decisions 1-13
3. docs/TASKS_EXPORT.md — design decisions 1-10 (platí dál, pokud v2
   neříká jinak)
4. app/services/export.py (celý), app/routers/runs.py export routy

KONTEXT: Export dnes nemá prompt-set scope, filtr data/stavu, personu,
cenu ani výsledky ověření citací. Tahle větev je přidá, plus souhrnný
list, strop velikosti a filtry History na /schedules s retry podle
filtru.

KRITICKÉ:
- Nové sloupce jen NA KONEC existujících n-tic (zpětná kompatibilita).
- Hodnoty content (answer/raw/full) a JSON root (pole) se nemění.
- Žádné N+1 — verdikty, labely a ceny hromadně pro celou dávku.
- XLSX nikdy neobsahuje plný text zdroje.
- Bez migrace. Export je read-only, editor/admin (viewer 403).
- UI texty přes t() v DE i EN; UI ověřit na ~375 / ~768 px / desktop.
- Testovací fixture: Skoda Auto, prompt 54, gemini-3.1-flash-lite.
- „Retry all errors" v prohlížeči jen s mým potvrzením (placené runy).
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Bump verze a tag dělá uživatel (AI_INSTRUCTIONS.md §4).
```

---

## EX2-1 — Filtry exportu + runs_for_prompt_set + strop

```
Úkol EX2-T1 z docs/TASKS_EXPORT_V2.md (design decisions 1-3, 9).

1. Implementuj T1 body 1-3 (přesun parseru dat, ExportFilters, nový
   helper, count + EXPORT_MAX_RUNS).
2. Testy T1 bod 4; testy retro-verify v test_clients.py musí projít beze
   změny.

Navržený commit: feat(runs): filter exports by date range and status
```

---

## EX2-2 — Nové sloupce Runs

```
Úkol EX2-T2 z docs/TASKS_EXPORT_V2.md (design decision 5).

1. Přečti app/services/cost.py (estimate_run_cost, load_price_components,
   prices_at) a hromadný vzor v ops_dashboard.py ~646.
2. Implementuj T2 body 1-3, testy T2 bod 4.

Navržený commit: feat(runs): add persona, market names and cost to exports
```

---

## EX2-3 — Ověření citací v exportu

```
Úkol EX2-T3 z docs/TASKS_EXPORT_V2.md (design decisions 6-7).

1. Přečti app/models/verification.py (CitationVerification,
   VerificationLabel, SourceDocument, SourceText) a
   verification_display.py (latest_verification_query, VERDICT_BUCKETS).
2. Navrhni mi dotazy (kolik, jaké) pro dávku runů, než začneš.
3. Implementuj T3 body 1-3, testy T3 bod 4 vč. počtu dotazů.

Navržený commit: feat(runs): include citation verification results in exports
```

---

## EX2-4 — Souhrnný list

```
Úkol EX2-T4 z docs/TASKS_EXPORT_V2.md (design decisions 8-9).

1. Implementuj T4 body 1-4 vč. 3a (list Definitions z
   app/metrics_catalog.py). U ZIP varianty mi vysvětli volbu (jeden
   vs. dva CSV soubory).
2. Přepnutí XLSX na write_only ověř proti všem existujícím testům exportu.
3. Testy T4 bod 5.

Navržený commit: feat(runs): add a summary sheet to client and prompt set exports
```

---

## EX2-5 — Routy a UI formulář exportu

```
Úkol EX2-T5 z docs/TASKS_EXPORT_V2.md (design decisions 10-11).

1. Přečti runs.py export routy, macros.html (export_button_group),
   clients/detail.html, prompt_sets/detail.html, prompts/detail.html
   (JS přepis odkazů — ten zůstává).
2. Krátce mi popiš formulář (pole, výchozí hodnoty, mobilní rozložení).
3. Implementuj T5 body 1-4, testy T5 bod 5.
4. Prohlížeč T5 bod 6 — stáhni všechny formáty pro fixture klienta,
   otevři XLSX/ZIP a ukaž mi listy/soubory a pár řádků. Screenshoty
   formuláře na třech šířkách.

Navržený commit: feat(runs): add a filterable export form for clients and prompt sets
```

---

## EX2-6 — History filtry + retry podle filtru

```
Úkol EX2-T6 z docs/TASKS_EXPORT_V2.md (design decision 12).

1. Přečti schedule_monitor.history_batches, queue.retry_all_errors,
   schedules.py (schedules_monitor, retry routy) a History část
   schedules/index.html.
2. Implementuj T6 body 1-2, testy T6 bod 3.
3. Prohlížeč na třech šířkách — filtry a počet v tlačítku; retry
   NEKLIKAT bez mého potvrzení.

Navržený commit: feat(runs): filter schedule history and retry errors by filter
```

---

## EX2-7 — Dokumentace + CHANGELOG

```
Úkol EX2-T7 z docs/TASKS_EXPORT_V2.md.

1. CHANGELOG.md [Unreleased] podle T7 (znění podle skutečné implementace).
2. docs/TASKS_EXPORT.md poznámka k design decision 8; docs/ROADMAP.md
   #25 a #21 (History); nápověda k exportu, pokud existuje.
Ukaž diff PŘED zápisem.

Navržený commit: docs(docs): document export v2 and history filters
```

---

## EX2-8 — Nasazení v1.6.0 a ověření

```
Úkol EX2-T8 z docs/TASKS_EXPORT_V2.md.

Vede mě krok za krokem podle T8 a docs/DEPLOYMENT.md kapitol 0-5.
Příkazy na serveru spouštím já — ty je připravíš a vyhodnotíš výstup.

1. Připomeň merge PR, bump v1.6.0, přesun [Unreleased], tag (dělám já).
2. Nasazení (bez migrace).
3. Ověření T8 kroky 3-4 vč. 3a (metric definitions).
4. End-of-branch docs obou větví — export-v2 i metric-definitions
   (T8 krok 5).

Navržený commit na konci:
docs(docs): record export v2 deploy and close the branch
```
