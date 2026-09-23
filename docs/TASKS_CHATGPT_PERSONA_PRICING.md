# SignalMap — Tasks: ChatGPT adapter + persona placeholder + AI-model price history

## Status: ✅ Done — PR #10, merged 2026-09-13, released in v1.0.0

## v1.0 | Září 2026
## Branch: feature/signalmap-chatgpt-persona-pricehistory
## Task ID prefix: CPH

> Fáze 1–6 (`docs/TASKS.md`, `docs/TASKS_PHASE2.md`–`docs/TASKS_PHASE6.md`), hardening
> (`docs/TASKS_HARDENING.md`), export (`docs/TASKS_EXPORT.md`) a search queries
> (`docs/TASKS_SEARCH_QUERIES.md`) jsou hotové a smergnuté. Tenhle dokument bundluje čtyři
> samostatné kusy práce do jedné branch (odsouhlaseno s uživatelem 2026-09-13, čtvrtý přidán
> později téhož dne — viz níže) — žádný z nich nezávisí na tom, že by musel běžet už živě v
> provozu, takže granularita "jedna branch na jeden úkol" z fází 2–4 tu není potřeba (stejné
> zdůvodnění jako `docs/TASKS_PHASE5.md`):
>
> 1. **Třetí AI provider — OpenAI/ChatGPT** (CPH-T6–T7) — stejný adapter pattern jako Google
>    Gemini (fáze 1) a Anthropic (fáze 2), s `web_search` toolem a geo-targetingem přes
>    `user_location`, ne jen textovým hintem.
> 2. **Perzona v system-instruction šabloně** (CPH-T3–T5) — dnešní natvrdo zadané "The person
>    asking..." (`app/routers/settings.py`) se stává datově řízeným `{persona}` placeholderem
>    s CRUD správou (muž/žena, dítě, manažer, politik, ...), volitelným per-run — stejný vzor,
>    jaký `runs.market_id` už dnes dělá pro jazyk/lokalitu.
> 3. **Historie cen AI modelu** (CPH-T1–T2) — `ai_models.cost_per_1k_*_usd` se dnes při editaci
>    přepíše beze stopy; nová `ai_model_price_history` tabulka zachovává každou předchozí cenu
>    s datem platnosti, připraveno i pro budoucí cost/ops dashboard z ROADMAPy.
> 4. **Ochrana proti vícenásobnému spuštění runu** (CPH-T9, přidáno 2026-09-13 po dokončení
>    CPH-T1–T8) — `trigger_run` je synchronní a čeká na odpověď providera (v testech
>    pozorováno 5,7–28 s), appka během čekání nedává žádnou vizuální zpětnou vazbu, takže
>    netrpělivé druhé kliknutí spustí druhý placený běh na stejném promptu. Nesouvisí věcně se
>    zbytkem branch, ale je dost malé (JS na tlačítku + jedna kontrola v už tak upravovaném
>    `trigger_run`), že si nezaslouží vlastní branch — bundlováno sem ze stejného důvodu jako
>    body 1–3.
>
> Design byl probraný a odsouhlasený s uživatelem přímo v konverzaci (viz `AskUserQuestion`
> 2026-09-13): per-run persona override (ne jen globální default), volný text u perzony (ne
> i18n), viditelná stránka s historií cen (ne jen tichý zápis), seedované OpenAI modely v
> migraci (stejný vzor jako Anthropic ve fázi 2), a — pro bod 4 — synchronní "zůstat u
> jednoho requestu" varianta (níže "varianta A") místo asynchronního běhu na pozadí (varianta
> B, prodiskutovaná a vědomě odložená, viz `docs/ROADMAP.md` bod 5 "Scheduler").

---

## ⚠️ Schema flagy (AI_INSTRUCTIONS.md §4) — potvrzeno s uživatelem 2026-09-13

