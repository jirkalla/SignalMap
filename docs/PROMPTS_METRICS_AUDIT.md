# SignalMap — Claude Code Session Prompts: Metrics Audit Fixes

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-metrics-audit-fixes (z aktuálního master)
## 2. Prompty MAF-0 až MAF-23 v tomto pořadí. Pořadí je z triage (vlny 1–3),
##    MAF-0 a MAF-12 jsou brány bez kódu — dokud neproběhnou, nepokračuj.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
##    Větev je dlouhá — nová konverzace aspoň na začátku každé vlny.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
##    Další prompt až po commitu předchozího.
## 5. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_METRICS_AUDIT.md přepni řádek v "Task Index" z ⏳ na ✅
##       (nebo ❌ s důvodem, pokud task odpadl po MAF-0 / MAF-12).
## 6. Zdůvodnění, nálezy F1–F19 a design decisions:
##    docs/TASKS_METRICS_AUDIT.md — přečti si ho celý před MAF-0.
## 7. Lokální testy běží v Dockeru — před prvním promptem zapni Docker Desktop.
## 8. Testovací data: Skoda Auto, prompt 54, gemini-3.1-flash-lite.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-metrics-audit-fixes.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_METRICS_AUDIT.md — CELÉ: tabulku nálezů, design decisions,
   Task Index (co je hotové, co odpadlo) a výsledky T0
3. Soubory z "Target" tasku, na kterém právě pracuji

KONTEXT: Průřezové review výpočtů na /dashboard, /ops a /schedules
(2026-09-25) našlo 19 možných problémů F1–F19 — čtením kódu, ne z dat.
Opravují se v jedné větvi podle triage: vlna 1 bez schématu a bez
rozhodnutí, vlna 2 po rozhodnutí uživatele (T12), vlna 3 po ověření
proti fakturám a datům (T0).

KRITICKÉ:
- Task, který T0 vyvrátil nebo T12 rozhodl "nedělat", se neimplementuje.
- Evidence se nepřepisuje (NFR-6): žádný UPDATE runs/raw_responses/
  citations/analysis_results. Změna minulosti = nové řádky.
- Migrace jen v T17 a T19 a jen po schválení v T12. Jinak žádný nový
  sloupec ani tabulka.
- Žádná nová závislost (ani freezegun) — čas se testuje přes čisté
  funkce s explicitním `now`.
- UI text jen přes t() v DE i EN.
- UI změny ověř v prohlížeči na ~640 px, ~1024 px a desktopu.
- Jeden task = jeden commit. Commit, push, merge, bump verze a tag dělá
  uživatel.
```

---
---

## MAF-0 — Ověření nálezů (bez kódu)

Viz `docs/TASKS_METRICS_AUDIT.md` T0.

Nejdřív navrhni SQL dotazy pro každý řádek tabulky T0 (read-only, jen
SELECT) a řekni, proti které DB poběží. Počkej na potvrzení.

**Kritické:**
- Nic nezapisuj do DB.
- Řádky F15 a F16 potřebují fakturační data — připrav uživateli, co přesně
  má porovnat (dny, modely, spočítanou cenu), a počkej na jeho výsledek.
- Netvrď "potvrzeno", dokud to dotaz neukáže. "Nelze ověřit z dat" je
  platný výsledek.

**Po dokončení:**
1. Vyplň sloupec „Výsledek" v tabulce T0.
2. Vyvrácené nálezy: navrhni ❌ v Task Indexu s jednou větou důvodu.
3. Navrhni commit message (nespouštěj git).

**Expected commit:**
```
docs(metrics-audit): record verification of findings
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-1 — První termín rozvrhu od „teď" (F2)

Viz T1. Shrnutí: helper v `scheduling.py` = `max(now, starts_on 00:00
v timezone rozvrhu)`, `create_schedule` ho používá.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T1) a počkej na potvrzení.

**Kritické:**
- Nejdřív test, který dnešní chybu reprodukuje (vytvořeno 14:00, denní
  09:00 → dnes `next_run_at` v minulosti), teprve pak oprava.
- Helper musí být čistá funkce (design decision 3) — MAF-3 ho použije
  taky.

**Po dokončení:**
1. Nové testy + celá sada:
   ```bash
   COPYFILE_DISABLE=1 tar -cf - tests requirements-dev.txt | docker compose run --rm --no-deps -T app sh -c 'tar -xf - && pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -p no:cacheprovider -q'
   ```
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
fix(schedules): start the first occurrence search from now
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-2 — Uvolnit lease i po retry (F1)

