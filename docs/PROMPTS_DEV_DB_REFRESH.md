# SignalMap — Claude Code Session Prompts: Dev DB Refresh

## Status: ✅ Done — PR #22, merged 2026-09-26, release pending

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-dev-db-refresh (z aktuálního master)
## 2. Čtyři prompty (DR-1 až DR-4), pořadí je závazné. DR-2 a DR-3 stojí na
##    sdílených funkcích z DR-1, DR-3 na kontrolách z DR-2, DR-4 popisuje hotový
##    skript.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (zprávu navrhne agent na konci promptu,
##    commit provádíš ty, ne agent). Agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, i když zprávu sám navrhl.
## 5. PROGRESS TRACKING po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_DEV_DB_REFRESH.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 6. Kompletní zdůvodnění vč. design decisions 1-16:
##    docs/TASKS_DEV_DB_REFRESH.md. Přečti si ho celý před DR-1.
## 7. PŘED DR-2: přepni v lokálním .env SCHEDULER_DRY_RUN=true (ručně, agent
##    .env nemění). Bez toho skript podle návrhu odmítne běžet. Že odmítne se
##    se `false` testuje taky, viz DR-2.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-dev-db-refresh.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_DEV_DB_REFRESH.md (CELÉ, hlavně design decisions 1-16)
3. tools/local/pull_backup.py a tools/local/restore_local.py (stávající
   řetězec záloh, na který se navazuje)
4. README.md, sekce "Backup, restore, or transfer local data"

KONTEXT: Nový lokální nástroj tools/local/refresh_dev_db.py nahraje do
lokální dev databáze produkční dump. Před tím zazálohuje dev databázi a
zajistí, že obnovená data nic nespustí. Žádná změna aplikace, schématu
ani migrací.

KRITICKÉ:
- Obnovená produkční data NESMÍ spustit volání providera. Tři pojistky
  (design decision 10): kontrola výsledné hodnoty SCHEDULER_DRY_RUN přes
  `docker compose config` PŘED jakoukoli změnou, vypnutí rozvrhů a zrušení
  fronty v obnovených datech, kontrola worker_heartbeats.dry_run PO startu.
- Skript NIKDY nepřepisuje .env, jen odmítne pokračovat.
- Každý přepis databáze nejdřív zálohuje, bez výjimky (i restore-dev).
- Obnova jde do signalmap_incoming a teprve po kontrole a úpravě dat se
  přejmenuje na signalmap. Při chybě zůstane dev databáze nedotčená.
- Kontroly běží dřív, než se cokoli zastaví, smaže nebo stáhne.
- Jen standardní knihovna. Skript běží na Windows hostiteli, Postgres volá
  přes `docker compose exec postgres`.
- pull_backup.py běží denně v Task Scheduleru. Jeho chování se nesmí změnit.
- Běhy `runs` se nemění (evidence, AI_INSTRUCTIONS §3).
- Testovací data: Skoda Auto, prompt 54, gemini-3.1-flash-lite.
```

---
---

## DR-1 — Sdílené pomocné funkce

Viz `docs/TASKS_DEV_DB_REFRESH.md` T1. Shrnutí: vytáhnout `compose()`,
`psql()`, `newest_dump()`, `DUMP_RE`, `COUNT_TABLES` a konstanty cest z
`restore_local.py` do nového `tools/local/_dbtools.py`. Čistý refaktor.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T1) a počkej na potvrzení.

**Kritické:** `DUMP_RE` je dnes ve dvou podobách: v `pull_backup.py` s
capture groups (používá je `dump_taken_at`), v `restore_local.py` bez nich.
Sjednoť je **jen tehdy, když se chování `pull_backup.py` prokazatelně
nezmění**. Ten skript běží bez dozoru každé ráno a chyba v něm by se
projevila až chybějícími zálohami. Když si nejsi jistý, nech ho být a napiš
proč.

**Po dokončení:**
1. `python tools/local/restore_local.py` doběhne stejně jako před změnou.
2. `python tools/local/pull_backup.py` doběhne s kódem 0 nebo 2.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
refactor(tools): share local db helpers between scripts
```

### DONE — commit f81babf

---

## DR-2 — CLI, `list`, `snapshot`, kontroly, plán

Viz `docs/TASKS_DEV_DB_REFRESH.md` T2. Shrnutí: kostra
`tools/local/refresh_dev_db.py` s podpříkazy (`refresh` jako výchozí,
`restore-dev`, `snapshot`, `list`), přepínače `--yes` a `--plan`, výběr
souboru podle začátku časového razítka, kontroly a výpis plánu s potvrzením.
`refresh` a `restore-dev` v tomhle kroku končí po plánu, zápis přijde v DR-3.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- **SCHEDULER_DRY_RUN se čte z `docker compose config --format json`,
  NE z textu `.env`.** Výsledná hodnota zahrnuje proměnnou ze shellu i
  výchozí hodnotu v Compose (`${SCHEDULER_DRY_RUN:-true}`). Hodnotu
  porovnávej stejně, jak ji parsuje pydantic v `app/config.py` (`true`/`1`/
  `yes`/`on`, bez ohledu na velikost písmen), ne jako přesný řetězec.
