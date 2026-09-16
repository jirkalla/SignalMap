# SignalMap — Tasks: Cost Components

## v1.2 | Září 2026
## Branch: feature/signalmap-cost-components
## Task ID prefix: CC

Status: navrženo v konverzaci (2026-09-15), rozpracováno do jednotlivých úkolů
(2026-09-16). Ops dashboard branch (`feature/signalmap-ops-dashboard`) je od
2026-09-16 smergnutá do master jako [PR #13](https://github.com/jirkalla/SignalMap/pull/13)
— podmínka "začít až po T4" (design decision 9) je splněná.

---

## Kontext a zjištění

Ops dashboard (T1, `docs/TASKS_OPS_DASHBOARD.md`) zavedl
`estimate_run_cost`/`run_cost_sql_expr` počítající cenu jen z input/output
tokenů. Při porovnání s reálnými konzolemi providerů (2026-09-15) vyšel
nesoulad:

- **Anthropic**: appka (jen lokální DB) viděla ~47 % skutečného provozu na
  účtu, a model `claude-sonnet-5` appka vůbec nespustila, přesto ho účet
  vykazoval.
- **OpenAI**: jediný den v konzoli převyšoval celou dosavadní historii
  appky.
- **Příčina**: appka od nedávna běží na **dvou oddělených databázích**
  (lokální PC + server) — každá session viděla jen svou polovinu provozu.
  Po sečtení lokální + server DB proti reálným konzolím providerů:
  - **OpenAI: přesná shoda** (699 329 in / 49 544 out / 66 search, do
    posledního tokenu).
  - **Anthropic: ~98 % shoda** (drobný zbytek pravděpodobně timing mezi
    měřeními), `claude-sonnet-5` sedí přesně (spouštěl se jen na serveru).
  - **Gemini: nesedí** (appka ukazuje ~6× víc než odhad z grafu, opačný
    směr než u zbylých dvou providerů) — čísla z grafu byla jen odhadnutá
    ze zaokrouhlených "k" hodnot, ne přesný export. **Nepovažovat za
    vyřešené.**
- **Závěr**: token data v appce jsou důvěryhodná (ověřeno na 2 ze 3
  providerů s přesností 98–100 %). Nesoulad ceny proti realitě není chyba
  ve sčítání tokenů, ale v tom, že `estimate_run_cost` počítá jen
  input/output cenu a ignoruje:
  1. **web search / grounding poplatky** (účtované samostatně, per-call,
     ne podle tokenů) — appka eviduje `search_queries` už teď, jen se
     nepoužívají ve výpočtu ceny.
  2. **cache tiery u Anthropicu** (cache read / cache write 5m / cache
     write 1h — každý s jinou cenou) — appka tahle pole už ukládá v
     `raw_responses.token_usage` (`cache_read_input_tokens`,
     `cache_creation.ephemeral_5m_input_tokens`,
     `cache_creation.ephemeral_1h_input_tokens`), jen je nikdo nečte.

---

## ⚠️ Schema flagy (AI_INSTRUCTIONS.md §4)

Tahle práce **mění schéma** — flagováno předem, jak §4 vyžaduje, protože
`schema_phase1.sql` (autoritativní) o tom zatím neví:

**Přibývá** (CC-1, migrace 0025):
- nová tabulka `ai_model_price_components` — není v `schema_phase1.sql`,
  je to fázově pozdější rozšíření cenového modelu (design decisions 1–4).

**Ubývá** (CC-8, migrace 0026, až na konci — viz design decision 12):
- `ai_models.cost_per_1k_input_usd`, `ai_models.cost_per_1k_output_usd` —
  **oba jsou v `schema_phase1.sql`** (řádky 69–70). Jejich zrušení je
  vědomá odchylka od phase-1 schématu, ne opomenutí.
- celá tabulka `ai_model_price_history` (zavedena migrací 0020, mimo
  `schema_phase1.sql`) — nahrazená verzováním uvnitř nové tabulky.

**Nedořešeno, potřebuje rozhodnutí uživatele (neřeš mlčky):** má se
`schema_phase1.sql` po CC-8 anotovat poznámkou, že tyhle dva sloupce už
neplatí, nebo zůstává jako historický záznam phase-1 stavu beze změny?
Viz Otevřené otázky.

**Beze změny zůstává**: `ai_models.is_free` (explicitní fakt, ne odvozený
z chybějící ceny — viz `AIModel` docstring), `raw_responses.token_usage`
(data pro nový výpočet už dnes obsahuje), `runs.search_queries`
(nepoužívá se, viz design decision 6).

---

## Design decisions

Body 1–10 odsouhlasené v konverzaci 2026-09-15/16. Body 11–14 přibyly
2026-09-16 při rozpisu úkolů do detailu, odsouhlaseny před psaním tohohle
dokumentu.

1. **Cenový model jako komponenty, ne pevné sloupce.** Místo
   `cost_per_1k_input_usd`/`cost_per_1k_output_usd` (2 čísla) bude cena
   modelu seznam komponent — `input`, `output`, `cache_read`,
   `cache_write` (jednotný tier — Gemini, OpenAI), `cache_write_5m`,
   `cache_write_1h` (Anthropic má dva tiery místo jednoho `cache_write`) —
   každý model/provider má jen ty komponenty, co reálně účtuje a co máme
   podložené reálným ceníkem (viz "Ceny podle ceníků providerů" níže).
   Stejný přístup jako zavedené LLM cost-tracking nástroje
   (Langfuse/Helicone-styl) — ověřená praxe, ne vlastní vynález.
2. **Normalizovaná tabulka, ne JSON blob.** Nová
   `ai_model_price_components` (id, ai_model_id FK, component_type,
   price_per_unit_usd, unit, effective_from, changed_by_user_id) — appka
   nad tím potřebuje SUM přes providery/modely (ops dashboard), JSON by se
   musel při každém dotazu rozbalovat ručně.
   **Revize 2026-09-16 (viz design decision 11)**: jednotka je
   `per_1m_tokens`, ne původně navržené `per_1k_tokens`.
3. **`component_type` jako pojmenovaná množina + CHECK constraint**,
   stejný vzor jako `User.ROLES`/`DomainClassification.DOMAIN_TYPES` —
   přidání nové komponenty (provider změní účtování) = jedna hodnota do
   tuple, žádná migrace schématu.
4. **Verzované v čase (`effective_from`), append-only** — stejný princip
   jako dnešní `ai_model_price_history`, jen rozšířený na komponenty. Run
   se vždy cení podle ceny platné v době runu, ne podle dnešní ceny.
5. **Čistý řez, ne souběh.** `ai_models.cost_per_1k_input_usd`/`output_usd`
   a stará `ai_model_price_history` se zruší (existující hodnoty se
   migrací přenesou do nové tabulky jako počáteční `input`/`output`
   řádky) — neběží obojí najednou. Padlo proto, že appka je v raném
   vývoji, kde je tahle změna levná; později by byla drahá.
   **Revize 2026-09-16 (viz design decision 12)**: čistý řez zůstává
   cílovým stavem, ale zahození staré struktury se odsouvá do posledního
   úkolu (CC-8), aby appka mezi jednotlivými úkoly pořád nastartovala.
6. **Search/tool-call poplatek — odloženo, mimo scope** (revidováno
   2026-09-16). Původně plánováno jako vlastní komponenta, ale: (a) u
   Anthropicu a OpenAI přesnou cenu za search nemáme (ceníky, co jsme
   dostali, ji neuvádí), (b) Gemini grounding má sdílený free-tier pool
   (5 000 dotazů/měsíc napříč všemi Gemini 3.x modely účtu, pak $14/1000)
   — to je účtované na úrovni **celého účtu za měsíc**, ne per-model
   cena, kterou by šlo vynásobit `search_queries` count jednoho runu bez
   znalosti, kolik z měsíčního poolu už bylo vyčerpáno. Rozhodnutí:
   plánovat jen s tím, co máme podložené — search fee jako samostatný
   budoucí task (CC-9), ne blokovat na něm zbytek.
7. **Tokeny se v UI ukazují vždy vedle ceny, nikdy místo ní.** Tokeny jsou
   tvrdá data přímo od providera, cena je jen tak přesná, jak přesná je
   nakonfigurovaná cenová tabulka. Cena zůstává vždy jasně označená jako
   odhad, nikdy jako přesná účetní hodnota.
8. **Vlastní branch `feature/signalmap-cost-components`, dokument
   samostatný.** `docs/TASKS_OPS_DASHBOARD.md` má vlastní scope (ops
   dashboard T0–T4) a nemá se mísit s jinou doménou (cenový model) —
   branch a dokument jsou nezávislé osy. *(Původní znění počítalo se
   sdílenou branch `feature/signalmap-ops-dashboard`, protože tam
   `cost.py` vzniklo; ta je mezitím smergnutá jako PR #13, takže tahle
   práce dostává vlastní branch — rozhodnuto 2026-09-16.)* Branch zakládá
   uživatel, ne agent (AI_INSTRUCTIONS.md §4/§9).
9. **Pořadí: až po T4 ops dashboardu.** ✅ Splněno — `docs/TASKS_OPS_DASHBOARD.md`
   T0–T4 hotové, branch smergnutá jako PR #13 (2026-09-16).
10. **Dvě věci zůstávají mimo dosah komponentního modelu, natrvalo, ne jen
   pro tuhle práci** (zjištěno z reálných ceníků 2026-09-16):
   - **Gemini cache storage price** ($1 / 1M tokenů / hodinu) — účtuje se
     podle času, co je cache držená živá, ne podle tokenů spotřebovaných
     v jednom runu. Appka cení runy jednotlivě (`estimate_run_cost` per
     run) — časově založená infrastrukturní cena se do toho modelu
     nevejde vůbec, ne jen "zatím ne". Nepočítat, nezmiňovat v UI jako
     "chybějící cenu", je to jiná kategorie nákladu.
   - **OpenAI short/long context tier** — ceny se zdvojnásobí nad určitou
     délkou kontextu (přesný práh neznámý z dostupného ceníku). Používáme
     jen "short context" ceny (to už appka nastavené má a sedí to na
     typický provoz) — pokud by šlo o systematické podhodnocení u dlouhých
     promptů, řešit jako samostatný task, až/jestli se ukáže, že na tom
     reálně záleží.
11. **Cena se ukládá za 1M tokenů v `NUMERIC(12,6)`, ne za 1k v
    `NUMERIC(10,5)`** (přidáno 2026-09-16, reviduje design decision 2).
    Dnešní sloupce mají 5 desetinných míst a drží cenu za 1k tokenů —
    Gemini 3.1 flash-lite cache read ($0.025 / 1M) vychází na `0.000025`
    za 1k, což `_parse_price` (`app/routers/ai_models.py`) zaokrouhlí
    ROUND_HALF_UP na `0.00003`, **tedy +20 % chyba hned u první nové
    komponenty**. Per-1M jednotka je zároveň ta, kterou provideři
    publikují a kterou admin reálně píše do formuláře (dnešní
    `cost_per_million_*_usd` pole se přepočítávají tam a zpět na třech
    místech: router, `cost_badge` makro, price-history tabulka) — uložením
    v per-1M se ty přepočty ruší, ne přidávají.
12. **Stará struktura se zahodí až v posledním úkolu (CC-8), ne v CC-1**
    (přidáno 2026-09-16, reviduje *pořadí* v design decision 5, ne její
    cíl). Kdyby migrace 0025 rovnou zahodila `cost_per_1k_*_usd`, appka
    by byla nespustitelná mezi CC-1 a CC-4 — a pravidlo "každý úkol končí
    tím, že appka nastartuje a jde ověřit v prohlížeči" by přestalo
    platit přesně tam, kde je nejvíc potřeba (změna schématu + přepis
    výpočtu ceny). Mezistav je vědomě "nová tabulka existuje, staré
    sloupce ještě žijí, ale už je nikdo nečte" — ne trvalý souběh dvou
    cenových modelů, což je to, co design decision 5 zakazuje.
13. **Cached tokeny se u každého providera počítají jinak — musí to být
    explicitní pravidlo, ne implicitní** (přidáno 2026-09-16, ověřeno
    proti tvaru `token_usage`, který adaptéry ukládají):
    - **Anthropic**: `input_tokens` cache tokeny **neobsahuje** — jsou
      vedle v `cache_read_input_tokens` / `cache_creation.*`.
    - **OpenAI**: `input_tokens` cached tokeny **obsahuje**
      (`input_tokens_details.cached_tokens` je jejich podmnožina).
    - **Gemini**: `prompt_token_count` cached tokeny **obsahuje**
      (`cached_content_token_count` je jejich podmnožina).

    Bez tohohle rozlišení by se u OpenAI a Gemini cached tokeny naúčtovaly
    **dvakrát** — jednou plnou input cenou, podruhé cache cenou. Proto
    `cost.py` dostane per-provider deklaraci tvaru usage payloadu
    (`TOKEN_USAGE_SHAPES`, CC-4) s explicitním příznakem
    `input_includes_cache_read`, ne jen seznam názvů klíčů jako dnešní
    `TOKEN_COUNT_KEY_PAIRS`.
14. **Chybějící cena komponenty u nenulového počtu tokenů → `None`, nikdy
    podhodnocená cena** (přidáno 2026-09-16). Když run má např. 12 000
    cache-read tokenů a model nemá `cache_read` komponentu nastavenou,
    `estimate_run_cost` vrátí `None` ("neznámá cena"), ne cenu spočítanou
    jen z input/output. Stejná disciplína jako dnešní chování při
    chybějící input/output ceně a jako `has_citations`/FR-13 — chybějící
    údaj se nikdy nemaskuje jako nula. Podhodnocená cena je přesně ten
    problém, kvůli kterému celá tahle práce vznikla; nahradit jedno tiché
    podhodnocení druhým by byl krok stranou, ne dopředu. Po CC-3 (zadané
    reálné ceny pro všech 8 modelů) se to reálně projeví jen u nově
    přidaného modelu, kterému ještě nikdo ceny nevyplnil — tam je "—"
    správná odpověď.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| CC-1 | Migrace 0025: `ai_model_price_components` + backfill ze staré historie | ⏳ |
| CC-2 | Model vrstva: `AIModelPriceComponent` + resolver cen platných v čase | ⏳ |
| CC-3 | Admin UI pro správu komponent (`/ai-models`) + zadání reálných cen pro 8 modelů | ⏳ |
| CC-4 | `cost.py`: přepis výpočtu na komponenty (Python i SQL twin) | ⏳ |
| CC-5 | `ops_dashboard.py`: agregace tokenů vedle ceny | ⏳ |
| CC-6 | `ops/index.html`: tokeny v UI vedle ceny | ⏳ |
| CC-7 | Testy: komponentní ceny, verzování v čase, agregace tokenů | ⏳ |
| CC-8 | Migrace 0026: zahodit staré sloupce a `ai_model_price_history` (čistý řez) | ⏳ |
| CC-9 | *(budoucí, mimo tenhle dokument)* Search/tool-call poplatek — až budou ceny Anthropic/OpenAI a řešení Gemini free-tier poolu | 💭 |

Pořadí je vynucené a nedá se přeházet:
CC-1 (tabulka + data) → CC-2 (ORM + resolver nad ní) → **CC-3 (UI + reálné
ceny zadané dřív, než na nich začne stát výpočet)** → CC-4 (výpočet) →
CC-5 (agregace) → CC-6 (UI) → CC-7 (testy nad hotovým celkem) → CC-8
(zahození staré struktury, až ji nikdo nečte).

CC-3 před CC-4 je záměr: kdyby se výpočet přepnul na komponenty dřív, než
jsou ceny zadané, ops dashboard by ukázal "—" u všech runů (design
decision 14) a nebylo by jak poznat chybu v kódu od chybějících dat.

*Poznámka k původnímu číslování*: v draftu tohohle dokumentu byl "search
fee" jako CC-8. Přečíslován na CC-9, protože CC-8 teď zabírá zahození
staré struktury (design decision 12).

---

## CC-1 — Migrace 0025: `ai_model_price_components` + backfill

**Target:** nový `alembic/versions/0025_ai_model_price_components.py`

Design decisions 2, 3, 4, 11, 12. Čistě aditivní — `ai_models.cost_per_1k_*_usd`
i `ai_model_price_history` zůstávají nedotčené a appka je pořád čte
(zahození je až CC-8).

1. `upgrade()` — `op.create_table("ai_model_price_components", ...)`:

   | Sloupec | Typ | Poznámka |
   |---|---|---|
   | `id` | Integer PK | |
   | `ai_model_id` | Integer FK `ai_models.id` ON DELETE CASCADE, NOT NULL | |
   | `component_type` | VARCHAR(30) NOT NULL | CHECK: `input`, `output`, `cache_read`, `cache_write`, `cache_write_5m`, `cache_write_1h` |
   | `price_per_unit_usd` | NUMERIC(12,6) NOT NULL | cena za jednotku v USD, nikdy NULL — chybějící cena = chybějící řádek (design decision 14) |
   | `unit` | VARCHAR(20) NOT NULL, server_default `'per_1m_tokens'` | CHECK: `per_1m_tokens`, `per_call` |
   | `effective_from` | TIMESTAMPTZ NOT NULL, server_default `now()` | |
   | `changed_by_user_id` | Integer FK `users.id`, NULL | |

   `unit = 'per_call'` se v CC-1..CC-8 **nikde nepoužije** — je v CHECKu
   předem kvůli CC-9 (search fee je per-call, ne per-token), aby ta
   budoucí práce nebyla migrace schématu. Zmínit to v docstringu migrace,
   ať je jasné, že to není mrtvý kód omylem.
2. Index `idx_price_components_lookup` na `(ai_model_id, component_type,
   effective_from DESC)` — přesně ten tvar, na který se ptá resolver
   (CC-2) i korelovaný subquery v SQL twinu (CC-4): "nejnovější cena téhle
   komponenty tohohle modelu platná k datu runu".
3. **Backfill celé historie, ne jen dnešních cen** — pro každý řádek
   `ai_model_price_history` s nenulovou cenou vznikne až 2 komponentní
   řádky (`input`, `output`), se **stejným `effective_from` i
   `changed_by_user_id`**, cena přepočtená z per-1k na per-1M (`* 1000`):
   ```sql
   INSERT INTO ai_model_price_components
       (ai_model_id, component_type, price_per_unit_usd, unit, effective_from, changed_by_user_id)
   SELECT ai_model_id, 'input', cost_per_1k_input_usd * 1000, 'per_1m_tokens', effective_from, changed_by_user_id
   FROM ai_model_price_history WHERE cost_per_1k_input_usd IS NOT NULL;
   -- totéž pro 'output' / cost_per_1k_output_usd
   ```
   Historie cen se tím neztrácí — verzování v čase (design decision 4) má
   od začátku reálná data, ne jen "všechno platí od dneška".
4. **Pojistka na rozjetí staré struktury**: pro každý model, jehož dnešní
   `ai_models.cost_per_1k_*_usd` se **neshoduje** s nejnovějším
   historickým řádkem (nebo který žádný historický řádek nemá), vložit
   navíc řádek s dnešní cenou a `effective_from = ai_models.updated_at`.
   Dnešní router (`_record_price_history`) drží obojí v sync, takže by
   tohle nemělo najít nic — ale migrace, která se spustí na dvou různých
   databázích (lokální PC + server, viz Kontext), si tuhle kontrolu
   zaslouží radši mít.
5. `downgrade()` — drop indexu + drop tabulky. Nic víc; stará struktura
   pořád existuje, takže downgrade je bezztrátový.

**Done when:** `alembic upgrade head` proběhne na lokální i serverové DB;
`SELECT component_type, count(*) FROM ai_model_price_components GROUP BY 1`
vrátí `input`/`output` v počtu odpovídajícím nenulovým cenám v
`ai_model_price_history`; appka nastartuje a `/ai-models` se chová **přesně
jako dřív** (novou tabulku zatím nikdo nečte).

Po dokončení:
1. `docker compose up -d --build` — appka nastartuje bez chyby
2. `psql` (nebo `docker compose exec db psql`) — ověřit počty řádků a že
   `price_per_unit_usd` u Gemini flash-lite inputu je `0.250000`, ne
   `0.000250` (kontrola, že přepočet per-1k → per-1M šel správným směrem)
3. `pytest` — všechny testy zelené beze změny (nic z nich novou tabulku nezná)
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(infra): add ai_model_price_components with a full price-history backfill
```

---

## CC-2 — Model vrstva: `AIModelPriceComponent` + resolver cen platných v čase

**Target:** `app/models/provider.py`, `app/models/__init__.py`, `app/services/cost.py`

Design decisions 3, 4, 11. Prerekvizita: CC-1. **Jen ORM + resolver — samotný
výpočet ceny se nemění, ten je CC-4.**

1. `app/models/provider.py` — třída `AIModelPriceComponent`:
   - `COMPONENT_TYPES = ("input", "output", "cache_read", "cache_write",
     "cache_write_5m", "cache_write_1h")` a `UNITS = ("per_1m_tokens",
     "per_call")` jako class-level tuples — stejný vzor jako `User.ROLES`
     / `DomainClassification.DOMAIN_TYPES` (design decision 3).
   - `__table_args__` se **zrcadlenými `CheckConstraint`y** (`component_type
     IN (...)`, `unit IN (...)`) a indexem. Tohle není volitelné: testy
     staví schéma přes `Base.metadata.create_all`, ne Alembicem, takže
     constraint, který žije jen v migraci, by v testech neexistoval —
     přesně ta past, na kterou narazila migrace 0024
     (`idx_runs_one_pending_per_prompt_model`, viz
     `docs/TASKS_BULK_IMPORT_MULTI_MODEL.md`, code-review kolo 1).
   - Docstring: append-only, nikdy se needituje ani nemaže; "žádný řádek" =
     "cenu neznáme" (ne "je nulová") — design decision 14.
2. `AIModel.price_components` relationship — `order_by="AIModelPriceComponent.effective_from.desc()"`,
   `cascade="all, delete-orphan"`, `back_populates`. `AIModel.price_history`
   (stará) zůstává, dokud ji CC-8 nezruší.
3. `app/models/__init__.py` — export `AIModelPriceComponent`.
4. `app/services/cost.py` — nové funkce, všechny bez vedlejších efektů:
   - `load_price_components(db, model_ids) -> dict[int, list[AIModelPriceComponent]]`
     — **jeden** dotaz přes všechny modely najednou, seřazený
     `effective_from DESC`. Ne N+1 dotaz na model (stejná disciplína jako
     `_model_rows` v `app/routers/ai_models.py`, které si run counts tahá
     jedním `GROUP BY` místo dotazu na řádek).
   - `prices_at(components, at: datetime) -> dict[str, Decimal]` — pro
     každý `component_type` vybere **nejnovější řádek s
     `effective_from <= at`**; komponenta bez takového řádku ve výsledku
     prostě není. Čistá funkce nad už načteným seznamem, testovatelná bez
     DB (stejně jako dnešní `estimate_run_cost`, viz hlavička
     `tests/test_cost.py`).
   - `current_prices(db, model_ids) -> dict[int, dict[str, Decimal]]`
     = `prices_at(..., now(UTC))` pro každý model — tohle potřebuje admin
     UI (CC-3) k zobrazení dnešních cen v seznamu modelů.

**Done when:** `pytest` zelený beze změny chování; jde načíst komponenty
modelu a `prices_at` vrátí pro minulé datum jinou cenu než pro dnešek
(pokud model má víc než jednu verzi ceny z backfillu).

Po dokončení:
1. `docker compose up -d --build` — appka nastartuje bez chyby
2. `pytest` — všechny testy zelené (nic se zatím nepřepojilo, jen přibylo)
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ai-models): add AIModelPriceComponent model and time-aware price resolution
```

---

## CC-3 — Admin UI pro správu komponent + zadání reálných cen

**Target:** `app/routers/ai_models.py`, `app/templates/ai_models/form.html`,
`app/templates/ai_models/list.html`, `app/templates/partials/macros.html`,
`app/routers/prompts.py`, `app/i18n/en.json`, `app/i18n/de.json`

Design decisions 1, 4, 11, 14. Prerekvizita: CC-2. **Pořád se zapisuje i do
staré struktury** (`_record_price_history` + `cost_per_1k_*_usd`) — aby
`estimate_run_cost`, který se přepíná až v CC-4, mezitím nepřestal fungovat.

1. **Formulář** (`ai_models/form.html`) — dnešní dvě pole
   (`cost_per_million_input_usd`/`output_usd`) nahradit **šesti** volitelnými
   poli, jedno na `component_type`, všechna v USD za 1M tokenů:
   `input`, `output`, `cache_read`, `cache_write`, `cache_write_5m`,
   `cache_write_1h`. Prázdné pole = "tuhle komponentu neúčtujeme / cenu
   neznáme" (žádný řádek — design decision 14). Reuse `text_field` makra,
   grid 2 sloupce jako dnes, hint pod blokem vysvětlí, že Anthropic má dva
   write tiery a ostatní provideři jeden.
2. **Parsování a validace** — `_parse_component_price(raw, t)` nahradí
   dnešní `_parse_price`: `Decimal(raw)` **bez dělení tisícem** (ukládáme
   per-1M, design decision 11), quantize na 6 desetinných míst, rozsah
   `0 <= value < 1_000_000`, chyba přes existující
   `errors.ai_model_invalid_price` / `errors.ai_model_price_out_of_range`.
3. **Zápis komponent — append-only, jen změněné komponenty.** Po validaci
   porovnat zadané hodnoty s dnes platnými (`prices_at(..., now)`) a
   vložit nový `AIModelPriceComponent` řádek **jen pro ty, co se reálně
   změnily** — stejná logika jako dnešní `price_changed` v
   `update_ai_model`, jen per komponenta. Editace `notes` nikdy nesmí
   založit nový cenový řádek.
4. **Vymazání už nastavené komponenty = validační chyba**, ne tiché
   ponechání staré ceny. Append-only tabulka neumí vyjádřit "od teď se
   neúčtuje" jinak než novým řádkem, a prázdné pole je nerozlišitelné od
   překlepu. Nová hláška
   (`errors.ai_model_price_component_cannot_be_cleared`) řekne: "pokud
   provider přestal účtovat, zadej 0". *(Alternativa — zapsat při vymazání
   řádek s cenou 0 — je záměrně nezvolená: `0` znamená "ověřeně zdarma",
   což je jiný fakt než "už nevíme", a appka nemá jak poznat, který z nich
   admin myslel.)*
