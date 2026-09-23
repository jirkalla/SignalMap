# SignalMap — Claude Code Session Prompts: ChatGPT adapter + persona + price history

## Status: ✅ Done — PR #10, merged 2026-09-13, released in v1.0.0

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-chatgpt-persona-pricehistory (z aktuálního master)
## 2. Devět kódových promptů (CPH-1 až CPH-9), POŘADÍ VYNUCENÉ — viz docs/TASKS_CHATGPT_
##    PERSONA_PRICING.md "Task Index" pro odůvodnění (T1→T2 historie cen nezávislá jde první;
##    T3→T4→T5 tabulka→CRUD→zapojení do settings/runs; T6→T7 schema→adaptér; T8 testuje vše;
##    T9 přidán 2026-09-13 po dokončení T1-T8, nezávislý, jde poslední).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit provádíš ty,
##    ne agent — agent NIKDY nespouští git commit/push sám bez výslovného potvrzení, a to i
##    přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_CHATGPT_PERSONA_PRICING.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-11: docs/TASKS_CHATGPT_PERSONA_PRICING.md —
##    přečti si konkrétní task ID před psaním kódu, ideálně celý soubor před CPH-1.
## 9. CPH-6 a CPH-7 obsahují kroky "ověř proti aktuální dokumentaci/skutečnosti" (OpenAI model
##    names/pricing, web_search/user_location API shape, openai SDK verze) — NEPŘEBÍREJ
##    čísla/stringy/předpoklady z paměti/tréninkových dat, jsou to fakta, která se v čase mění.
## 10. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na tuhle větev (viz
##     Completion Checklist v TASKS_CHATGPT_PERSONA_PRICING.md).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch
feature/signalmap-chatgpt-persona-pricehistory.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_CHATGPT_PERSONA_PRICING.md — CELÉ, hlavně design decisions 1-11

KONTEXT: Fáze 1-6 a všechny doprovodné branch (hardening, export, search
queries) jsou hotové a smergnuté do master. Tahle větev bundluje tři
nezávislé kusy práce do jedné branch (odsouhlaseno s uživatelem
2026-09-13, stejné zdůvodnění jako docs/TASKS_PHASE5.md):
1) třetí AI provider (OpenAI/ChatGPT), 2) perzona jako datově řízený
placeholder v system-instruction šabloně s per-run override, 3) historie
cen AI modelu (SCD-2-style tabulka místo přepisu na místě).

KRITICKÉ:
- OpenAI model names, aktuální ceník, web_search/user_location API shape
  a openai SDK verze se MUSÍ ověřit proti aktuální OpenAI dokumentaci
  v době psaní kódu (CPH-6, CPH-7) — neuhaduj/nepřebírej z paměti.
- Nový provider = nová adapter třída (app/adapters/<code>.py) + nový
  ADAPTERS registry entry + nové providers/ai_models řádky — NIKDY
  natvrdo zadaný seznam providerů v routeru.
- Persona.label je volný text (jako Market.label) — NEPROCHÁZÍ přes t()/
  i18n JSON, jde přímo do anglické system_instruction šablony.
- Právě jedna Persona smí mít is_default=True (partial unique index) —
  přepnutí defaultu vždy nejdřív vynuluje starý default řádek, pak
  nastaví nový, v jedné transakci.
- ai_model_price_history dostává nový řádek JEN když se cena skutečně
  změní (porovnání staré vs. nové hodnoty před commitem) — ne při každé
  editaci modelu.
- Evidence řádky (Run, RawResponse, Citation) — NIKDY delete endpoint.
  Persona smazatelná jen když není is_default a nemá žádný Run.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný
JavaScript framework), Alembic migrace, Docker Compose. Backend kód
anglicky vč. komentářů/error_code, UI texty vždy přes t() mechanismus
v app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom
commitu (výjimka: Persona.label, viz výše). Formuláře přes existující
makra v app/templates/partials/macros.html (text_field, select_field,
checkbox_field, delete_button, pill_badge, cost_badge, ...) — rozšiř je,
než píšeš nový markup od nuly.

KRITICKÁ PRAVIDLA:
- Nová migrace pro každou schema změnu, navazující revision ID
  (poslední je 0019 — nová je 0020, pak 0021, 0022, 0023).
