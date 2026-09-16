# SignalMap — Tasks: Ops Dashboard

## v1.0 | Září 2026

Status: navrženo v konverzaci (2026-09-15), čeká na vlastní branch. Realizuje
`docs/ROADMAP.md` §4 (Cost/ops dashboard) — interní, engineering-facing
viditelnost do nákladů/latence/chyb, ne klientský report. Vizuální návrh a
rozsah pohledů (globál → klient → prompt set → prompt, plus samostatná osa
"podle uživatele") jsou odsouhlasené na mockup artefaktu z téže konverzace.

Tahle branch je **prerekvizita pro Scheduler** (`docs/ROADMAP.md` §5) — chceme
vidět náklady/chyby dřív, než appka začne spouštět runy automaticky a
nehlídaně (stejný důvod, proč to `ROADMAP.md` řadí před scheduler).

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Nová samostatná route `/ops`, ne rozšíření `/dashboard`.** Jiná cílovka
   (interní engineering/ops, ne klientský report) a jiná role-hranice —
   admin/editor, nikdy viewer, a nikdy client-facing (na rozdíl od dnešního
   perception dashboardu, který má do budoucna vlastní "client view" ambici
   — viz `ROADMAP.md` §10 diskuze). Míchat oba účely do jedné stránky by
   časem vynutilo řešit dvě různé access-control osy na jednom místě.
2. **Hybrid architektura zůstává** — Jinja2 shell + JEDEN další Vue3 ostrůvek
   z CDN, žádný build step, stejný vzor jako fáze 4. Přechod na plnou Vue SPA
   dává smysl, až (a) víc obrazovek sdílí stav napříč sebou, (b) je potřeba
   client-side routing mezi pohledy, nebo (c) interaktivních obrazovek je víc
   než CRUD (`TASKS_PHASE4.md` design decision 1) — žádná z podmínek tu není
   splněná.
3. **Drill-down má dvě nezávislé osy nad stejnými runy**, ne jeden strom:
   Klient → Prompt Set → Prompt (vnořené), a odděleně Uživatel (křížové —
   jeden člověk může spouštět runy napříč víc klienty). Kliknutí na klienta
   v pohledu uživatele opouští osu uživatele a vrací do normálního
   klient/prompt-set/prompt drilldownu, ne naopak.
4. **Agregace vždy v SQL (`GROUP BY`/`SUM`/`COUNT`/`AVG`), nikdy v Pythonu
   nad plným seznamem `Run` řádků.** Jediný způsob, jak tohle zůstane rychlé
   i při tisících runů — appka na to netahá řádky do paměti, aby je sečetla.
5. **Časový bucket u grafu runů za den se mění podle zvoleného rozsahu** —
   den pro 7d/30d, týden pro 90d/quarter (oba ~90denní řádově, stejná
   zrnitost), měsíc pro "vše" — stejný princip jako existující
   `app/services/dashboard.py` (pevný týdenní bucket + strop ~8 viditelných
   popisků), jen o úroveň jemnější kvůli kratším rozsahům, co ops dashboard
   nabízí navíc.
6. **"Poslední runy" ukazuje jen posledních ~15, s odkazem na existující plný
   run-list** (fáze 1, Task 6 — `/prompts/{id}/runs` a ekvivalent). Žádné
   nové stránkování se uvnitř ops dashboardu nestaví — appka už jedno má.
7. **Tabulky Klienti/Uživatelé na globální úrovni**: seřazené sestupně podle
   objemu/nákladů, top 15–20 + jednoduché textové hledání (client-side).
   Bez stránkování zatím — počet klientů/uživatelů roste s velikostí firmy,
   ne s objemem runů, takže zůstává v rozumném řádu podstatně déle než
   seznam runů.
8. **Nová `app/services/cost.py`** (ne přílepek do `dashboard.py` — jiná
   doména, cost estimation se bude hodit i budoucímu billing enginu).
   `estimate_run_cost(token_usage, model) -> float | None` — `None`, když
   chybí `token_usage` NEBO modelu chybí `cost_per_1k_input_usd`/
   `cost_per_1k_output_usd` — nikdy tichá `0.0` (stejná disciplína jako
   `has_citations`/FR-13 — chybějící data se nikdy nemaskují jako nulová
   hodnota).
9. **Date-range filtr na existujícím `/dashboard` se rozšiřuje na
   `7d/30d/90d/quarter/all`** (přidává `7d`, `quarter` zůstává beze změny —
   rozhodnuto v konverzaci 2026-09-15, ne nahrazení, jak bylo původně
   navrženo) — malá, samostatná prerekvizita (T0), aby oba dashboardy
   nabízely konzistentní rozsahy dřív, než na tom ops dashboard staví
   vlastní bucket-switching logiku.