5. **Historie cen** v edit formuláři — `_price_history_rows` přepsat nad
   komponentami: řádky seřazené `effective_from DESC`, sloupce
   *Platí od / Platí do / Komponenta / Cena*, `valid_until` počítané
   **v rámci jednoho `component_type`** (ne napříč všemi — jinak by změna
   ceny `input` vypadala jako konec platnosti ceny `cache_read`). Stará
   `price_history` sekce se ze šablony odstraní (tabulka sama padne v CC-8).
6. **`cost_badge` makro** (`partials/macros.html`) — dnes čte
   `model.cost_per_1k_*_usd` přímo. Přepsat na signaturu
   `cost_badge(model, prices, free_label, per_million_suffix)`, kde
   `prices` je dict z `current_prices`. Volající:
   - `ai_models/list.html` — `_model_rows(db)` doplní prices do trojice
     `(model, run_count, prices)`,
   - `prompts/detail.html` — `_runnable_model_groups`
     (`app/routers/prompts.py:33-55`) doplní prices ke každému modelu.

   Badge ukazuje dál `input / output per 1M`; cache komponenty se do badge
   necpou (nevejdou se a pro výběr modelu při spuštění runu nejsou to
   hlavní) — ty jsou vidět na `/ai-models` edit formuláři.
7. **i18n (EN+DE, jeden commit)** — nové klíče:
   `ai_model.cost_component_input_label`, `..._output_label`,
   `..._cache_read_label`, `..._cache_write_label`, `..._cache_write_5m_label`,
   `..._cache_write_1h_label`, `ai_model.cost_components_hint`,
   `ai_model.price_history_component_label`,
   `errors.ai_model_price_component_cannot_be_cleared`.
