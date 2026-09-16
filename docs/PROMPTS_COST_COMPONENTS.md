# SignalMap — Claude Code Session Prompts: Cost Components

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-cost-components (z aktuálního master)
##    Zakládá ji UŽIVATEL před CC-1, ne agent — agent branch nikdy sám
##    nezakládá ani nepřepíná (AI_INSTRUCTIONS.md §4/§9). Na začátku každé
##    další session pak: git checkout feature/signalmap-cost-components
## 2. Osm kódových promptů (CC-1..CC-8). POŘADÍ JE VYNUCENÉ, nedá se přeházet:
##    CC-1 → CC-2 → CC-3 → CC-4 → CC-5 → CC-6 → CC-7 → CC-8.
##    Hlavně CC-3 (zadání reálných cen) MUSÍ být před CC-4 (přepnutí výpočtu)
##    — jinak ops dashboard ukáže "—" u všech runů a nejde odlišit chybu
##    v kódu od chybějících dat.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle práci.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická
##    kontrola daného promptu), než jdeš na další. Proto se stará cenová
##    struktura ruší až v CC-8, ne v CC-1 (design decision 12).
## 5. Po každém promptu: git commit (message navržená na konci promptu).
##    Agent NIKDY nespouští git commit/branch/push sám bez výslovného
##    potvrzení, a to i přesto, že zprávu sám navrhl.
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_COST_COMPONENTS.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-14 a ceníkové tabulky:
##    docs/TASKS_COST_COMPONENTS.md — přečti si konkrétní task ID před psaním
##    kódu, ideálně celý soubor před CC-1.
## 9. CC-1 a CC-8 mění schéma (nová tabulka / zrušené sloupce) — obojí je
##    předem flagované v sekci "⚠️ Schema flagy" v TASKS_COST_COMPONENTS.md.
##    Nic dalšího ve schématu se měnit nesmí.
## 10. Až je práce hotová a potvrzená v prohlížeči: doplnit poznámku do
##     docs/REQUIREMENTS.md / TASKS.md / ROADMAP.md (viz Completion Checklist
##     v TASKS_COST_COMPONENTS.md) — teprve PO potvrzení uživatele
##     (AI_INSTRUCTIONS.md §7).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této práci)

```
Pracuji na projektu SignalMap, branch feature/signalmap-cost-components.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_COST_COMPONENTS.md — CELÉ, hlavně design decisions 1-14,
   sekci "⚠️ Schema flagy" a "Ceny podle ceníků providerů"

KONTEXT: appka počítala odhad ceny runu jen z input/output tokenů, což
výrazně podhodnocovalo realitu — chyběly cache tiery (Anthropic má cache
read + dva write tiery, Gemini a OpenAI po jednom cache tieru). Reálná
data z appky se porovnala s konzolemi providerů: tokeny appka měří
správně (OpenAI shoda do posledního tokenu, Anthropic ~98 %), chyba je
výhradně ve výpočtu ceny.

Řešení: nahradit ai_models.cost_per_1k_input_usd/output_usd (2 pevné
sloupce) novou tabulkou ai_model_price_components — component_type
(input/output/cache_read/cache_write/cache_write_5m/cache_write_1h),
cena za 1M tokenů, verzovaná v čase přes effective_from (append-only,
po vzoru dnešní ai_model_price_history). Run se cení podle ceníku
platného v době runu, ne podle dnešního.

MIMO SCOPE (nedělat, neplánovat): search/tool-call poplatek (CC-9,
chybí ceny u Anthropic/OpenAI a Gemini má sdílený měsíční free-tier pool
na úrovni celého účtu), Gemini cache storage price (účtuje se za čas,
ne za tokeny runu), OpenAI long-context tier (neznámý práh).

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Alembic, Jinja2 + HTMX +
jeden Vue3 ostrůvek z CDN na /ops, Docker Compose. Backend kód anglicky
vč. komentářů a docstringů, UI texty přes t() mechanismus v
app/i18n/{en,de}.json.

KRITICKÁ PRAVIDLA:
- Schéma se mění POUZE dvakrát a přesně tak, jak to popisuje "⚠️ Schema
  flagy": CC-1 přidá ai_model_price_components, CC-8 zahodí staré
  sloupce a ai_model_price_history. Žádný jiný sloupec ani tabulka.
- Ceny se ukládají za 1M tokenů v NUMERIC(12,6), NE za 1k v NUMERIC(10,5)
  — Gemini cache read $0.025/1M by se ve starém schématu zaokrouhlil
  o 20 % nahoru (design decision 11).
- Cached tokeny se počítají u každého providera jinak: Anthropic je má
  MIMO input_tokens, OpenAI a Gemini je mají UVNITŘ input. Bez toho se
  u OpenAI/Gemini naúčtují dvakrát (design decision 13).
- Chybějící cena komponenty u nenulových tokenů → None ("neznámá cena"),
  NIKDY podhodnocená cena (design decision 14). Tiché podhodnocení je
  přesně ten problém, kvůli kterému tahle práce vznikla.
- ai_model_price_components je append-only — řádek se nikdy needituje ani
  nemaže, změna ceny = nový řádek (stejně jako u raw_responses/citations).
- Nikdy `git commit`, `git checkout -b`, `git push` ani merge do master bez
  mého výslovného potvrzení — i po tom, co sám navrhneš commit message,
  čekáš na "ano, commitni", než cokoliv spustíš. Branch zakládám já, ne ty.

Po každém promptu ukaž implementation summary a navrhni commit message.
Nikdy nespouštěj git add/commit/branch/push sám bez výslovného pokynu —
a to i tehdy, když jsi zprávu sám navrhl v předchozí větě.
```