- Sloupec s NOT NULL na existující tabulce (runs.persona_id): stejná
  technika jako alembic/versions/0002_run_market_override.py — přidat
  nullable, backfillnout, pak NOT NULL + FK + index.
- Každá route funkce dostane docstring; každé netriviální Form/Field
  pole description=....
- Nikdy git commit ani git push bez tvého výslovného potvrzení — i po
  tom, co agent sám navrhne commit message, čeká na "ano, commitni" než
  cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message.
Nikdy nespouštěj git add/commit/push sám bez výslovného pokynu — a to
i tehdy, když jsi zprávu sám navrhl v předchozí větě.
```

---
---

## PROMPT CPH-1 — Schema + zápis: ai_model_price_history

```
Task: Prompt CPH-1 — price history schema + capture-on-change

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T1 CELÝ, hlavně
design decision 1 (SCD-2-style, zápis jen při skutečné změně ceny).

1. Nová migrace 0020_ai_model_price_history.py:
   - CREATE TABLE ai_model_price_history — id PK, ai_model_id
     (FK ai_models.id, ON DELETE CASCADE), cost_per_1k_input_usd
     NUMERIC(10,5), cost_per_1k_output_usd NUMERIC(10,5),
     effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
     changed_by_user_id (FK users.id, nullable).
   - Index na (ai_model_id, effective_from).
   - Backfill: pro každý existující ai_models řádek vlož jeden počáteční
     historický řádek s jeho aktuální cenou a effective_from =
     ai_models.created_at (NE now()).
2. app/models/provider.py — nový AIModelPriceHistory model; AIModel
   dostává price_history relationship (order_by effective_from.desc()).
3. app/models/__init__.py — export AIModelPriceHistory.
4. app/routers/ai_models.py:
   - create_ai_model — po vytvoření modelu zapiš první
     AIModelPriceHistory řádek se stejnou cenou.
   - update_ai_model — porovnej starou vs. novou cenu PŘED přepsáním
     model.cost_per_1k_*_usd; pokud se aspoň jedna liší, přidej nový
     AIModelPriceHistory řádek s NOVOU cenou, effective_from=now(),
     changed_by_user_id = aktuální uživatel (přidej
     user: User = Depends(current_active_user) parametr, stejný vzor
     jako app/routers/runs.py trigger_run).
   Aktualizuj docstringy — vysvětli, kdy se historie zapisuje.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověř: každý existující model má právě jeden historický řádek
   s effective_from = jeho created_at.
3. V prohlížeči uprav cenu existujícího modelu → nový historický řádek.
   Ulož model znovu se stejnou cenou (jen změň notes) → žádný nový
   řádek.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ai-models): record price history on every actual price change
```

---
---

## PROMPT CPH-2 — UI: historie cen na /ai-models/{id}/edit

```
Task: Prompt CPH-2 — price history view

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T2 CELÝ.
Prerekvizita: CPH-1 hotový.

1. edit_ai_model_form (app/routers/ai_models.py) — načti
   model.price_history (seřazené effective_from DESC), dopočítej pro
   každý řádek platnost "do" (effective_from následujícího/staršího
   řádku v pořadí; u nejnovějšího řádku "do" = dodnes) — žádný nový
   sloupec, čistě výpočet nad načteným seznamem. Předej do šablony jako
   price_history.
2. ai_models/form.html — pod cenovými poli, jen v edit módu, read-only
   tabulka: input/output cena přepočtená na $/1M (stejný přepočet jako
   cost_badge makro), platnost "od effective_from do valid_until"
   (nejnovější řádek: "do" = aktuální/dodnes), seřazeno od nejnovější.
3. i18n (EN+DE, jeden commit) — ai_model.price_history_title,
   ai_model.price_history_empty.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči /ai-models/{id}/edit u modelu s aspoň dvěma cenovými
   změnami: historie vidět, seřazená od nejnovější, správné datumy
   a částky.
3. Ověř na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ai-models): show price history on the model edit screen
```

---
---

## PROMPT CPH-3 — Schema: personas tabulka + seed defaultní perzony

```
Task: Prompt CPH-3 — personas schema + seed

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T3 CELÝ, design
decisions 3 a 4 (volný text, partial unique index na is_default).