8. **Zadání reálných cen pro všech 8 aktivních modelů** podle tabulky
   "Ceny podle ceníků providerů" níže — ručně přes `/ai-models` UI (ne
   migrací: jsou to provozní data, ne schéma, a zároveň je to nejlepší
   ověření, že formulář z kroků 1–4 reálně funguje). Gemini modely dostanou
   `cache_read` bez `cache_write` (jejich ceník cache write neuvádí
   samostatně), OpenAI `cache_read` + `cache_write`, Anthropic `cache_read`
   + `cache_write_5m` + `cache_write_1h`.

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči: `/ai-models` → edit každého z 8 modelů → zadat ceny podle
   tabulky → uložit → znovu otevřít: hodnoty sedí **na desetinu centu**
   (hlavně Gemini flash-lite `cache_read` = `0.025`, což je ta hodnota,
   která se ve starém `NUMERIC(10,5)` per-1k schématu zaokrouhlila o 20 %
   nahoru — design decision 11)
3. Editovat jen `notes` → uložit → historie cen **nepřibude** o nový řádek
4. Změnit jednu komponentu → historie ukáže nový řádek jen pro ni, ostatní
   komponenty si drží své původní `effective_from`
5. Vymazat už vyplněnou komponentu → validační chyba s hláškou, nic se neuloží
6. `/prompts/{id}` — cost badge u modelů pořád ukazuje input/output cenu
7. Ověřit na ~640px / ~1024px / desktop šířce (6 cenových polí nesmí
   rozbít layout formuláře na mobilu)
