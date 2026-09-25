# SignalMap — Claude Code Session Prompts: OpenAI SDK Import Race

## Status: 🔜 Merged (PR #21, 2026-09-25) — production deploy and verification (OIR-3) pending

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-openai-import-race (z aktuálního master)
## 2. Tři prompty (OIR-1 až OIR-3), pořadí vynucené — OIR-2 zapisuje do
##    CHANGELOGu to, co OIR-1 opravil, a OIR-3 to nasadí.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 5. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_OPENAI_IMPORT_RACE.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 6. Kompletní zdůvodnění vč. příčiny a design decisions 1-8:
##    docs/TASKS_OPENAI_IMPORT_RACE.md — přečti si ho celý před OIR-1.
## 7. Lokální testy běží v Dockeru — před OIR-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-openai-import-race.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_OPENAI_IMPORT_RACE.md — CELÉ, hlavně sekci „Příčina" a
   design decisions 1-8
3. app/adapters/__init__.py a jeden openai-based adaptér
   (app/adapters/grok.py nebo deepseek.py)

KONTEXT: Po nasazení v1.1.0 (2026-09-25) spadl první testovací run na Grok
s "deadlock detected by _ModuleLock('openai.resources.chat')". Opakovaný
run prošel. openai SDK (3.13.0) načítá `client.chat` a `client.responses`
líně přes cached_property s lokálním importem — první import tak běží ve
vlákně requestu a dva souběžné runy se zaseknou na import zámku.
Stejný vzor má anthropic SDK (1.4.0, `client.messages`). google-genai ne.
Dotčené adaptéry: openai, perplexity, grok, deepseek, anthropic (tabulka
v TASKS). Nejnovější openai (3.19.2) je pořád líný — upgrade neřeší nic
a do téhle větve nepatří (design decision 8).

KRITICKÉ:
- Oprava = načíst moduly předem při startu (import v app/adapters/__init__.py),
  NE zámek kolem prvního volání a NE retry v adaptéru (TASKS design
  decisions 1 a 6).
- Cesty modulů ověř proti SDK nainstalovanému v kontejneru, ne z paměti.
  Dokumentované cesty platí pro openai==3.13.0 a anthropic==1.4.0.
- Pro google-genai nic nepřidávej — Models se importuje eagerly (TASKS
  design decision 5). Kdyby kontejner ukázal něco jiného, zastav se.
- Regresní test musí běžet v SAMOSTATNÉM procesu (subprocess), jinak nic
  nedokazuje.
- Uložený error run z incidentu se nepřepisuje (NFR-6).
- Změna čísla verze a tag dělá uživatel, ne agent (AI_INSTRUCTIONS.md §4).
```

---
---

## OIR-1 — Eager import + regresní test

Viz `docs/TASKS_OPENAI_IMPORT_RACE.md` T1. Shrnutí: v
`app/adapters/__init__.py` předem naimportovat `openai.resources.chat` a
`openai.resources.responses` a `anthropic.resources.messages`, v
`tests/test_adapters.py` test v samostatném procesu, že je `import
app.adapters` načte.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T1) a počkej na potvrzení.

**Kritické — před psaním kódu:**
1. Požádej uživatele o výstup příkazu z T1 bod 1 (produkční logy). Když
   z něj souběh dvou runů nevyplývá, **zastav se** a vrať se k diagnóze.
2. Ověř cesty modulů příkazem z T1 bod 2 v lokálním kontejneru — pro
   `openai`, `anthropic` i `google.genai`. **Netvrď, že modul existuje,
   dokud ho nevidíš ve zdrojáku nainstalované verze.**

**Po dokončení:**
1. Nový test projde; pak celá sada:
   ```bash
   COPYFILE_DISABLE=1 tar -cf - tests requirements-dev.txt | docker compose run --rm --no-deps -T app sh -c 'tar -xf - && pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -p no:cacheprovider -q'
   ```
2. Ověř, že test opravdu hlídá: dočasně zakomentuj importy → test musí
   selhat → vrať zpět.
3. `docker compose up -d --build --wait` a jeden run přes openai-based
   providera v prohlížeči (Skoda Auto, prompt 54).
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
fix(adapters): preload openai SDK resource modules at startup
```

### DONE — commit 441286a

---

## OIR-2 — CHANGELOG

Viz `docs/TASKS_OPENAI_IMPORT_RACE.md` T2. Shrnutí: pod `## [Unreleased]`
založit `### Fixed` s jednou odrážkou.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** jen `[Unreleased]`. Sekci `[1.1.1]` nezakládej a číslo verze
neměň — to je krok uživatele při releasu (`docs/DEPLOYMENT.md` kapitola 0).

**Po dokončení:**
1. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(changelog): record the openai import race fix
```

### DONE — commit 441286a (bundled with OIR-1 in one commit)

---

## OIR-3 — Nasazení v1.1.1 a ověření na produkci

Viz `docs/TASKS_OPENAI_IMPORT_RACE.md` T3. **Tenhle prompt nepíše kód** —
provází nasazení podle `docs/DEPLOYMENT.md` a ověřuje opravu.

**Kritické:**
- Merge, bump verze na `1.1.1`, přesun CHANGELOGu a tag dělá **uživatel**.
  Agent je navrhne a počká.
- Příkazy pro server dávej ve tvaru pro otevřenou SSH session (bez
  `ssh signalmap '…'` obalu), lokální příkazy pro **PowerShell**, a u
  každého kroku označ, kde běží.
- Ověření opravy **hned po 4.4**, dokud je kontejner `app` čerstvě
  spuštěný — později už jsou moduly načtené a test by nic nedokázal.

**Postup:**
1. Celý runbook `docs/DEPLOYMENT.md`, kapitoly 1–5. Migrace žádné →
   1.3 je prázdný.
2. Po 4.4: současně (dva taby) run na DeepSeek a na Grok → oba projdou.
3. End-of-branch docs: `## Status: ...` v obou souborech této větve,
   řádek v `docs/00_INDEX.md`.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(openai-import-race): record deploy and close the branch
```

### (sem dopiš DONE — commit {hash} až bude hotovo)
