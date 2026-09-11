# SignalMap — Claude Code Session Prompts: Phase 5 (Dashboard extensions + competitive visibility)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-phase5-competitive-visibility (z aktuálního master)
## 2. Deset kódových promptů (P5-1 až P5-10) — viz docs/TASKS_PHASE5.md "Task Index" pro
##    závislosti. P5-1, P5-2, P5-9, P5-10 jsou nezávislé na zbytku i na sobě navzájem, jdou
##    v libovolném pořadí — klidně P5-9/P5-10 jako úplně první dva, jsou nejméně rizikové.
##    P5-3 až P5-7 MUSÍ jít v pořadí (schema -> UI -> engine -> wiring -> dashboard). P5-8
##    testuje všechno.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit provádíš ty,
##    ne agent — agent NIKDY nespouští git commit/push sám bez výslovného potvrzení, a to i
##    přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_PHASE5.md přepni řádek daného task ID v tabulce "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-11 a obou schema návrhů: docs/TASKS_PHASE5.md
##    — přečti si konkrétní task ID před psaním kódu, ideálně celý soubor před P5-1.
## 9. P5-2 a P5-3 nezačínej, dokud nejsou schema návrhy (domain_classifications;
##    tracked_entities/tracked_entity_aliases) výslovně potvrzené uživatelem — viz "Schema
##    flagy" na začátku docs/TASKS_PHASE5.md. P5-1 na potvrzení nečeká.
## 10. Tahle větev NEOBSAHUJE sentiment skill (2d) ani gap/opportunity score — obojí čeká,
##     až tahle práce běží ověřená v provozu (sentiment) nebo má reálná data k dispozici
##     (gap score). Pokud se během implementace zdá lákavé přidat kus jednoho z nich "když
##     už jsme u toho", ZASTAV a zeptej se — scope creep mimo odsouhlasené.
## 11. Query-level transparentnost (search queries) NENÍ součástí týhle větve — je hotová a
##     smergnutá, viz docs/TASKS_SEARCH_QUERIES.md (všech pět SQ-T úkolů ✅).
## 12. P5-10 (help stránka) píše JEN o metrikách, co v appce dnes reálně běží (fáze 4) —
##     nepředbíhej a nepiš tam o trendových deltách/doménové typologii/share-of-voice, dokud
##     příslušný prompt (P5-1/P5-2/P5-7) neproběhl.
## 13. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na tuhle větev (viz
##     Completion Checklist v TASKS_PHASE5.md).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-phase5-competitive-visibility.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_PHASE5.md — CELÉ, hlavně design decisions 1-11 a oba schema návrhy

KONTEXT: Fáze 1-4, hardening, runs export a search query capture (docs/TASKS_SEARCH_QUERIES.md,
5/5 hotovo) jsou hotové a smergnuté do master. Tahle větev přidává tři kusy práce najednou
(sólo vývoj, žádný druhý recenzent, proto jedna branch místo tří):
1. Trendové delty na dashboardu (P5-T1/prompt P5-1)
2. Ruční doménová typologie na dashboardu (P5-T2/prompt P5-2)
3. Konkurenční viditelnost — sledované entity, share-of-voice, position jako nový
   analysis skill (P5-T3 až P5-T7 / prompty P5-3 až P5-7)

KRITICKÉ:
- Klient sám NEMÁ řádek v tracked_entities — engine za běhu sloučí client.name +
  client.aliases (fáze 3) s tracked_entities. Duplikace klientovy identity do nové tabulky
  by byla druhá nezávislá kopie stejné informace (design decision 1).
- match_spans() se PŘESOUVÁ z app/analysis/mention_visibility.py do nového
  app/analysis/matching.py jako sdílený helper (design decision 2) — nekopíruj regex logiku
  podruhé pro competitive_visibility.
- share_of_voice/position jsou None (ne 0), když nemají smysl počítat — nikdy zavádějící
  nula tam, kde je správná odpověď "nedá se zjistit" (design decision 4-5).
- domain_classifications NEOBSAHUJE kategorii "competitor" — to už je odvoditelné z
  tracked_entities.domain, druhá ruční kopie stejné informace by byla duplicita.