8. `pytest` — všechny testy zelené (testy na starou price history ještě
   platí, ta se ruší až v CC-8)
9. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ai-models): manage per-component model prices in the admin UI
```

---

## CC-4 — `cost.py`: přepis výpočtu na komponenty

**Target:** `app/services/cost.py`, `app/services/ops_dashboard.py`,
`tests/test_cost.py`, `tests/test_ops_dashboard.py`

Design decisions 1, 4, 13, 14. Prerekvizita: CC-3 hotový **včetně zadaných
reálných cen** — jinak se nedá odlišit chyba ve výpočtu od chybějících dat.

1. **Nejdřív ověřit skutečný tvar dat**, ne psát podle dokumentace
   providerů (stejná disciplína, jaká odhalila Gemini
   `prompt_token_count` při T1 ops dashboardu):
   ```sql
   SELECT p.code, jsonb_object_keys(rr.token_usage) AS key, count(*)
   FROM raw_responses rr
   JOIN runs r ON r.id = rr.run_id
   JOIN ai_models m ON m.id = r.model_id
   JOIN providers p ON p.id = m.provider_id
   GROUP BY 1, 2 ORDER BY 1, 2;
   ```
   Výsledek zapsat do docstringu `TOKEN_USAGE_SHAPES` — je to jediný
   doklad, proč tam ty klíče jsou.
2. **`TOKEN_USAGE_SHAPES` nahradí `TOKEN_COUNT_KEY_PAIRS`** — frozen
   dataclass na tvar usage payloadu jednoho providera:
   `input_key`, `output_key`, `cache_read_path: tuple[str, ...] | None`,
   `cache_write_paths: dict[str, tuple[str, ...]]` (component_type → cesta
   v JSONu), `input_includes_cache_read: bool` (design decision 13).
   Očekávané tvary:

   | Provider | input / output | cache read | cache write | input obsahuje cache read? |
   |---|---|---|---|---|
   | Anthropic | `input_tokens` / `output_tokens` | `cache_read_input_tokens` | `cache_creation.ephemeral_5m_input_tokens` → `cache_write_5m`, `cache_creation.ephemeral_1h_input_tokens` → `cache_write_1h` | **ne** |
   | OpenAI | `input_tokens` / `output_tokens` | `input_tokens_details.cached_tokens` | *(neúčtuje se samostatně v reportu)* | **ano** |
   | Gemini | `prompt_token_count` / `candidates_token_count` | `cached_content_token_count` | *(neuvádí se)* | **ano** |

3. **Výběr tvaru — podle rozlišujícího klíče, ne podle providera na
   modelu.** Anthropic i OpenAI sdílejí `input_tokens`/`output_tokens`, což
   dnešnímu kódu nevadí (cena se počítala stejně), ale nově vadí (jiný
   `input_includes_cache_read`). Pořadí kontrol:
   1. `prompt_token_count` v payloadu → Gemini
   2. `input_tokens_details` → OpenAI
   3. `cache_creation` nebo `cache_read_input_tokens` → Anthropic
   4. jen `input_tokens` + `output_tokens` → sdílený "plain" tvar bez cache
      komponent (nejednoznačné Anthropic/OpenAI, ale bez cache tokenů je
      ten rozdíl bezpředmětný — jediné, čím se ty dva tvary liší, je
      zacházení s cache)

   Důvod, proč ne `model.provider.code`: `estimate_run_cost` je dnes čistá
   funkce nad `(token_usage, model)` bez přístupu k relacím (viz hlavička
   `tests/test_cost.py` — testy staví `AIModel` bez session), a SQL twin by
   musel navíc joinovat `providers` do každé agregace.
4. **`estimate_run_cost(token_usage, model, prices: dict[str, Decimal]) -> float | None`**:
   - `model.is_free` → `0.0` (beze změny).
   - Chybí `token_usage` / nesedí žádný tvar / chybí cena `input` nebo
     `output` → `None` (beze změny).
   - `billable_input = input_tokens - cache_read_tokens` když
     `shape.input_includes_cache_read`, jinak `input_tokens`; ošetřit
     `max(0, ...)` (defenzivně proti nekonzistentnímu payloadu).
   - Cena = `billable_input/1e6 * prices["input"] + output/1e6 * prices["output"]`
     `+ cache_read/1e6 * prices["cache_read"] + Σ write komponenty`.
   - **Nenulový počet tokenů komponenty bez ceny → `None`** (design
     decision 14). Nulový počet bez ceny → nevadí, komponenta se přeskočí.
   - `round(cost, 6)`.
5. **SQL twin `run_cost_sql_expr(token_usage_col, model_id_col,
   started_at_col, is_free_col)`** — cena platná **v čase runu**
   (`Run.started_at`), ne dnešní. Pro každou komponentu korelovaný skalární
   subquery:
   ```sql
   (SELECT c.price_per_unit_usd FROM ai_model_price_components c
     WHERE c.ai_model_id = runs.model_id AND c.component_type = :ct
       AND c.effective_from <= runs.started_at
     ORDER BY c.effective_from DESC LIMIT 1)
   ```
   — přesně ten tvar, na který míří `idx_price_components_lookup` (CC-1).
   Token osy stavět `COALESCE` napříč tvary z `TOKEN_USAGE_SHAPES`, jako
   dnes — sdílená konstanta zůstává jediným zdrojem pravdy pro obě
   implementace, aby se nemohly rozejít (dnešní docstring to říká o
   `TOKEN_COUNT_KEY_PAIRS`, platí to dál).
   `CASE`: `is_free → 0.0`; input/output tokeny nebo jejich cena `NULL` →
   `NULL`; nenulové cache tokeny bez ceny → `NULL` (stejné pravidlo jako
   Python verze).
6. **`_cost_expr()`** (`app/services/ops_dashboard.py`) přepojit na nové
   argumenty (`RawResponse.token_usage`, `Run.model_id`, `Run.started_at`,
   `AIModel.is_free`). Zůstává jediným místem, kde se twin napojuje na
   reálné sloupce.
7. **`recent_runs`** (`ops_dashboard.py:521+`) — dnes volá `estimate_run_cost`
   v Pythonu nad ~15 řádky. Doplnit načtení komponent pro modely těch runů
   jedním `load_price_components` (CC-2) a per řádek `prices_at(...,
   row.Run.started_at)`. Ne dotaz na řádek.
8. **Stávající testy** (`tests/test_cost.py`, `tests/test_ops_dashboard.py`)
   upravit na novou signaturu, aby sada zůstala zelená — **nové** pokrytí
   (verzování v čase, dvojité účtování, chybějící komponenty) přidává až
   CC-7.

Po dokončení:
1. `docker compose up -d --build`
2. `/ops` v prohlížeči: celková cena je **vyšší** než před CC-4 u runů s
   cache tokeny a **stejná** u runů bez nich; žádný run nespadl na "—"
   (pokud ano, chybí cena komponenty → zkontrolovat CC-3 data, ne kód)
3. `pytest` — všechny testy zelené
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
refactor(ops): price runs from cost components effective at run time
```

