# SignalMap — Tasks: Metric Definitions (vydání 4, první větev)

## v1.0 | Září 2026
## Branch: feature/signalmap-metric-definitions
## Task ID prefix: MD
## Cílová verze: v1.6.0 (MINOR) — nasazuje se společně s `feature/signalmap-export-v2`

Status: navrženo 2026-09-30 (`docs/ROADMAP.md` #26, požadavek uživatele).
Pět úkolů, bez migrace.

**Goal:** každá metrika v appce má u sebe ikonu ⓘ s krátkým vysvětlením
*co znamená* a *jak se počítá* — na desktopu při najetí myší, na mobilu
po klepnutí, z klávesnice při fokusu. Plný slovníček metrik je na `/help`.
Definice mají **jeden zdroj** (i18n), který použije dashboard, `/ops`,
`/help` i export (list „Definitions" v Export v2).

**Pořadí v rámci vydání 4:** tahle větev první → merge do `master` **bez
nasazení** → `feature/signalmap-export-v2` (EX2-T4 přidá list s definicemi
ze stejného zdroje) → EX2-T8 nasadí obě jako v1.6.0.

**Proč:** vysvětlení metrik přímo u čísla je standard analytických
nástrojů (Google Analytics, Stripe, Datadog, Mixpanel, konkurenti
Profound/Peec). Metriky SignalMap (own-domain rate, share of voice,
verdikty ověření) nejsou intuitivní a bez vysvětlení je nepřečte klient
v budoucím portálu (#14).

---

## Výchozí stav (kód k 2026-09-30, master 7807f7a)

- **Žádná metrika v appce nemá vysvětlení** — `title=`/tooltip/
  `aria-describedby` se v `dashboard/index.html`, `ops/index.html`,
  `clients/detail.html` ani `runs/detail.html` nevyskytuje.
- **Dashboard** (`app/templates/dashboard/index.html`, Vue 3 island,
  `[[ ]]` delimitery) — KPI: `kpi_runs`, `kpi_citations`, `kpi_domains`,
  `kpi_own_rate`, `kpi_share_of_voice` (+ trend v p. b.); graf s metrikami
  `metric_runs`, `metric_citations`, `metric_own_rate`,
  `metric_share_of_voice`, `metric_position`; tabulky domén (`table_*`:
  citations, cited_in, first/last seen, rank), konkurence
  (`competitive_table_*`), typy domén (`domain_type_*`), ověření citací
  (`verification_*`: verified / partial / unsupported / unverifiable,
  podle providera a domény, xAI poznámka).
- **Výpočty** — `app/services/dashboard.py`: `count_runs`,
  `citation_totals`, `own_domain_rate`, `avg_share_of_voice`,
  `weekly_values`, `domain_league_rows`, `entity_league_rows`,
  `citation_verification_rates_by_provider/_by_domain`
  (`VERDICT_BUCKETS`, `verification_display.py:80`), `delta_pct`,
  `previous_range_bounds`. Analýzy: `app/analysis/mention_visibility.py`,
  `competitive_visibility.py`.
- **/ops** (`app/templates/ops/index.html`, 75 `ops.*` klíčů) — náklady
  (`app/services/cost.py`, odhad z tokenů × ceníkových složek), úspěšnost
  runů, latence, fronta ověřování, capture důvody, osy Klient/Uživatel.
- **Detail runu / klienta** — verdikty citací (`partials/verification.html`),
  po vydání 2 kvóta a přeskočené runy.
- `/help` (`app/templates/help.html`) má nápovědu k obrazovkám a polím,
  ne slovníček metrik.
- `REQUIREMENTS.md` FR-12 a poznámka za FR-15 popisují význam
  `count(citations)` („claim-source vazby") — příklad definice, která
  dnes existuje jen v dokumentaci.

---

## Design decisions

1. **Ikona ⓘ vedle názvu metriky, ne hover na čísle.** Hover na mobilu
   a tabletu neexistuje (appka musí fungovat na ~375 / ~768 px). Chování:
   desktop — najetí myší i fokus; dotyk — klepnutí otevře, klepnutí mimo
   / Esc zavře. Přístupnost: `<button type="button" aria-describedby=…>`,
   popover s `role="tooltip"`, ovladatelné klávesnicí. Nativní
   `title=` **nestačí** (nefunguje na dotyku ani s čtečkou spolehlivě).
2. **Bez knihovny.** Appka nemá build krok; jedna malá komponenta:
   Jinja makro `metric_help(metric_id)` + sdílený delegovaný listener
   v `base.html` (stejný vzor jako `data-confirm`) + ekvivalent ve Vue
   islandu (malá komponenta se stejným markupem a CSS). Tailwind třídy,
   žádný externí tooltip balík.
3. **Jeden zdroj definic = i18n klíče.** Pro každou metriku
   `metric.<id>.name`, `metric.<id>.what` (co znamená, 1 věta),
   `metric.<id>.how` (jak se počítá: vzorec, co se (ne)započítává,
   období). DE i EN. Tooltip ukazuje `what` + `how`; slovníček na `/help`
   a list „Definitions" v exportu totéž. Žádný text definice v šabloně
   ani v Pythonu mimo i18n.
4. **Katalog metrik v kódu** — `app/metrics_catalog.py`:
   `METRICS: tuple[MetricDef, ...]` (`id`, `group` — dashboard / ops /
   verification / quota, `unit` — count / percent / pp / usd / ms,
   `source` — odkaz na funkci, která ji počítá, jako text pro vývojáře).
   Slouží slovníčku (pořadí, skupiny), testu pokrytí a exportu. Není to
   seznam providerů ani konfigurace, jen popis UI — v souladu s §3.
5. **Definice se píšou z kódu, ne z paměti.** MD-T1 projde každou funkci
   výpočtu a zapíše, co skutečně dělá (jmenovatel, filtry, co se
   vylučuje — např. test klienti v `/ops`, `source_reachable` mimo
   buckety ověření, citace = claim-source vazby). **Nesrovnalosti mezi
   kódem, UI popiskem a `REQUIREMENTS.md` se nehotfixují potichu** —
   zapíšou se do sekce „Nálezy" tohoto dokumentu a uživatel rozhodne.
6. **Krátce.** `what` ≤ 1 věta, `how` ≤ 2 věty; delší vysvětlení jen ve
   slovníčku (`/help#metric-<id>`), tooltip má odkaz „Více".
7. **Test pokrytí:** každý `METRICS[i].id` má všechny tři klíče v en i
   de; každé volání `metric_help('<id>')` / Vue `<metric-help id=…>`
   v šablonách odkazuje na existující id (grep šablon v testu).
8. **Rozsah obrazovek:** dashboard (KPI, graf, tabulky, ověření), `/ops`
   (KPI dlaždice, tabulky nákladů/úspěšnosti/fronty), detail runu
   (legenda verdiktů), detail klienta (kvóta z vydání 2). Formuláře
   a CRUD obrazovky ne — to nejsou metriky.
9. **Bez migrace, bez změny výpočtů.** Tahle větev jen popisuje; pokud
   MD-T1 najde chybu ve výpočtu, oprava je samostatný `fix` commit
   s potvrzením uživatele (nebo samostatná větev).

---

## Nálezy

*(Vyplní MD-T1 — nesrovnalosti mezi kódem, popisky a dokumentací.)*

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Katalog metrik + definice z kódu (DE/EN) | ⏳ |
| T2 | Komponenta ⓘ (Jinja makro + Vue) | ⏳ |
| T3 | Nasazení na obrazovky + slovníček na `/help` | ⏳ |
| T4 | Test pokrytí definic | ⏳ |
| T5 | Dokumentace + CHANGELOG | ⏳ |

---

## T1 — Katalog metrik + definice z kódu

**Target:** `app/metrics_catalog.py` (nový), `app/i18n/en.json`,
`app/i18n/de.json`, sekce „Nálezy" v tomto dokumentu

1. Projít každou metriku zobrazenou na obrazovkách z design decision 8
   a dohledat funkci, která ji počítá (`app/services/dashboard.py`,
   `ops_dashboard.py`, `cost.py`, `verification_display.py`,
   `app/analysis/*`).
2. Tabulka pro uživatele **před zápisem**: metrika → funkce → jak se
   počítá (vlastními slovy z kódu) → navržené `what`/`how` (EN) →
   nesrovnalosti. Uživatel schválí texty.
3. `METRICS` katalog + i18n klíče DE/EN podle schválené tabulky.
4. Nesrovnalosti do „Nálezy" (design decision 5).

**Done when:** uživatel odsouhlasil tabulku; klíče zapsané.

**Expected commit:** `feat(i18n): add a catalog of metric definitions`

---

## T2 — Komponenta ⓘ

**Target:** `app/templates/partials/macros.html`, `app/templates/base.html`,
`app/templates/dashboard/index.html` (Vue komponenta), testy šablon
(`tests/test_templating.py` nebo nový)

1. Makro `metric_help(metric_id)` — tlačítko ⓘ + skrytý popover
   s `what`, `how` a odkazem „Více" na `/help#metric-<id>`; unikátní id
   pro `aria-describedby`.
2. Delegovaný listener v `base.html`: hover/focus (desktop), click/tap
   toggle, Esc a klik mimo zavírá; popover se nevejde → otočit nad/pod
   (jednoduché, bez knihovny). Jeden otevřený najednou.
3. Vue: komponenta `metric-help` se stejným markupem a CSS; definice
   předané z routeru v `dashboard_init` (jen potřebná id), ne duplikované
   v JS.
4. Test: makro vykreslí přístupný markup (button, aria, role).

**Done when:** komponenta funguje na jedné metrice dashboardu a jedné
v `/ops` na ~375 / ~768 px / desktop (myš, klepnutí, klávesnice) —
screenshoty.

**Expected commit:** `feat(i18n): add an accessible metric help popover`

---

## T3 — Nasazení na obrazovky + slovníček na `/help`

**Target:** `app/templates/dashboard/index.html`, `app/routers/dashboard.py`,
`app/templates/ops/index.html`, `app/routers/ops_dashboard.py`,
`app/templates/partials/verification.html` / `runs/detail.html`,
`app/templates/clients/detail.html`, `app/templates/help.html`,
`app/routers/help.py`

1. ⓘ u všech metrik z design decision 8.
2. `/help`: sekce „Metriky" generovaná z `METRICS` (skupiny, kotvy
   `#metric-<id>`), `name` + `what` + `how`.
3. Prohlížeč ~375 / ~768 px / desktop na všech čtyřech obrazovkách +
   `/help`; ověřit, že ikony nerozbíjí rozložení KPI dlaždic a hlaviček
   tabulek na mobilu. Screenshoty.

**Done when:** screenshoty ukázané, uživatel potvrdil texty v kontextu.

**Expected commit:** `feat(i18n): explain metrics on the dashboard, ops and help pages`

---

## T4 — Test pokrytí definic

**Target:** `tests/test_i18n_coverage.py` (z vydání 1, rozšířit) nebo
`tests/test_metrics_catalog.py`

1. Design decision 7: katalog × klíče × jazyky; šablony → existující id.
2. Test testu (fiktivní id přes `monkeypatch` → chyba s názvem klíče).

**Expected commit:** `test(i18n): require a definition for every displayed metric`

---

## T5 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/REQUIREMENTS.md` (odkaz na katalog
jako zdroj definic, pokud se definice metrik v dokumentu opakují),
`docs/ROADMAP.md` #26

1. `CHANGELOG.md` `[Unreleased]` → Added: every metric on the dashboard,
   ops page, run and client pages has an ⓘ explaining what it means and
   how it is calculated; a metric glossary on the help page.
2. Nálezy z T1, které zůstaly otevřené → `docs/ROADMAP.md` (nový bod
   nebo poznámka), ať se neztratí.

**Done when:** diff ukázaný a odsouhlasený. Pak merge do `master`
(s potvrzením) **bez nasazení** — nasadí EX2-T8.

**Expected commit:** `docs(docs): document metric definitions`

---

## Ověření po nasazení (provádí se v EX2-T8)

1. Dashboard Knaufu: ⓘ u KPI a v legendě ověření — desktop hover,
   telefon klepnutí.
2. `/ops`: ⓘ u nákladů a úspěšnosti.
3. `/help#metric-own_rate` (nebo jiné id) — kotva funguje, odkaz „Více"
   z tooltipu tam vede.
4. Export (XLSX) — list „Definitions" má stejné texty jako tooltips.

---

## Co tahle větev vědomě nedělá

- **Nemění výpočty** — jen je popisuje (design decision 9).
- **Žádné tooltips u formulářových polí** — to je nápověda, ne metrika;
  pole mají vlastní `help` (např. Vision).
- **Žádná tooltip knihovna / build krok.**
- **Neřeší klientský portál** (#14) — definice ale budou připravené.
