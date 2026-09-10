# SignalMap — Tasks: Phase 4 (Dashboard v0 — domain league table + time series)

## v1.0 | Září 2026
## Branch: feature/signalmap-phase4-dashboard-v0
## Task ID prefix: P4

> Fáze 1 (`docs/TASKS.md`), fáze 2 (`docs/TASKS_PHASE2.md`), production-readiness
> hardening (`docs/TASKS_HARDENING.md`), runs export (`docs/TASKS_EXPORT.md`) a fáze 3
> (`docs/TASKS_PHASE3.md`) jsou hotové a smergnuté. Tenhle dokument pokrývá krok 4 z
> build sequencing (`signalmap-conventions` skill) — dashboard nad tím, co už v databázi
> existuje.
>
> Cíl větve: pro jednoho vybraného klienta ukázat (a) league table domén, které AI
> provideři citují, když o klientovi mluví, a (b) časovou řadu objemu citací/běhů v
> čase. **Žádný nový analysis skill** — čte se přímo z `citations`/`runs`/
> `raw_responses`, které už existují. Jediná výjimka je "own-domain citation rate", což
> je bonus zdarma z `analysis_results` (fáze 3, `mention_visibility.cited`), ne nový
> výpočet.
>
> Design byl probraný a odsouhlasený s uživatelem před psaním tohohle dokumentu
> (hybrid vs. plný Vue frontend, charting knihovna, mockup vizuálu — viz artifact
> odkazovaný v konverzaci). Mockup: "Domain Signal Dashboard" artifact z přípravné
> konverzace — vizuální jazyk (IBM Plex Sans/Mono, muted paleta, league table +
> time series se share-bary a hover tooltipem) je referenční, ne závazný pixel-for-pixel.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Hybrid zůstává — Jinja2 shell + JEDEN Vue3 ostrůvek, ne přechod na plný Vue
   frontend.** Probráno s uživatelem: přechod na plnou Vue SPA (build step, FastAPI
   jako čisté JSON API všude) dává smysl, až (a) víc obrazovek sdílí stav napříč sebou,
   (b) je potřeba client-side routing mezi pohledy, nebo (c) interaktivních obrazovek
   je víc než CRUD. Dashboard je první takováhle obrazovka — žádná z podmínek není
   splněná. Revidovat při source/signal map fázi (další plánovaná interaktivní
   obrazovka, pravděpodobně se stejnými filtry klient/rozsah dat — tam už sdílení
   stavu dává smysl).
2. **Vue3 z CDN, pinnutá verze, global build — žádný build step.** Stejný vzor, jakým
   `base.html` dnes načítá Tailwind (`cdn.tailwindcss.com`) a HTMX (`unpkg.com`).
   Script tag `<script src="https://cdn.jsdelivr.net/npm/vue@<pinned>/dist/vue.global.prod.js">`,
   ne přes npm/Vite.
3. **Charting: ruční inline SVG (jako v mockupu), ne Chart.js.** Probráno s uživatelem
   — v0 potřebuje jeden typ grafu (line/area, jedna metrika najednou přes toggle), což
   nezdůvodňuje novou JS závislost. Revidovat, až (pokud) v1 bude potřebovat víc
   simultánních sérií nebo víc typů grafů najednou.
4. **Dashboard je scoped na jednoho klienta, ne all-clients agregát.** `client_id` je
   povinný query parametr — stejný vzor jako zbytek appky (všechno je dnes client-
   scoped). Cross-client "kdo je nejvíc citovaný napříč všemi klienty" pohled je
   vědomě mimo rozsah v0 — možný v1 nápad, ne dnešní úkol.
5. **Časový bucket pevně týdenní, žádný toggle den/týden.** Fáze 1 FR-9: runy jsou
   pořád jen manuální (žádné scheduled runy), takže denní bucket by byl u typického
   objemu řídký/prázdný většinu dní. Nastavitelná granularita je v1 nice-to-have, ne
   dnešní úkol.