---

## CC-5 — `ops_dashboard.py`: agregace tokenů vedle ceny

**Target:** `app/services/cost.py`, `app/services/ops_dashboard.py`,
`app/routers/ops_dashboard.py`

Design decision 7 (tokeny vedle ceny, nikdy místo ní). Prerekvizita: CC-4.

1. `app/services/cost.py` — `run_token_sql_expr(token_usage_col, axis)` pro
   osy `input`, `output`, `cache_read`, `cache_write` (součet všech write
   tierů). `COALESCE` napříč `TOKEN_USAGE_SHAPES` stejně jako cost twin;
   chybějící hodnota → `0`, ne `NULL` (na rozdíl od ceny: "kolik tokenů"
   je vždy odpověditelné, zatímco "kolik to stálo" ne).
   **Pozor**: `input` osa vrací **surový** `input_tokens`, ne
   `billable_input` — v UI se ukazují tokeny tak, jak je vykázal provider
   (design decision 7: tvrdá data), odečtení cached tokenů je věc *ceny*,
   ne *zobrazení*.
2. `OpsSummary` (+ `ops_summary`) — nová pole `total_input_tokens`,
   `total_output_tokens`, `total_cache_read_tokens`,
   `total_cache_write_tokens` (`int | None`, `None` jen když v rozsahu
   není žádný run s `raw_responses` řádkem).
