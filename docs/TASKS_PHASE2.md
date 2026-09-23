# SignalMap — Tasks: Phase 2 (Anthropic Adapter + Provider/Model Admin)

## Status: ✅ Done — PR #2, merged 2026-09-09, released in v1.0.0

## v1.0 | Září 2026
## Branch: feature/signalmap-phase2-anthropic-admin
## Task ID prefix: P2

> Fáze 1 (`docs/TASKS.md`) je hotová a ověřená end-to-end proti reálnému Gemini API, včetně
> production-readiness hardeningu (`docs/TASKS_HARDENING.md`). Tenhle dokument pokrývá první dva
> body z `docs/TASKS.md` sekce "After phase 1": druhého providera (Anthropic/Claude) a admin UI
> pro providery/modely — HD-T4's design decision 6 je obě výslovně odložila sem jako jeden celek.
>
> Uživatel má k dispozici Anthropic API klíč. Cíl větve: druhý provider funguje end-to-end
> (stejně ověřeně jako Gemini ve fázi 1 — reálný běh, ne mock), a správa providerů/modelů
> (aktivace, cena, parametry) jde přes formulář, ne přes ruční SQL.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Routy bez `/admin` prefixu.** `/providers` a `/ai-models`, stejná úroveň jako `/clients`,
   `/markets`. Appka nemá auth/role rozlišení (to je až fáze 5) — `/admin/*` prefix by dnes byl
   jen kosmetický, bez reálné ochrany. Až přijde auth fáze, tyhle routy dostanou role-gate jako
   všechno ostatní.
2. **`Provider` admin UI: jen list + edit, žádné create/delete.** Nový provider pořád vyžaduje
   nový adaptér soubor (`app/adapters/<code>.py`) — vytvoření provider řádku z UI bez adaptéru by
   vedlo k tomu, že se objeví ve `/providers`, ale při pokusu o běh spadne s "no adapter
   registered". Providery se nadále seedují migrací spolu s adaptérem (jako `google_gemini` ve
   fázi 1, `anthropic` v P2-T1 zde). Edit z UI: jen `name` (display).

   **Vědomě odložené (2026-09-09, probráno s uživatelem):** provider-level `is_active` jako
   "kill switch" (deaktivuje celého providera najednou, pamatuje si stav jednotlivých modelů
   pod ním na rozdíl od hromadného "deactivate all") — reálný trigger (vypršelý API klíč,
   problém s platbou u celého providera) zatím nenastal, a `AIModel.is_active` dnes na úrovni
   jednotlivého modelu plně stačí pro 2 providery/5 modelů. Až/pokud se to bude hodit: nový
   sloupec `providers.is_active`, a filtr "co je spustitelné" (`_runnable_model_groups` v
   `app/routers/prompts.py`) rozšířit na `provider.is_active AND model.is_active AND
   provider.code in ADAPTERS` — držet na jednom místě v kódu, ne rozesetý po appce, aby
   nevznikly dvě nezávislé pravdy o tom, co je spustitelné.
3. **`AIModel` admin UI: plné CRUD, delete blokovaný při existující evidenci.** Stejná politika
   jako HD-T4 u Client/Prompt/PromptSet: `runs.model_id` nemá `ondelete`, takže model použitý v
   jakémkoliv běhu **nejde smazat** (strukturovaná chyba s počtem běhů) — jde jen deaktivovat
   (`is_active = false`, stejný vzor jako `Prompt.is_active`). Model bez jediného běhu jde smazat
   rovnou (typicky omylem založený řádek).

   **Vědomě odložené (2026-09-09, probráno s uživatelem):** search/filter box nad `/ai-models`
   (podle textu a/nebo providera). Dnešní rozsah — 5 modelů, 2 provideři — se vejde na jednu
   obrazovku bez scrollování, takže hledání v tabulce zatím není skutečný problém. Až/pokud se
   to bude hodit (řádově desítky modelů): preferovat client-side vanilla-JS filtr (žádná nová
   route, žádný query param, konzistentní s `confirm()` listenerem v `base.html`) před
   server-side `?q=`/`?provider_id=` filtrem — ten by musel `toggle-active`/`delete` redirecty
   nově přeposílat s query parametry zpět, jinak filtr po každé akci zmizí.
