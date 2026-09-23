# SignalMap — Claude Code Session Prompts: Phase 4 (Dashboard v0)

## Status: ✅ Done — PR #6, merged 2026-09-11, released in v1.0.0

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-phase4-dashboard-v0 (z aktuálního master)
## 2. Pět kódových promptů (P4-1 až P4-5), POŘADÍ VYNUCENÉ — viz docs/TASKS_PHASE4.md
##    "Task Index" pro odůvodnění (P4-2 potřebuje index/helper z P4-1; P4-3 staví na
##    API z P4-2; P4-4 rozšiřuje P4-3 o edge-case stavy; P4-5 testuje všechno).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola
##    daného promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit
##    provádíš ty, ne agent — agent NIKDY nespouští git commit/push sám bez
##    výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_PHASE4.md přepni řádek daného task ID v tabulce "Task Index"
##       z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-11: docs/TASKS_PHASE4.md — přečti
##    si konkrétní task ID před psaním kódu, ideálně celý soubor před P4-1.
## 9. Tahle větev NEZAVÁDÍ build step ani plný Vue frontend — jeden Vue3 ostrůvek z
##    CDN uvnitř jinak Jinja2/HTMX appky (design decision 1-2). Pokud se během
##    implementace zdá, že je potřeba Vite/npm/druhý Vue ostrůvek se sdíleným stavem,
##    ZASTAV a zeptej se — to by byl scope creep mimo to, co bylo odsouhlaseno.
## 10. Žádný nový analysis skill v týhle větvi — league table čte přímo `citations`,
##     own-domain rate čte existující `analysis_results` z fáze 3 (design decision 6).
## 11. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na tuhle větev
##     (viz Completion Checklist v TASKS_PHASE4.md), a upravit docs/REQUIREMENTS.md §4.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-phase4-dashboard-v0.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_PHASE4.md — CELÉ, hlavně design decisions 1-11

KONTEXT: Fáze 1 (docs/TASKS.md), fáze 2 (docs/TASKS_PHASE2.md), runs export
(docs/TASKS_EXPORT.md) a fáze 3 (docs/TASKS_PHASE3.md) jsou hotové a
smergnuté do master. Tahle větev přidává dashboard v0 — league table domén
citovaných AI providery a časovou řadu objemu citací/běhů, pro jednoho
vybraného klienta. ŽÁDNÝ NOVÝ ANALYSIS SKILL — čte se přímo z
citations/runs/raw_responses; own-domain citation rate je bonus z
existujícího mention_visibility výsledku (fáze 3), ne nový výpočet.

KRITICKÉ:
- Hybrid architektura zůstává: Jinja2 shell + JEDEN Vue3 ostrůvek z CDN
  (pinnutá verze, global build, žádný build step/npm/Vite). Nepřidávej
  Chart.js ani jinou JS knihovnu — time series graf je ruční inline SVG
  (design decision 3).
- Dashboard je scoped na jednoho klienta (client_id povinný query param),
  ne all-clients agregát (design decision 4).
- Časový bucket je pevně týdenní, žádný den/týden toggle (design
  decision 5).
- normalize_domain je sdílený helper v app/utils.py (po P4-1), použitý
  jak v app/analysis/mention_visibility.py, tak v dashboard league table
  — nikdy dvě nezávislé kopie doménové normalizace.
- Filtry žijí uvnitř Vue aplikace (v-model), ne jako Jinja2 <form> — jinak
  se filtr nezmění bez reloadu stránky, což je celý smysl Vue ostrůvku tady.
- Prázdné stavy (klient bez běhů, klient bez domain) ukazují "—"/vysvětlující
  text, nikdy zavádějící 0/false (design decision 11).

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX pro zbytek
appky, Alembic migrace, Docker Compose. Backend kód anglicky vč.
komentářů/error_code, UI texty vždy přes t() mechanismus v
app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom
commitu. Tailwind CDN pro styling (žádný vlastní CSS soubor).

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation, AnalysisResult) — appka je
  jen ČTE, dashboard nic nezapisuje ani needituje.
