# SignalMap — Claude Code Session Prompts: Worker Throughput

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-worker-throughput (z aktuálního master)
## 2. Pět promptů (WT-1 až WT-5), pořadí vynucené — WT-2 staví na smyčce
##    rozdělené ve WT-1, WT-3 (víc replik) až po WT-2 (jinak 4× víc
##    falešných `worker.stale`), WT-4 dokumentuje, WT-5 nasazuje.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 5. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_WORKER_THROUGHPUT.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 6. Kompletní zdůvodnění vč. naměřených dat, simulace a design decisions 1-9:
##    docs/TASKS_WORKER_THROUGHPUT.md — přečti si ho celý před WT-1.
## 7. Lokální testy běží v Dockeru — před WT-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-worker-throughput.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_WORKER_THROUGHPUT.md — CELÉ, hlavně „Výchozí stav" a
   design decisions 1-9
3. app/worker.py (celý), app/services/queue.py::claim_next,
   app/services/schedule_monitor.py, služba `worker` v docker-compose.yaml

KONTEXT: Denní dávka Knaufu (100 runů, 4 modely) trvá ~42 min. 80 % je
čekání na providery, 20 % pevná 5s pauza workeru po KAŽDÉ položce. Běží
jeden worker, i když fronta (SKIP LOCKED, heartbeat per worker_name,
advisory lock v kvótě) víc workerů zvládá. Tahle větev: A = spát jen
při prázdné frontě, B = WORKER_REPLICAS v compose (produkce 4), plus
oprava heartbeatu, který dnes při volání > 60 s hlásí falešný
`worker.stale` (stalo se 2026-09-25 10:37, run 375, 73 s).

KRITICKÉ:
- Souběžnost podle poskytovatele (C1/C2) do téhle větve NEPATŘÍ —
  docs/ROADMAP.md #18. Žádné vlákna/async pro volání providerů.
- SCHEDULER_PROVIDER_CONCURRENCY v app/config.py zůstává nečtený,
  neodstraňovat.
- Repliky přes `deploy.replicas: ${WORKER_REPLICAS:-1}`, NE `--scale`
  v runbooku (design decision 3). Výchozí 1 — dev PC se nemění.
- WORKER_NAME nesmí být nastavený při víc replikách.
- Lokální ověření s víc workery jen s SCHEDULER_DRY_RUN=true nebo
  FakeAdapterem — žádné placené runy navíc.