3. `ProviderCostRow` (+ `provider_cost_rows`) — `total_input_tokens`,
   `total_output_tokens`.
4. `RecentRunRow` (+ `recent_runs`) — `input_tokens`, `output_tokens` per
   run (Python strana, bounded fetch, jako cena).
5. `app/routers/ops_dashboard.py` — zrcadlit nová pole v
   `OpsSummaryResponse`, `OpsProviderRow` a recent-run response modelech,
   všechna s `Field(..., description=...)` (AI_INSTRUCTIONS §3).

Po dokončení:
1. `docker compose up -d --build`
2. `curl` / prohlížeč na `/ops/api/summary` a `/ops/api/providers` — token
   pole jsou v odpovědi a nejsou nulová
3. Namátkou ověřit jeden provider proti konzoli providera (tokeny, ne cenu)
4. `pytest` — všechny testy zelené
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ops): aggregate token counts alongside estimated cost
```

---

## CC-6 — `ops/index.html`: tokeny v UI vedle ceny

**Target:** `app/templates/ops/index.html`, `app/i18n/en.json`, `app/i18n/de.json`

Design decision 7. Prerekvizita: CC-5.

1. **KPI dlaždice "Tokens"** vedle dlaždice ceny — `in / out` ve zkráceném
   tvaru (`fmtTokens`: `1.2M` / `847k` / `312`), podtitulek s cache podílem,
   když je nenulový.
2. **Dlaždice ceny** — podtitulek musí explicitně říct *odhad podle
   nakonfigurovaného ceníku*, ne jen "cost" (design decision 7: cena je
   vždy označená jako odhad, nikdy jako účetní hodnota). Upravit
   `ops.kpi_cost_sub` v obou jazycích, pokud to dnešní text neříká
   dostatečně jasně.
3. **Tabulka providerů** — sloupec tokenů (in / out) vedle sloupce ceny.
4. **Tabulky posledních runů** (prompt-detail i user-detail) — sloupec
   tokenů vedle ceny.
5. `fmtTokens(n)` helper v Vue ostrůvku vedle existujícího `fmtCost`,
   `null` → `noData` (stejný vzor).
6. i18n (EN+DE, jeden commit): `ops.kpi_tokens`, `ops.kpi_tokens_sub`,
   `ops.col_tokens`, případná úprava `ops.kpi_cost_sub`.

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči `/ops`: tokeny vidět na globální úrovni, u providerů i u
   jednotlivých runů; nikde tokeny **nenahradily** cenu, vždy stojí vedle ní
3. Ověřit na ~640px / ~1024px / desktop šířce (nový sloupec nesmí rozbít
   tabulky na mobilu — ops tabulky jsou dnes nejširší obrazovka appky)
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ops): show token counts next to the estimated cost
```