4. **Žádný nový `cost_tier` sloupec s pevnými cenovými prahy.** Riziko: hranice "levné/drahé"
   bych si teď musel vymyslet a časem zestárnou nezávisle na skutečné ceně. Místo toho: skutečná
   cena (`cost_per_1k_input_usd`/`cost_per_1k_output_usd`, dnes v USD/1k tokenů, přepočtená v
   šabloně na $/1M pro čitelnost) + nový explicitní `is_free` flag + znovupoužití existujícího
   `capability_tier` (`flagship`/`standard`/`economy`) jako vizuální proxy pro drahé/levné.
   Levné/drahé je tedy vidět ze skutečného čísla, ne z prahu, který si vymyslí AI agent.
5. **`is_free` je explicitní sloupec, ne odvozený z `cost = 0/NULL`.** Stejný precedent jako
   `has_citations` (FR-13) — nikdy neponechávat nejednoznačné, jestli je model zdarma, nebo cena
   jen ještě nebyla vyplněná.

   **Revize (2026-09-09, po diskuzi s uživatelem):** Gemini seed modely dostanou
   `is_free = FALSE`, ne `TRUE`. Důvod: FR-8 dělá Google Search grounding povinným pro každý
   reálný běh (je to jádro produktu — citace jsou FR-10 až FR-13), a grounding na Gemini API
   podle dosavadního zjištění buď vyžaduje billing-enabled (placený) projekt, nebo má jen
   omezenou free kvótu, po jejímž vyčerpání se účtuje — v obou případech to znamená, že žádný
   Gemini běh, který SignalMap skutečně provede, není spolehlivě "zdarma". Přesný mechanismus
   (billing-enabled projekt vs. free denní kvóta) nebyl v době psaní tohohle dokumentu ověřený
   proti aktuální Google dokumentaci/Cloud Console — **ověř to v P2-T1 před nastavením seed
   hodnoty** (zkontroluj billing status projektu za stávajícím `GOOGLE_API_KEY`, který dnes
   fázi 1 prokazatelně provozuje s groundingem). Pokud se ukáže, že billing na tom projektu
   zapnutý není a grounding přesto funguje v rámci free kvóty, over `is_free = TRUE` zpátky —
   jinak nech `FALSE`. Anthropic modely `is_free = FALSE` (Anthropic nemá free tier vůbec).

   Sloupec `is_free` v schématu zůstává (levný, informační, pro případný budoucí skutečně-zdarma
   model) — jen se dnes u žádného seedovaného modelu pravděpodobně nenastaví na `TRUE`. Žádný
   dual-key (free/paid `GOOGLE_API_KEY`) routing mechanismus se v týhle větvi **nestaví** — byl
   probíraný, ale zamítnutý jako řešení problému, který podle výše uvedeného pravděpodobně vůbec
   nenastává (žádný Gemini model, co appka reálně používá, není spolehlivě zdarma). Pokud se do
   budoucna objeví skutečně bezplatný model/provider bez groundingu, dual-key/multi-key routing
   se dá navrhnout tehdy — teď by to bylo řešení pro hypotetický, neověřený případ.
6. **Žádný automatický pricing sync.** Ani Google, ani Anthropic nemá spolehlivé strojově
   čitelné pricing API — stavět na to scraping/sync skript by byla komplexita neúměrná pár
   modelům dvou providerů. Admin UI z P2-T2/P2-T3 je jediný a dostatečný mechanismus aktualizace
   — stejný vzor, jakým `/settings` už dnes řeší per-provider konfiguraci bez nutnosti redeploy.
7. **Seed ceny pro Anthropic modely v P2-T1 zůstávají NULL, ne vymyšlené číslo.** Nechci do
   migrace zapsat cenu, kterou jsem si k dnešnímu datu neověřil — riziko tichého driftu od
   reality. `model_name` (přesný string pro Anthropic Messages API) a aktuální ceník se **musí
   ověřit proti aktuální Anthropic dokumentaci v době implementace P2-T1**, ne převzít odsud.
   Po P2-T1 je prvním ručním krokem doplnění reálné ceny přes `/ai-models` (P2-T3) — hezky to
   rovnou ověří, že admin UI funguje.
8. **Audit columns (`created_at`/`updated_at`) na `providers`/`ai_models` — teď dává smysl.**
   HD-T6 je záměrně vynechal s odůvodněním "nemají dnes žádnou cestu k editaci" (design decision
   7 tamtéž). P2-T2/P2-T3 tu cestu vytvářejí, takže sloupce teď nejsou mrtvé schema.