- Kontroly vrací **seznam** problémů, `--plan` vypíše všechny najednou.
- Kontrola alembic (design decision 15): revize v `alembic_version`
  obnovovaného dumpu se zjistí **bez přepsání** čehokoli. Např. obnovou
  jen té tabulky do dočasné databáze (`pg_restore -t alembic_version`), nebo
  jiným způsobem, který navrhneš. Porovnej s revizemi v `alembic/versions/`.
- Není-li stdin terminál a chybí `--yes`, skončit chybou, nečekat.
- `snapshot` zapisuje přes `.tmp` a přejmenuje až po kontrole hlavičky
  `PGDMP`, stejně jako `pull_backup.py`.

**Po dokončení:**
1. `list` vypíše prod dumpy i dev zálohy.
2. `snapshot` vytvoří zálohu a ta jde obnovit (`pg_restore --list` přes
   `docker compose exec`).
3. Dočasně `SCHEDULER_DRY_RUN=false` v prostředí shellu →
   `refresh --plan` vypíše chybu kontroly a skončí nenulovým kódem.
   Tím se ověří, že shell má přednost před `.env`.
4. Se `true` → `refresh --plan` vypíše plán, skončí s 0 a **nic se
   nezměnilo** (stejné kontejnery, stejná dev databáze).
5. Výběr souboru: nejednoznačný prefix vypíše shody a skončí.
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(tools): add dev db refresh CLI with safety preflight
```

### DONE — commit 04b0cdf

---

## DR-3 — `refresh` a `restore-dev`

Viz `docs/TASKS_DEV_DB_REFRESH.md` T3. Shrnutí: kroky 1–13 z T3 v přesně
tomhle pořadí: kontroly → pull → plán → záloha → stop app/worker → obnova do
`signalmap_incoming` → kontrola + úprava dat → výměna + komentář → start s
dry-run → create_admin → kontrola heartbeatu → souhrn.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení. **Navrhni i
konkrétní SQL pro úpravu dat** (design decisions 10b a 12) a počkej, než ho
potvrdím.

**Kritické:**
- Úprava dat probíhá v `signalmap_incoming`, **PŘED** výměnou. `signalmap`
  nikdy nesmí obsahovat neupravená produkční data.
- Povolené hodnoty `notification_outbox.status` a `run_queue.status` ověř v
  `alembic/versions/0030_scheduler.py` (a novějších migracích). Nevymýšlej je.
- `inactive_reason='user'`, ne nová hodnota (design decision 12).
- Při chybě v krocích 5–8: `signalmap` zůstává původní, `signalmap_incoming`
  se uklidí, vypíše se cesta k záloze a kontejnery se **nespouštějí**
  automaticky.
- `DROP DATABASE signalmap` až po úspěšné obnově, kontrole i úpravě dat.
  `ALTER DATABASE ... RENAME` hned po něm.
- Kontrola heartbeatu: čekat na řádek s `last_seen_at` novějším než start
  workeru, ne na jakýkoli existující řádek. Staré řádky se sice mažou, ale
  kontrola se na to nemá spoléhat.

**Po dokončení:**
1. `refresh` s nejnovějším dumpem: v UI jsou produkční klienti (Skoda
   Auto), všechny rozvrhy vypnuté, stránka rozvrhů ukazuje dry-run,
   přihlášení dev adminem funguje.
2. Po několika minutách chodu: `SELECT count(*) FROM runs` se nezměnil.
3. `restore-dev` vrátí stav před refreshem (porovnat počty ze souhrnu).
4. Simulovaná chyba (zkrácený dump přes plnou cestu): dev databáze beze
   změny, `signalmap_incoming` neexistuje.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(tools): refresh the local dev db from a prod dump
```

### DONE — commit 9041851

---

## DR-4 — Dokumentace

Viz `docs/TASKS_DEV_DB_REFRESH.md` T4. Shrnutí: odstavec v `README.md`
(sekce „Backup, restore, or transfer local data“), rozlišení
`restore_local.py` × `refresh_dev_db.py`, bod v `CHANGELOG.md` pod
`## [Unreleased]`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** README je v angličtině, drž jeho styl. Nový dokumentační
soubor nezakládej (AI_INSTRUCTIONS §4).

**Po dokončení:**
1. Implementation summary + navrhni commit message (nespouštěj git)
2. Připomeň end-of-branch krok (AI_INSTRUCTIONS §7.5): `## Status:` v
   tomhle souboru a v TASKS, řádek v `docs/00_INDEX.md`, těsně před
   mergem.

**Expected commit:**
```
docs(docs): document the dev db refresh script
```

### DONE — commit 48706f1