---

## CC-7 — Testy

**Target:** `tests/test_cost.py`, `tests/test_ops_dashboard.py`,
nový `tests/test_ai_model_price_components.py`

Prerekvizita: CC-6. Pokrývá to, co CC-4 udělal minimálně (jen udržel sadu
zelenou) — tohle je plné pokrytí nového chování.

1. **`tests/test_cost.py`** — komponentní výpočet:
   - Anthropic tvar s cache read + oběma write tiery → cena = součet všech
     komponent, ověřená ručně spočítaným číslem.
   - **OpenAI a Gemini: cached tokeny se nenaúčtují dvakrát** (design
     decision 13) — run, kde `input_tokens = 10 000` a
     `cached_tokens = 4 000`, se musí naúčtovat jako 6 000 plnou cenou +
     4 000 cache cenou. **Tohle je nejdůležitější test celé sady** — přesně
     tenhle rozdíl je to, co komponentní model může pokazit tiše.
   - Anthropic: `input_tokens` se **neponižuje** o cache read.
   - Nenulové cache tokeny bez ceny komponenty → `None` (design decision 14).
   - Nulové cache tokeny bez ceny komponenty → cena se spočítá normálně.
   - `is_free` → `0.0` bez ohledu na komponenty (regresní, beze změny).
   - Nejednoznačný "plain" tvar (jen input/output klíče) → funguje jako dřív.
2. **Verzování v čase**: model se dvěma verzemi ceny (`effective_from`
   včera / dnes), run z včerejška → **včerejší cena**, dnešní run → dnešní.
   Ověřit **oběma cestami**: `estimate_run_cost` (Python) i přes
   `/ops/api/summary` (SQL twin).
3. **Parita Python ↔ SQL**: nad stejnou sadou runů musí `SUM` z
   `run_cost_sql_expr` sedět na součet `estimate_run_cost` po řádcích (do
   zaokrouhlovací tolerance). Tohle je jediná pojistka proti tomu, aby se
   ty dvě implementace časem rozešly — dnešní testy ji nemají.
4. **`tests/test_ai_model_price_components.py`** — admin UI (CC-3):
   - Vytvoření modelu s cenami → vznikly komponentní řádky jen pro
     vyplněná pole.
   - Editace jen `notes` → žádný nový cenový řádek.
   - Změna jedné komponenty → nový řádek **jen pro ni**.
   - Vymazání vyplněné komponenty → validační chyba, nic se neuložilo
     (CC-3 krok 4).
   - Cena `0.025` se uloží a přečte **beze ztráty přesnosti** (regresní test
     na design decision 11 — v původním `NUMERIC(10,5)` per-1k schématu by
     tenhle test spadl).