- _run_active_analysis_skills (app/routers/runs.py) NEMĚNÍ signaturu — nový runner čte
  client.tracked_entities přímo z už předávaného client parametru (design decision 7).

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX + jeden Vue3 ostrůvek na
dashboardu, Alembic migrace, Docker Compose. Backend kód anglicky vč. komentářů/error_code,
UI texty vždy přes t() mechanismus v app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba
jazyky v jednom commitu. Tailwind CDN pro styling.

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation, AnalysisResult) — appka je jen ČTE tam, kde to
  není explicitně úkol zápisu (nové AnalysisResult řádky v P5-6 jsou očekávaný zápis,
  editace/přepis existujících evidence řádků není nikdy v pořádku).
- Nová migrace pro každou schema/index změnu, navazující revision ID (poslední je 0015 —
  ověř `alembic heads` před vytvořením další, aby mezitím nepřibylo něco jiného).
- Každá route funkce dostane docstring; každý netriviální Query/Form/Field parametr
  description=....
- Nikdy git commit ani git push bez tvého výslovného potvrzení — i po tom, co agent sám
  navrhne commit message, čeká na "ano, commitni" než cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message. Nikdy nespouštěj git
add/commit/push sám bez výslovného pokynu — a to i tehdy, když jsi zprávu sám navrhl v
předchozí větě.
```

---
---

## PROMPT P5-1 — Dashboard: trendové delty

```
Task: Prompt P5-1 — dashboard period-over-period trend deltas

Přečti docs/TASKS_PHASE5.md úkol P5-T1 CELÝ, hlavně design decision 10 (jak se počítá
srovnávané předchozí období, proč None místo dělení nulou).
Žádná prerekvizita — nezávislé na zbytku branch.

1. app/services/dashboard.py — nová previous_range_bounds(date_from, date_to) ->
   tuple[datetime, datetime] | None — stejně dlouhé období bezprostředně předcházející
   date_from. None pro range="all".
2. app/routers/dashboard.py — DashboardSummary dostane runs_count_delta_pct: float | None,
   citations_count_delta_pct: float | None, own_domain_rate_delta_pct: float | None
   (procentní body, ne relativní % u own_domain_rate). dashboard_summary route zavolá
   existující count_runs/citation_totals/own_domain_rate podruhé nad run_ids_query
   postaveným z previous_range_bounds(). None, když předchozí období nemá žádné runy.
3. app/templates/dashboard/index.html — %-change badge vedle každé KPI dlaždice (šipka +
   hodnota), skrytý když je delta None.
4. i18n (EN+DE, jeden commit) — dashboard.trend_no_data.

Po dokončení:
1. docker compose up -d --build
2. /dashboard na klientovi s runy v aktuálním i předchozím období — delty sedí na ruční
   přepočet z /dashboard/api/summary volaného zvlášť pro obě období.
3. Klient s daty jen v aktuálním období -> delta None/skrytá, ne zavádějící "+100 %"/pád.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(dashboard): add period-over-period trend deltas to KPI tiles
```

---
---

## PROMPT P5-2 — Dashboard: doménová typologie

```
Task: Prompt P5-2 — manual domain type classification

Přečti docs/TASKS_PHASE5.md úkol P5-T2 CELÝ a "Návrh A" v sekci schema flagů.
Prerekvizita: Návrh A výslovně potvrzený uživatelem. Nezávislé na P5-1/P5-3+.

1. Nová migrace — CREATE TABLE domain_classifications dle Návrhu A (domain jako primary key,
   normalize_domain() výstup; domain_type s CHECK constraintem na
   institutional/editorial/corporate/reference/ugc/other — BEZ kategorie "competitor", viz
   design decision 9).
2. app/models/domain_classification.py — DomainClassification model.
3. app/routers/dashboard.py — DomainRow dostane domain_type: str | None; dashboard_domains
   LEFT JOIN na domain_classifications (normalizovaná doména).
4. Nový POST /dashboard/api/domains/{domain}/classify (Form domain_type) — upsert řádku,
   AppError na neplatnou hodnotu.