Tahle branch zavádí **dvě nové tabulky** nad rámec `schema_phase1.sql`
(`ai_model_price_history`, `personas`) a **dva nové sloupce** na existujících tabulkách
(`runs.persona_id`, plus seed dat na `providers`/`ai_models` pro OpenAI). Všechny čtyři byly
probrané a odsouhlasené s uživatelem v této konverzaci před psaním tohohle dokumentu — žádné
z nich se nevymýšlí za pochodu při implementaci.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Historie cen: SCD-2-style tabulka, ne přepis na místě.** `ai_models.cost_per_1k_input_usd`/
   `cost_per_1k_output_usd` zůstávají "aktuální cena" (nic ve stávajícím kódu, co dnes čte tyhle
   sloupce, se nemusí měnit). Nová `ai_model_price_history` dostává nový řádek pokaždé, když se
   cena **skutečně změní** — porovnání staré vs. nové hodnoty před commitem, ne při každé editaci
   modelu (úprava nesouvisejícího pole jako `notes` historii neplodí). První řádek historie se
   zapíše při vytvoření modelu (CPH-T1). Stejný princip jako Stripe/AWS cenové historie a jako
   dnešní `prompt` verzování v tomhle projektu ("never overwrite historical rows").
2. **Historie cen: viditelná sekce na `/ai-models/{id}/edit`, ne jen tichý zápis.** Read-only
   tabulka pod cenovými poli — předchozí cena + platnost "od–do". `ai_model_price_history`
   neukládá žádný `effective_until` sloupec (design decision 1) — "do" se dopočítá jako
   `effective_from` následujícího řádku téhož modelu (nebo "dodnes" u posledního/aktuálního
   řádku), čistě ve čtecím dotazu/šabloně, ne jako uložené pole. Malý rozsah práce navíc za
   okamžitou viditelnost (uživatelská volba 2026-09-13).
3. **Perzona: nová tabulka `personas`, `label` je volný text, ne přes `t()`.** Stejná disciplína
   jako `Market.label` — `label` (např. "person", "manager", "politician") jde přímo do anglické
   `system_instruction` šablony poslané providerovi, není to vývojářský UI popisek, takže
   neprochází DE/EN i18n vrstvou. Admin si ho při vytváření perzony napíše sám.
4. **Perzona: právě jeden `is_default` řádek, vynucené partial unique indexem.**
   `CREATE UNIQUE INDEX ... WHERE is_default` — standardní Postgres technika pro "at most one
   default row" (stejný princip jako výchozí platební metoda v platebních systémech). Přepnutí
   defaultu (v `update_persona`) nejdřív vynuluje `is_default` na aktuálním default řádku, pak
   nastaví nový — dvě samostatné `UPDATE` v jedné transakci, aby index nikdy neviděl dva `TRUE`
   řádky současně.
5. **Perzona: per-run override, ne jen globální default v `/settings`.** Nový `runs.persona_id`
   (NOT NULL, FK) — stejný vzor jako `runs.market_id` override (`alembic/versions/0002_run_
   market_override.py`): sloupec přidaný jako nullable, existující řádky backfillnuté na id
   defaultní perzony, pak `NOT NULL` + FK + index. Umožňuje srovnat odpověď pro "manažer" vs.
   "politik" na stejném promptu a zůstává navždy zapsané u konkrétního běhu jako důkaz
   (`request_payload`).
6. **Perzona: delete blokovaný, když je řádek `is_default`, nebo má existující `Run`.** Stejná
   evidence-retention politika jako `markets`/`ai_models` delete (referencovaný `Run` nejde
   smazat) plus jedna navíc invarianta — tabulka nikdy nesmí zůstat bez defaultní perzony, takže
   aktuální default musí být nejdřív přeřazen na jinou perzonu, než se smí smazat. Seed
   (CPH-T3) zaručuje, že `personas` nikdy nezačíná prázdná.
7. **`DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE`: "The person asking..." → "The {persona} asking...".**
   `.format()` ignoruje nepoužité kwargy, takže existující uložené `SystemInstructionTemplate`
   řádky (např. Anthropicova zkrácená jazyková šablona bez zmínky o "person") zůstávají beze
   změny funkční i bez `{persona}` — placeholder je opt-in, ne vynucený. `_DRY_RUN_VALUES`
   (validace šablony při ukládání) dostává vzorovou hodnotu `"persona": "person"`.
