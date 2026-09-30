# SignalMap — Tasks: Market Locale Names (vydání 3)

## v1.0 | Září 2026
## Branch: feature/signalmap-market-locale-names
## Task ID prefix: ML
## Cílová verze: v1.5.0 (MINOR)

Status: navrženo 2026-09-30 jako **vydání 3** z plánu vydání
(`docs/ROADMAP.md` „Plán vydání"). Pokrývá `docs/ROADMAP.md` #22. Šest
úkolů, **jedna migrace** (jen přidává).

**Goal:** market se zakládá jedním kódem (`cs-CZ`), zbytek se předvyplní,
a system instruction dostává jazyk a zemi slovy — `{market_language}` =
„Czech", `{market_country}` = „Czech Republic" — místo `cs`/`CZ`. ISO kódy
dál slouží API (`user_location.country`) beze změny.

**Vydání obsahuje jen tuhle změnu, záměrně.** Je to změna metodiky (jiný
text instrukce pro modely) — pokud se po nasazení posunou odpovědi, musí
být jasné proč. Proto nic dalšího v tomhle vydání.

**Flagováno předem (`AI_INSTRUCTIONS.md` §4):**
- nová závislost **`babel`** (CLDR data, čistý Python);
- migrace **`markets.language_name`, `markets.country_name`** + backfill;
- **změna významu** placeholderů `{market_language}`/`{market_country}`.

---

## Výchozí stav (kód k 2026-09-30, master 7807f7a)

Markety (produkce):

| code | language | country | locale_name |
|---|---|---|---|
| cs-CZ | cs | CZ | Czech (Czech Republic) |
| de-DE | de | DE | German (Germany) |
| en-US | en | US | English (United States) |
| fr-FR | fr | FR | France ← nekonzistentní |

- `app/models/market.py`: `code String(20)`, `language String(10)`,
  `country String(10) NULL`, `locale_name String(100) NULL`.
- `app/routers/markets.py`: čtyři ručně psaná pole; `_validate_iso_format`
  kontroluje jen formát (`^[a-z]{2}$`, `^[A-Z]{2}$`); `code` se
  nevaliduje a nemusí odpovídat `language`/`country`.
- `app/services/run_execution.py::_build_system_instruction` plní
  `market_code`, `market_language` (= `cs`), `market_country` (= `CZ`),
  `market_locale_name`, `persona`; výsledek jde do
  `runs.request_payload["system_instruction"]` (`build_request_payload`, :181).
- `app/routers/settings.py`: `DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE`
  (`{market_locale_name}` + `{market_language}` → „…writing in cs.
  Answer in cs…"), `_DRY_RUN_VALUES` pro validaci šablony.
- **`market.country` jde jako ISO kód do `user_location.country`**
  (`anthropic.py:184`, `grok.py:124`, OpenAI) — **nesmí se změnit**.
- `market.locale_name` čte i `dashboard.py:267` (label filtru)
  a `app/utils.py:18` (select).
- i18n: `settings.placeholders_hint`, `help.settings.body` (DE/EN).
  `findings.html:124` cituje starý placeholder — editoriální stránka
  uživatele, **neupravovat**.

---

## Design decisions

1. **Kód locale (BCP 47) je jediný povinný vstup.** `language`/`country`
   se odvodí na serveru, uživatel je nepíše. Normalizace (`CS_cz` →
   `cs-CZ`). V1 jen `jazyk` a `jazyk-ZEMĚ`; skript/varianta (`zh-Hant-TW`,
   `sr-Latn-RS`, `es-419`) → srozumitelná chyba (country musí být
   dvoupísmenný ISO kód pro API).
2. **CLDR přes Babel**, ne ruční slovník (hardcoded seznam, §3). Validuje
   reálné kódy (`xx-QQ` neprojde). Verze pinovaná.
3. **Názvy se ukládají (snapshot), nepočítají za běhu.** Text instrukce je
   součást měřicí metodiky; CLDR se mění (CZ: „Czech Republic" →
   „Czechia"), upgrade knihovny nesmí potichu změnit prompty. Babel jen
   předvyplní, do DB jde, co je ve formuláři.
4. **Názvy anglicky** (šablony jsou anglické). Lokalizace názvů v DE UI
   mimo rozsah.
5. **Nové sloupce** `language_name`, `country_name` (`String(100) NULL`).
   ISO sloupce beze změny. `locale_name` zůstává, předvyplní se jako
   „Language (Country)".
6. **Placeholdery:** `{market_language}` → `language_name` (fallback
   `language`); `{market_country}` → `country_name` (fallback `country`,
   pak `locale_name`/`code` pro market bez země); **nové**
   `{market_language_code}`, `{market_country_code}`; `{market_code}`,
   `{market_locale_name}`, `{persona}` beze změny. Přepnutí významu je
   záměr — uložené šablony se přepnou samy. Riziko (šablona počítá
   s kódem) se ověří SQL na produkci **před merge** (ML-T6 krok 1).
7. **Nový default šablony:** „The {persona} asking this question is
   located in {market_country} and writing in {market_language}. Answer
   in {market_language}, using regional context and examples relevant
   there where applicable."
8. **Backfill v migraci přes Babel + výjimky:** CZ → „Czech Republic";
   `fr-FR` `locale_name` → „French (France)"; neparsovatelný kód → `NULL`
   + log. Import Babelu v migraci je v pořádku — jednorázový snímek.
9. **Předvyplnění přes HTMX** (`GET /markets/resolve?code=` → fragment,
   `hx-trigger="change"`). Bez JS doplní server při POST.
10. **Editace:** změna `code` přepočítá ISO kódy; názvy se nepřepisují,
    pokud je uživatel nevymazal (prázdné → doplní Babel).
11. **Historie se nepřepisuje** (NFR-6); starý text instrukce zůstává
    v `runs.request_payload`. Datum a čas přepnutí do CHANGELOG a deploy
    logu (hranice pro trendy).
12. **Verze MINOR** — migrace jen přidává (rollback i downgradem), nová
    závislost je v image, žádná nová povinná env proměnná.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Babel + resolver locale | ⏳ |
| T2 | Migrace: názvy jazyka/země na `markets` | ⏳ |
| T3 | `/markets`: kód jako jediný vstup, HTMX předvyplnění | ⏳ |
| T4 | Placeholdery v system instruction + `/settings` | ⏳ |
| T5 | Dokumentace + CHANGELOG | ⏳ |
| T6 | Nasazení v1.5.0 a ověření | ⏳ |

---

## T1 — Babel + resolver locale

**Target:** `requirements.txt`, `app/services/locale_names.py` (nový),
`tests/test_locale_names.py` (nový)

1. `babel==<aktuální>` (`pip index versions babel`), přestavět image,
   ověřit import v kontejneru.
2. `resolve_locale(code) -> ResolvedLocale` (`code`, `language`,
   `country | None`, `language_name`, `country_name | None`,
   `locale_name`) přes `Locale.parse(code, sep="-")` (přijmout i `_`),
   `get_language_name("en")`, `get_territory_name("en")`.
3. `InvalidLocaleCode(reason)` — `unknown` / `unsupported`. Žádné `t()`
   ve službě.
4. Testy: `cs-CZ`; normalizace `cs_cz`, `CS-cz`; `en` bez země; `xx-QQ`
   → unknown; `zh-Hant-TW`, `es-419` → unsupported; prázdný řetězec.
   Zapsat, co CLDR vrací pro CZ (změna při upgradu se ukáže v testu).

**Expected commit:** `feat(i18n): add CLDR-backed locale code resolver`

---

## T2 — Migrace: názvy jazyka/země na `markets`

**Target:** `alembic/versions/00NN_market_locale_names.py`,
`app/models/market.py`, fixtures v testech, které zakládají `Market`

1. `ADD COLUMN language_name`, `country_name` (`VARCHAR(100) NULL`).
2. Backfill podle design decision 8; docstring: proč, jen přidává,
   downgrade zahodí sloupce (oprava `fr-FR` se nevrací).
3. Model + docstring (ISO pro API, názvy pro prompt, snapshot).
4. Lokálně na kopii produkční DB: upgrade → downgrade -1 → upgrade;
   `select code, language, country, language_name, country_name,
   locale_name from markets`.

**Expected commit:** `feat(i18n): store English language/country names on markets`

---

## T3 — `/markets`: kód jako jediný vstup

**Target:** `app/routers/markets.py`, `app/templates/markets/form.html`,
`app/templates/markets/_resolved_fields.html` (nový), `app/templates/markets/list.html`,
`app/i18n/*.json`, `tests/test_markets.py`

1. Create/edit: `Form` jen `code` + volitelné `language_name`,
   `country_name`, `locale_name` (`description=` u všech). ISO kódy
   z `resolve_locale`, nikdy z formuláře. Chyby
   `errors.market_invalid_code` / `errors.market_unsupported_code`
   (DE/EN), inline 409 jako dnes. Pravidlo editace = design decision 10.
2. `GET /markets/resolve?code=` (editor/admin) → fragment, neplatný kód
   = fragment s hláškou a status 200. Docstring pro `/docs`.
3. Formulář: Code s `hx-get`/`hx-trigger="change"`; odvozené kódy
   read-only; názvy přes makra. Bez JS funguje.
4. List (tabulka + mobilní karty): Code · Language (název + kód) ·
   Country (název + kód) · Locale name · Prompts.
5. i18n; nepoužívané klíče mazat jen po grepu.
6. Testy: create jen s `code`; ručně zadaný název se nepřepíše;
   invalid/unsupported → 409; edit přepočítá ISO a zachová názvy;
   `/markets/resolve`; stávající test mazání.
7. Prohlížeč ~375 / ~768 px / desktop: duplicitní `cs-CZ`, `de-AT`,
   `xx-QQ`, `zh-Hant-TW`, editace. Testovací market smazat.

**Expected commit:** `feat(i18n): derive market language/country from the locale code`

---

## T4 — Placeholdery v system instruction + `/settings`

**Target:** `app/services/run_execution.py`, `app/routers/settings.py`,
`app/i18n/*.json`, `tests/test_run_execution.py`, testy validace šablony
(`grep -rn _validate_template tests`)

1. `_build_system_instruction` podle design decisions 6 a 7 (fallback
   bez země). Docstring.
2. `DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE`, `_DRY_RUN_VALUES` (názvy +
   `*_code`).
3. `settings.placeholders_hint`, `help.settings.body` (DE/EN) — seznam
   placeholderů s příkladem hodnoty.
4. Testy: `{market_language}` → „Czech"; `{market_country_code}` → „CZ";
   fallbacky; validace s novými placeholdery; FakeAdapter dostává
   `market_country == "CZ"`.
5. `findings.html` neupravovat — jen upozornit uživatele.

**Done when:** lokální run (Skoda Auto, prompt 54, gemini-3.1-flash-lite)
má v `request_payload ->> 'system_instruction'` „Czech Republic" a „Czech".

**Expected commit:** `feat(runs): pass language and country names to system instructions`

---

## T5 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/REQUIREMENTS.md` (pole marketu —
ukázat diff), `docs/ROADMAP.md` #22

1. `CHANGELOG.md` `[Unreleased]`:
   - Added: market se zakládá jen kódem locale (`cs-CZ`); jazyk, země
     a jejich názvy se doplní.
   - Changed: system instructions pojmenovávají jazyk a zemi slovy
     (`Czech`, `Czech Republic`) místo ISO kódů; nové placeholdery
     `{market_language_code}`, `{market_country_code}`; runy před tímto
     vydáním dostaly kódy, přesný text je uložený u každého runu.

**Expected commit:** `docs(docs): document market locale names`

---

## T6 — Nasazení v1.5.0 a ověření

**Target:** produkce; end-of-branch docs

1. **Před merge — uložené šablony na produkci:**
   ```sql
   select p.code, t.template
   from system_instruction_templates t join providers p on p.id = t.provider_id
   where t.template ~ '\{market_(language|country)\}';
   ```
   U každé posoudit větu s „Czech"/„Czech Republic" místo `cs`/`CZ`;
   kde počítá s kódem → po nasazení přepnout na `{market_*_code}`.
   Výsledek zapsat sem.
2. `select code, language, country, locale_name from markets;` — kódy
   pro kontrolu backfillu.
3. Merge PR, bump **v1.5.0**, přesun `[Unreleased]`, tag — uživatel.
4. Nasazení podle `docs/DEPLOYMENT.md` 1–5:
   - 1.3: migrace jen přidává → rollback zálohou i downgradem;
   - 3.3: **rebuild image nutný** (nová závislost `babel`);
   - **nasadit mimo okna scheduleru** (Knauf 10:00) — přepnutí instrukce
     má být čistá hranice mezi dvěma dny, ne uprostřed dávky.
5. Ověření (4.1): `/markets` (názvy, `fr-FR` → „French (France)");
   `/markets/new` s `de-AT` → předvyplnění (neukládat); `/settings`
   nápověda.
6. **Jeden testovací run** (Skoda Auto, prompt 54, gemini-3.1-flash-lite):
   ```sql
   select id, request_payload ->> 'system_instruction'
   from runs order by id desc limit 1;
   ```
   Čekané „…located in Czech Republic and writing in Czech. Answer in
   Czech…". U Anthropic/OpenAI/Grok runu zkontrolovat `user_location.country`
   = `CZ`.
7. Druhý den: runy Knaufu bez nových chyb, odpovědi dál v jazyce marketu.
8. Deploy log: **datum a čas přepnutí instrukce**.
9. End-of-branch docs: `## Status: ...` v obou `*_MARKET_LOCALE_NAMES.md`,
   řádek v `docs/00_INDEX.md`, `docs/ROADMAP.md` „Plán vydání" → vydání 3 ✅,
   #22 ✅.

**Expected commit:** `docs(docs): record market locale names deploy and close the branch`

---

## Co tohle vydání vědomě nedělá

- **Nemění ISO kódy ani to, co jde do API** (`user_location`).
- **Nepodporuje skripty/varianty locale** (design decision 1).
- **Nepočítá názvy za běhu** (3) a **nelokalizuje je v UI** (4).
- **Neverzuje system-instruction šablony** — historie v `request_payload`.
- **Nemaže `locale_name`.**
- **Neupravuje `/findings`.**