---
---

## PROMPT CC-1 — Migrace 0025: `ai_model_price_components` + backfill

```
Task: Prompt CC-1 — add the ai_model_price_components table with a backfill

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-1 CELÝ, plus design decisions
2, 3, 4, 11, 12 a sekci "⚠️ Schema flagy".

Tenhle úkol je ČISTĚ ADITIVNÍ: ai_models.cost_per_1k_*_usd i tabulka
ai_model_price_history zůstávají nedotčené a appka je pořád čte. Zahození
staré struktury je až CC-8 — nedělej ho tady, ani "při té příležitosti".

1. Nová migrace alembic/versions/0025_ai_model_price_components.py
   (down_revision = "0024"). Tabulka ai_model_price_components:
   - id (PK), ai_model_id (FK ai_models.id ON DELETE CASCADE, NOT NULL)
   - component_type VARCHAR(30) NOT NULL + CHECK constraint na hodnoty
     input / output / cache_read / cache_write / cache_write_5m / cache_write_1h
   - price_per_unit_usd NUMERIC(12,6) NOT NULL (nikdy NULL — chybějící
     cena = chybějící řádek)
   - unit VARCHAR(20) NOT NULL server_default 'per_1m_tokens' + CHECK na
     per_1m_tokens / per_call
   - effective_from TIMESTAMPTZ NOT NULL server_default now()
   - changed_by_user_id (FK users.id, nullable)
2. Index idx_price_components_lookup na (ai_model_id, component_type,
   effective_from DESC) — přesně tvar dotazu, který bude dělat resolver
   (CC-2) i korelovaný subquery v SQL twinu (CC-4).
3. Backfill CELÉ historie, ne jen dnešních cen: každý řádek
   ai_model_price_history s nenulovou cenou → až 2 komponentní řádky
   ('input', 'output') se STEJNÝM effective_from i changed_by_user_id,
   cena přepočtená z per-1k na per-1M (* 1000). SQL je v CC-1 kroku 3.
4. Pojistka: pro model, jehož dnešní ai_models.cost_per_1k_*_usd se
   neshoduje s nejnovějším historickým řádkem (nebo který historii nemá),
   vlož navíc řádek s dnešní cenou a effective_from = ai_models.updated_at.
5. downgrade() = drop indexu + drop tabulky, nic víc (stará struktura
   pořád existuje, takže downgrade je bezztrátový).
6. Docstring migrace: proč per-1M místo per-1k (design decision 11), a že
   unit='per_call' je v CHECKu předem kvůli budoucímu search fee (CC-9),
   ne omylem zbylý mrtvý kód.

Po dokončení:
1. docker compose up -d --build — appka nastartuje bez chyby
2. psql: SELECT component_type, count(*) FROM ai_model_price_components
   GROUP BY 1 — počty odpovídají nenulovým cenám v ai_model_price_history
3. psql: ověř, že u gemini-3.1-flash-lite je 'input' = 0.250000, ne
   0.000250 (kontrola směru přepočtu per-1k → per-1M)
4. pytest — všechny testy zelené beze změny
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(infra): add ai_model_price_components with a full price-history backfill
```