9. **`context_window_tokens`/`max_output_tokens` — nové nullable sloupce, čistě informační.**
   Žádná validace/vynucování limitu v aplikační vrstvě v týhle fázi — to by bylo nad rámec
   (prompt délku nikdo dnes neměří). Jen se zobrazí v `/ai-models` listu.
10. **Geo-targeting: Anthropic adaptér použije `user_location` v `web_search` toolu.** Tohle je
    hlavní motivace mít druhého providera podle `docs/REQUIREMENTS.md` §3a a `/findings` —
    Gemini nemá žádný lokalizační parametr, jen textový hint. **Přesný tool type/verzovaný
    string a `user_location` shape se musí ověřit proti aktuální Anthropic API dokumentaci v
    době implementace P2-T4**, ne převzít z paměti/tréninkových dat — API se mezi verzemi mění.
11. **Cena/free badge v run-trigger dropdownu: nativní `<select>` beze změny + živý badge panel
    pod ním, revidováno 2026-09-09 po diskuzi s uživatelem.** `<option>` prvek nepodporuje HTML,
    takže v samotné položce dropdownu může být jen prostý text/emoji — ale místo se s tím
    spokojit se pro každý model předrenderuje skrytý `<div>` se skutečným `cost_badge` makrem
    (stejná komponenta jako `/ai-models`, žádný nový vizuální jazyk), a malý vanilla-JS listener
    (~10 řádků, stejný vzor jako `confirm()` listener v `base.html` — žádný framework) na
    `change` eventu dropdownu přepne, který badge je vidět. Dropdown samotný zůstává nativní/
    přístupný (klávesnice, mobil) — žádná custom-widget reimplementace.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| P2-T1 | Schema: rozšíření `providers`/`ai_models` + seed Anthropic provider a modelů | ⏳ |
| P2-T2 | Provider admin UI (list + edit) | ⏳ |
| P2-T3 | AIModel admin UI (plné CRUD, cena/parametry, cost badge) | ⏳ |
| P2-T4 | Anthropic adaptér (`app/adapters/anthropic.py`) + `.env` | ⏳ |
| P2-T5 | Run-trigger dropdown: cena/free badge u modelu | ⏳ |
| P2-T6 | Testy: rozšíření pytest sady o Anthropic seed + admin CRUD | ⏳ |

Pořadí je vynucené: P2-T2/P2-T3 potřebují nové sloupce z P2-T1; P2-T4 dává smysl až po P2-T3,
protože prvním krokem po seedu je ověřit/doplnit reálnou cenu Anthropic modelů přes admin UI
dřív, než se přes ně spustí první placený běh; P2-T5 je kosmetická úprava nad tím, co P2-T3 zavádí;
P2-T6 testuje všechno předchozí najednou.

---

## P2-T1 — Schema: rozšíření `providers`/`ai_models` + seed Anthropic

**Target:** nová migrace `alembic/versions/0009_*.py`, `app/models/provider.py`,
`.env.example`, `app/config.py`

1. Migrace 0009:
   - `providers` — `ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()`,
     `ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
   - `ai_models` — `ADD COLUMN is_free BOOLEAN NOT NULL DEFAULT FALSE`,
     `ADD COLUMN context_window_tokens INTEGER`, `ADD COLUMN max_output_tokens INTEGER`,
     `ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()`,
     `ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
   - **Před dalším krokem ověř billing status Google Cloud projektu za stávajícím
     `GOOGLE_API_KEY`** (ten, co dnes v produkci prokazatelně provozuje grounding — viz design
     decision 5, revize). Pokud billing NENÍ zapnutý a grounding přesto funguje v rámci free
     kvóty: `UPDATE ai_models SET is_free = TRUE WHERE provider_id = (SELECT id FROM providers
     WHERE code = 'google_gemini')`. Pokud billing JE zapnutý (grounding vyžaduje placený
     tier): žádný UPDATE, `ai_models.is_free` zůstává na defaultu `FALSE` pro oba Gemini řádky.
   - `INSERT INTO providers (code, name) VALUES ('anthropic', 'Anthropic Claude')`.
   - `INSERT INTO ai_models (...)` — 2-3 řádky pro Claude modely (např. jeden standard/flagship
     tier, jeden economy tier). **Před psaním INSERTu ověř proti aktuální Anthropic
     dokumentaci:** přesné `model_name` stringy pro Messages API (obvykle datované snapshoty,
     ne aliasy), `context_window_tokens`, `max_output_tokens`. `cost_per_1k_input_usd`/
     `cost_per_1k_output_usd` nech `NULL`, `is_free = FALSE`, `capability_tier` podle relativní
     pozice v Anthropic lineupu, `supports_web_search = TRUE` (Anthropic `web_search` tool),
     `notes = 'Pricing not yet verified — set via /ai-models'`.