5. **`tests/test_ops_dashboard.py`** — agregace tokenů (CC-5): součty
   sedí na seedovaná data, `None`/`0` chování u runu bez `raw_responses`.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové), uveď celkový počet
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test(ops): cover component pricing, price versioning and token aggregation
```

---

## CC-8 — Migrace 0026: zahodit starou cenovou strukturu (čistý řez)

**Target:** nový `alembic/versions/0026_drop_flat_model_prices.py`,
`app/models/provider.py`, `app/models/__init__.py`, `app/routers/ai_models.py`,
`tests/test_ai_model_price_history.py`, `tests/test_ai_models.py`

Design decisions 5, 12. Prerekvizita: CC-7 zelené. Teprve tady vzniká
"čistý řez" — do CC-7 staré sloupce žijí, ale od CC-4 už je nikdo nečte.

1. **Ověřit, že opravdu nikdo nečte** — `grep -rn "cost_per_1k\|price_history\|AIModelPriceHistory"
   app tests` musí vracet jen místa, která tenhle úkol ruší. Když vrátí
   cokoliv jiného, **zastavit a nahlásit**, ne to opravit mimochodem.
2. Migrace 0026 — `op.drop_table("ai_model_price_history")` (s indexem),
   `op.drop_column("ai_models", "cost_per_1k_input_usd")`,
   `op.drop_column("ai_models", "cost_per_1k_output_usd")`.
   `downgrade()` sloupce i tabulku vrátí (prázdné — data se z komponent
   zpětně nedopočítávají, protože přepočet per-1M → per-1k je ztrátový
   přesně u těch hodnot, kvůli kterým se to měnilo; napsat to do
   docstringu, ne to tiše zaokrouhlit).
3. `app/models/provider.py` — smazat `AIModelPriceHistory`, atributy
   `AIModel.cost_per_1k_*_usd` a relationship `price_history`.
   `app/models/__init__.py` — odstranit export.
4. `app/routers/ai_models.py` — smazat `_record_price_history`, zbytky
   zápisu do `cost_per_1k_*` v `create_ai_model`/`update_ai_model`
   (ponechané kvůli CC-3 mezistavu) a import `AIModelPriceHistory`.
5. `tests/test_ai_model_price_history.py` — soubor zrušit; jeho účel
   přebírá `tests/test_ai_model_price_components.py` (CC-7). Ověřit, že
   každý jeho scénář má v novém souboru ekvivalent — **než se smaže**.
   `tests/test_ai_models.py::test_price_history_is_visible_on_the_edit_form`
   přepsat na komponentní historii.
6. **Flagované, NEDĚLAT bez pokynu uživatele** (nahlásit v summary):
   - `app/templates/findings.html` (~ř. 198) zmiňuje
     `cost_per_1k_input_usd`/`output_usd` jménem v textu nálezu o
     nezachycené ceně web searche. Po CC-8 je ten text fakticky neplatný
     **a zároveň** je ten nález obsahově vyřešený až v CC-9 (search fee).
     Obsah `/findings` je editorial rozhodnutí uživatele — jen upozornit.
   - `schema_phase1.sql` ř. 69–70 — viz "⚠️ Schema flagy" výše.

Po dokončení:
1. `docker compose up -d --build` — appka nastartuje bez chyby
2. `alembic upgrade head` na lokální i serverové DB
3. V prohlížeči: `/ai-models` (seznam, create, edit, historie cen), `/ops`,
   `/prompts/{id}` (cost badge) — všechno funguje beze změny proti CC-7 stavu
4. `pytest` — všechny testy zelené
5. Implementation summary (včetně flagovaných bodů z kroku 6) + navrhni
   commit message (nespouštěj git)

**Expected commit:**
```
refactor(infra): drop the flat per-model price columns and old price history
```

---

## Ceny podle ceníků providerů (screenshoty 2026-09-15/16, $ za 1M tokenů)

Ověřeno proti dnešnímu nastavení appky — **input/output ceny už appka má
správně u všech 8 aktivních modelů**, nesoulad proti realitě byl v
chybějících komponentách, ne ve špatných cenách za token. Tohle je zdroj
dat pro krok 8 v CC-3.

| Model | Input | Output | Cache read | Cache write 5m | Cache write 1h |
|---|---|---|---|---|---|
| claude-haiku-4-5 | $1 | $5 | $0.10 | $1.25 | $2 |
| claude-sonnet-5 | $2 | $10 | $0.20 | $2.50 | $4 |
| claude-opus-5 | $5 | $25 | $0.50 | $6.25 | $10 |

| Model | Input | Output | Context caching (= cache_read) |
|---|---|---|---|
| gemini-3.1-flash-lite | $0.25 | $1.50 | $0.025 |
| gemini-3.5-flash | $1.50 | $9.00 | $0.15 |

| Model | Input (short) | Output (short) | Cached input (short) | Cache writes (short) |
|---|---|---|---|---|
| gpt-5.6-luna | $0.20 | $1.20 | $0.02 | $0.25 |
| gpt-5.6-terra | $2 | $12 | $0.20 | $2.50 |
| gpt-5.6-sol | $4 | $20 | $0.40 | $5 |

Gemini grounding (search): 5 000 free/měsíc sdíleno napříč Gemini 3.x
modely celého účtu, pak $14/1000 dotazů — viz design decision 6, proč se
tohle (zatím) do CC-1..CC-8 nepočítá.

---

## Otevřené otázky

**Blokující: žádné.** Branch strategie (poslední blokující bod) rozhodnuta
2026-09-16 — vlastní `feature/signalmap-cost-components`, viz design
decision 8. Branch ještě neexistuje; zakládá ji uživatel před CC-1, ne
agent:

```
git checkout -b feature/signalmap-cost-components
```

(z aktuálního master — ops dashboard PR #13 je v něm už smergnutý.)

**Neblokující (dá se rozhodnout až v CC-8):**
- **`schema_phase1.sql` po CC-8** — anotovat poznámkou, že
  `cost_per_1k_*_usd` už neplatí, nebo nechat jako historický záznam
  phase-1 stavu? Viz "⚠️ Schema flagy".
- **`/findings` záznam o web search ceně** — po CC-8 odkazuje na neexistující
  sloupce; obsahově ho řeší až CC-9. Editorial rozhodnutí uživatele.

**Trvale otevřené (neblokují nic):**
- **Gemini token rekonciliace** — appka pořád ukazuje ~6× víc tokenů než
  odhad z grafu konzole. Nenašel se přesný export (Google AI Studio ani
  GCP Billing Reports). **Rozhodnutí 2026-09-16: plánujeme dál bez tohohle
  vyřešeného** — token data appky jsou už ověřená jako důvěryhodná u
  Anthropic/OpenAI (98–100% shoda), takže komponentní model může stavět na
  nich i bez Gemini rekonciliace.
- **Search/tool-call cena** (Anthropic, OpenAI) — viz design decision 6,
  odloženo do CC-9.

---

## Completion Checklist

- [ ] `ai_model_price_components` existuje na lokální i serverové DB, s
      kompletní historií cen přenesenou ze staré struktury (CC-1)
- [ ] Ceny všech 8 aktivních modelů zadané přes `/ai-models` podle ceníkové
      tabulky, včetně cache komponent (CC-3)
- [ ] `$0.025 / 1M` se uloží a zobrazí přesně, ne zaokrouhleně (CC-3/CC-7)
- [ ] Cena runu se počítá z komponent, podle ceníku **platného v době runu**
      (CC-4)
- [ ] Cached tokeny se u OpenAI/Gemini nenaúčtují dvakrát; u Anthropicu se
      input nesnižuje (CC-4/CC-7)
- [ ] Chybějící cena komponenty u nenulových tokenů dá "—", ne podhodnocenou
      cenu (CC-4/CC-7)
- [ ] `/ops` ukazuje tokeny vedle ceny, cena je označená jako odhad (CC-5/CC-6)
- [ ] Ověřeno na ~640px / ~1024px / desktop šířce (`/ai-models` formulář,
      `/ops` tabulky)
- [ ] `pytest` sada zelená, včetně parity Python ↔ SQL (CC-7)
- [ ] Stará struktura zahozená, žádný souběh dvou cenových modelů (CC-8)
- [ ] `docs/REQUIREMENTS.md` / `docs/TASKS.md` / `docs/ROADMAP.md` — poznámka
      o komponentním cenovém modelu, **až po tom, co uživatel v prohlížeči
      potvrdí, že to funguje** (AI_INSTRUCTIONS.md §7)