6. **Own-domain citation rate čte `AnalysisResult.output->>'cited'` (fáze 3
   `mention_visibility`), nepočítá se znovu v dashboard query vrstvě.** Kdyby dashboard
   měl vlastní kopii doménové normalizace/subdoménové tolerance, riskuje se, že se
   časem rozejde od logiky v `app/analysis/mention_visibility.py` — dvě nezávislé
   pravdy o tom, co je "vlastní doména". Místo toho se `_normalize_domain` (dnes
   private v `mention_visibility.py`) přesune do `app/utils.py` jako sdílené
   `normalize_domain()` a použije se na obou místech: fáze-3 skill i "Own domain"
   badge v league table (P4-T1). League table samotná (citace seskupené podle domény)
   žádnou analýzu nepotřebuje — čte se přímo z `citations.source_domain`, přesně jak
   bylo zadáno.
7. **Nové indexy, žádná změna tvaru schématu.** `citations.source_domain` (GROUP BY v
   league table), `prompts.prompt_set_id` (JOIN pro client-scoping), `runs.started_at`
   (date-range filtr) — pořád přes Alembic migraci (0014), i když je to jen index, ne
   `ALTER TABLE ... ADD COLUMN` — pravidlo "nikdy manuální ALTER TABLE proti běžící
   databázi" platí i pro indexy.
8. **Nové Pydantic response modely pro 3 JSON endpointy — první opravdové JSON API v
   appce.** Zatím každý router vrací HTML/redirect; dashboard JSON endpointy (pro Vue
   fetch) jsou první výjimka. Modely zůstávají colocated v `app/routers/dashboard.py`
   (3 malé modely nezakládají nový `app/schemas/` balíček — to má smysl, až/pokud
   jich přibude víc).
9. **Routy bez `/admin` prefixu: `/dashboard` (stránka) + `/dashboard/api/summary
   |domains|timeseries` (JSON).** Stejné zdůvodnění jako P2 design decision 1 — appka
   nemá auth/role rozlišení, prefix by byl dnes jen kosmetický.