---
---

## PROMPT CC-2 — Model vrstva: `AIModelPriceComponent` + resolver

```
Task: Prompt CC-2 — AIModelPriceComponent model + time-aware price resolver

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-2 CELÝ, hlavně design
decisions 3, 4, 11.
Prerekvizita: CC-1 hotový (migrace 0025 aplikovaná).

Tenhle úkol přidává JEN ORM vrstvu a resolver. Samotný výpočet ceny
(estimate_run_cost / run_cost_sql_expr) se NEMĚNÍ — ten je CC-4.

1. app/models/provider.py — třída AIModelPriceComponent:
   - COMPONENT_TYPES a UNITS jako class-level tuples (stejný vzor jako
     User.ROLES / DomainClassification.DOMAIN_TYPES)
   - __table_args__ se ZRCADLENÝMI CheckConstraint-y a indexem. Tohle není
     volitelné: testy staví schéma přes Base.metadata.create_all, ne
     Alembicem, takže constraint žijící jen v migraci by v testech
     neexistoval (stejná past jako u migrace 0024).
   - docstring: append-only, "žádný řádek" = "cenu neznáme", ne "nulová".
2. AIModel.price_components relationship (order_by effective_from desc,
   cascade all/delete-orphan, back_populates). Starou AIModel.price_history
   NECHAT beze změny — ruší se až v CC-8.
3. app/models/__init__.py — export AIModelPriceComponent.
4. app/services/cost.py — tři nové funkce, žádná nemění existující chování:
   - load_price_components(db, model_ids) -> dict[int, list[...]] — JEDEN
     dotaz přes všechny modely (ne N+1 na model), seřazený effective_from DESC
   - prices_at(components, at: datetime) -> dict[str, Decimal] — čistá
     funkce nad už načteným seznamem: pro každý component_type nejnovější
     řádek s effective_from <= at; komponenta bez takového řádku ve
     výsledku není
   - current_prices(db, model_ids) -> dict[int, dict[str, Decimal]] =
     prices_at(..., now UTC), pro admin UI v CC-3

Po dokončení:
1. docker compose up -d --build — appka nastartuje bez chyby
2. pytest — všechny testy zelené (nic se nepřepojilo, jen přibylo)
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ai-models): add AIModelPriceComponent model and time-aware price resolution
```

---
---

## PROMPT CC-3 — Admin UI pro komponenty + zadání reálných cen

