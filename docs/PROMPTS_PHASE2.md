# SignalMap — Claude Code Session Prompts: Phase 2 (Anthropic + Admin UI)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-phase2-anthropic-admin (z aktuálního master)
## 2. Šest kódových promptů (P2-1 až P2-6), POŘADÍ VYNUCENÉ — viz docs/TASKS_PHASE2.md
##    "Task Index" pro odůvodnění (P2-2/P2-3 potřebují sloupce z P2-1; P2-4 dává smysl až po
##    P2-3, protože cenu Anthropic modelů je potřeba ověřit/doplnit v admin UI dřív, než přes
##    ně poběží první placený běh; P2-5 staví na P2-3; P2-6 testuje všechno).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit provádíš ty,
##    ne agent — agent NIKDY nespouští git commit/push sám bez výslovného potvrzení, a to i
##    přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_PHASE2.md přepni řádek daného task ID v tabulce "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-11: docs/TASKS_PHASE2.md — přečti si
##    konkrétní task ID před psaním kódu, ideálně celý soubor před P2-1.
## 9. P2-1 a P2-4 obsahují kroky "ověř proti aktuální dokumentaci/skutečnosti" (Anthropic model
##    names/pricing, web_search/user_location API shape, a v P2-1 navíc billing status Google
##    Cloud projektu za stávajícím GOOGLE_API_KEY, který rozhoduje o Gemini is_free hodnotě) —
##    NEPŘEBÍREJ čísla/stringy/předpoklady z paměti/tréninkových dat, jsou to fakta, která se
##    v čase mění a musí sedět k reálnému účtu/API.
## 10. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na tuhle větev (viz
##     Completion Checklist v TASKS_PHASE2.md), a odstranit "Any provider other than Google
##     Gemini" z docs/REQUIREMENTS.md §4.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-phase2-anthropic-admin.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_PHASE2.md — CELÉ, hlavně design decisions 1-11

KONTEXT: Fáze 1 (docs/TASKS.md) a production-readiness hardening
(docs/TASKS_HARDENING.md) jsou hotové a smergnuté do master. Tahle větev
přidává druhého AI providera (Anthropic Claude) a admin UI pro správu
providerů/modelů (aktivace, cena, parametry) — obojí bylo ve fázi 1
vědomě odložené (docs/TASKS.md "After phase 1", HD-T4 design decision 6).

KRITICKÉ:
- Anthropic model names, aktuální ceník a web_search/user_location API
  shape se MUSÍ ověřit proti aktuální Anthropic dokumentaci v době psaní
  kódu (P2-1, P2-4) — neuhaduj/nepřebírej z paměti, jsou to fakta, která
  se mezi verzemi API mění.
- Nový provider = nová adapter třída (app/adapters/<code>.py) + nový
  ADAPTERS registry entry + nové providers/ai_models řádky — NIKDY
  natvrdo zadaný seznam providerů v routeru.
- AIModel delete: blokovaný, pokud na něm existuje jakýkoliv Run (stejná
  politika jako HD-T4 u Client/Prompt) — jinak jen deaktivace
  (is_active = false), nikdy smazání evidence.
- Provider admin UI: JEN list + edit jména. Žádné create/delete z UI —
  nový provider bez adaptéru by byl nefunkční mrtvý řádek.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný
JavaScript framework), Alembic migrace, Docker Compose. Backend kód
anglicky vč. komentářů/error_code, UI texty vždy přes t() mechanismus
v app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom
commitu. Formuláře přes existující makra v
app/templates/partials/macros.html — rozšiř je, než píšeš nový markup
od nuly.

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation) — NIKDY delete endpoint.
- Nová migrace pro každou schema změnu, navazující revision ID
  (poslední je 0008 — nová je 0009).
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

## PROMPT P2-1 — Schema: rozšíření providers/ai_models + seed Anthropic