- Nová migrace pro každou schema/index změnu, navazující revision ID
  (poslední je 0013 — nová je 0014).
- Každá route funkce dostane docstring; každý netriviální Query/Form/Field
  parametr description=....
- Nikdy git commit ani git push bez tvého výslovného potvrzení — i po
  tom, co agent sám navrhne commit message, čeká na "ano, commitni" než
  cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message.
Nikdy nespouštěj git add/commit/push sám bez výslovného pokynu — a to
i tehdy, když jsi zprávu sám navrhl v předchozí větě.
```

---
---

## PROMPT P4-1 — Foundations: performance indexy + sdílený normalize_domain helper

### DONE — commit 2136eb9

```
Task: Prompt P4-1 — dashboard foundations (indexes + shared domain helper)

Přečti docs/TASKS_PHASE4.md úkol P4-T1 CELÝ, hlavně design decision 6 a 7
(proč se domain normalizace sdílí místo duplikuje, proč jsou tohle jen
indexy bez změny tvaru schématu).

1. Nová migrace 0014 (žádná ALTER TABLE, jen indexy):
   - CREATE INDEX idx_citations_source_domain ON citations(source_domain)
   - CREATE INDEX idx_prompts_prompt_set ON prompts(prompt_set_id)
   - CREATE INDEX idx_runs_started_at ON runs(started_at)
2. app/utils.py — přesuň _normalize_domain z
   app/analysis/mention_visibility.py sem jako veřejnou
   normalize_domain(domain: str) -> str. Beze změny chování (lowercase +
   ořízni 'www.' prefix), jen přesun + veřejný název + docstring
   vysvětlující sdílení mezi mention_visibility skillem a dashboard league
   table.
3. app/analysis/mention_visibility.py — nahraď lokální _normalize_domain
   importem from app.utils import normalize_domain, uprav volání v
   _matching_citation_domains. Žádná jiná změna chování.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. pytest tests/test_analysis_mention_visibility.py — musí zůstat zelené
   beze změny (refactor nesmí změnit chování).
3. V DB ověř přes \d citations / \d prompts / \d runs, že nové indexy
   existují.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
refactor(dashboard): extract shared domain-normalization helper and add supporting indexes
```

---
---

## PROMPT P4-2 — Dashboard JSON API: agregační dotazy

### DONE — commit 4e0d64b

```
Task: Prompt P4-2 — dashboard aggregation API (summary/domains/timeseries)

Přečti docs/TASKS_PHASE4.md úkol P4-T2 CELÝ, hlavně design decision 6, 8, 9
(own-domain rate ze existujících analysis_results, Pydantic response
modely colocated v routeru, routy bez /admin prefixu).
Prerekvizita: P4-1 hotový (normalize_domain existuje v app/utils.py).

1. Nový app/routers/dashboard.py:
   - Query parametry (Pydantic Query s description=... na netriviálních
     polích): client_id: int (povinný, 404 AppError když klient
     neexistuje), range: Literal["30d","90d","quarter","all"] = "90d",
     market_id: int | None = None, provider_id: int | None = None.
   - _range_bounds(range: str) -> tuple[datetime | None, datetime | None]
     — přeloží range shorthand na skutečné date_from/date_to.
   - _scoped_runs_query(db, client_id, date_from, date_to, market_id,
     provider_id) — SQLAlchemy Select: runs JOIN prompts JOIN prompt_sets
     (client_id filtr) JOIN ai_models (provider_id filtr), runs.market_id
     filtr, runs.status == 'success', started_at bounds. Vrací Select, ne
     materializovaný list — každý endpoint si napojí vlastní GROUP
     BY/JOIN.
   - GET /dashboard/api/summary -> Pydantic DashboardSummary (runs_count,
     citations_count, distinct_domains_count, own_domain_rate: float |
     None — None když client.domain je None, jinak podíl runů v rozsahu s
     analysis_results.output->>'cited' = 'true' pro mention_visibility
     skill mezi runy, co vůbec analysis_result mají).
   - GET /dashboard/api/domains?limit=10 -> list[DomainRow] (rank, domain,
     citations_count, run_coverage_pct, first_seen: date, last_seen: date,
     is_own_domain: bool přes normalize_domain), seřazené citations_count
     DESC.
   - GET /dashboard/api/timeseries?metric=citations|runs|own_rate ->
     TimeseriesResponse (weeks: list[WeekPoint {week_start: date, value:
     float}]) — date_trunc('week', runs.started_at) bucket. Prázdné týdny
     v rozsahu MUSÍ mít value=0, ne vynechaný bod.
   - Docstring na každé route funkci.