```
Task: Prompt CC-3 — per-component price management in the /ai-models admin UI

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-3 CELÝ, hlavně design
decisions 1, 4, 11, 14, a tabulku "Ceny podle ceníků providerů".
Prerekvizita: CC-2 hotový.

DŮLEŽITÉ: v tomhle úkolu se POŘÁD zapisuje i do staré struktury
(_record_price_history + cost_per_1k_*_usd) — estimate_run_cost se
přepíná až v CC-4 a do té doby na nich stojí ops dashboard.

1. app/templates/ai_models/form.html — dnešní dvě cenová pole nahraď šesti
   volitelnými, jedno na component_type (input, output, cache_read,
   cache_write, cache_write_5m, cache_write_1h), všechna v USD za 1M
   tokenů. Prázdné = komponenta se neúčtuje / cenu neznáme. Reuse
   text_field makra, grid 2 sloupce, hint pod blokem (Anthropic má dva
   write tiery, ostatní jeden).
2. app/routers/ai_models.py — _parse_component_price(raw, t) nahradí
   _parse_price: Decimal BEZ dělení tisícem (ukládáme per-1M), quantize
   na 6 desetinných míst, rozsah 0 <= value < 1_000_000, existující
   chybové klíče errors.ai_model_invalid_price /
   errors.ai_model_price_out_of_range.
3. Zápis append-only, jen změněné komponenty: porovnej zadané hodnoty
   s dnes platnými (prices_at(..., now)) a vlož nový řádek JEN pro ty,
   co se reálně změnily. Editace notes nikdy nesmí založit cenový řádek.
4. Vymazání už nastavené komponenty = validační chyba (nový i18n klíč
   errors.ai_model_price_component_cannot_be_cleared, text: pokud provider
   přestal účtovat, zadej 0). NEZAPISOVAT při vymazání nulu — 0 znamená
   "ověřeně zdarma", což je jiný fakt než "už nevíme".
5. _price_history_rows přepiš nad komponentami: Platí od / Platí do /
   Komponenta / Cena, valid_until počítané V RÁMCI jednoho component_type
   (ne napříč všemi). Starou price_history sekci ze šablony odstraň
   (tabulka sama padne až v CC-8).
6. cost_badge makro (partials/macros.html) — dnes čte model.cost_per_1k_*
   přímo. Přepiš na cost_badge(model, prices, free_label,
   per_million_suffix). Volající: ai_models/list.html (_model_rows doplní
   prices) a prompts/detail.html (_runnable_model_groups v
   app/routers/prompts.py doplní prices). Badge dál ukazuje jen
   input / output per 1M, cache komponenty ne.
7. i18n EN+DE (jeden commit): ai_model.cost_component_*_label (6×),
   ai_model.cost_components_hint, ai_model.price_history_component_label,
   errors.ai_model_price_component_cannot_be_cleared.
8. Zadání reálných cen pro všech 8 aktivních modelů podle ceníkové tabulky
   — ručně přes /ai-models UI, ne migrací (provozní data, ne schéma, a
   zároveň nejlepší ověření, že formulář funguje). Gemini: cache_read bez
   cache_write. OpenAI: cache_read + cache_write. Anthropic: cache_read +
   cache_write_5m + cache_write_1h.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči /ai-models: zadej ceny všech 8 modelů, ulož, otevři znovu
   — hodnoty sedí přesně, hlavně gemini-3.1-flash-lite cache_read = 0.025
   (ve starém per-1k NUMERIC(10,5) schématu by se zaokrouhlilo o 20 % nahoru)
3. Editace jen notes → historie cen NEpřibude o řádek
4. Změna jedné komponenty → nový řádek jen pro ni, ostatní si drží své
   původní effective_from
5. Vymazání vyplněné komponenty → validační chyba, nic se neuloží
6. /prompts/{id} → cost badge u modelů pořád ukazuje input/output cenu
7. Ověř na ~640px / ~1024px / desktop šířce (6 cenových polí nesmí rozbít
   layout formuláře na mobilu)
8. pytest — všechny testy zelené (testy staré price history pořád platí)
9. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ai-models): manage per-component model prices in the admin UI
```

---
---

## PROMPT CC-4 — `cost.py`: přepis výpočtu na komponenty