```
Task: Prompt P2-1 — schema extension + Anthropic seed

Přečti docs/TASKS_PHASE2.md úkol P2-T1 CELÝ, hlavně design decision 5
a jeho revizi (2026-09-09) — proč Gemini seed modely NEDOSTÁVAJÍ
automaticky is_free=TRUE.

Než napíšeš INSERT pro Anthropic modely: ověř proti aktuální Anthropic
dokumentaci (docs.anthropic.com nebo web search) přesné model_name
stringy pro Messages API (obvykle datované snapshoty), jejich
context_window_tokens a max_output_tokens. Cenu NEZJIŠŤUJ ani
nevymýšlej — cost_per_1k_input_usd/cost_per_1k_output_usd zůstávají
NULL, is_free = FALSE, notes = 'Pricing not yet verified — set via
/ai-models'.

Než napíšeš UPDATE pro Gemini is_free: ověř billing status Google Cloud
projektu za stávajícím GOOGLE_API_KEY (ten, co dnes prokazatelně
provozuje fázi 1 s groundingem). FR-8 dělá grounding povinným pro
každý reálný běh, a grounding pravděpodobně buď vyžaduje billing-enabled
projekt, nebo má jen omezenou free kvótu — v obou případech pravděpodobně
NENÍ žádný Gemini běh spolehlivě "zdarma". NEPŘEDPOKLÁDEJ is_free=TRUE
jen proto, že model je na "free tier" podle jména/kategorie.

1. Nová migrace 0009:
   - providers — ADD COLUMN created_at, updated_at (TIMESTAMPTZ NOT
     NULL DEFAULT now()).
   - ai_models — ADD COLUMN is_free (BOOLEAN NOT NULL DEFAULT FALSE),
     context_window_tokens (INTEGER), max_output_tokens (INTEGER),
     created_at, updated_at (TIMESTAMPTZ NOT NULL DEFAULT now()).
   - Gemini is_free: pokud billing na projektu NENÍ zapnutý a grounding
     přesto funguje v rámci free kvóty → UPDATE ai_models SET
     is_free = TRUE WHERE provider je google_gemini. Pokud billing JE
     zapnutý → žádný UPDATE, zůstává default FALSE.
   - INSERT provider 'anthropic' / 'Anthropic Claude'.
   - INSERT 2-3 Claude model řádky (ověřené názvy/parametry, viz výše),
     capability_tier podle relativní pozice v lineupu,
     supports_web_search = TRUE.
2. app/models/provider.py — Provider dostává created_at/updated_at;
   AIModel dostává is_free, context_window_tokens, max_output_tokens,
   created_at, updated_at (stejný vzor jako app/models/client.py).
3. .env.example — přidej ANTHROPIC_API_KEY= s komentářem (odkaz na
   console.anthropic.com/settings/keys), stejný formát jako
   GOOGLE_API_KEY.
4. app/config.py — Settings dostává anthropic_api_key: str = "".

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověř: providers má anthropic řádek; ai_models má nové Claude
   řádky s is_free=false, cenou NULL; Gemini řádky mají is_free podle
   skutečně ověřeného billing statusu (pravděpodobně false).
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add anthropic provider + model rows, pricing/context columns on ai_models
```

---
---

## PROMPT P2-2 — Provider admin UI (list + edit)

```
Task: Prompt P2-2 — provider admin screen

Přečti docs/TASKS_PHASE2.md úkol P2-T2 CELÝ, hlavně design decision 2
(proč jen list+edit, žádné create/delete).
Prerekvizita: P2-1 hotový (created_at/updated_at sloupce existují).

1. Nový app/routers/providers.py — GET /providers (list s inline edit
   formulářem na name, podobný vzor jako app/routers/settings.py),
   POST /providers/{id} (uloží name). Žádné create/delete endpointy.
   Docstring na obou route funkcích.
2. Nový app/templates/providers/list.html — tabulka: code (needitovatelný),
   name (edit input), počet aktivních modelů, updated_at. Reuse
   text_field/button makra z partials/macros.html.
3. app/main.py — zaregistruj router.
4. i18n (EN+DE, jeden commit) — provider.list_title, provider.name_label,
   provider.model_count.
5. Nav odkaz v base.html na /providers vedle stávajících.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči /providers na ~640px/~1024px/desktop šířce: oba
   providery vidět, editace jména Anthropic řádku se uloží a zobrazí
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(providers): add read/edit admin screen for AI providers
```

---
---

## PROMPT P2-3 — AIModel admin UI (plné CRUD, cena/parametry, cost badge)