2. app/main.py — zaregistruj router.

Po dokončení:
1. docker compose up -d --build
2. /docs (Swagger) — všechny tři endpointy vidět se správnými popisky.
3. curl proti všem třem s reálným client_id z existujících dat (klient s
   běhy z fáze 1-3) — tvar odpovědi odpovídá modelům, čísla dávají smysl.
4. curl bez client_id -> strukturovaná 422. curl s neexistujícím
   client_id -> strukturovaná 404.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(dashboard): add citation league table and time series aggregation API
```

---
---

## PROMPT P4-3 — Dashboard stránka + Vue3 ostrůvek

### DONE — commit 760ba51

```
Task: Prompt P4-3 — dashboard page with Vue3 island

Přečti docs/TASKS_PHASE4.md úkol P4-T3 CELÝ, hlavně design decision 1-3,
10 (hybrid, CDN Vue, ruční SVG graf, filtry uvnitř Vue ne Jinja formulář).
Prerekvizita: P4-2 hotový (API endpointy existují a fungují).

1. app/routers/dashboard.py — nová GET /dashboard route: načte seznam
   klientů (id, name), marketů, providerů pro select options, vyrenderuj
   dashboard/index.html. Docstring.
2. Nový app/templates/dashboard/index.html ({% extends "base.html" %}):
   - <script type="application/json" id="dashboard-init"> s klienty/
     markety/providery jako JSON (Jinja | tojson filtr).
   - <div id="dashboard-app"> mount point.
   - <script src="https://cdn.jsdelivr.net/npm/vue@<ověř aktuální stabilní
     verzi>/dist/vue.global.prod.js"></script> — pinnutá verze, ne "latest".
   - Inline <script> s Vue Composition API komponentou: setup() čte
     #dashboard-init, drží reaktivní filters (client_id, range, market_id,
     provider_id) a data (summary/domains/timeseries), fetch() proti P4-2
     endpointům při mountu a při každé změně filtru (watch) — ŽÁDNÝ page
     reload.
   - Layout: filter bar v jedné řadě, 4 KPI dlaždice, dvousloupcové main
     (league table + time series), responsive na ~640px/~1024px
     (jednosloupcové, KPI 2x2) — Tailwind utility třídy, žádný vlastní CSS
     soubor.
   - League table: rank, doména (own-domain badge), citace + share bar
     (relativní k citations_count první řádky), "cited in" %, first/last
     seen (skryté pod 640px).
   - Time series: ruční inline SVG, metric toggle (Citations/Runs/Own-
     domain rate), hover crosshair + tooltip, endpoint hodnota v grafu.
3. app/templates/base.html — nav odkaz /dashboard na top-level (vedle
   /clients, ne v "Configuration" dropdownu).