8. **OpenAI adapter: Responses API (`client.responses.create`), ne Chat Completions.** Chat
   Completions je dnes v udržovacím režimu pro nové schopnosti — built-in `web_search` tool a
   anotace-based citace (`url_citation`) existují jen v Responses API. Stejná architektura jako
   Gemini (grounding tool) a Anthropic (`web_search` tool) — třetí adaptér, ne výjimka.
9. **OpenAI adapter: `user_location` geo-targeting, na paritě s Anthropicem.** OpenAI `web_search`
   tool podporuje `user_location` (country/city/region) — na rozdíl od Gemini (žádný lokalizační
   parametr). `instructions` top-level pole Responses API = `system_instruction`.
10. **OpenAI: přesný tool type string, shape `user_location` a aktuální ceník se MUSÍ ověřit
    proti aktuální OpenAI dokumentaci v době implementace CPH-T6/CPH-T7** — nepřebírat z paměti/
    tréninkových dat, stejné pravidlo jako u Anthropic geo-targetingu ve fázi 2 (design decision
    10, `docs/TASKS_PHASE2.md`). Totéž platí pro `openai` Python SDK verzi v `requirements.txt`.
11. **Žádný automatický pricing sync, žádný nový `cost_tier`.** Stejné zdůvodnění jako fáze 2
    design decisions 4 a 6 — admin UI (`/ai-models`, teď i s historií z CPH-T1/T2) je jediný a
    dostatečný mechanismus aktualizace ceny.
12. **Cena běhu se v týhle branch nepočítá.** CPH-T1/T2 jen ukládají historii cen — žádný
    přepočet `token_usage × cena platná v době běhu` na run detailu (potvrzeno s uživatelem
    2026-09-13). Skutečný výpočet patří do budoucího cost/ops dashboardu (`docs/ROADMAP.md`
    bod 4), který navíc musí řešit normalizaci `token_usage` napříč providery (Gemini/
    Anthropic/OpenAI mají různý tvar) — samostatný problém, ne detail historie cen. Tahle
    branch jen zajišťuje, že historická cena bude existovat, až se ten výpočet bude psát.
13. **Ochrana proti vícenásobnému spuštění runu: varianta A (zůstat synchronní), ne varianta B
    (běh na pozadí + polling).** Probráno a kriticky zhodnoceno s uživatelem 2026-09-13 —
    varianta B (appka okamžitě přesměruje, provider call běží mimo request, stránka pollinguje
    stav) je architektonicky "správnější" řešení dlouhodobě, ale vyžaduje mechanismus běhu na
    pozadí, který appka dnes vůbec nemá (žádný `BackgroundTasks`, Celery, RQ — ověřeno), a
    věcně patří ke stejné infrastruktuře, kterou bude jednou potřebovat i Scheduler
    (`docs/ROADMAP.md` bod 5) — řešit frontu úloh dvakrát nezávisle by byla duplicitní práce.
    Varianta A (zůstat u dnešního synchronního requestu, jen zablokovat duplicitu) je zvolena
    jako přiměřená oprava teď; přechod na variantu B je odložený na dobu, kdy se bude stavět
    Scheduler.
14. **Kontrola duplicity: per `prompt_id`, ne per kombinace model/market/persona.** `trigger_run`
    už dnes ukládá `Run(status='pending')` v samostatném commitu před voláním adaptéru (žádná
    schema změna potřeba) — pending řádek tedy v DB reálně existuje po celou dobu čekání na
    providera. Kontrola "existuje pending Run pro tenhle prompt" odpovídá přesně popsanému
    problému (dvojklik na stejný formulář) — záměrně neumožňuje ani paralelní běh stejného
    promptu s jinými parametry, což by bylo řešení hypotetické potřeby, ne popsaného problému.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| CPH-T1 | Schema + zápis: `ai_model_price_history` | ✅ |
| CPH-T2 | UI: historie cen na `/ai-models/{id}/edit` | ✅ |
| CPH-T3 | Schema: `personas` tabulka + seed defaultní perzony | ✅ |
| CPH-T4 | Personas admin UI (CRUD) | ✅ |
| CPH-T5 | Perzona v system-instruction šabloně + per-run override na `Run` | ✅ |
| CPH-T6 | Schema: OpenAI provider + seed modelů | ✅ |
| CPH-T7 | OpenAI adaptér (`app/adapters/openai.py`) | ✅ |
| CPH-T8 | Testy: rozšíření pytest sady o všechny tři featury | ✅ |
| CPH-T9 | Ochrana proti vícenásobnému spuštění runu (varianta A) | ⏳ |