10. **Filtry žijí uvnitř Vue aplikace (`v-model`), ne jako Jinja2 `<form>`.** To je to,
    co umožní "změna filtru → refetch → překreslení bez reloadu" — přesně důvod, proč
    tahle obrazovka vůbec dostává Vue ostrůvek (skill: "Vue3 islands... dashboard,
    live filtering"). Jinja2 renderuje jen počáteční shell stránky a seznamy pro
    selecty (klienti, markety, provideři) jako JSON data island (`<script
    type="application/json" id="dashboard-init">`), který Vue přečte při mountu.
11. **Explicitní edge-case stavy, ne mlčenlivé nuly.** Klient bez jediného běhu → KPI
    dlaždice a tabulka ukážou prázdný stav s textem, ne 0/prázdný graf bez vysvětlení.
    Klient bez vyplněné `domain` → own-domain KPI a badge ukážou "—" (not applicable),
    ne 0 % (což by vypadalo jako "nikdy není citovaný", což je jiná informace).
    Stejný instinkt jako `has_citations` (FR-13) — nikdy neponechávat nejednoznačné.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| P4-T1 | Foundations: performance indexy + sdílený `normalize_domain` helper | ✅ |
| P4-T2 | Dashboard JSON API: agregační dotazy (summary/domains/timeseries) | ⏳ |
| P4-T3 | Dashboard stránka + Vue3 ostrůvek (filtry, KPI, league table, graf) | ⏳ |
| P4-T4 | Empty states + responsive polish | ⏳ |
| P4-T5 | Testy: agregační dotazy + API endpointy | ⏳ |

Pořadí je vynucené: P4-T2 potřebuje index/helper z P4-T1 (`normalize_domain` pro own-
domain výpočet v `domains` endpointu); P4-T3 staví na API z P4-T2 (Vue fetchuje z
hotových endpointů, ne naopak); P4-T4 rozšiřuje P4-T3 o stavy, které se dají ověřit až
je UI hotové; P4-T5 testuje všechno předchozí najednou.

---

## P4-T1 — Foundations: performance indexy + sdílený `normalize_domain` helper

**Target:** nová migrace `alembic/versions/0014_*.py`, `app/utils.py`,
`app/analysis/mention_visibility.py`

1. Nová migrace 0014 (pouze indexy, žádná změna tvaru):
   - `CREATE INDEX idx_citations_source_domain ON citations(source_domain)`
   - `CREATE INDEX idx_prompts_prompt_set ON prompts(prompt_set_id)`
   - `CREATE INDEX idx_runs_started_at ON runs(started_at)`
2. `app/utils.py` — přesunout `_normalize_domain` z `app/analysis/mention_visibility.py`
   sem jako veřejnou `normalize_domain(domain: str) -> str` (lowercase, ořízni `www.`
   prefix — beze změny chování, jen přesun + veřejný název). Docstring vysvětlující, že
   je sdílená mezi `mention_visibility` skillem a dashboard league table.
3. `app/analysis/mention_visibility.py` — nahradit lokální `_normalize_domain` importem
   `from app.utils import normalize_domain`, upravit volání. `_matching_citation_domains`
   beze změny chování.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. `pytest tests/test_analysis_mention_visibility.py` — beze změny, zelené (refactor
   nesmí změnit chování).
3. V DB ověřit `\d citations`/`\d prompts`/`\d runs` — nové indexy existují.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
refactor(dashboard): extract shared domain-normalization helper and add supporting indexes
```

---

## P4-T2 — Dashboard JSON API: agregační dotazy

**Target:** nový `app/routers/dashboard.py`, `app/main.py` (registrace routeru)

1. Sdílené query parametry (Pydantic `Query(...)` s `description=...` na každém
   netriviálním poli, per NFR-4):
   - `client_id: int` (povinný) — 404 strukturovaná chyba (`AppError`), pokud klient
     neexistuje.
   - `range: Literal["30d", "90d", "quarter", "all"] = "90d"` — přeloží se na
     `date_from`/`date_to` bounds interní funkcí `_range_bounds(range: str) ->
     tuple[datetime | None, datetime | None]`.
   - `market_id: int | None = None`, `provider_id: int | None = None` — volitelné
     dodatečné filtry.
2. Sdílená interní funkce `_scoped_runs_query(db, client_id, date_from, date_to,
   market_id, provider_id)` — základ pro všechny tři endpointy: `runs` JOIN `prompts`
   JOIN `prompt_sets` (`client_id` filtr) JOIN `ai_models` (pro `provider_id` filtr),
   `runs.market_id` filtr (per-run override má přednost — stejná logika jako run
   trigger), `runs.status == 'success'` (jen úspěšné běhy mají `raw_responses`/
   `citations` co zobrazit), `started_at` bounds. Vrací SQLAlchemy `Select`, ne
   materializovaný list — každý endpoint si na to napojí vlastní `GROUP BY`/`JOIN`.
3. `GET /dashboard/api/summary` → Pydantic `DashboardSummary` (`runs_count: int`,
   `citations_count: int`, `distinct_domains_count: int`, `own_domain_rate: float |
   None` — `None` když `client.domain` není vyplněná, jinak podíl runů v rozsahu, kde
   `AnalysisResult.output->>'cited' = 'true'` pro `mention_visibility` skill, mezi
   runy, co vůbec analysis_result mají).
4. `GET /dashboard/api/domains?limit=10` → Pydantic `list[DomainRow]` (`rank: int`,
   `domain: str`, `citations_count: int`, `run_coverage_pct: float` — % runů v rozsahu,
   kde se doména objevila aspoň jednou, `first_seen: date`, `last_seen: date`,
   `is_own_domain: bool` — `normalize_domain(domain) == normalize_domain(client.domain)
   nebo subdoména`, per P4-T1 helper), seřazené `citations_count DESC`.
5. `GET /dashboard/api/timeseries?metric=citations|runs|own_rate` → Pydantic
   `TimeseriesResponse` (`weeks: list[WeekPoint]`, `WeekPoint` = `{week_start: date,
   value: float}`) — `date_trunc('week', runs.started_at)` bucket; `own_rate` metrika
   dělá stejný `AnalysisResult.output->>'cited'` join jako `summary`, agregovaný po
   týdnech. Prázdné týdny v rozsahu (žádný run) → `value = 0`, ne vynechaný bod
   (časová řada nesmí mít díry, jinak graf zavádí).
6. Docstring na každé route funkci (co vrací, jaké filtry akceptuje — Swagger `/docs`
   musí být čitelná bez čtení kódu).
7. `app/main.py` — zaregistrovat router.

Po dokončení:
1. `docker compose up -d --build`
2. `/docs` (Swagger) — všechny tři endpointy vidět se správnými popisky parametrů.
3. `curl` proti všem třem s reálným `client_id` z existujících dat (klient s běhy z
   fáze 1–3) — tvar odpovědi odpovídá modelům, čísla dávají smysl (ruční sanity check
   proti tomu, co je vidět na `/runs`/`/prompts` detailu daného klienta).
4. `curl` bez `client_id` → strukturovaná 422, ne surová chyba. `curl` s neexistujícím
   `client_id` → strukturovaná 404 (`AppError`).
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): add citation league table and time series aggregation API
```

---

## P4-T3 — Dashboard stránka + Vue3 ostrůvek

**Target:** nový `app/templates/dashboard/index.html`, `app/routers/dashboard.py`
(rozšíření o `GET /dashboard` HTML route), `app/templates/base.html` (nav odkaz),
`app/i18n/en.json`, `app/i18n/de.json`

1. `app/routers/dashboard.py` — nová `GET /dashboard` route: načte seznam klientů
   (`id`, `name`), marketů, providerů pro select options, vyrenderuje
   `dashboard/index.html`. Docstring.
2. `app/templates/dashboard/index.html` (`{% extends "base.html" %}`):
   - `<script type="application/json" id="dashboard-init">` s klienty/markety/
     providery jako JSON (Jinja `| tojson` filtr) — počáteční data pro Vue, ne
     hardcoded do JS.
   - `<div id="dashboard-app">` — mount point.
   - `<script src="https://cdn.jsdelivr.net/npm/vue@<pinned>/dist/vue.global.prod.js"></script>`
     (pinnutá verze), pak inline `<script>` s Vue Composition API komponentou:
     - `setup()` čte `#dashboard-init`, drží reaktivní `filters` (client_id, range,
       market_id, provider_id) a `data` (summary/domains/timeseries), `fetch()` proti
       P4-T2 endpointům při mountu a při každé změně filtru (`watch`), žádný page
       reload.
     - Vizuální jazyk podle mockup artifactu z přípravné konverzace: filter bar v
       jedné řadě, 4 KPI dlaždice, dvousloupcové `main` (league table + time series),
       responsive na ~640px/~1024px (jednosloupcové, KPI 2×2).
     - League table: rank, doména (own-domain badge), citace + share bar (relativní k
       `citations_count` první řádky), "cited in" %, first/last seen (skryté pod
       640px).
     - Time series: ruční inline SVG (per design decision 3), metric toggle
       (Citations/Runs/Own-domain rate), hover crosshair + tooltip, endpoint hodnota
       přímo v grafu.
   - Tailwind utility třídy pro layout/spacing (žádné inline `<style>` blok navíc —
     appka dnes stylizuje přes Tailwind, ne vlastní CSS soubor).
3. `app/templates/base.html` — nav odkaz `/dashboard` na top-level (vedle `/clients`,
   ne v "Configuration" dropdownu — je to primární obrazovka, ne admin nastavení).
4. i18n (EN+DE, jeden commit) — `nav.dashboard`, `dashboard.title`,
   `dashboard.filter_client`/`filter_range`/`filter_market`/`filter_provider`,
   `dashboard.kpi_runs`/`kpi_citations`/`kpi_domains`/`kpi_own_rate`,
   `dashboard.table_title`, `dashboard.table_rank`/`domain`/`citations`/`cited_in`/
   `first_seen`/`last_seen`, `dashboard.own_domain_badge`, `dashboard.chart_title`,
   `dashboard.metric_citations`/`metric_runs`/`metric_own_rate`.

Po dokončení:
1. `docker compose up -d --build`
2. `/dashboard` v prohlížeči na klientovi s existujícími běhy (fáze 1–3 data) — league
   table i graf ukazují reálná čísla, ne mock.
3. Změna filtru (klient, rozsah, market, provider) → tabulka i graf se přepočítají bez
   plného reloadu stránky (ověřit network tab: jde jen `fetch`, ne document navigation).
4. Metric toggle na grafu přepíná data, hover ukazuje tooltip se správnou hodnotou.
5. Ověřit na ~640px/~1024px/desktop šířce.
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): add dashboard page with Vue3 filter/table/chart island
```

---

## P4-T4 — Empty states + responsive polish

**Target:** `app/templates/dashboard/index.html`, `app/routers/dashboard.py`

1. Klient bez jediného běhu v rozsahu → KPI dlaždice ukážou "—"/0 s vysvětlujícím
   textem (ne prázdná tabulka/graf bez kontextu), league table ukáže empty-state
   řádek/zprávu místo prázdné tabulky.
2. Klient bez vyplněné `domain` → own-domain KPI dlaždice a `is_own_domain` badge
   ukážou "—" (not applicable), ne `0 %`/`false` (design decision 11 — nesmí to
   vypadat jako "nikdy citovaný", což je jiná informace než "nedá se zjistit").
3. `range=all` u klienta s běhy staršími než pár týdnů → time series bucketing pořád
   dává čitelný graf (žádné přeplnění x-labelů — reuse stejnou "každý druhý label"
   logiku jako mockup, pokud týdnů je hodně).
4. Finální responsive průchod na ~640px/~1024px/desktop se všemi výše uvedenými stavy,
   ne jen s daty.

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči: dočasně vytvořit/vybrat klienta bez běhů → ověřit empty state.
   Klienta bez `domain` → ověřit "—" na own-domain KPI/badge.
3. Ověřit na ~640px/~1024px/desktop šířce (všechny stavy, ne jen šťastnou cestu).
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): handle empty states and verify responsive layout
```