1. Nová migrace 0021_personas.py:
   - CREATE TABLE personas — id PK, label VARCHAR(100) NOT NULL UNIQUE,
     is_default BOOLEAN NOT NULL DEFAULT FALSE, created_at/updated_at
     TIMESTAMPTZ NOT NULL DEFAULT now().
   - CREATE UNIQUE INDEX idx_personas_one_default ON personas
     (is_default) WHERE is_default.
   - INSERT INTO personas (label, is_default) VALUES ('person', TRUE).
2. app/models/persona.py — nový Persona model (stejný vzor jako
   app/models/market.py).
3. app/models/__init__.py — export Persona.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověř: personas má právě jeden řádek, label='person',
   is_default=true.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add personas table with seeded default "person" row
```

---
---

## PROMPT CPH-4 — Personas admin UI (CRUD)

```
Task: Prompt CPH-4 — personas CRUD screen

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T4 CELÝ, design
decisions 4 a 6 (default-swap technika, delete-block pravidla).
Prerekvizita: CPH-3 hotový. Referenční vzor: app/routers/markets.py
(stejná CRUD kostra).

1. Nový app/routers/personas.py:
   - GET /personas — list: label, "Default" badge (pill_badge makro),
     počet běhů, co tuhle perzonu použily, Edit/Delete tlačítka.
   - GET/POST /personas/new — label (required, unikátní), is_default
     checkbox.
   - GET/POST /personas/{id}/edit — stejný formulář. Pokud submit
     zaškrtne is_default a řádek jím ještě není: v JEDNÉ transakci
     nejdřív UPDATE personas SET is_default = FALSE WHERE is_default
     (na aktuálním default řádku), pak nastav is_default = TRUE na
     editovaný řádek.
   - POST /personas/{id}/delete — blokovaný (strukturovaná inline
     chyba), pokud (a) řádek je is_default, nebo (b) existuje Run
     s tímhle persona_id (stejný vzor jako errors.market_in_use,
     s počtem běhů).
   Docstring na každé route funkci; description= na Form polích.
2. Nové app/templates/personas/list.html, app/templates/personas/
   form.html — reuse text_field/checkbox_field/delete_button/
   pill_badge makra, žádná nová markup od nuly.
3. app/main.py — zaregistruj router (editor+admin gate, stejně jako
   markets.router).
4. app/templates/base.html — nav odkaz na /personas.
5. i18n (EN+DE, jeden commit) — persona.* klíče (list_title,
   create_title, edit_title, label_field, is_default_field,
   is_default_badge, delete_button/confirm), errors.persona_not_found,
   errors.persona_label_conflict, errors.persona_is_default,
   errors.persona_in_use (stejný {count} formát jako
   errors.market_in_use).

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči /personas: vidět seedovanou "person" perzonu s
   "Default" badge. Vytvoř "manager"/"politician"/"child" — uloží se.
   Nastav "manager" jako default → "person" ztrácí badge, "manager"
   ho získá. Zkus smazat aktuální default → zablokované se
   srozumitelnou chybou. Zkus smazat nepoužitou, ne-default perzonu →
   funguje.
3. Ověř na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(personas): add full CRUD admin screen with single-default enforcement
```

---
---

## PROMPT CPH-5 — Perzona v system-instruction šabloně + per-run override

```
Task: Prompt CPH-5 — persona wired into system_instruction + Run

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T5 CELÝ, design
decisions 5 a 7 (per-run override, {persona} placeholder je opt-in).
Prerekvizita: CPH-4 hotový. Referenční technika pro migraci: alembic/
versions/0002_run_market_override.py (přesně stejný backfill postup).

1. Nová migrace 0022_run_persona_override.py:
   - ADD COLUMN persona_id INTEGER (nullable).
   - UPDATE runs SET persona_id = (SELECT id FROM personas
     WHERE is_default).
   - ALTER COLUMN persona_id SET NOT NULL.
   - ADD FOREIGN KEY + index (stejný vzor jako market_id ve 0002).
2. app/models/run.py — Run.persona_id (FK personas.id, NOT NULL),
   persona relationship. Aktualizuj docstring (mirror market_id's
   odstavce, uprav pro perzonu).
3. app/routers/settings.py:
   - DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE — "The person asking..." →
     "The {persona} asking...".
   - _DRY_RUN_VALUES — přidej "persona": "person".