5. app/templates/dashboard/index.html — badge s typem u každé domény v league table;
   nezařazená doména dostane inline select k rychlému zařazení (fetch bez reloadu, stejný
   Vue vzor jako filtry).
6. i18n (EN+DE, jeden commit) —
   dashboard.domain_type_{institutional,editorial,corporate,reference,ugc,other},
   dashboard.domain_type_unclassified.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. docker compose up -d --build
3. V league table zařaď doménu přes inline select -> badge se zobrazí bez reloadu, hodnota
   přežije refresh stránky.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(dashboard): add manual domain type classification to the league table
```

---
---

## PROMPT P5-3 — Schema: tracked_entities/tracked_entity_aliases + seed skillu

```
Task: Prompt P5-3 — tracked entities schema and competitive_visibility skill seed

Přečti docs/TASKS_PHASE5.md úkol P5-T3 CELÝ a "Návrh B" v sekci schema flagů, hlavně design
decision 1 (proč klient sám nemá řádek v tracked_entities).
Prerekvizita: Návrh B výslovně potvrzený uživatelem.

1. Nová migrace — CREATE TABLE tracked_entities, CREATE TABLE tracked_entity_aliases dle
   Návrhu B (case-insensitive unique indexy na (client_id, lower(name)) resp.
   (tracked_entity_id, lower(alias)) OD ZAČÁTKU, ne jako pozdější oprava — na rozdíl od
   client_aliases, kde to přišlo dodatečně migrací 0013). Seed nového řádku do
   analysis_skills: key='competitive_visibility', name='Competitive Visibility Detection',
   version=1, execution_type='rule_based', output_schema popisující entities (pole s
   name/is_own_client/mentioned/mention_count/first_position/cited/cited_domains),
   share_of_voice, position jako JSON schema text (stejný vzor jako P3-T1 seed pro
   mention_visibility), is_active=true.
2. app/models/tracked_entity.py — TrackedEntity, TrackedEntityAlias modely dle Návrhu B.
3. app/models/client.py — Client dostane tracked_entities: Mapped[list["TrackedEntity"]]
   relationship (cascade="all, delete-orphan", stejný vzor jako aliases).
4. app/models/__init__.py — zaregistruj nové modely do importů a __all__.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověř: tracked_entities/tracked_entity_aliases existují prázdné; analysis_skills má
   nový řádek competitive_visibility, execution_type='rule_based', is_active=true.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add tracked_entities/tracked_entity_aliases and seed competitive_visibility skill
```

---
---

## PROMPT P5-4 — UI: správa sledovaných entit

```
Task: Prompt P5-4 — tracked entity management UI

Přečti docs/TASKS_PHASE5.md úkol P5-T4 CELÝ, hlavně design decision 8 (krátká jména dostanou
varování, ne blokaci).
Prerekvizita: P5-3 hotový.

1. app/routers/clients.py:
   - POST /clients/{client_id}/tracked-entities (Form name, volitelně domain) — vytvoří
     TrackedEntity. Duplicitní jméno (case-insensitive) -> strukturovaná AppError, ne 500.
   - POST /clients/{client_id}/tracked-entities/{entity_id}/delete.
   - POST /clients/{client_id}/tracked-entities/{entity_id}/aliases (Form alias) — mirror
     existující alias endpointy klienta, jen o úroveň níž.
   - POST /clients/{client_id}/tracked-entities/{entity_id}/aliases/{alias_id}/delete.
2. app/templates/clients/detail.html — nová sekce "Tracked entities", mirror vizuálního
   vzoru existující sekce "Aliases": řádek na entitu (jméno, doména, delete), rozbalitelný
   mini-seznam aliasů pod každou entitou s vlastním add/delete. Jméno kratší než 4 znaky ->
   inline varovný text (neblokuje uložení).
3. i18n (EN+DE, jeden commit) — client.tracked_entities_title,
   client.tracked_entity_add_placeholder, client.tracked_entity_domain_placeholder,
   client.tracked_entity_delete_confirm, client.tracked_entity_short_name_warning,
   errors.tracked_entity_duplicate, errors.tracked_entity_not_found.