2. `app/models/provider.py` — `Provider` dostává `created_at`/`updated_at`; `AIModel` dostává
   `is_free: Mapped[bool]`, `context_window_tokens: Mapped[int | None]`,
   `max_output_tokens: Mapped[int | None]`, `created_at`/`updated_at` (stejný vzor jako
   `app/models/client.py` — `server_default=func.now()`, `updated_at` navíc
   `onupdate=func.now()`).
3. `.env.example` — přidat `ANTHROPIC_API_KEY=` s komentářem (odkaz na
   `https://console.anthropic.com/settings/keys`, stejný formát jako `GOOGLE_API_KEY` komentář).
4. `app/config.py` — `Settings` dostává `anthropic_api_key: str = ""`.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověřit: `providers` má nový řádek `anthropic`; `ai_models` má nové Claude řádky s
   `is_free = false`, cenou `NULL`; existující Gemini řádky mají `is_free` podle skutečně
   ověřeného billing statusu (viz krok 1 výše — pravděpodobně `false`, ne automaticky `true`).
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add anthropic provider + model rows, pricing/context columns on ai_models
```

---

## P2-T2 — Provider admin UI (list + edit)

**Target:** nový `app/routers/providers.py`, nový `app/templates/providers/list.html`,
`app/main.py` (router registrace), `app/i18n/en.json`, `app/i18n/de.json`

1. `app/routers/providers.py` — `GET /providers` (list s inline edit formulářem na jméno,
   podobně jako `/settings` dnes dělá pro template), `POST /providers/{id}` (uloží `name`).
   Žádné create/delete endpointy (design decision 2). Docstring na obou route funkcích.
2. `app/templates/providers/list.html` — tabulka: kód (needitovatelný, jen zobrazený), jméno
   (edit input), počet aktivních modelů (odkaz na `/ai-models?provider_id=...` z P2-T3), datum
   `updated_at`. Reuse `text_field`/`button` makra z `partials/macros.html`.
3. `app/main.py` — zaregistrovat nový router.
4. i18n — `provider.list_title`, `provider.name_label`, `provider.model_count`,
   `provider.updated_at_label` (EN+DE, jeden commit).
5. Nav odkaz v `base.html` na `/providers` (vedle stávajících `/clients`, `/markets`, atd.).

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči `/providers` na ~640px/~1024px/desktop šířce: oba providery vidět, editace jména
   u Anthropic řádku se uloží a zobrazí.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(providers): add read/edit admin screen for AI providers
```

---

## P2-T3 — AIModel admin UI (plné CRUD, cena/parametry, cost badge)

**Target:** nový `app/routers/ai_models.py`, nové `app/templates/ai_models/list.html`,
`app/templates/ai_models/form.html`, `app/main.py`, `app/templates/partials/macros.html`
(nové makro `cost_badge`), `app/i18n/en.json`, `app/i18n/de.json`

1. `app/routers/ai_models.py`:
   - `GET /ai-models` — list všech modelů (napříč providery, seskupené jako v
     `_runnable_model_groups`), s cenou, `is_free`/`capability_tier` badge, `is_active` stavem,
     tlačítky Edit/Deactivate-Activate/Delete.
   - `GET/POST /ai-models/new` — create formulář (provider select, `model_name`, `display_name`,
     `capability_tier` select, cena, `context_window_tokens`, `max_output_tokens`, `is_free`
     checkbox, `supports_web_search` checkbox, `notes`).
   - `GET/POST /ai-models/{id}/edit` — stejný formulář, předvyplněný.
   - `POST /ai-models/{id}/delete` — helper "má tento model nějaký běh?" (stejný vzor jako
     HD-T4 in-use kontrola), blokovat strukturovanou chybou s počtem běhů; jinak smazat.
   - `POST /ai-models/{id}/toggle-active` — přepne `is_active` (žádné mazání evidence).
   Každá route dostane docstring; každé netriviální `Form(...)` pole `description=...`.