4. app/routers/runs.py:
   - _market_system_instruction → přejmenuj na
     _build_system_instruction, nový parametr persona: Persona,
     .format(..., persona=persona.label).
   - trigger_run — nový persona_id: int = Form(..., description=...).
     Lookup + 404 stejně jako market_id. request_payload dostává
     "persona": persona.label. Run(...) konstruktor dostává
     persona_id=persona.id.
5. Run-trigger formulář (prompts/detail.html) — nový <select> pro
   perzonu vedle marketu (select_field makro), přednastavený na
   defaultní perzonu.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V prohlížeči /settings — výchozí šablona teď obsahuje {persona},
   uloží se beze změny validace.
3. Spusť běh na existujícím promptu s přepnutou perzonou na "manager"
   → request_payload ukazuje "persona": "manager". Spusť druhý běh
   se stejným promptem, jinou perzonou ("politician") →
   request_payload odlišný, oba běhy zůstávají v historii nezávisle.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): make system-instruction persona a per-run, admin-managed override
```

---
---

## PROMPT CPH-6 — Schema: OpenAI provider + seed modelů

```
Task: Prompt CPH-6 — OpenAI provider/model seed

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T6 CELÝ.

Než napíšeš INSERT: ověř proti aktuální OpenAI dokumentaci
(platform.openai.com/docs/pricing nebo web search) přesné model_name
stringy pro Responses API, context_window_tokens, max_output_tokens
a aktuální ceny. NEPŘEBÍREJ z paměti/tréninkových dat — stejné pravidlo
jako u Anthropic seedu ve fázi 2 (P2-T1).

1. Nová migrace 0023_openai_provider_and_models.py:
   - INSERT INTO providers (code, name) VALUES ('openai',
     'OpenAI ChatGPT').
   - INSERT 2-3 OpenAI model řádky (ověřené názvy/parametry/ceny),
     capability_tier podle relativní pozice v lineupu,
     supports_web_search = TRUE, is_free = FALSE, notes s odkazem na
     zdroj + datum ověření.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověř: providers má openai řádek; ai_models má nové OpenAI
   řádky s reálnou (ne vymyšlenou) cenou.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add OpenAI provider + model rows
```

---
---

## PROMPT CPH-7 — OpenAI adaptér

```
Task: Prompt CPH-7 — OpenAI ChatGPT adapter

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T7 CELÝ a
app/adapters/anthropic.py + app/adapters/base.py jako referenční vzor
(stejná disciplína u has_citations, raw_payload beze změny).
Prerekvizita: CPH-6 hotový (OpenAI modely existují a mají ověřenou
cenu).

Než píšeš kód: ověř proti aktuální OpenAI API dokumentaci (web search)
přesný tvar web_search toolu Responses API a jeho user_location
parametru (country/city/region) — API se mezi verzemi mění,
NEPŘEBÍREJ z paměti. Ověř i aktuální stabilní verzi openai Python SDK.

1. requirements.txt — přidej pinnutou aktuální stabilní verzi openai
   SDK.
2. app/config.py — Settings dostává openai_api_key: str = "".
3. .env.example — přidej OPENAI_API_KEY= s komentářem (odkaz na
   platform.openai.com/api-keys), stejný formát jako
   ANTHROPIC_API_KEY.
4. Nový app/adapters/openai.py — OpenAIAdapter implementující
   ProviderAdapter.run() (app/adapters/base.py):
   - Responses API (client.responses.create) s web_search toolem
     zapnutým.
   - system_instruction → instructions top-level pole.
   - user_location ve web_search tool configu, odvozený z
     market_country (ověřený shape).
   - Mapování url_citation anotací na AdapterCitation — has_citations
     explicitně False (ne jen prázdný seznam), stejná disciplína jako
     Gemini/Anthropic adaptér.
   - raw_payload = kompletní, netransformovaná SDK odpověď (FR-10).
   - Nech SDK vyhazovat na transport/API chyby — caller
     (app/routers/runs.py) to zachytává už dnes.
5. app/adapters/__init__.py — zaregistruj "openai": OpenAIAdapter
   v ADAPTERS.

Po dokončení:
1. Reálný .env s OPENAI_API_KEY (uživatel má klíč připravený)
2. docker compose up -d --build
3. V prohlížeči: spusť běh proti OpenAI modelu na existujícím
   promptu → skutečný běh. Ověř run detail: rendered text, raw JSON
   obsahuje OpenAI response shape, citace pokud nějaké přišly.