```
Task: Prompt CC-4 — price runs from cost components effective at run time

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-4 CELÝ, hlavně design
decisions 13 (cached tokeny u každého providera jinak) a 14 (chybějící
cena → None).
Prerekvizita: CC-3 hotový VČETNĚ zadaných reálných cen všech 8 modelů —
jinak nejde odlišit chybu ve výpočtu od chybějících dat.

1. NEJDŘÍV ověř skutečný tvar dat, nepiš podle dokumentace providerů.
   Spusť SQL z CC-4 kroku 1 (jsonb_object_keys nad raw_responses.token_usage
   po providerech) a výsledek zapiš do docstringu TOKEN_USAGE_SHAPES — je
   to jediný doklad, proč tam ty klíče jsou. Když se reálný tvar liší od
   tabulky v CC-4 kroku 2, ZASTAV a nahlas to, nepřizpůsobuj tabulku mlčky.
2. TOKEN_USAGE_SHAPES nahradí dnešní TOKEN_COUNT_KEY_PAIRS — frozen
   dataclass: input_key, output_key, cache_read_path, cache_write_paths
   (component_type → cesta v JSONu), input_includes_cache_read: bool.
   Očekávané tvary jsou v tabulce v CC-4 kroku 2.
3. Výběr tvaru podle rozlišujícího klíče, NE podle providera na modelu
   (estimate_run_cost je čistá funkce bez přístupu k relacím, testy staví
   AIModel bez session). Pořadí: prompt_token_count → Gemini;
   input_tokens_details → OpenAI; cache_creation/cache_read_input_tokens →
   Anthropic; jen input_tokens+output_tokens → sdílený "plain" tvar bez
   cache komponent.
4. estimate_run_cost(token_usage, model, prices: dict[str, Decimal]):
   - is_free → 0.0 (beze změny)
   - chybí token_usage / nesedí tvar / chybí cena input nebo output → None
   - billable_input = input_tokens - cache_read_tokens, KDYŽ
     shape.input_includes_cache_read; jinak input_tokens. max(0, ...)
     defenzivně.
   - cena = billable_input/1e6 * input + output/1e6 * output +
     cache_read/1e6 * cache_read + suma write komponent
   - NENULOVÝ počet tokenů komponenty bez ceny → None (nikdy podhodnocená
     cena). Nulový počet bez ceny → komponenta se přeskočí.
   - round(cost, 6)
5. run_cost_sql_expr(token_usage_col, model_id_col, started_at_col,
   is_free_col) — SQL twin, cena platná V ČASE RUNU (Run.started_at).
   Per komponenta korelovaný skalární subquery nad
   ai_model_price_components (ORDER BY effective_from DESC LIMIT 1, WHERE
   effective_from <= started_at) — tvar, na který míří
   idx_price_components_lookup. Token osy COALESCE napříč
   TOKEN_USAGE_SHAPES. Stejná pravidla pro NULL jako Python verze.
6. _cost_expr() v app/services/ops_dashboard.py přepoj na nové argumenty
   (RawResponse.token_usage, Run.model_id, Run.started_at, AIModel.is_free).
7. recent_runs v ops_dashboard.py — doplň load_price_components jedním
   dotazem pro modely těch ~15 runů a per řádek prices_at(...,
   row.Run.started_at). Ne dotaz na řádek.
8. Stávající testy v tests/test_cost.py a tests/test_ops_dashboard.py uprav
   na novou signaturu, aby sada zůstala zelená. NOVÉ pokrytí (verzování
   v čase, dvojité účtování, chybějící komponenty) přidává až CC-7 —
   nepiš ho tady.

Po dokončení:
1. docker compose up -d --build
2. /ops v prohlížeči: celková cena je VYŠŠÍ než před CC-4 u runů s cache
   tokeny a STEJNÁ u runů bez nich; žádný run nespadl na "—" (pokud ano,
   chybí cena komponenty → zkontroluj data z CC-3, ne kód)
3. pytest — všechny testy zelené
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
refactor(ops): price runs from cost components effective at run time
```

---
---

## PROMPT CC-5 — Agregace tokenů vedle ceny

```
Task: Prompt CC-5 — aggregate token counts alongside estimated cost

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-5 CELÝ, hlavně design
decision 7 (tokeny vedle ceny, nikdy místo ní).
Prerekvizita: CC-4 hotový.

1. app/services/cost.py — run_token_sql_expr(token_usage_col, axis) pro osy
   input, output, cache_read, cache_write (součet všech write tierů).
   COALESCE napříč TOKEN_USAGE_SHAPES jako cost twin. Chybějící hodnota →
   0, ne NULL (na rozdíl od ceny: "kolik tokenů" je vždy odpověditelné).
   POZOR: input osa vrací SUROVÝ input_tokens, ne billable_input — v UI se
   tokeny ukazují tak, jak je vykázal provider; odečtení cached tokenů je
   věc ceny, ne zobrazení.
2. OpsSummary + ops_summary — total_input_tokens, total_output_tokens,
   total_cache_read_tokens, total_cache_write_tokens (int | None, None jen
   když v rozsahu není žádný run s raw_responses řádkem).
3. ProviderCostRow + provider_cost_rows — total_input_tokens,
   total_output_tokens.
4. RecentRunRow + recent_runs — input_tokens, output_tokens per run
   (Python strana, bounded fetch, stejně jako cena).
5. app/routers/ops_dashboard.py — zrcadli nová pole v OpsSummaryResponse,
   OpsProviderRow a recent-run response modelech, všechna s
   Field(..., description=...) (AI_INSTRUCTIONS §3).

Po dokončení:
1. docker compose up -d --build
2. /ops/api/summary a /ops/api/providers — token pole jsou v odpovědi a
   nejsou nulová
3. Namátkou ověř jednoho providera proti jeho konzoli (tokeny, ne cenu)
4. pytest — všechny testy zelené
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ops): aggregate token counts alongside estimated cost
```