Viz T2 a design decision 4.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- `run_id` na queue itemu NEMAŽ — rozšiř podmínku uvolnění.
- Item navázaný na `pending` Run se uvolnit nesmí (patří
  `reconcile_interrupted_runs`) — pokryj testem.
- Pokud T0 našel zaseknuté položky, připrav jednorázový dotaz pro
  uživatele; sám ho nespouštěj.

**Po dokončení:** celá sada testů, implementation summary, commit message.

**Expected commit:**
```
fix(runs): release expired leases left behind by a retried item
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-3 — Náhled v editaci od „teď" (F3)

Viz T3. Použij helper z MAF-1.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Po dokončení:**
1. Testy + celá sada.
2. Prohlížeč: edituj existující rozvrh → náhled začíná v budoucnu.
3. Implementation summary + commit message.

**Expected commit:**
```
fix(schedules): preview occurrences from now when editing
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-4 — „Cited in %" jen ze search modelů (F9)

Viz T4. Filtr `supports_web_search` převezmi z
`_mention_visibility_base_query`, nepiš druhou kopii.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Po dokončení:** testy, prohlížeč (dashboard Skoda Auto s providerem
„všechny"), summary, commit message.

**Expected commit:**
```
fix(dashboard): exclude non-search models from domain coverage
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-5 — Limit hloubky fronty (F19)

Viz T5.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Expected commit:**
```
fix(runs): count deferred and leased items toward queue depth
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-6 — Latence jen z úspěšných runů (F11)

Viz T6. Popisek dlaždice v DE i EN.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Expected commit:**
```
fix(ops): average latency over successful runs only
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-7 — Sloupec „first position" (F8)

Viz T7. Jen popisek + tooltip, výpočet beze změny.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Po dokončení:** prohlížeč na 640 / 1024 / desktop (tooltip se nesmí
oříznout), summary, commit message.

**Expected commit:**
```
fix(dashboard): clarify the first-mention column label
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-8 — Runy bez ceny (F6)

Viz T8 a design decision 7.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Neúplnou sumu nedopočítávej odhadem — jen ukaž počet.
- Runy bez `raw_responses` (chyby) se do počtu nepočítají — nemají co
  ocenit.
- Stejná logika v SQL agregacích i v `recent_runs` (Python cesta).

**Po dokončení:** testy, prohlížeč `/ops` na třech šířkách, summary,
commit message.

**Expected commit:**
```
feat(ops): show how many runs are missing a price
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-9 — Mezery místo nul + osa position (F7)

Viz T9 a design decision 8.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- `runs` a `citations` dál vrací 0 — `null` jen pro rate/SoV/position.
- Přepiš docstring `dashboard_timeseries` (dnes slibuje „never a gap").
- Graf: segmenty mezi `null`, žádná čára přes mezeru, hover `null`
  přeskočí.

**Po dokončení:** testy, prohlížeč (range 90d, klient s mezerami v
datech) na třech šířkách, summary, commit message.

**Expected commit:**
```
fix(dashboard): leave gaps for weeks without data in rate charts
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-10 — Denní limit v náhledu rozvrhu (F18)

Viz T10.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** varuj, neblokuj — rozvrh jde uložit i přes varování.

**Expected commit:**
```
feat(schedules): warn when a schedule exceeds the daily run limit
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-11 — Seskupení po oknech (F4)

Viz T11 a design decision 6.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Jeden sdílený výraz klíče okna pro `history_batches` i
  `schedule_health`, ne dvě kopie.
- Existující historie se opraví čtením, data se nemění.

**Po dokončení:** testy, prohlížeč `/schedules` → Historie, summary,
commit message. Pak se zeptej uživatele na **checkpoint po vlně 1**
(mergovat vlnu 1 samostatně, nebo pokračovat).

**Expected commit:**
```
fix(schedules): group history and health strip by schedule window
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-12 — Rozhodnutí vlny 2 (bez kódu)

Viz T12 a design decision 9.

**Postup:**
1. Pro každý z F5, F12 (krok 2), F13, F14, F16, F17 shrň: co dnes číslo
   znamená, doporučení, alternativu, dopad na stará data. Použij výsledky
   T0.
2. Pro F5 a F16 **nahlas migraci** (tabulka, sloupec, typ, nullable, FK,
   index) — bez souhlasu uživatele se v T17/T19 nic nepíše.
3. Rozhodnutí zapiš jako design decisions 12+ do TASKS a Task Index
   uprav (❌ pro „nedělat").

**Expected commit:**
```
docs(metrics-audit): record wave 2 decisions
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-13 — Share of voice jako souhrnný poměr (F13)

Viz T13. Jen pokud MAF-12 rozhodl pro změnu.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** uložený per-run `share_of_voice` se nemění; mění se jen
agregace při čtení.

**Expected commit:**
```
feat(dashboard): compute share of voice as a pooled ratio
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-14 — Pokrytí vedle position (F14)

Viz T14.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Expected commit:**
```
feat(dashboard): show mention coverage next to position
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-15 — Metrika „cited sources" (F17)

Viz T15, podle MAF-12.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** změna KPI dlaždice mění číslo, které klient mohl vidět —
nový popisek, tooltip s definicí, odrážka v CHANGELOGu (T21).

**Expected commit:**
```
feat(dashboard): count cited sources per run instead of raw citation rows
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-16 — Tabulka entit (F12)

Viz T16. Krok 1 vždy, krok 2 podle MAF-12 (pak dva commity).

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Expected commit:**
```
fix(dashboard): scope competitor coverage to runs where it was tracked
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-17 — Vazba run → queue item (F5)

Viz T17. **Jen po schválení migrace v MAF-12.**

Nejdřív navrhni CO uděláš + PROČ, včetně přesného znění migrace, a počkej
na potvrzení.

**Kritické:**
- Migrace přes Alembic, nullable sloupec, žádný backfill starých runů
  bez souhlasu.
- Ops tooltip poctivě říká, že staré runy bez vazby se počítají jako
  samostatné výsledky.

**Po dokončení:** `alembic upgrade head` a `downgrade -1` lokálně, testy,
prohlížeč `/ops`, summary, commit message.

**Expected commit:**
```
feat(ops): distinguish final outcomes from retry attempts
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-18 — Gemini thinking tokeny (F15)

Viz T18. Jen pokud T0 potvrdil.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** Python (`estimate_run_cost`) a SQL (`run_cost_sql_expr`)
cesta musí dát pro stejný payload stejnou cenu — test na obě.

**Expected commit:**
```
fix(ops): price Gemini thinking tokens as output
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-19 — Poplatky za web search (F16)

Viz T19. **Jen po T0 a schválení migrace v MAF-12.**

Nejdřív navrhni CO uděláš + PROČ, včetně migrace a zdroje počtu
vyhledávání na run, a počkej na potvrzení.

**Kritické:** ceny za vyhledávání nevymýšlej — zadá je uživatel
v `/ai-models`. Bez ceny = cena runu NULL (design decision 14 z
`docs/TASKS_COST_COMPONENTS.md`), což MAF-8 ukáže jako „bez ceny".

**Expected commit:**
```
feat(ops): include per-search fees in run cost
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-20 — Backfill analýz (F10)

Viz T20. Jen pokud T0 našel dotčené klienty.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Dva commity v tomto pořadí: nejdřív čtení jen posledního výsledku
  (čísla se nesmí změnit — ověř na dashboardu před a po), teprve pak
  backfill příkaz.
- Backfill zapisuje jen nové řádky. Spouští ho uživatel.

**Expected commits:**
```
refactor(dashboard): read only the latest analysis result per response
feat(clients): add an analysis backfill command
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-21 — CHANGELOG

Viz T21. Jen `[Unreleased]`, číslo verze neměň.

Nejdřív navrhni odrážky a počkej na potvrzení.

**Expected commit:**
```
docs(changelog): record metrics audit fixes
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-22 — Definice metrik v REQUIREMENTS.md

Viz T22. Ukaž diff, počkej na potvrzení.

**Expected commit:**
```
docs(requirements): define dashboard and ops metrics
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MAF-23 — Nasazení a end-of-branch docs

Viz T23. **Tenhle prompt nepíše kód.**

**Kritické:**
- Merge, bump verze, přesun CHANGELOGu a tag dělá **uživatel**.
- Příkazy pro server ve tvaru pro otevřenou SSH session, lokální pro
  **PowerShell**, u každého kroku kde běží.
- Pokud větev obsahuje migrace (T17, T19), projdi DB-aware rollback
  v `docs/DEPLOYMENT.md` ještě před nasazením.

**Postup:**
1. `docs/DEPLOYMENT.md` kapitoly 1–5.
2. Ověření podle T23 bod 3.
3. `## Status: ...` v obou souborech, řádek v `docs/00_INDEX.md`.
4. Implementation summary + commit message.

**Expected commit:**
```
docs(metrics-audit): record deploy and close the branch
```

### (sem dopiš DONE — commit {hash} až bude hotovo)