4. Vyvolej chybu (dočasně špatný OPENAI_API_KEY) → Run.status ==
   'error' se smysluplnou hláškou, ne surová 500.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(adapters): add OpenAI ChatGPT adapter with web_search geo-targeting
```

---
---

## PROMPT CPH-8 — Testy: rozšíření pytest sady

```
Task: Prompt CPH-8 — extend test suite for all three features

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T8 CELÝ.
Prerekvizita: CPH-1 až CPH-7 hotové.

1. tests/conftest.py:
   - seed fixture — přidej openai provider + jeden testovací model;
     FakeAdapter zaregistrovaný i pro "openai" (stejný vzor jako
     google_gemini/anthropic).
   - seed fixture — přidej seedovanou defaultní Persona
     (label="person", is_default=True) — bez ní by sample_prompt/
     trigger_run testy selhaly na chybějící persona_id FK.
2. Nový tests/test_personas.py — create → list → edit → set-default
   (přepnutí is_default mezi dvěma perzonami) → delete zablokovaný na
   aktuálním default → delete zablokovaný na perzoně s existujícím
   Run → delete projde na nepoužité, ne-default perzoně.
3. Nový tests/test_ai_model_price_history.py — vytvoření modelu zapíše
   první historický řádek; editace se změnou ceny přidá řádek; editace
   beze změny ceny žádný nový řádek nepřidá.
4. tests/test_ai_models.py — rozšiř o assert, že price_history je
   vidět na edit formuláři.
5. tests/test_runs.py — rozšiř o běh proti OpenAI modelu přes
   FakeAdapter (úspěch i chybová cesta); běh s explicitně přepnutou
   perzonou → request_payload["persona"] odpovídá zvolené perzoně.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové)
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover OpenAI provider path, persona CRUD/override, and price history
```

---
---

## PROMPT CPH-9 — Ochrana proti vícenásobnému spuštění runu (varianta A)

```
Task: Prompt CPH-9 — prevent duplicate run submission

Přečti docs/TASKS_CHATGPT_PERSONA_PRICING.md úkol CPH-T9 CELÝ, design
decisions 13-14 (varianta A vs. B, kontrola per prompt_id).
Nezávislé na CPH-1 až CPH-8 — jen dotýká se stejného trigger_run, co
CPH-5 už upravoval.

trigger_run už dnes ukládá Run(status='pending') v samostatném commitu
před voláním adaptéru — žádná schema změna potřeba, jen dotaz nad
existujícím stavem.

1. app/routers/runs.py, trigger_run — před vytvořením nového Run
   zkontroluj, jestli pro prompt_id už neexistuje Run se
   status='pending'. Pokud ano → AppError("run_already_pending",
   t("errors.run_already_pending"), status_code=409) — žádný nový Run
   řádek, žádné volání adaptéru.
2. app/templates/prompts/detail.html — malý vanilla-JS blok (stejné
   umístění/vzor jako confirm() delegated listener v base.html):
   submit listener na run-trigger formuláři, okamžitě disabled=true na
   submit tlačítku a přepnutí textu na t('prompt.trigger_run_pending')
   ("Spouštím…"), ať k tomu dojde ještě před odpovědí serveru. Musí
   fungovat i při validation-error re-renderu (formulář se znovu
   vykreslí s tlačítkem zpátky aktivním).
3. i18n (EN+DE, jeden commit) — errors.run_already_pending,
   prompt.trigger_run_pending.
4. Nový test v tests/test_runs.py — vytvoř Run(status='pending') přímo
   přes ORM pro sample_prompt, pak zavolej trigger_run (POST) na
   stejný prompt → očekávej 409, žádný druhý Run řádek nevznikl. Druhý
   test: po dokončení běhu (status success/error) jde spustit další
   běh normálně.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči: klikni "Run prompt" → tlačítko se okamžitě zablokuje
   a změní text, než dorazí odpověď. Zkus (např. přes druhou kartu)
   spustit druhý běh na stejném promptu, zatímco první ještě běží →
   zablokované s chybou, ne druhý Run řádek.
3. Po dokončení prvního běhu spusť druhý normálně → projde bez
   problémů.
4. pytest — všechny testy zelené (staré i nové)
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): block triggering a second run while one is already pending
```
