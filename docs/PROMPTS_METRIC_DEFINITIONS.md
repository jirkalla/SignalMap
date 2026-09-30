# SignalMap — Claude Code Session Prompts: Metric Definitions (vydání 4, první větev)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-metric-definitions (z master po vydání 3)
## 2. Pět promptů (MD-1 až MD-5), v tomhle pořadí. MD-1 je analytický —
##    texty definic schvaluješ ty, než se zapíšou.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent). Commit dokončit před dalším promptem.
## 5. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_METRIC_DEFINITIONS.md v "Task Index" ⏳ → ✅.
## 6. Po MD-5: merge do master BEZ nasazení. Nasazení proběhne v EX2-8
##    (docs/PROMPTS_EXPORT_V2.md) jako v1.6.0.
## 7. Zdůvodnění a design decisions 1-9: docs/TASKS_METRIC_DEFINITIONS.md.
## 8. Lokální testy běží v Dockeru — před MD-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-metric-definitions
(vydání 4, první větev; nasazuje se až s export-v2 jako v1.6.0).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_METRIC_DEFINITIONS.md — CELÉ, hlavně „Výchozí stav"
   a design decisions 1-9
3. docs/ROADMAP.md #26
4. Soubory z „Target" aktuálního úkolu

KONTEXT: Žádná metrika v appce nemá vysvětlení. Cíl: ikona ⓘ u každé
metriky (dashboard, /ops, detail runu a klienta) s textem „co znamená"
a „jak se počítá" — hover na desktopu, klepnutí na mobilu, klávesnice;
slovníček metrik na /help. Jeden zdroj definic v i18n + katalog
app/metrics_catalog.py, který později použije i export.

KRITICKÉ:
- Definice se píšou podle KÓDU, ne podle paměti nebo dokumentace.
  Nesrovnalosti se neopravují potichu — zapsat do „Nálezy", rozhodnu já.
- Tahle větev NEMĚNÍ výpočty metrik.
- Ne jen hover / title= — musí fungovat na dotyku a z klávesnice.
- Žádná tooltip knihovna, žádný build krok.
- Texty jen v i18n (DE i EN), nikde natvrdo v šabloně/Pythonu/JS.
- UI ověřit na ~375 / ~768 px / desktop.
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Tahle větev se nenasazuje samostatně; bump verze a tag dělá uživatel.
```

---

## MD-1 — Katalog metrik + definice z kódu

```
Úkol MD-T1 z docs/TASKS_METRIC_DEFINITIONS.md (design decisions 3-6).

1. Projdi obrazovky z design decision 8 a pro každou zobrazenou metriku
   najdi funkci, která ji počítá.
2. Ukaž mi tabulku: metrika → funkce (soubor:řádek) → jak se počítá
   (tvými slovy z kódu: jmenovatel, filtry, vyloučení, období) →
   navržené what/how (EN) → nesrovnalosti s UI popiskem nebo
   docs/REQUIREMENTS.md. NIC nezapisuj, dokud texty neschválím.
3. Po schválení: app/metrics_catalog.py + i18n klíče DE/EN.
4. Nesrovnalosti do sekce „Nálezy" v TASKS dokumentu.

Navržený commit: feat(i18n): add a catalog of metric definitions
```

---

## MD-2 — Komponenta ⓘ

```
Úkol MD-T2 z docs/TASKS_METRIC_DEFINITIONS.md (design decisions 1-2).

1. Přečti base.html (delegovaný listener data-confirm jako vzor),
   macros.html a strukturu Vue islandu v dashboard/index.html.
2. Krátce mi popiš markup, chování a přístupnost, než začneš.
3. Implementuj T2 body 1-3, test T2 bod 4.
4. Ověř na jedné metrice dashboardu a jedné v /ops — myš, klepnutí
   (mobile preset), klávesnice (Tab, Enter, Esc). Screenshoty.

Navržený commit: feat(i18n): add an accessible metric help popover
```

---

## MD-3 — Nasazení na obrazovky + slovníček na /help

```
Úkol MD-T3 z docs/TASKS_METRIC_DEFINITIONS.md (design decision 8).

1. Implementuj T3 body 1-2.
2. Prohlížeč T3 bod 3 — všechny obrazovky na třech šířkách, hlavně
   KPI dlaždice a hlavičky tabulek na mobilu. Screenshoty.

Navržený commit: feat(i18n): explain metrics on the dashboard, ops and help pages
```

---

## MD-4 — Test pokrytí definic

```
Úkol MD-T4 z docs/TASKS_METRIC_DEFINITIONS.md (design decision 7).

1. Rozšiř tests/test_i18n_coverage.py (z vydání 1), nebo založ
   tests/test_metrics_catalog.py — zdůvodni volbu.
2. Test podle T4 bodů 1-2.

Navržený commit: test(i18n): require a definition for every displayed metric
```

---

## MD-5 — Dokumentace + CHANGELOG

```
Úkol MD-T5 z docs/TASKS_METRIC_DEFINITIONS.md.

1. CHANGELOG.md [Unreleased] → Added.
2. docs/REQUIREMENTS.md — odkaz na katalog, kde se definice opakují.
3. docs/ROADMAP.md #26; otevřené nálezy z MD-1 do roadmapy.
Ukaž diff PŘED zápisem.

Navržený commit: docs(docs): document metric definitions

Pak se mě zeptej na merge do master (bez nasazení) a připomeň, že
další je feature/signalmap-export-v2.
```