- Testovací fixture: Skoda Auto, prompt 54, gemini-3.1-flash-lite.
- Historické runy a notifikace se nepřepisují (NFR-6).
- Bump verze a tag dělá uživatel, ne agent (AI_INSTRUCTIONS.md §4).
```

---
---

## WT-1 — Smyčka nespí, když zpracovala položku

Viz `docs/TASKS_WORKER_THROUGHPUT.md` T1. Shrnutí: vytáhnout tělo smyčky
`run_forever` do testovatelné funkce vracející, jestli zpracovala
položku; `time.sleep(5)` jen když ne.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T1) a počkej na potvrzení.

**Kritické:**
- Ticker/reconcile/notifikační kontroly zůstávají na minutovém rytmu
  (`_TICKER_INTERVAL`) — zrušení pauzy je nesmí zrychlit.
- SIGTERM se kontroluje mezi položkami jako dnes — worker dokončí
  rozpracovanou položku a skončí.
- Ověř, že fronta plná `deferred` položek s budoucím `scheduled_for`
  nemůže smyčku roztočit naprázdno (`claim_next` je nevrátí → `None` →
  sleep).

**Po dokončení:**
1. Nové testy projdou; pak celá sada:
   ```bash
   COPYFILE_DISABLE=1 tar -cf - tests requirements-dev.txt | docker compose run --rm --no-deps -T app sh -c 'tar -xf - && pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -p no:cacheprovider -q'
   ```
2. `docker compose up -d --build --wait`, `docker compose logs --tail=20 worker`.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): skip the worker poll sleep while the queue has work
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## WT-2 — Heartbeat nezávislý na délce volání providera

Viz `docs/TASKS_WORKER_THROUGHPUT.md` T2 a design decision 6. Shrnutí:
daemon vlákno zapisuje DB heartbeat + touchfile každých 10 s s vlastní
session; zastaví se při SIGTERM před `deregister_heartbeat`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- SQLAlchemy session se mezi vlákny NESDÍLÍ — vlákno má vlastní
  `SessionLocal()`.
- Chyba ve vlákně se zaloguje a zkusí příští tik; vlákno nesmí umřít.
- Vlákno nevolá žádné SDK providera (import race z
  docs/TASKS_OPENAI_IMPORT_RACE.md se tím netýká).
- Do komentáře zapiš, co heartbeat přestává hlídat (zaseknuté volání)
  a co to hlídá místo něj (lease, design decision 14 ve
  docs/TASKS_SCHEDULER.md).
- Práh `WORKER_STALE_THRESHOLD_SECONDS` zůstává 60 s — mění se jen jeho
  zdůvodnění v komentáři.

**Po dokončení:**
1. Nové testy + celá sada `pytest` (příkaz z WT-1).
2. Ruční ověření podle T2 „Done when" (dlouhé volání přes FakeAdapter,
   `/schedules` zůstává „běží", `docker ps` healthy).
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
fix(runs): keep the worker heartbeat alive during long provider calls
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## WT-3 — Repliky workeru v compose

Viz `docs/TASKS_WORKER_THROUGHPUT.md` T3. Shrnutí:
`deploy.replicas: ${WORKER_REPLICAS:-1}` u služby `worker`,
`.env.example`, ověření se 4 replikami lokálně v dry-run.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické — před úpravou:**
1. `WORKER_REPLICAS=4 docker compose config` musí ukázat `replicas: 4`.
   Když compose interpolaci v `replicas` nebere, **zastav se** a vrať se
   k návrhu — neobcházej to potichu.
2. `grep -rn "worker-1" tools docs README.md` — nic nesmí mířit jen na
   první repliku bez vědomí uživatele; nálezy vypiš.

**Po dokončení:**
1. Lokálně `WORKER_REPLICAS=4` + `SCHEDULER_DRY_RUN=true`: 4 kontejnery
   healthy, `/schedules` ukazuje 4 workery s různými jmény, jedno
   „spustit teď" setu → každá položka právě jednou, `leased_by` rozdělené
   mezi víc workerů. Pak zpět na 1.
2. Celá sada `pytest`.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(infra): make the worker replica count configurable
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## WT-4 — Dokumentace + CHANGELOG

Viz `docs/TASKS_WORKER_THROUGHPUT.md` T4. Shrnutí: CHANGELOG
(`### Changed` + `### Fixed`), `docs/DEPLOYMENT.md` 4.2 pro N replik,
komentář u `scheduler_provider_concurrency`, poznámka v
`docs/ROADMAP.md` #18.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Jen `[Unreleased]` — sekci s verzí nezakládej, číslo verze neměň.
- Diff dokumentace ukaž, netlač ho potichu (AI_INSTRUCTIONS.md §3).

**Po dokončení:**
1. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(docs): document worker replicas and throughput changes
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## WT-5 — Nasazení a měření na produkci

Viz `docs/TASKS_WORKER_THROUGHPUT.md` T5. **Tenhle prompt nepíše kód** —
provází nasazení podle `docs/DEPLOYMENT.md` a měří výsledek.

**Kritické:**
- Před nasazením si od uživatele vyžádej potvrzení, že ověřil OpenAI
  RPM/TPM limity pro 4 souběžné požadavky (T5 bod 1).
- Merge, bump verze, přesun CHANGELOGu a tag dělá **uživatel**.
- `WORKER_REPLICAS=4` jde do serverového `.env`, ne do repa.
- Příkazy pro server dávej ve tvaru pro otevřenou SSH session (bez
  `ssh signalmap '…'` obalu), lokální pro **PowerShell**; u každého kroku
  označ, kde běží.

**Postup:**
1. Runbook `docs/DEPLOYMENT.md`, kapitoly 1–5. Migrace žádné.
2. Po 4.2: 4 repliky healthy, každá s `dry_run=False`, `enabled=True`.
3. Druhý den po doběhnutí Knaufu: SQL z T5 bod 4 a 5 — porovnat s
   „Výchozí stav" (42 min). Když přijdou 429 od OpenAI: `WORKER_REPLICAS=3`
   a navrhnout posun `docs/ROADMAP.md` #18 (C1) výš.
4. End-of-branch docs: `## Status: ...` v obou souborech této větve,
   řádek v `docs/00_INDEX.md`, stav #18 v `docs/ROADMAP.md`.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(worker-throughput): record deploy and close the branch
```

### (sem dopiš DONE — commit {hash} až bude hotovo)