Po dokončení:
1. docker compose up -d --build
2. Na existujícím klientovi přidej 2-3 konkurenty s doménou, přidej/smaž pár aliasů u
   jednoho z nich -> seznam se aktualizuje bez chyby. Zkus duplicitní jméno -> strukturovaná
   chyba. Zkus krátké jméno (< 4 znaky, např. "UAE") -> varování se zobrazí, uložení projde.
3. Ověř na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(clients): add tracked entity management for competitive visibility matching
```

---
---

## PROMPT P5-5 — Engine: sdílený matching helper + competitive_visibility runner

```
Task: Prompt P5-5 — shared matching helper and competitive_visibility runner

Přečti docs/TASKS_PHASE5.md úkol P5-T5 CELÝ, hlavně design decision 2-5 (proč se
_match_spans přesouvá, proč entity nemají cross-entity deduplikaci, kdy je share_of_voice/
position None).
Prerekvizita: P5-3 hotový. P5-4 doporučeno pro ruční ověření, ne blokující.

1. Nový app/analysis/matching.py — přesuň _match_spans() z
   app/analysis/mention_visibility.py sem jako veřejnou match_spans(rendered_text,
   candidates), BEZE ZMĚNY CHOVÁNÍ. Docstring vysvětlující sdílení mezi mention_visibility a
   competitive_visibility.
2. app/analysis/mention_visibility.py — importuj match_spans z nového modulu místo lokální
   definice, uprav volání. Žádná jiná změna.
3. Nový app/analysis/competitive_visibility.py — CompetitiveVisibilityRunner:
   - Sestav seznam entit: [(is_own=True, client.name, [client.name] + aliasy klienta)] +
     [(is_own=False, entity.name, [entity.name] + jeho aliasy) for entity in
     client.tracked_entities].
   - Pro KAŽDOU entitu zavolej match_spans() nad jejím vlastním seznamem kandidátů,
     NEZÁVISLE na ostatních entitách (žádná cross-entity deduplikace překryvů).
   - cited/cited_domains per entitu stejnou logikou jako mention_visibility (is_own_domain
     z app/utils.py), nad entity.domain místo client.domain.
   - share_of_voice: own.mention_count / sum(mention_count všech entit), None když je
     jmenovatel 0.
   - position: pořadí own entity podle first_position mezi entitami s mention_count > 0,
     vzestupně; None když own.mention_count == 0. Tiebreak při shodném first_position:
     abecedně podle jména entity.
   - Vrať dict přesně podle output_schema seedovaného v P5-3.
4. app/analysis/__init__.py — přidej "competitive_visibility": CompetitiveVisibilityRunner
   do ANALYSIS_SKILLS.

Po dokončení (jednotkově, bez UI):
1. Krátký ad-hoc skript v kontejneru: zavolej CompetitiveVisibilityRunner().run(...) s ručně
   sestaveným textem a Client/TrackedEntity instancemi v paměti (bez DB) — ověř
   share_of_voice/position na pár ručně vymyšlených případech: klient zmíněný první, klient
   zmíněný poslední, klient nezmíněný vůbec, žádná entita zmíněná, dvě entity se shodným
   first_position (tiebreak).
2. pytest tests/test_analysis_mention_visibility.py — musí zůstat zelené beze změny (přesun
   match_spans nesmí změnit chování).
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(analysis): add competitive_visibility skill with shared matching helper
```

---
---

## PROMPT P5-6 — Wiring: zapojení do trigger_run + zobrazení na run detailu

```
Task: Prompt P5-6 — wire competitive_visibility into trigger_run and run detail

Přečti docs/TASKS_PHASE5.md úkol P5-T6 CELÝ, hlavně design decision 7 (proč se nemění
signatura _run_active_analysis_skills).
Prerekvizita: P5-5 hotový.

1. app/routers/runs.py — _run_active_analysis_skills NEPOTŘEBUJE změnu signatury, registr
   už competitive_visibility najde a spustí automaticky vedle mention_visibility. OVĚŘ (ne
   předpokládej), že client.tracked_entities je čitelné v místě volání i po
   db.expire_on_commit = False — pokud lazy-load selže/vrátí prázdno neočekávaně, over to a
   oprav, ne obejdi.