4. i18n (EN+DE, jeden commit) — nav.dashboard, dashboard.title,
   dashboard.filter_client/filter_range/filter_market/filter_provider,
   dashboard.kpi_runs/kpi_citations/kpi_domains/kpi_own_rate,
   dashboard.table_title, dashboard.table_rank/domain/citations/cited_in/
   first_seen/last_seen, dashboard.own_domain_badge, dashboard.chart_title,
   dashboard.metric_citations/metric_runs/metric_own_rate.

Po dokončení:
1. docker compose up -d --build
2. /dashboard v prohlížeči na klientovi s existujícími běhy — league table
   i graf ukazují reálná čísla, ne mock.
3. Změna filtru (klient, rozsah, market, provider) -> tabulka i graf se
   přepočítají BEZ plného reloadu stránky (ověř network tab: jen fetch,
   žádná document navigation).
4. Metric toggle na grafu přepíná data, hover ukazuje tooltip se správnou
   hodnotou.
5. Ověř na ~640px/~1024px/desktop šířce.
6. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(dashboard): add dashboard page with Vue3 filter/table/chart island
```

---
---

## PROMPT P4-4 — Empty states + responsive polish

### DONE — commit 41463f6

```
Task: Prompt P4-4 — dashboard empty states and responsive polish

Přečti docs/TASKS_PHASE4.md úkol P4-T4 CELÝ, hlavně design decision 11.
Prerekvizita: P4-3 hotový.

1. Klient bez jediného běhu v rozsahu -> KPI dlaždice ukážou "—"/0 s
   vysvětlujícím textem, league table ukáže empty-state zprávu místo
   prázdné tabulky.
2. Klient bez vyplněné domain -> own-domain KPI dlaždice a is_own_domain
   badge ukážou "—" (not applicable), NIKDY 0 %/false — to by vypadalo
   jako "nikdy citovaný", což je jiná informace než "nedá se zjistit".
3. range=all u klienta s běhy staršími než pár týdnů -> time series
   bucketing pořád dává čitelný graf (žádné přeplnění x-labelů — "každý
   druhý label" logika, pokud týdnů je hodně).
4. Finální responsive průchod na ~640px/~1024px/desktop se všemi výše
   uvedenými stavy, ne jen se šťastnou cestou.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči: dočasně vyber/vytvoř klienta bez běhů -> ověř empty
   state. Klienta bez domain -> ověř "—" na own-domain KPI/badge.
3. Ověř na ~640px/~1024px/desktop šířce (všechny stavy).
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(dashboard): handle empty states and verify responsive layout
```

---
---

## PROMPT P4-5 — Testy

### DONE — commit 17a7412 (plus code-review fix pass: commit a81323e, findings viz
docs/TASKS_PHASE4.md Completion Checklist)

```
Task: Prompt P4-5 — test coverage for dashboard aggregation and API

Přečti docs/TASKS_PHASE4.md úkol P4-T5 CELÝ.
Prerekvizita: P4-1 až P4-4 hotové.

1. Nový tests/test_dashboard.py:
   - Aggregation logika (přes FakeAdapter-seedovaná fixture data — víc
     runů/citací pro jednoho klienta napříč víc týdny, aspoň jedna
     own-domain citace): summary čísla sedí na ručně spočítaný fixture,
     own_domain_rate je None když client.domain je None; domains pořadí
     podle citations_count DESC, is_own_domain správně označená doména i
     subdoména, run_coverage_pct sedí; timeseries týdny bez dat mají
     value=0 (ne chybějící bod), metric=own_rate sedí na
     analysis_results.cited fixture data.
   - API endpointy: chybějící client_id -> 422; neexistující client_id ->
     strukturovaná 404; filtry (range, market_id, provider_id) skutečně
     omezují výsledek (run mimo rozsah/market/provider se v odpovědi
     neobjeví); jen status=='success' runy se počítají (error run bez
     raw_response se nezapočítá ani nezpůsobí pád dotazu).
   - GET /dashboard HTML route -> 200, obsahuje dashboard-init JSON blok
     se seznamem klientů.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover dashboard aggregation queries and API endpoints
```