---

## P4-T5 — Testy

**Target:** nový `tests/test_dashboard.py`

1. Aggregation logika (přes `FakeAdapter`-seedovaná fixture data — víc runů/citací pro
   jednoho klienta napříč víc týdny, aspoň jedna own-domain citace):
   - `summary` — `runs_count`/`citations_count`/`distinct_domains_count` sedí na ručně
     spočítaný fixture; `own_domain_rate` je `None`, když `client.domain` je `None`,
     jinak správné číslo.
   - `domains` — pořadí podle `citations_count DESC`; `is_own_domain` správně
     označená doména i její subdoména; `run_coverage_pct` sedí.
   - `timeseries` — týdny bez dat mají `value = 0`, ne chybějící bod; `metric=own_rate`
     sedí na `analysis_results.cited` fixture data.
2. API endpointy:
   - Chybějící `client_id` → 422. Neexistující `client_id` → strukturovaná 404.
   - Filtry (`range`, `market_id`, `provider_id`) skutečně omezují výsledek — test s
     runem mimo rozsah/market/provider se v odpovědi neobjeví.
   - Jen `status == 'success'` runy se počítají (error run bez `raw_response` se
     nezapočítá do `runs_count` ani nezpůsobí pád dotazu).
3. `GET /dashboard` HTML route — 200, obsahuje `dashboard-init` JSON blok se seznamem
   klientů.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover dashboard aggregation queries and API endpoints