---
---

## PROMPT CC-6 — Tokeny v UI vedle ceny

```
Task: Prompt CC-6 — show token counts next to the estimated cost on /ops

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-6 CELÝ, hlavně design
decision 7.
Prerekvizita: CC-5 hotový.

1. app/templates/ops/index.html — nová KPI dlaždice "Tokens" vedle
   dlaždice ceny: in / out ve zkráceném tvaru (1.2M / 847k / 312),
   podtitulek s cache podílem, když je nenulový.
2. Dlaždice ceny — podtitulek musí explicitně říct, že jde o ODHAD podle
   nakonfigurovaného ceníku, ne jen "cost". Uprav ops.kpi_cost_sub v obou
   jazycích, pokud to dnešní text neříká dost jasně.
3. Tabulka providerů — sloupec tokenů (in / out) vedle sloupce ceny.
4. Tabulky posledních runů (prompt-detail i user-detail) — sloupec tokenů
   vedle ceny.
5. fmtTokens(n) helper vedle existujícího fmtCost, null → noData.
6. i18n EN+DE (jeden commit): ops.kpi_tokens, ops.kpi_tokens_sub,
   ops.col_tokens, případná úprava ops.kpi_cost_sub.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči /ops: tokeny vidět globálně, u providerů i u jednotlivých
   runů; nikde tokeny NENAHRADILY cenu, vždy stojí vedle ní
3. Ověř na ~640px / ~1024px / desktop šířce — nový sloupec nesmí rozbít
   tabulky na mobilu (ops tabulky jsou dnes nejširší obrazovka appky)
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ops): show token counts next to the estimated cost
```

---
---

## PROMPT CC-7 — Testy

```
Task: Prompt CC-7 — cover component pricing, price versioning, token aggregation

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-7 CELÝ.
Prerekvizita: CC-6 hotový.

1. tests/test_cost.py — komponentní výpočet:
   - Anthropic tvar s cache read + oběma write tiery → cena = součet všech
     komponent, ověřená ručně spočítaným číslem
   - NEJDŮLEŽITĚJŠÍ TEST SADY: OpenAI a Gemini nesmí naúčtovat cached
     tokeny dvakrát — run s input_tokens=10000 a cached_tokens=4000 se
     musí naúčtovat jako 6000 plnou cenou + 4000 cache cenou
   - Anthropic: input_tokens se NEPONIŽUJE o cache read
   - nenulové cache tokeny bez ceny komponenty → None
   - nulové cache tokeny bez ceny komponenty → cena se spočítá normálně
   - is_free → 0.0 bez ohledu na komponenty (regresní)
   - nejednoznačný "plain" tvar (jen input/output klíče) → funguje jako dřív
2. Verzování v čase: model se dvěma verzemi ceny (effective_from včera /
   dnes), run z včerejška → včerejší cena, dnešní run → dnešní. Ověř OBĚMA
   cestami: estimate_run_cost (Python) i přes /ops/api/summary (SQL twin).
3. Parita Python ↔ SQL: nad stejnou sadou runů musí SUM z
   run_cost_sql_expr sedět na součet estimate_run_cost po řádcích (do
   zaokrouhlovací tolerance). Tohle je jediná pojistka proti tomu, aby se
   ty dvě implementace časem rozešly — dnešní testy ji nemají.
4. Nový tests/test_ai_model_price_components.py — admin UI z CC-3:
   - vytvoření modelu s cenami → řádky jen pro vyplněná pole
   - editace jen notes → žádný nový cenový řádek
   - změna jedné komponenty → nový řádek jen pro ni
   - vymazání vyplněné komponenty → validační chyba, nic se neuložilo
   - cena 0.025 se uloží a přečte beze ztráty přesnosti (regresní test na
     design decision 11 — ve starém NUMERIC(10,5) per-1k schématu by
     tenhle test spadl)
5. tests/test_ops_dashboard.py — agregace tokenů z CC-5: součty sedí na
   seedovaná data, None/0 chování u runu bez raw_responses.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové), uveď celkový počet
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test(ops): cover component pricing, price versioning and token aggregation
```

---
---