2. `app/templates/partials/macros.html` — nové makro `cost_badge(model)`: pokud `is_free`,
   zobrazí "Free" badge (zelená, stejná paleta jako `status_badge`'s success); jinak zobrazí
   `capability_tier` jako badge (flagship=červená/drahá, standard=amber, economy=stone) **plus**
   skutečnou cenu vedle (`${{ (model.cost_per_1k_input_usd * 1000)|round(2) }}/$
   {{ (model.cost_per_1k_output_usd * 1000)|round(2) }} per 1M`, `—` když `NULL`).
3. `app/templates/ai_models/list.html` — tabulka: provider, model_name, display_name,
   `cost_badge`, context/max tokens, `supports_web_search`, `is_active` toggle, **"Last
   updated" sloupec (`updated_at`, relativně nebo jako datum — stejná informace, kterou
   `/providers` z P2-T2 už zobrazuje)**, Edit, Delete (`delete_button` makro, blokovaný stav
   vidět jako disabled/tooltip pokud má běhy — zjisti run count per model v routeru a předej
   do šablony). `created_at` se zobrazí jen v edit formuláři (read-only řádek), ne v listu —
   `updated_at` je to, co admin skutečně chce vidět na první pohled (kdy naposledy někdo
   ověřil/upravil cenu).
4. `app/templates/ai_models/form.html` — reuse `text_field`/`select_field`/`checkbox_field`/
   `textarea_field` makra, žádná nová markup od nuly.
5. `app/main.py` — zaregistrovat router.
6. i18n — `ai_model.*` klíče (list_title, create_title, edit_title, cost_free,
   cost_per_million, updated_at_label, created_at_label, delete_button/confirm,
   deactivate_button/confirm, `errors.ai_model_in_use` se stejným `{count}` placeholder
   formátem jako `errors.market_in_use`) — EN+DE, jeden commit.
7. Nav odkaz v `base.html` na `/ai-models`.

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči `/ai-models`: doplnit reálnou cenu Anthropic modelů ze seedu P2-T1 (z aktuálního
   Anthropic ceníku) — uloží se, badge se přepočítá.
3. Založit testovací model bez běhů → smazat → funguje. Zkusit smazat Gemini model, který má
   existující běhy z fáze 1 → zablokované se správným počtem.
4. Deaktivovat/aktivovat model → zmizí/objeví se v run-trigger dropdownu na `/prompts/{id}`.
5. Ověřit na ~640px/~1024px/desktop šířce.
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ai-models): add full CRUD admin screen with pricing, context params, cost badge
```

---

## P2-T4 — Anthropic adaptér

**Target:** nový `app/adapters/anthropic.py`, `app/adapters/__init__.py`, `requirements.txt`

1. `requirements.txt` — přidat pinnutou verzi `anthropic` SDK (ověřit aktuální stabilní verzi
   k datu implementace, stejný přístup jako HD-T3).
2. `app/adapters/anthropic.py` — `AnthropicAdapter` implementující `ProviderAdapter.run()`
   (`app/adapters/base.py`):
   - Anthropic Messages API s `web_search` toolem zapnutým.
   - `system_instruction` parametr namapovaný na Anthropic `system` pole.
   - **Geo-targeting: `user_location` v `web_search` tool configu, odvozený z marketu runu**
     (stejná informace, kterou dnes Gemini adaptér dostává jen jako textový hint) — tohle je
     hlavní rozdíl oproti Gemini adaptéru a hlavní důvod, proč tenhle provider vůbec přidáváme
     (`docs/REQUIREMENTS.md` §3a, `/findings`). Přesný shape `user_location` (country/region/
     city/timezone) ověř proti aktuální Anthropic API dokumentaci — needsazuj z paměti.
   - Mapování Anthropic `web_search_tool_result`/citation bloků na `AdapterCitation` (stejná
     `has_citations` disciplína jako `app/adapters/google.py` — explicitně `False`, ne jen
     prázdný seznam, když provider nevrátí žádné citace).
   - `raw_payload` = kompletní, netransformovaná SDK odpověď (FR-10, stejně jako Gemini).
   - Necháva SDK vyhazovat na transport/API chyby (stejný vzor jako Gemini — caller v
     `app/routers/runs.py` to zachytává a ukládá jako `Run.status = 'error'`).
3. `app/adapters/__init__.py` — registrace `"anthropic": AnthropicAdapter` v `ADAPTERS`.

Po dokončení:
1. Reálný `.env` s `ANTHROPIC_API_KEY` (uživatel má klíč už vytvořený).
2. `docker compose up -d --build`
3. V prohlížeči: na existujícím promptu spustit běh proti Claude modelu (musí být `is_active`
   z P2-T3) → skutečný běh, žádný mock. Ověřit v run detailu: rendered text, raw JSON obsahuje
   Anthropic response shape, citace (pokud provider nějaké vrátil).
4. Vyvolat chybu (např. dočasně špatný `ANTHROPIC_API_KEY`) → `Run.status == 'error'` se
   smysluplnou hláškou, ne surová 500.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(adapters): add Anthropic Claude adapter with web_search geo-targeting
```