Pořadí: CPH-T1→T2 (historie cen je plně nezávislá, nejmenší, jde první). CPH-T3→T4→T5 (tabulka
před CRUD UI před zapojením do `/settings`+`runs`). CPH-T6→T7 (schema před adaptérem, stejné
pořadí jako fáze 2 P2-T1→P2-T4). CPH-T8 testuje všechno předchozí najednou. CPH-T9 přidán
2026-09-13 po dokončení CPH-T1–T8 (samostatný, nezávislý na zbytku) — jde poslední.

---

## CPH-T1 — Schema + zápis: `ai_model_price_history`

**Target:** nová migrace `alembic/versions/0020_ai_model_price_history.py`,
`app/models/provider.py`, `app/routers/ai_models.py`

1. Migrace 0020:
   - `CREATE TABLE ai_model_price_history` — `id` PK, `ai_model_id INTEGER NOT NULL REFERENCES
     ai_models(id) ON DELETE CASCADE`, `cost_per_1k_input_usd NUMERIC(10,5)`,
     `cost_per_1k_output_usd NUMERIC(10,5)`, `effective_from TIMESTAMPTZ NOT NULL DEFAULT now()`,
     `changed_by_user_id INTEGER REFERENCES users(id)`.
   - `CREATE INDEX idx_price_history_model ON ai_model_price_history(ai_model_id, effective_from)`.
   - Backfill: pro každý existující řádek `ai_models` vlož jeden počáteční historický řádek s
     jeho aktuální cenou a `effective_from = ai_models.created_at` (ne `now()`) — jinak by
     historie u dnešních modelů "začínala" až tímhle nasazením, ne jejich skutečným vznikem.
2. `app/models/provider.py` — nový `AIModelPriceHistory` model (stejný soubor jako `AIModel`,
   mirroring pattern); `AIModel` dostává `price_history: Mapped[list["AIModelPriceHistory"]] =
   relationship(back_populates="ai_model", order_by="AIModelPriceHistory.effective_from.desc()")`.
3. `app/models/__init__.py` — export `AIModelPriceHistory`.
4. `app/routers/ai_models.py`:
   - `create_ai_model` — po vytvoření modelu rovnou zapiš první `AIModelPriceHistory` řádek se
     stejnou cenou (i když `NULL`).
   - `update_ai_model` — před přepsáním `model.cost_per_1k_input_usd`/`cost_per_1k_output_usd`
     porovnej s novou hodnotou z `parsed["cost_in"]`/`parsed["cost_out"]`; pokud se aspoň jedna
     liší, přidej nový `AIModelPriceHistory` řádek s **novou** cenou a `effective_from=now()`
     (`changed_by_user_id` = aktuální uživatel — routa potřebuje nový `user: User =
     Depends(current_active_user)` parametr, stejný vzor jako `app/routers/runs.py`
     `trigger_run`).
   Docstring update na obou funkcích vysvětlující, kdy se historie zapisuje (jen při skutečné
   změně ceny).

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověřit: každý existující model má právě jeden `ai_model_price_history` řádek s
   `effective_from` odpovídajícím jeho `created_at`.
3. V prohlížeči upravit cenu existujícího modelu → nový historický řádek vznikne; uložit model
   znovu se stejnou cenou (jen změnit `notes`) → žádný nový historický řádek.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ai-models): record price history on every actual price change