## PROMPT CC-8 — Zahodit starou cenovou strukturu (čistý řez)

```
Task: Prompt CC-8 — drop the flat per-model price columns and old price history

Přečti docs/TASKS_COST_COMPONENTS.md úkol CC-8 CELÝ, plus design decisions
5 a 12 a sekci "⚠️ Schema flagy".
Prerekvizita: CC-7 zelené. Tohle je jediný úkol, který něco ruší.

1. NEJDŘÍV ověř, že starou strukturu opravdu nikdo nečte:
   grep -rn "cost_per_1k\|price_history\|AIModelPriceHistory" app tests
   Musí vrátit jen místa, která tenhle úkol ruší. Když vrátí cokoliv
   jiného, ZASTAV a nahlas to — neopravuj to mimochodem.
2. Nová migrace alembic/versions/0026_drop_flat_model_prices.py
   (down_revision = "0025"): drop_table ai_model_price_history (i s
   indexem), drop_column ai_models.cost_per_1k_input_usd a
   cost_per_1k_output_usd. downgrade() sloupce i tabulku vrátí prázdné —
   data se z komponent zpětně nedopočítávají, protože přepočet per-1M →
   per-1k je ztrátový přesně u těch hodnot, kvůli kterým se to měnilo.
   Napiš to do docstringu, netiš to zaokrouhlením.
3. app/models/provider.py — smaž AIModelPriceHistory, atributy
   AIModel.cost_per_1k_*_usd a relationship price_history.
   app/models/__init__.py — odstraň export.
4. app/routers/ai_models.py — smaž _record_price_history, zbytky zápisu do
   cost_per_1k_* v create_ai_model/update_ai_model (ponechané kvůli CC-3
   mezistavu) a import AIModelPriceHistory.
5. tests/test_ai_model_price_history.py — soubor zruš, ale NEJDŘÍV ověř,
   že každý jeho scénář má ekvivalent v tests/test_ai_model_price_components.py
   (CC-7). tests/test_ai_models.py::test_price_history_is_visible_on_the_edit_form
   přepiš na komponentní historii.
6. FLAGUJ, NEDĚLEJ (jen nahlas v summary):
   - app/templates/findings.html (~ř. 198) zmiňuje cost_per_1k_input_usd/
     output_usd jménem. Po téhle migraci je ten text fakticky neplatný,
     ale obsah /findings je editorial rozhodnutí uživatele.
   - schema_phase1.sql ř. 69-70 — oba rušené sloupce tam jsou. Má se
     soubor anotovat, nebo zůstává jako historický záznam phase-1 stavu?

Po dokončení:
1. docker compose up -d --build — appka nastartuje bez chyby
2. alembic upgrade head na lokální i serverové DB
3. V prohlížeči: /ai-models (seznam, create, edit, historie cen), /ops,
   /prompts/{id} (cost badge) — všechno funguje beze změny proti CC-7 stavu
4. pytest — všechny testy zelené
5. Implementation summary (VČETNĚ obou flagovaných bodů z kroku 6) +
   navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
refactor(infra): drop the flat per-model price columns and old price history
```

---
---

## PO DOKONČENÍ CC-1..CC-8

1. Uživatel potvrdí v prohlížeči, že cenový model funguje (AI_INSTRUCTIONS §7
   — browser walkthrough agenta není náhrada za potvrzení uživatele).
2. TEPRVE POTOM: aktualizovat docs/REQUIREMENTS.md, docs/TASKS.md a
   docs/ROADMAP.md o poznámku ke komponentnímu cenovému modelu — viz
   Completion Checklist v docs/TASKS_COST_COMPONENTS.md. Ukázat diff,
   netiše driftovat.
3. Zvážit /code-review high na celém diffu (stejně jako u ops dashboard
   branch, kde to našlo 10 nálezů) — hlavně na CC-4 (dvě implementace
   téhož výpočtu) a CC-1/CC-8 (migrace na dvou databázích).
4. Merge `feature/signalmap-cost-components` do master — jen na výslovný
   pokyn uživatele (AI_INSTRUCTIONS.md §9), stejně jako push branche.
5. Rozhodnout o CC-9 (search/tool-call poplatek) — samostatný dokument,
   až budou ceny od Anthropicu/OpenAI a řešení Gemini free-tier poolu.