2. app/templates/runs/detail.html — nová podsekce v "Analysis" (vedle existujícího
   mention_visibility výstupu): tabulka entit tohoto runu (jméno, own/competitor badge,
   zmíněn, počet, pozice) + dvě čísla "Share of voice"/"Position". Klient bez
   tracked_entities -> vysvětlující prázdný stav, NE 0 %.
3. i18n (EN+DE, jeden commit) — analysis.competitive_section_title,
   analysis.share_of_voice_label, analysis.position_label, analysis.no_tracked_entities.

Po dokončení:
1. docker compose up -d --build
2. Na klientovi s aspoň 2 sledovanými entitami (z P5-4) spusť run, kde je v odpovědi
   zmíněný klient i konkurent -> run detail ukazuje správnou tabulku, share of voice a
   position, sedí na ruční přepočet.
3. Klient bez tracked_entities -> sekce ukazuje vysvětlující prázdný stav, ne chybu/nulu.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): compute and display competitive visibility after each run
```

---
---

## PROMPT P5-7 — Dashboard: share-of-voice/position

```
Task: Prompt P5-7 — dashboard share-of-voice and position

Přečti docs/TASKS_PHASE5.md úkol P5-T7 CELÝ, hlavně design decision 11 (JSONB
jsonb_array_elements dotaz, v codebase zatím nepoužitý vzor — ověř syntaxi, neodhaduj ji).
Prerekvizita: P5-6 hotový a ověřený na reálných datech (potřeba aspoň pár
competitive_visibility výsledků v DB).

1. app/services/dashboard.py:
   - Nová entity_league_rows(db, client, run_ids_query) — čte analysis_results.output pro
     competitive_visibility skill, jsonb_array_elements na entities pole. Agreguje per
     entitu: průměrný mention_count, průměrná position (jen z runů, kde entita byla
     zmíněná), run_coverage_pct.
   - DashboardMetric Literal rozšířen o "share_of_voice", "position"; weekly_values dostane
     větev pro oba, čte output->>'share_of_voice'/output->>'position' z
     competitive_visibility výsledků (mirror _mention_visibility_base_query, nová
     _competitive_visibility_base_query).
2. app/routers/dashboard.py — nový GET /dashboard/api/entities (mirror /api/domains), nová
   EntityRow Pydantic model. dashboard_summary dostane share_of_voice: float | None KPI pole.
3. app/templates/dashboard/index.html — nová KPI dlaždice "Share of voice", nová
   "Competitive league table" sekce (mirror domain league table), metric toggle na grafu
   rozšířený o obě nové hodnoty.
4. i18n (EN+DE, jeden commit) — dashboard.kpi_share_of_voice,
   dashboard.competitive_table_title, dashboard.metric_share_of_voice,
   dashboard.metric_position.

Po dokončení:
1. docker compose up -d --build
2. /dashboard na klientovi s několika runy analyzovanými competitive_visibility (z P5-6) —
   KPI, league table i graf ukazují reálná čísla, sedí na ruční přepočet z jednotlivých run
   detailů.
3. Klient bez tracked_entities -> sekce ukazuje prázdný stav, ne 0 %/pád dotazu.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(dashboard): add share-of-voice and position KPIs, league table, and chart metric
```

---
---

## PROMPT P5-8 — Testy

```
Task: Prompt P5-8 — test coverage for phase 5

Přečti docs/TASKS_PHASE5.md úkol P5-T8 CELÝ.
Prerekvizita: P5-1 až P5-7 hotové.

1. Nový tests/test_tracked_entities.py — CRUD entit/aliasů přes /clients/{id}/tracked-entities;
   duplicitní jméno -> strukturovaná chyba, ne 500; krátké jméno neblokuje uložení.
2. Nový tests/test_analysis_competitive_visibility.py — přímé jednotkové testy runneru: žádná
   entita zmíněná (share_of_voice=None), klient zmíněný první/poslední, klient nezmíněný
   (position=None), tiebreak při shodném first_position, cited/cited_domains per entitu.