```

---

## CPH-T2 — UI: historie cen na `/ai-models/{id}/edit`

**Target:** `app/routers/ai_models.py`, `app/templates/ai_models/form.html`,
`app/i18n/en.json`, `app/i18n/de.json`

1. `edit_ai_model_form` — načti `model.price_history` (už seřazené `effective_from DESC` díky
   `relationship`'s `order_by`), dopočítej pro každý řádek jeho platnost "do" (`effective_from`
   předchozího/novějšího řádku v pořadí, u prvního/nejnovějšího "dodnes" — žádný uložený
   sloupec, čistě výpočet nad už načteným seznamem), předej do šablony jako `price_history`
   (list dvojic nebo malý dataclass s `row`/`valid_until`).
2. `ai_models/form.html` — pod cenovými poli, jen v edit módu (`model is not none`), read-only
   tabulka: input/output cena přepočtená na $/1M (stejný přepočet jako `cost_badge`), platnost
   "od `effective_from` do `valid_until`" (poslední řádek: "do" = "dodnes"/aktuální). Žádná akce
   (needitovatelné řádky) — čistě evidence.
3. i18n (EN+DE, jeden commit) — `ai_model.price_history_title`, `ai_model.price_history_empty`
   (jeden počáteční řádek nikdy není "empty", ale klíč pro konzistenci s ostatními prázdnými
   stavy v appce).

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči `/ai-models/{id}/edit` u modelu s aspoň dvěma cenovými změnami (z CPH-T1 kroku
   3): historie vidět, seřazená od nejnovější, správné datumy a částky.
3. Ověřit na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ai-models): show price history on the model edit screen
```

---

## CPH-T3 — Schema: `personas` tabulka + seed defaultní perzony

**Target:** nová migrace `alembic/versions/0021_personas.py`, nový `app/models/persona.py`,
`app/models/__init__.py`

1. Migrace 0021:
   - `CREATE TABLE personas` — `id` PK, `label VARCHAR(100) NOT NULL UNIQUE`,
     `is_default BOOLEAN NOT NULL DEFAULT FALSE`, `created_at`/`updated_at TIMESTAMPTZ NOT NULL
     DEFAULT now()` (`updated_at` s `onupdate` jen na ORM úrovni, stejně jako `ai_models`).
   - `CREATE UNIQUE INDEX idx_personas_one_default ON personas (is_default) WHERE is_default`.
   - `INSERT INTO personas (label, is_default) VALUES ('person', TRUE)` — zachovává dnešní
     chování beze změny (viz design decision 7), dokud admin nepřidá další perzony.
2. `app/models/persona.py` — nový `Persona` model, stejný vzor jako `app/models/market.py`.
3. `app/models/__init__.py` — export `Persona`.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověřit: `personas` má právě jeden řádek, `label='person'`, `is_default=true`.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add personas table with seeded default "person" row
```

---

## CPH-T4 — Personas admin UI (CRUD)

**Target:** nový `app/routers/personas.py`, nové `app/templates/personas/list.html`,
`app/templates/personas/form.html`, `app/main.py`, `app/templates/base.html`,
`app/i18n/en.json`, `app/i18n/de.json`

1. `app/routers/personas.py` — stejná kostra jako `app/routers/markets.py`:
   - `GET /personas` — list, sloupce: `label`, "Default" badge (`pill_badge` makro), počet
     běhů, které tuhle perzonu použily (pro delete-block info), tlačítka Edit/Delete.
   - `GET/POST /personas/new` — `label` (required, unikátní), `is_default` checkbox.
   - `GET/POST /personas/{id}/edit` — stejný formulář. Pokud submit zaškrtne `is_default` a
     řádek jím ještě není: v jedné transakci nejdřív `UPDATE personas SET is_default = FALSE
     WHERE is_default` (na aktuálním default řádku), pak nastav `is_default = TRUE` na editovaný
     řádek (design decision 4).
   - `POST /personas/{id}/delete` — blokovaný (strukturovaná inline chyba, ne raw error), pokud
     (a) řádek je `is_default` (chybová hláška: "nastav jinou perzonu jako výchozí první"), nebo
     (b) existuje `Run` s tímhle `persona_id` (stejný vzor jako `errors.market_in_use`, s
     počtem běhů).
   Docstring na každé route funkci; `description=...` na `Form(...)` polích.
2. `app/templates/personas/list.html`, `app/templates/personas/form.html` — reuse `text_field`,
   `checkbox_field`, `delete_button`, `pill_badge` makra z `partials/macros.html`, žádná nová
   markup od nuly.
3. `app/main.py` — zaregistrovat router (editor+admin gate, stejně jako `markets.router`).
4. `app/templates/base.html` — nav odkaz na `/personas`.
5. i18n (EN+DE, jeden commit) — `persona.*` klíče (list_title, create_title, edit_title,
   label_field, is_default_field, is_default_badge, delete_button/confirm),
   `errors.persona_not_found`, `errors.persona_label_conflict`, `errors.persona_is_default`,
   `errors.persona_in_use` (stejný `{count}` placeholder formát jako `errors.market_in_use`).

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči `/personas`: vidět seedovanou "person" perzonu s "Default" badge. Vytvořit
   "manager"/"politician"/"child" — uloží se. Nastavit "manager" jako default → "person" ztrácí
   badge, "manager" ho získá. Zkusit smazat aktuální default → zablokované se srozumitelnou
   chybou. Zkusit smazat perzonu bez použití v žádném běhu → funguje.
3. Ověřit na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(personas): add full CRUD admin screen with single-default enforcement
```