10. **`Run.triggered_by_user_id`** (fáze 6) pohání pohled "Podle uživatele".
    `trigger_type='scheduled'` + `triggered_by_user_id IS NULL` se agreguje
    jako pseudo-uživatel "Scheduler" — ne jako chybějící/NULL řádek.
11. **Žádná nová JS knihovna.** Grafy jsou ruční inline SVG, stejně jako
    fáze 4 (design decision 3) — appka nemá build krok a nepřidává ho kvůli
    jednomu dashboardu navíc.
12. **Vizuální návrh odsouhlasený na mockup artefaktu** (konverzace
    2026-09-15) — neutrální ops paleta (ne ExpressYourself.AI rebrand tokeny,
    ten je samostatná, odložená položka `ROADMAP.md` §9), IBM Plex Mono pro
    tabulková čísla (existující konvence appky).

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T0 | Rozšířit date-range filtr na `/dashboard` (7d/30d/90d/quarter/all) | ⏳ |
| T1 | Foundations: `cost.py` + sdílený date-range resolver | ⏳ |
| T2 | Ops Dashboard JSON API | ⏳ |
| T3 | Ops Dashboard stránka + Vue3 ostrůvek | ⏳ |
| T4 | Testy: agregace, role gate, cross-client/cross-user izolace | ⏳ |

Pořadí je vynucené: T0 mění tvar `DashboardRange`, na kterém T1's sdílený
resolver staví; T1 dává T2 hotový `estimate_run_cost` a range-bounds helper;
T2 potřebuje T3 (stránka fetchuje z hotových endpointů, ne naopak); T4
testuje všechno předchozí najednou.

---

## T0 — Rozšířit date-range filtr na `/dashboard`

**Target:** `app/services/dashboard.py`, `app/routers/dashboard.py`,
`app/templates/dashboard/index.html`, `app/i18n/en.json`, `app/i18n/de.json`,
`tests/test_dashboard.py`

Čistě aditivní změna — `quarter` zůstává beze změny chování, jen přibývá
`7d` (rozhodnuto v konverzaci 2026-09-15).

1. V `app/services/dashboard.py`: `DashboardRange = Literal["7d", "30d",
   "90d", "quarter", "all"]`. V resolve-bounds funkci přidat větev pro
   `"7d"` (now − 7 dní, stejný tvar jako `"30d"`/`"90d"`) — nad existující
   větve, žádná se neodstraňuje.
2. V `app/routers/dashboard.py`: upravit `_RANGE_QUERY` popis/enum na
   `7d, 30d (rolling), 90d (rolling), quarter (calendar), all`.
3. V `app/templates/dashboard/index.html`: přidat `<option value="7d">`
   nad `<option value="30d">`. Ponechat default `filters.range = '90d'` a
   existující `<option value="quarter">` beze změny.