3. tests/test_dashboard.py — rozšíř o: trendové delty sedí na ruční přepočet fixture dat,
   None bez předchozích dat; doménová typologie se vrací v /api/domains po klasifikaci;
   entity league table a share-of-voice/position timeseries sedí na fixture data.
4. tests/test_runs.py — rozšíř run-trigger test o ověření, že po úspěšném běhu existuje
   AnalysisResult i pro competitive_visibility, ne jen mention_visibility.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover tracked entities, competitive visibility skill, and dashboard extensions
```

---
---

## PROMPT P5-9 — Dashboard: odkaz na detail klienta

```
Task: Prompt P5-9 — link from dashboard to the selected client's detail page

Přečti docs/TASKS_PHASE5.md úkol P5-T9 CELÝ.
Žádná prerekvizita — nezávislé na zbytku branch.

1. app/templates/dashboard/index.html — malý odkaz/tlačítko vedle KPI dlaždic (nebo u
   filtru klienta), vedoucí na /clients/{filters.client_id} vybraného klienta — čistě
   klientská Vue vazba (:href postavený z filters.client_id, co Vue island už drží), ŽÁDNÁ
   změna backendu (dashboard_init už dnes posílá id/name/domain pro každého klienta).
2. i18n (EN+DE, jeden commit) — dashboard.view_client_link.

Po dokončení:
1. docker compose up -d --build
2. /dashboard na libovolném klientovi -> klik na nový odkaz -> přesměruje na /clients/{id}
   se správným klientem.
3. Přepnutí klienta ve filtru -> odkaz se aktualizuje na nově vybraného klienta bez plného
   reloadu stránky.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(dashboard): add link to the selected client's detail page
```

---
---

## PROMPT P5-10 — Help: dashboard metriky a jejich definice

```
Task: Prompt P5-10 — document dashboard metrics in /help

Přečti docs/TASKS_PHASE5.md úkol P5-T10 CELÝ, hlavně bod 3 (rozsah záměrně jen fáze 4).
Žádná prerekvizita — dokumentuje jen to, co dnes běží.

1. app/templates/help.html — nová <section> "Dashboard" mezi existující sekcí "Results" a
   "Analysis" (mirror struktury sekcí "Clients"/"Prompts" — úvodní odstavec + tabulka
   pojem/popis, reuse existujícího vizuálního vzoru, ŽÁDNÁ nová komponenta):
   - Tabulka KPI dlaždic: Runs, Citations, Distinct domains, Own-domain citation rate (vč.
     vysvětlení, že "—" znamená chybějící domain u klienta, ne 0 %).
   - Tabulka sloupců league table: rank, domain, citations + share bar, "cited in %",
     first/last seen, own-domain badge.
   - Callout box (stejný vzor jako sekce "Prompt sets") vysvětlující chování týdenního
     grafu — tři přepínatelné metriky, týdny bez dat mají hodnotu 0, ne vynechaný bod.
   - Odkaz na /dashboard (stejný vzor jako odkazy v sekcích "Providers"/"Settings").
2. i18n (EN+DE, jeden commit) — help.dashboard.title, help.dashboard.body,
   help.dashboard.kpi_runs/kpi_citations/kpi_domains/kpi_own_rate,
   help.dashboard.table_rank/domain/citations/cited_in/first_seen/last_seen/
   own_domain_badge, help.dashboard.chart_note.
3. NEPIŠ sem trendové delty, doménovou typologii ani share-of-voice/position — ty ještě
   neběží (pokud jsi tenhle prompt spustil jako jeden z prvních v branch, PŘED P5-1/P5-2/
   P5-7). Psát o metrikách, co appka ještě neumí, do živé in-app nápovědy by matlo skutečné
   uživatele.

Po dokončení:
1. docker compose up -d --build
2. /help — nová sekce "Dashboard" se zobrazuje na správném místě, obsah sedí na to, co
   /dashboard skutečně zobrazuje.
3. Ověř na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
docs(help): document dashboard metrics and how they're calculated
```
