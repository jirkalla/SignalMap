# SignalMap — Claude Code Session Prompts: Peec Comparison

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-peec-comparison (z aktuálního master)
## 2. Pět promptů (PC-1 až PC-5), pořadí vynucené — PC-2 počítá nad
##    daty, která načte PC-1, PC-3 zapisuje výsledky PC-2.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 5. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_PEEC_COMPARISON.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 6. Kompletní zdůvodnění vč. pilotních čísel a design decisions 1-15:
##    docs/TASKS_PEEC_COMPARISON.md — přečti si ho celý před PC-1.
## 7. Potřeba: běžící lokální Compose stack (dev DB s produkčními daty),
##    exporty v docs/peec/, openpyxl na hostiteli.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-peec-comparison.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_PEEC_COMPARISON.md — CELÉ, hlavně „Výchozí stav",
   „Výstup — struktura složky" a design decisions 1-15
3. tools/local/_dbtools.py a tools/local/refresh_dev_db.py (vzor CLI,
   volání psql přes docker compose)
4. jeden soubor docs/peec/peec_knauf_*.json (struktura odpovědi Peec)

KONTEXT: Peec a SignalMap sbírají odpovědi AI na stejných 25 promptů
Knaufu. Porovnáváme jen modely v obou nástrojích (gpt-5.6-terra,
claude-haiku-4-5). Pilot ukázal: značky se shodují na úrovni šumu,
zdrojové domény se liší víc než šum; SignalMap nenajde „Saint-Gobain",
protože entita se jmenuje „Saint Gobain" bez aliasů. Výstup je složka
v docs/peec/ pro Philipa (XLSX/CSV + README), anglicky.

KRITICKÉ:
- Jen lokální nástroj: žádná změna app/, žádná migrace, z DB jen SELECT.
- docs/peec/ je gitignored klientská data — výstup se necommituje.
- Prompty se párují podle TEXTU proti DB, ne podle názvu souboru.
- Srovnání textu doslovně nedává smysl (Jaccard slov ~0,2 i uvnitř
  jednoho nástroje) — metriky L2/L3/L5, ne diff textu. L4 NE.
- Každý rozdíl se ukazuje vedle šumu uvnitř nástrojů (decision 7).
- Nic neopravovat v datech (alias Saint-Gobain je krok uživatele v UI).
- Testovací fixture pro ruční běhy appky: Skoda Auto, prompt 54,
  gemini-3.1-flash-lite (tady se ale nic nespouští).
```

---
---

## PC-1 — Načtení, párování a export zdrojů

Viz `docs/TASKS_PEEC_COMPARISON.md` T1. Shrnutí: `tools/local/compare_peec.py`
načte exporty Peec a data SignalMapu, spáruje je (prompt × model × lokální
den) a zapíše `sources/` (CSV + `sources.xlsx` + kopie JSON). `--plan`
jen vypíše počty a cíl.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T1) a počkej na potvrzení.

**Kritické:**
- DB jen přes `_dbtools.psql` / `COPY (SELECT …) TO STDOUT`, žádný zápis.
- Neshoda textu promptu nebo soubor s víc prompty = chyba s názvem
  souboru, ne tichý přeskok.
- `openpyxl` importovat až při zápisu XLSX; bez něj srozumitelná chyba.
- Existující výstupní složka bez `--overwrite` = chyba.

**Po dokončení:**
1. `python tools/local/compare_peec.py --plan` → 25 promptů, 2 modely,
   150 odpovědí Peec a 100 runů SignalMapu na model.
2. Plný běh; `sources/sources.xlsx` otevřít, přehlásky čitelné.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(tools): export Peec and SignalMap answers for comparison
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## PC-2 — Metriky a šum

Viz `docs/TASKS_PEEC_COMPARISON.md` T2. Shrnutí: pravítko značek,
normalizace domén, metriky na pár → `pairs.csv`, agregáty za model,
reference šumu, bootstrap verdikt, detector check.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Skupina prompt × model × den se nejdřív zprůměruje (3 opakování Peec
  23. 9. mají váhu 1, ne 3).
- Pravidla pravítka jako jedna tabulka v kódu, zapsaná i do
  `tracked_brands.csv` — žádné rozeseté regexy.
- Bootstrap s pevným seedem; seed v README.
- Výsledky porovnej s pilotem v TASKS „Výchozí stav" a rozdíly vysvětli.

**Po dokončení:**
1. Plný běh; ukaž tabulku cross vs šum za terra a haiku.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(tools): compute Peec vs SignalMap agreement metrics
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## PC-3 — `report.xlsx` + README

Viz `docs/TASKS_PEEC_COMPARISON.md` T3 (tabulka listů). Shrnutí:
report pro Philipa — README (metoda, jen společné modely a proč),
Summary s verdikty, Coverage, Brands, Domains, Detector check, Pairs, Gaps.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Anglicky. README srozumitelné bez znalosti SignalMapu.
- Gaps musí výslovně obsahovat: neporovnané modely s důvodem, chybějící
  páry, metriky „víc než šum", problémy detektorů, neznámou konfiguraci
  Peec (k vyplnění uživatelem), co se neměří (L4, sentiment).
- Hlavní zjištění v Summary generovat z čísel, ne natvrdo z pilotu.

**Po dokončení:**
1. Plný běh s `--overwrite`; ukaž strukturu složky a obsah README.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(tools): write the Peec comparison report workbook
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## PC-4 — Testy + ověření

Viz `docs/TASKS_PEEC_COMPARISON.md` T4. Shrnutí: stdlib `unittest` pro
čisté funkce, běh nad reálnými daty, ruční kontrola 3 párů.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Po dokončení:**
1. `python -m unittest tools/local/test_compare_peec.py` projde.
2. Ověř, že test opravdu hlídá: dočasně rozbij normalizaci domén → test
   selže → vrať zpět.
3. 3 náhodné páry z `pairs.csv` ručně proti textům (vypiš je).
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test(tools): cover the Peec comparison helpers
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## PC-5 — Dokumentace a uzavření

Viz `docs/TASKS_PEEC_COMPARISON.md` T5.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Jen `[Unreleased]` v CHANGELOGu, číslo verze neměň.
- End-of-branch docs až těsně před merge (AI_INSTRUCTIONS.md §7 bod 5).

**Po dokončení:**
1. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(docs): document the Peec comparison tool
```

### (sem dopiš DONE — commit {hash} až bude hotovo)