```

---

## Completion Checklist

- [ ] `citations.source_domain`/`prompts.prompt_set_id`/`runs.started_at` mají indexy;
      `normalize_domain` je sdílený helper v `app/utils.py`, `mention_visibility.py`
      ho importuje místo vlastní kopie
- [ ] `/dashboard/api/summary|domains|timeseries` fungují, zdokumentované ve Swaggeru,
      strukturované chyby na chybějící/neexistující `client_id`
- [ ] `/dashboard` — filtry, KPI dlaždice, league table, time series graf fungují nad
      reálnými daty, filtr se mění bez reloadu stránky
- [ ] Own-domain citation rate čte `analysis_results` (fáze 3), nepočítá se znovu
- [ ] Empty states (žádné běhy, chybějící `domain`) neukazují zavádějící čísla
- [ ] `pytest` sada zelená, pokrývá agregační dotazy i API endpointy
- [ ] `docs/TASKS.md` — poznámka, že fáze 4 větev existuje a co pokrývá (odkaz na
      tenhle soubor) — **hotovo v rámci přípravy tohoto dokumentu**, ověřit že zůstává
      aktuální po mergi
- [ ] `docs/REQUIREMENTS.md` §4 "Explicitly Out of Scope" — odstranit/upravit
      "Dashboard, source/signal map, intervention hypotheses" řádek (jen "Dashboard"
      část — source/signal map a intervention hypotheses zůstávají mimo rozsah, to
      jsou pozdější fáze), jakmile je větev smergnutá