---

## CPH-T5 — Perzona v system-instruction šabloně + per-run override na `Run`

**Target:** nová migrace `alembic/versions/0022_run_persona_override.py`,
`app/models/run.py`, `app/routers/settings.py`, `app/routers/runs.py`,
`app/templates/prompts/detail.html` (run-trigger formulář), `app/i18n/en.json`,
`app/i18n/de.json`

1. Migrace 0022 — přesně stejná technika jako `0002_run_market_override.py`:
   - `ADD COLUMN persona_id INTEGER` (nullable).
   - `UPDATE runs SET persona_id = (SELECT id FROM personas WHERE is_default)`.
   - `ALTER COLUMN persona_id SET NOT NULL`.
   - `ADD FOREIGN KEY` + index.
2. `app/models/run.py` — `Run.persona_id: Mapped[int] = mapped_column(ForeignKey("personas.id"),
   nullable=False)`, `persona: Mapped["Persona"] = relationship()`. Docstring update (mirror
   `market_id`'s existing docstring paragraph, adjusted for persona).
3. `app/routers/settings.py`:
   - `DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE` — "The person asking..." → "The {persona} asking...".
   - `_DRY_RUN_VALUES` — přidat `"persona": "person"`.
4. `app/routers/runs.py`:
   - `_market_system_instruction` → přejmenovat na `_build_system_instruction`, nový parametr
     `persona: Persona`, `.format(..., persona=persona.label)`.
   - `trigger_run` — nový `persona_id: int = Form(..., description="Persona to frame the
     question as — defaults to the default persona but can be overridden per run.")`. Lookup +
     404 stejně jako `market_id`. `request_payload` dostává `"persona": persona.label`.
     `Run(...)` konstruktor dostává `persona_id=persona.id`.
5. Run-trigger formulář (`prompts/detail.html`) — nový `<select>` pro perzonu vedle marketu,
   `select_field` makro, přednastavený na defaultní perzonu (`is_default`).

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V prohlížeči: `/settings` — výchozí šablona teď obsahuje `{persona}`, uloží se beze změny
   validace.
3. Spustit běh na existujícím promptu s přepnutou perzonou na "manager" → run detail/
   `request_payload` ukazuje `"persona": "manager"`, odpověď providerovi obsahuje "The manager
   asking...". Spustit druhý běh se stejným promptem, jinou perzonou ("politician") →
   `request_payload` odlišný, oba běhy zůstávají v historii nezávisle.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): make system-instruction persona a per-run, admin-managed override
```

---

## CPH-T6 — Schema: OpenAI provider + seed modelů

**Target:** nová migrace `alembic/versions/0023_openai_provider_and_models.py`

1. Než píšeš INSERT: ověř proti aktuální OpenAI dokumentaci (platform.openai.com/docs/pricing)
   přesné `model_name` stringy pro Responses API, `context_window_tokens`, `max_output_tokens`
   a aktuální ceny — stejná disciplína jako `0009_anthropic_provider_and_model_columns.py`.
   Necituj z paměti/tréninkových dat.
2. `INSERT INTO providers (code, name) VALUES ('openai', 'OpenAI ChatGPT')`.
3. `INSERT INTO ai_models (...)` — 2–3 řádky (např. jeden flagship, jeden economy tier),
   `supports_web_search = TRUE`, `is_free = FALSE`, `notes` s odkazem na zdroj + datum ověření
   (stejný formát jako Anthropic seed notes).

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověřit: `providers` má `openai` řádek; `ai_models` má nové OpenAI řádky s reálnou (ne
   vymyšlenou) cenou.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add OpenAI provider + model rows
```