```
Task: Prompt P2-3 — ai_models admin screen

Přečti docs/TASKS_PHASE2.md úkol P2-T3 CELÝ, hlavně design decisions
3, 4, 5 (delete policy, proč žádný nový cost_tier sloupec, is_free
explicitní).
Prerekvizita: P2-1 a P2-2 hotové.

1. Nový app/routers/ai_models.py:
   - GET /ai-models — list napříč providery (seskupené jako
     _runnable_model_groups v app/routers/prompts.py), s cenou,
     is_free/capability_tier badge, is_active stavem, tlačítky
     Edit/Deactivate-Activate/Delete.
   - GET/POST /ai-models/new — create formulář (provider select,
     model_name, display_name, capability_tier select, cena,
     context_window_tokens, max_output_tokens, is_free checkbox,
     supports_web_search checkbox, notes).
   - GET/POST /ai-models/{id}/edit — stejný formulář, předvyplněný.
   - POST /ai-models/{id}/delete — helper "má tenhle model běh?"
     (stejný vzor jako HD-T4 in-use kontrola), blokovat strukturovanou
     chybou s počtem běhů; jinak smazat.
   - POST /ai-models/{id}/toggle-active — přepne is_active.
   Docstring na každé route funkci; description= na netriviálních
   Form polích.
2. app/templates/partials/macros.html — nové makro cost_badge(model):
   is_free → zelený "Free" badge (stejná paleta jako status_badge's
   success); jinak capability_tier badge (flagship=červená,
   standard=amber, economy=stone) PLUS skutečná cena vedle
   (cost_per_1k_input_usd/output_usd přepočtené na $/1M, "—" když
   NULL).
3. Nový app/templates/ai_models/list.html — tabulka: provider,
   model_name, display_name, cost_badge, context/max tokens,
   supports_web_search, is_active toggle, "Last updated" sloupec
   (updated_at — stejná informace jako u /providers z P2-2), Edit,
   delete_button makro (disabled/tooltip pokud má model běhy —
   spočítej run count per model v routeru). created_at jen v edit
   formuláři jako read-only řádek, ne v listu.
4. Nový app/templates/ai_models/form.html — reuse text_field/
   select_field/checkbox_field/textarea_field makra.
5. app/main.py — zaregistruj router.
6. i18n (EN+DE, jeden commit) — ai_model.* klíče (list_title,
   create_title, edit_title, cost_free, cost_per_million,
   delete_button/confirm, deactivate_button/confirm),
   errors.ai_model_in_use se stejným {count} placeholder formátem
   jako errors.market_in_use.
7. Nav odkaz v base.html na /ai-models.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči /ai-models: doplň reálnou cenu Anthropic modelů ze
   seedu P2-1 (z aktuálního Anthropic ceníku) — uloží se, badge se
   přepočítá
3. Založ testovací model bez běhů → smaž → funguje. Zkus smazat Gemini
   model s existujícími běhy z fáze 1 → zablokované se správným počtem
4. Deaktivuj/aktivuj model → zmizí/objeví se v run-trigger dropdownu
   na /prompts/{id}
5. Ověř na ~640px/~1024px/desktop šířce
6. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ai-models): add full CRUD admin screen with pricing, context params, cost badge
```

---
---

## PROMPT P2-4 — Anthropic adaptér