4. V `app/i18n/en.json` + `de.json`: přidat `dashboard.range_7d` ("Last 7
   days" / "Letzte 7 Tage"). `dashboard.range_quarter` zůstává.
5. V `tests/test_dashboard.py`: přidat case s `range=7d` (hranice okna,
   podobně jako existující `range=30d` test na trend delta) — nic
   existujícího se neodstraňuje ani neupravuje.

**Done when:** `/dashboard` nabízí 7/30/90/This quarter/Vše (5 voleb);
všechny stávající testy + nový `range=7d` test zelené.

**Expected commit:** `feat(dashboard): add 7-day range option`

---

## T1 — Foundations: `cost.py` + sdílený date-range resolver

**Target:** nová `app/services/date_ranges.py`, nová `app/services/cost.py`,
úprava `app/services/dashboard.py` (import místo lokální definice)

1. `app/services/date_ranges.py` — přesunout `DashboardRange` Literal +
   resolve-bounds funkci sem z `dashboard.py` (beze změny chování,
   `dashboard.py` ji odtud importuje zpátky). Ops dashboard na tom staví
   nezávisle na `dashboard.py`, ne skrz něj.
2. `app/services/cost.py` — `estimate_run_cost(token_usage: dict | None,
   model: AIModel) -> float | None` podle design decision 8. Čte
   `token_usage["prompt_tokens"]`/`["candidate_tokens"]` (nebo ekvivalentní
   klíče, ověřit přesný tvar uložený v `raw_responses.token_usage` napříč
   providery — Gemini/Anthropic/OpenAI mají různé názvy polí, sjednotit při
   ukládání, ne při čtení).
3. Jednotkový test (`tests/test_cost.py` nebo přidat do existující sady) —
   happy path na reálných číslech run 35 (57 input + 1061 output tokenů,
   gemini-3.1-flash-lite), chybějící `token_usage` → `None`, model bez
   ceny → `None`.

**Done when:** `estimate_run_cost` vrací správnou hodnotu pro run 35 a
`None` pro oba chybějící případy — ne `0.0`.

**Expected commit:** `feat(ops): add cost estimation helper and shared date-range resolver`

---

## T2 — Ops Dashboard JSON API

**Target:** nová `app/services/ops_dashboard.py`, nová
`app/routers/ops_dashboard.py`, registrace routeru v `app/main.py`

Všechny endpointy pod `require_role("admin", "editor")`:

1. `GET /ops/api/summary` — KPI (runs, success rate, cost, avg latency),
   volitelné `range`/`client_id`/`prompt_set_id`/`prompt_id`/`user_id` query
   parametry, progresivní zúžení (stejný vzor jako `dashboard.py`).
2. `GET /ops/api/daily` — časová řada success/error, bucket podle `range`
   (design decision 5).
3. `GET /ops/api/providers` — náklady seskupené podle providera.
4. `GET /ops/api/clients` — tabulka klientů (top N + `search` param).
5. `GET /ops/api/users` — tabulka uživatelů; `trigger_type='scheduled'` +
   `triggered_by_user_id IS NULL` agregováno jako řádek `"Scheduler"`
   (design decision 10).
6. `GET /ops/api/prompt-sets?client_id=`, `GET /ops/api/prompts?
   prompt_set_id=`, `GET /ops/api/prompt-detail?prompt_id=` (model srovnání
   + posledních 15 runů + `runs_url` odkaz na existující plný run-list).
7. `GET /ops/api/user-detail?user_id=` (nebo `scheduler`) — breakdown podle
   klienta + posledních 15 runů, stejný tvar jako `prompt-detail`.
8. Žádný endpoint nenačítá `Run` řádky do Pythonu, aby je sečetl — vše přes
   SQLAlchemy `func.sum/count/avg` + `group_by` (design decision 4).

**Done when:** každý endpoint vrací správná čísla proti seed datům
(porovnat ručně se `SELECT` v `psql`); `require_role` odmítá viewera 403.

**Expected commit:** `feat(ops): add ops dashboard aggregation API`

---

## T3 — Ops Dashboard stránka + Vue3 ostrůvek

**Target:** `app/templates/ops/index.html` (nový), shell route v
`app/routers/ops_dashboard.py`, nav odkaz v `app/templates/base.html`

1. Jinja2 shell + JSON data island (seznam klientů pro select) + Vue3
   ostrůvek z CDN (pinnutá verze), přesně podle odsouhlaseného mockup
   artefaktu: breadcrumb, 4 KPI dlaždice, denní graf (inline SVG), graf
   nákladů podle providera (inline SVG), tabulky Klienti / Uživatelé /
   Prompt sety / Prompty / Srovnání modelů / Poslední runy.
2. Drill-down (klient / prompt set / prompt / uživatel) mění jen Vue stav a
   znovu fetchuje z `/ops/api/*` — žádný page reload (stejné zdůvodnění jako
   `TASKS_PHASE4.md` design decision 10).
3. "Zobrazit všechny runy →" odkaz na existující run-list route místo
   nového stránkování (design decision 6).
4. Nav odkaz na `/ops` viditelný jen adminovi/editorovi — přes existující
   `can_edit()`-styl centralizovaný helper (`app/templating.py`), ne inline
   `role in (...)` v šabloně (precedent z code review fáze 6).
5. Responsive průchod ~375px / ~768px / desktop na všech pohledech, ne jen
   na globálním.

**Done when:** appka nastartuje; `/ops` je přístupné adminovi/editorovi,
403 vieweru; drill-down (všechny 4 úrovně + osa uživatele) funguje bez
reloadu na reálných datech; responsive ověřené.

**Expected commit:** `feat(ops): add ops dashboard page and Vue3 island`

---

## T4 — Testy

**Target:** nový `tests/test_ops_dashboard.py`

1. Agregační správnost — cost sum, error count, daily bucketing na
   hranicích rozsahu (stejný precedent jako `test_dashboard.py`).
2. Role gate — viewer/nepřihlášený → 403/redirect na `/ops` i `/ops/api/*`.
3. Cross-client a cross-user izolace — žádná agregace neunikne mimo
   požadovaný scope (stejný precedent jako fáze 4 "cross-client
   data-isolation regression test").
4. `estimate_run_cost` edge cases, pokud ještě nejsou pokryté z T1.

**Done when:** `pytest` zelený včetně nové sady.

**Expected commit:** `test(ops): add ops dashboard aggregation and access tests`

---

## Completion Checklist (až je branch hotová a smergnutá)

- Doplnit do `docs/TASKS.md` odkaz na tuhle branch (stejný vzor jako
  ostatní položky).
- Upravit `docs/ROADMAP.md` — přepnout stav položky #4 na hotovo, poznamenat
  že odemyká Scheduler (#5).
- Zvážit, jestli `docs/REQUIREMENTS.md` potřebuje nový NFR/FR záznam pro
  ops-only viditelnost (admin/editor, nikdy client-facing).
