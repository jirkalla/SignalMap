# SignalMap — Claude Code Session Prompts: Market Locale Names (vydání 3)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-market-locale-names (z master po vydání 2)
## 2. Šest promptů (ML-1 až ML-6), v tomhle pořadí — ML-2 volá resolver
##    z ML-1, ML-3 a ML-4 potřebují sloupce z ML-2.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent). Commit dokončit před dalším promptem.
## 5. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_MARKET_LOCALE_NAMES.md v "Task Index" ⏳ → ✅.
## 6. Zdůvodnění a design decisions 1-12: docs/TASKS_MARKET_LOCALE_NAMES.md.
## 7. Lokální testy běží v Dockeru — před ML-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-market-locale-names
(vydání 3, cíl v1.5.0).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_MARKET_LOCALE_NAMES.md — CELÉ, hlavně „Výchozí stav"
   a design decisions 1-12
3. docs/ROADMAP.md #22
4. Soubory z „Target" aktuálního úkolu

KONTEXT: System instruction dnes dostává {market_language}=cs a
{market_country}=CZ. Cíl: market se zakládá jen kódem (cs-CZ), zbytek
z CLDR přes Babel, placeholdery vrací anglické názvy (Czech / Czech
Republic), ISO kódy zůstávají pro API. Jediná změna tohoto vydání —
mění text instrukce pro modely.

KRITICKÉ:
- market.language / market.country zůstávají ISO kódy — country jde jako
  user_location.country do Anthropic/OpenAI/Grok. Adaptéry se NEMĚNÍ.
- Názvy se UKLÁDAJÍ (snapshot), nikdy se nepočítají za běhu.
- V1 jen `jazyk` a `jazyk-ZEMĚ`; skript/varianta se odmítá.
- Jedna migrace, jen přidává; flagovaná v TASKS dokumentu.
- locale_name se nemaže. /findings se neupravuje.
- UI texty přes t() v DE i EN; UI ověřit na ~375 / ~768 px / desktop.
- Testovací fixture: Skoda Auto, prompt 54, gemini-3.1-flash-lite.
- Historické runy se nepřepisují (NFR-6).
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Bump verze a tag dělá uživatel (AI_INSTRUCTIONS.md §4).
```

---

## ML-1 — Babel + resolver locale

```
Úkol ML-T1 z docs/TASKS_MARKET_LOCALE_NAMES.md (design decisions 1-4).

1. Zjisti aktuální verzi babel, pinni ji, přestav image, ověř import.
2. app/services/locale_names.py podle T1 bodů 2-3.
3. tests/test_locale_names.py podle T1 bodu 4.
4. pytest.

Navržený commit: feat(i18n): add CLDR-backed locale code resolver
```

---

## ML-2 — Migrace: názvy jazyka/země na markets

```
Úkol ML-T2 z docs/TASKS_MARKET_LOCALE_NAMES.md (design decisions 5, 8).

1. Zjisti poslední číslo migrace v alembic/versions/.
2. Migrace + backfill + model podle T2 bodů 1-3.
3. Upravit fixtures jen tam, kde to test potřebuje.
4. Ověření T2 bod 4 na kopii produkční DB (tools/local/refresh_dev_db.py
   — zeptej se, než ho spustíš). Ukaž výstup SELECTu.
5. pytest.

Navržený commit: feat(i18n): store English language/country names on markets
```

---

## ML-3 — /markets: kód jako jediný vstup

```
Úkol ML-T3 z docs/TASKS_MARKET_LOCALE_NAMES.md (design decisions 1, 9, 10).

1. Přečti markets.py, markets/*.html, macros.html, tests/test_markets.py.
2. Krátce mi popiš rozložení formuláře a chování při editaci, než začneš.
3. Implementuj T3 body 1-5.
4. Testy T3 bod 6.
5. Prohlížeč T3 bod 7, screenshoty. Testovací market po sobě smaž.

Navržený commit: feat(i18n): derive market language/country from the locale code
```

---

## ML-4 — Placeholdery v system instruction + /settings

```
Úkol ML-T4 z docs/TASKS_MARKET_LOCALE_NAMES.md (design decisions 6-7).

1. Implementuj T4 body 1-3. market_country pro ADAPTÉR zůstává ISO —
   ověř, že se ho změna netkne.
2. Testy T4 bod 4.
3. Řekni mi, kde /findings cituje starý placeholder (neupravuj).
4. Lokální run fixture (dry-run vypnutý jen pro tenhle jeden ruční run),
   ukaž request_payload ->> 'system_instruction'.

Navržený commit: feat(runs): pass language and country names to system instructions
```

---

## ML-5 — Dokumentace + CHANGELOG

```
Úkol ML-T5 z docs/TASKS_MARKET_LOCALE_NAMES.md.

1. CHANGELOG.md [Unreleased] podle T5 (znění podle skutečné implementace).
2. docs/REQUIREMENTS.md — pole marketu, navrhni doplnění.
3. docs/ROADMAP.md #22.
Ukaž diff PŘED zápisem.

Navržený commit: docs(docs): document market locale names
```

---

## ML-6 — Nasazení v1.5.0 a ověření

```
Úkol ML-T6 z docs/TASKS_MARKET_LOCALE_NAMES.md.

Vede mě krok za krokem podle T6 a docs/DEPLOYMENT.md kapitol 0-5.
Příkazy na serveru spouštím já — ty je připravíš a vyhodnotíš výstup.

1. PŘED MERGE: SQL z T6 kroků 1-2, vyhodnoť šablony, výsledek zapiš
   do TASKS dokumentu.
2. Připomeň merge PR, bump v1.5.0, přesun [Unreleased], tag (dělám já).
3. Nasazení — rebuild kvůli babel, mimo okno scheduleru.
4. Ověření T6 kroky 5-6.
5. Druhý den T6 krok 7.
6. Deploy log s datem/časem přepnutí instrukce, end-of-branch docs.

Navržený commit na konci:
docs(docs): record market locale names deploy and close the branch
```