---

## P2-T5 — Run-trigger dropdown: cena/free badge u modelu

**Target:** `app/routers/prompts.py` (`_runnable_model_groups`), `app/templates/prompts/detail.html`

Podle design decision 11 (revidováno 2026-09-09): nativní `<select>` beze změny + živý badge
panel pod ním, reuse `cost_badge` makra z `partials/macros.html` (P2-T3).

1. `_runnable_model_groups` (`app/routers/prompts.py`) — vrací teď rovnou `AIModel` objekty (ne
   jen `(id, text)` tuple), aby šablona měla přístup ke všem polím pro `cost_badge`.
2. `prompts/detail.html` — pro každý model v `model_groups` předrenderovat skrytý `<div
   data-model-badge="{{ model.id }}" hidden>` s `cost_badge(model, t('ai_model.cost_free'))`
   uvnitř. Pod `<select id="model_id">` viditelný kontejner `<div id="model-badge-display">`.
3. Malý vanilla-JS blok (stejný vzor/umístění jako `confirm()` listener v `base.html`) —
   `change` listener na `#model_id`: schová všechny `[data-model-badge]`, ukáže ten se shodným
   id, přesune jeho vnitřní HTML/clone do `#model-badge-display`. Spustit i jednou při načtení
   stránky (aby se badge zobrazil u výchozí předvyplněné volby, ne až po první změně).

Po dokončení:
1. V prohlížeči `/prompts/{id}`: pod dropdownem je vidět barevný `cost_badge` (stejný vizuál
   jako `/ai-models`) pro aktuálně vybraný model, mění se okamžitě při přepnutí modelu, funguje
   i pro výchozí předvyplněnou volbu bez nutnosti dropdown nejdřív změnit.
2. Klávesnice (šipky/Tab přes `<select>`) pořád funguje standardně — žádná custom-widget
   reimplementace.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): show price/free indicator on model choice in run-trigger dropdown
```

---

## P2-T6 — Testy: rozšíření pytest sady

**Target:** `tests/conftest.py`, nový `tests/test_ai_models.py`, `tests/test_providers.py`,
úprava `tests/test_runs.py`

1. `tests/conftest.py` — seed fixture rozšířit o `anthropic` provider + jeden testovací Claude
   model; `FakeAdapter` registrovaný i pro `"anthropic"` v `ADAPTERS` (stejný vzor jako u
   `google_gemini` z HD-T5), nikdy nevolá reálné Anthropic API v testech.
2. `tests/test_providers.py` — `/providers` list; edit jména se uloží.
3. `tests/test_ai_models.py` — create → list → edit → deactivate/activate; delete zablokovaný,
   když model má existující běh (přes `FakeAdapter` run fixture); delete projde na modelu bez
   běhu.
4. `tests/test_runs.py` — rozšířit o běh proti Anthropic modelu přes `FakeAdapter` (úspěch i
   chybová cesta), stejně jako dnešní Gemini test.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover Anthropic provider path and providers/ai-models admin CRUD
```

---

## Completion Checklist

- [x] `providers`/`ai_models` mají nové sloupce (audit, `is_free`, context/max tokens);
      Anthropic provider + modely seedované
- [x] `/providers` — list + edit jména funguje
- [x] `/ai-models` — plné CRUD, delete blokovaný při existující evidenci, cena/free badge vidět
- [x] Reálný běh přes Claude model funguje end-to-end (rendered text, raw JSON, citace)
- [x] Chybová cesta u Anthropic běhu ukládá strukturovanou chybu, ne surovou 500
- [x] Run-trigger dropdown ukazuje cenu/free u každého modelu
- [x] `pytest` sada zelená, pokrývá nové providery/ai-models/anthropic run cesty
- [ ] `docs/TASKS.md` — poznámka, že fáze 2 větev existuje a co pokrývá (odkaz na tenhle soubor)
- [ ] `docs/REQUIREMENTS.md` §4 "Explicitly Out of Scope" — odstranit/upravit "Any provider
      other than Google Gemini" řádek, jakmile je větev smergnutá