---

## CPH-T7 — OpenAI adaptér

**Target:** nový `app/adapters/openai.py`, `app/adapters/__init__.py`, `app/config.py`,
`.env.example`, `requirements.txt`

1. Než píšeš kód: ověř proti aktuální OpenAI API dokumentaci přesný tvar `web_search` toolu
   Responses API a jeho `user_location` parametru (country/city/region) — API se mezi verzemi
   mění, nepřebírej z paměti. Ověř i aktuální stabilní verzi `openai` Python SDK.
2. `requirements.txt` — přidat pinnutou verzi `openai`.
3. `app/config.py` — `Settings` dostává `openai_api_key: str = ""`.
4. `.env.example` — `OPENAI_API_KEY=` s komentářem (odkaz na platform.openai.com/api-keys),
   stejný formát jako `ANTHROPIC_API_KEY`.
5. `app/adapters/openai.py` — `OpenAIAdapter` implementující `ProviderAdapter.run()`
   (`app/adapters/base.py`):
   - Responses API (`client.responses.create`) s `web_search` toolem zapnutým.
   - `system_instruction` → `instructions` top-level pole.
   - `user_location` ve `web_search` tool configu, odvozený z `market_country` (ověřený shape).
   - Mapování `url_citation` anotací na `AdapterCitation` — `has_citations` explicitně `False`
     (ne jen prázdný seznam), stejná disciplína jako Gemini/Anthropic adaptér.
   - `raw_payload` = kompletní, netransformovaná SDK odpověď (FR-10).
   - Necháva SDK vyhazovat na transport/API chyby (stejný vzor jako ostatní dva adaptéry).
6. `app/adapters/__init__.py` — registrace `"openai": OpenAIAdapter` v `ADAPTERS`.

Po dokončení:
1. Reálný `.env` s `OPENAI_API_KEY`.
2. `docker compose up -d --build`
3. V prohlížeči: spustit běh proti OpenAI modelu na existujícím promptu → skutečný běh, žádný
   mock. Ověřit run detail: rendered text, raw JSON obsahuje OpenAI response shape, citace
   (pokud provider nějaké vrátil).
4. Vyvolat chybu (dočasně špatný `OPENAI_API_KEY`) → `Run.status == 'error'` se smysluplnou
   hláškou, ne surová 500.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(adapters): add OpenAI ChatGPT adapter with web_search geo-targeting
```

---

## CPH-T8 — Testy: rozšíření pytest sady

**Target:** `tests/conftest.py`, nový `tests/test_personas.py`, nový
`tests/test_ai_model_price_history.py`, úprava `tests/test_ai_models.py`,
`tests/test_runs.py`

1. `tests/conftest.py`:
   - `seed` fixture — přidat `openai` provider + jeden testovací model; `FakeAdapter`
     zaregistrovaný i pro `"openai"` (stejný vzor jako `google_gemini`/`anthropic`).
   - `seed` fixture — přidat seedovanou defaultní `Persona` (`label="person", is_default=True"`),
     stejně jako `Market`/`Provider` — bez ní by `sample_prompt`/`trigger_run` testy selhaly na
     chybějící `persona_id` FK.
2. `tests/test_personas.py` — create → list → edit → set-default (přepnutí `is_default` mezi
   dvěma perzonami) → delete zablokovaný na aktuálním default → delete zablokovaný na perzoně
   s existujícím `Run` → delete projde na nepoužité, ne-default perzoně.
3. `tests/test_ai_model_price_history.py` — vytvoření modelu zapíše první historický řádek;
   editace se změnou ceny přidá řádek; editace beze změny ceny žádný nový řádek nepřidá.
4. `tests/test_ai_models.py` — rozšířit o assert, že `price_history` je vidět na edit formuláři.
5. `tests/test_runs.py` — rozšířit o běh proti OpenAI modelu přes `FakeAdapter` (úspěch i
   chybová cesta, stejně jako dnešní Gemini/Anthropic testy); běh s explicitně přepnutou
   perzonou → `request_payload["persona"]` odpovídá zvolené perzoně.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover OpenAI provider path, persona CRUD/override, and price history