```
Task: Prompt P2-4 — Anthropic Claude adapter

Přečti docs/TASKS_PHASE2.md úkol P2-T4 CELÝ, hlavně design decision 10
(user_location geo-targeting — hlavní důvod tohoto providera) a
app/adapters/google.py + app/adapters/base.py jako referenční vzor
(stejná disciplína u has_citations, raw_payload beze změny).
Prerekvizita: P2-1 a P2-3 hotové (Anthropic modely existují a mají
ověřenou cenu).

Než píšeš kód: ověř proti aktuální Anthropic API dokumentaci (web
search) přesný tvar web_search toolu a jeho user_location parametru
(country/region/city/timezone) — API se mezi verzemi mění,
NEPŘEBÍREJ z paměti.

1. requirements.txt — přidej pinnutou aktuální stabilní verzi
   anthropic SDK.
2. Nový app/adapters/anthropic.py — AnthropicAdapter implementující
   ProviderAdapter.run() (app/adapters/base.py):
   - Anthropic Messages API s web_search toolem zapnutým.
   - system_instruction parametr → Anthropic system pole.
   - user_location ve web_search tool configu, odvozený z marketu
     runu (ověřený shape z dokumentace).
   - Mapování Anthropic citation bloků na AdapterCitation — has_citations
     explicitně False (ne jen prázdný seznam), když provider nevrátí
     citace, stejně jako u Gemini.
   - raw_payload = kompletní, netransformovaná SDK odpověď (FR-10).
   - Nech SDK vyhazovat na transport/API chyby — caller
     (app/routers/runs.py) to zachytává už dnes.
3. app/adapters/__init__.py — zaregistruj "anthropic": AnthropicAdapter
   v ADAPTERS.

Po dokončení:
1. Reálný .env s ANTHROPIC_API_KEY (uživatel má klíč připravený)
2. docker compose up -d --build
3. V prohlížeči: spusť běh proti Claude modelu na existujícím promptu
   → skutečný běh. Ověř run detail: rendered text, raw JSON obsahuje
   Anthropic response shape, citace pokud nějaké přišly
4. Vyvolej chybu (dočasně špatný ANTHROPIC_API_KEY) → Run.status ==
   'error' se smysluplnou hláškou, ne surová 500
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(adapters): add Anthropic Claude adapter with web_search geo-targeting
```

---
---

## PROMPT P2-5 — Run-trigger dropdown: cena/free badge

```
Task: Prompt P2-5 — price/free indicator in model dropdown

Přečti docs/TASKS_PHASE2.md úkol P2-T5 CELÝ, design decision 11
(revidováno 2026-09-09: nativní <select> beze změny + živý badge
panel pod ním, reuse cost_badge makra z P2-3 — ne prostý text v
<option>, <option> HTML stejně nepodporuje).
Prerekvizita: P2-3 hotový.

1. app/routers/prompts.py (_runnable_model_groups) — vrať rovnou
   AIModel objekty (ne jen (id, text) tuple), ať šablona má přístup
   ke všem polím pro cost_badge.
2. app/templates/prompts/detail.html — pro každý model v
   model_groups předrenderuj skrytý <div data-model-badge="{{ model.id
   }}" hidden> s cost_badge(model, t('ai_model.cost_free')) uvnitř.
   Pod <select id="model_id"> viditelný kontejner
   <div id="model-badge-display">.
3. Malý vanilla-JS blok (stejné umístění/vzor jako confirm()
   listener v base.html) — change listener na #model_id: schová
   všechny [data-model-badge], ukáže odpovídající, přesune jeho HTML
   do #model-badge-display. Spusť i jednou při načtení stránky (ať
   se badge zobrazí i u výchozí předvyplněné volby).

Po dokončení:
1. V prohlížeči /prompts/{id}: pod dropdownem je vidět barevný
   cost_badge (stejný vizuál jako /ai-models) pro aktuálně vybraný
   model, mění se okamžitě při přepnutí, funguje i pro výchozí volbu
   bez nutnosti dropdown nejdřív změnit
2. Klávesnice (šipky/Tab) přes <select> pořád funguje standardně
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): show price/free indicator on model choice in run-trigger dropdown
```

---
---

## PROMPT P2-6 — Testy: rozšíření pytest sady

```
Task: Prompt P2-6 — extend test suite for Anthropic + admin CRUD

Přečti docs/TASKS_PHASE2.md úkol P2-T6 CELÝ.
Prerekvizita: P2-1 až P2-5 hotové.

1. tests/conftest.py — seed fixture rozšiř o anthropic provider +
   jeden testovací Claude model; FakeAdapter zaregistrovaný i pro
   "anthropic" v ADAPTERS (stejný vzor jako google_gemini z HD-T5),
   nikdy nevolá reálné Anthropic API.
2. Nový tests/test_providers.py — /providers list; edit jména se
   uloží.
3. Nový tests/test_ai_models.py — create → list → edit →
   deactivate/activate; delete zablokovaný, když model má existující
   běh (přes FakeAdapter run fixture); delete projde na modelu bez
   běhu.
4. tests/test_runs.py — rozšiř o běh proti Anthropic modelu přes
   FakeAdapter (úspěch i chybová cesta).

Po dokončení:
1. pytest — všechny testy zelené (staré i nové)
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover Anthropic provider path and providers/ai-models admin CRUD
```