```

---

## CPH-T9 — Ochrana proti vícenásobnému spuštění runu (varianta A)

**Target:** `app/routers/runs.py` (`trigger_run`), `app/templates/prompts/detail.html`,
`app/errors.py` nebo přímo `AppError` volání, `app/i18n/en.json`, `app/i18n/de.json`, nový test
v `tests/test_runs.py`

Design decisions 13–14 — zůstat u synchronního requestu (varianta A), kontrola per `prompt_id`,
ne per model/market/persona kombinace. `trigger_run` už dnes ukládá `Run(status='pending')`
v samostatném commitu před voláním adaptéru — žádná schema změna potřeba, jen dotaz nad
existujícím stavem.

1. `app/routers/runs.py`, `trigger_run` — před vytvořením nového `Run` zkontroluj, jestli pro
   `prompt_id` už neexistuje `Run` se `status='pending'` (`select(func.count(Run.id)).where(
   Run.prompt_id == prompt_id, Run.status == 'pending')`, případně `select(Run.id)...limit(1)`
   pro existenci). Pokud ano → `AppError("run_already_pending", t("errors.run_already_pending"),
   status_code=409)` — žádný nový `Run` řádek, žádné volání adaptéru.
2. `app/templates/prompts/detail.html` — malý vanilla-JS blok (stejné umístění/vzor jako
   `confirm()` delegated listener v `base.html`): `submit` listener na run-trigger formuláři,
   okamžitě `disabled = true` na submit tlačítku a přepnutí textu na `t('prompt.trigger_run_
   pending')` ("Spouštím…"/"Running…"), ať k tomu dojde ještě před odpovědí serveru. Musí
   fungovat i při validation-error re-renderu stránky (formulář se znovu vykreslí s
   tlačítkem zpátky aktivním).
3. i18n (EN+DE, jeden commit) — `errors.run_already_pending`, `prompt.trigger_run_pending`.
4. `tests/test_runs.py` — nový test: vytvoř `Run(status='pending')` přímo přes ORM pro
   `sample_prompt`, pak zavolej `trigger_run` (POST) na stejný prompt → očekávej 409, žádný
   druhý `Run` řádek nevznikl. Druhý test: po dokončení běhu (status `success`/`error`, jak to
   dnešní testy už dělají) jde spustit další běh normálně (regrese by jinak natrvalo
   zablokovala prompt).

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči: vytvoř/najdi prompt, klikni "Run prompt" → tlačítko se okamžitě zablokuje a
   změní text, než dorazí odpověď. Zkus (např. přes druhou kartu/curl) spustit druhý běh na
   stejném promptu, zatímco první ještě běží → zablokované s chybou, ne druhý `Run` řádek.
3. Po dokončení prvního běhu spusť druhý normálně → projde bez problémů.
4. `pytest` — všechny testy zelené (staré i nové).
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): block triggering a second run while one is already pending
```

---

## Completion Checklist

- [x] `ai_model_price_history` existuje, zapisuje se jen při skutečné změně ceny, viditelná na
      `/ai-models/{id}/edit`
- [x] `personas` — plné CRUD, právě jeden `is_default`, delete blokovaný na default/in-use řádku
- [x] `runs.persona_id` — per-run override funguje, `request_payload` zaznamenává použitou
      perzonu, `DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE` používá `{persona}`
- [x] OpenAI provider + modely seedované s ověřenou (ne vymyšlenou) cenou
- [x] Reálný běh přes OpenAI model funguje end-to-end (rendered text, raw JSON, citace)
- [x] Chybová cesta u OpenAI běhu ukládá strukturovanou chybu, ne surovou 500
- [x] `pytest` sada zelená (139/139), pokrývá všechny tři featury
- [ ] Ochrana proti vícenásobnému spuštění runu (CPH-T9) — tlačítko se zablokuje, druhý běh na
      stejném promptu je odmítnutý, dokud první neskončí
- [ ] `docs/TASKS.md` — poznámka, že tahle branch existuje a co pokrývá (odkaz na tenhle soubor)
