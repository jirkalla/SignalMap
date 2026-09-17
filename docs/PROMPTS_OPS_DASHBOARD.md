# SignalMap — Claude Code Session Prompts: Ops Dashboard

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-ops-dashboard (z aktuálního master)
## 2. Pět kódových promptů (OPS-0 až OPS-4), POŘADÍ VYNUCENÉ — viz
##    docs/TASKS_OPS_DASHBOARD.md "Task Index" pro odůvodnění (OPS-1 staví na
##    tvaru DashboardRange z OPS-0; OPS-2 potřebuje helpery z OPS-1; OPS-3
##    staví na API z OPS-2; OPS-4 testuje všechno).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická
##    kontrola daného promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_OPS_DASHBOARD.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-12: docs/TASKS_OPS_DASHBOARD.md
##    — přečti si konkrétní task ID před psaním kódu, ideálně celý soubor
##    před OPS-0.
## 9. Tahle větev NEZAVÁDÍ build step ani druhý nezávislý Vue frontend — jeden
##    Vue3 ostrůvek z CDN uvnitř jinak Jinja2/HTMX appky (design decision 2).
##    Pokud se během implementace zdá, že je potřeba Vite/npm/chart knihovna,
##    ZASTAV a zeptej se — to by byl scope creep mimo to, co bylo odsouhlaseno.
## 10. Žádná agregace v Pythonu nad plným seznamem Run řádků — vždy SQL
##     GROUP BY/SUM/COUNT/AVG (design decision 4). Pokud se v průběhu ukáže,
##     že nějaký endpoint potřebuje víc než agregaci (např. řadit podle
##     vypočítaného pole, co SQL neumí rozumně vyjádřit), ZASTAV a zeptej se.
## 11. `/ops` je admin/editor only, NIKDY viditelné vieweru ani budoucímu
##     client-facing účtu (design decision 1) — každý endpoint i šablona to
##     musí vynucovat na serveru, ne jen skrýt v UI.
## 12. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na
##     tuhle větev a upravit docs/ROADMAP.md §4 (viz Completion Checklist
##     v TASKS_OPS_DASHBOARD.md).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-ops-dashboard.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_OPS_DASHBOARD.md — CELÉ, hlavně design decisions 1-12

KONTEXT: Fáze 1-6 (docs/TASKS.md) a navazující branche (export, bulk import,
ChatGPT/persony/pricing, local time fix) jsou hotové a smergnuté do master.
Existující dashboard (fáze 4-5, /dashboard) je klientský perception report
— league table, share-of-voice, sentiment. Tahle větev přidává DRUHÝ,
oddělený dashboard (/ops) — interní engineering/ops viditelnost do
nákladů/latence/chyb, admin/editor only, nikdy client-facing. Je to
prerekvizita pro Scheduler (ROADMAP.md #5), ne jeho součást.

KRITICKÉ:
- Hybrid architektura zůstává: Jinja2 shell + JEDEN Vue3 ostrůvek z CDN
  (pinnutá verze, global build, žádný build step/npm/Vite). Nepřidávej
  chart knihovnu — grafy jsou ruční inline SVG (design decision 11).
- /ops je zcela oddělená route od /dashboard, ne jeho rozšíření (design
  decision 1) — jiná access-control hranice.
- Drill-down má DVĚ nezávislé osy: Klient→Prompt Set→Prompt (vnořené) a
  Uživatel (křížové, napříč klienty) — design decision 3, viz mockup.
- Agregace vždy v SQL, nikdy Python smyčka nad Run řádky (design decision 4).
- Vizuální návrh je odsouhlasený na mockup artefaktu z konverzace
  2026-09-15 — dodržet strukturu pohledů (KPI dlaždice, denní graf, graf
  nákladů podle providera, tabulky), ne redesignovat.
```

---
---

## OPS-0 — Rozšířit date-range filtr na `/dashboard`

Viz `docs/TASKS_OPS_DASHBOARD.md` T0 pro plný seznam cílových souborů a
kroků. Shrnutí: přidat `"7d"` do `DashboardRange`
(`app/services/dashboard.py`) — čistě aditivní, `"quarter"` zůstává beze
změny — upravit select v `app/templates/dashboard/index.html`, i18n klíče,
přidat test.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T0 v TASKS dokumentu) a počkej na potvrzení, než
začneš psát kód.

**Po dokončení:**
1. `docker compose up -d --build`
2. V prohlížeči: `/dashboard` → ověřit, že select nabízí všech 5 voleb
   (Last 7 days / Last 30 days / Last 90 days / This quarter / All time) a
   že přepnutí na "Last 7 days" přepočítá KPI/graf.
3. `pytest tests/test_dashboard.py -v`
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): add 7-day range option
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## OPS-1 — Foundations: `cost.py` + sdílený date-range resolver

Viz `docs/TASKS_OPS_DASHBOARD.md` T1. Shrnutí: `app/services/date_ranges.py`
(přesun `DashboardRange` + resolve-bounds z `dashboard.py`, beze změny
chování), `app/services/cost.py` (`estimate_run_cost`), jednotkový test na
reálných číslech run 35.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** `estimate_run_cost` vrací `None` (nikdy `0.0`) když chybí
`token_usage` nebo cena modelu — ověř přesný tvar `token_usage` uložený
napříč Gemini/Anthropic/OpenAI runy v databázi, než napíšeš parsing (různé
providery mají různé názvy polí v surovém payloadu, ale `token_usage` na
`RawResponse` by měl být už sjednocený adapterem — zkontroluj).

**Po dokončení:**
1. `docker compose up -d --build`
2. Jednotkový test na `estimate_run_cost`: happy path (run 35 čísla),
   chybějící `token_usage`, model bez ceny.
3. `pytest -v`
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ops): add cost estimation helper and shared date-range resolver
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## OPS-2 — Ops Dashboard JSON API

Viz `docs/TASKS_OPS_DASHBOARD.md` T2. Shrnutí: `app/services/ops_dashboard.py`
+ `app/routers/ops_dashboard.py`, sedm endpointů (summary, daily, providers,
clients, users, prompt-sets/prompts/prompt-detail, user-detail), všechny
`require_role("admin", "editor")`, všechny agregace v SQL.

Nejdřív navrhni CO uděláš + PROČ, včetně přesného tvaru odpovědi (JSON
shape) pro každý endpoint, a počkej na potvrzení — tohle je nejvíc nový
kód v celé větvi, stojí za to potvrdit kontrakt před psaním.

**Kritické:** "Scheduler" pseudo-uživatel (design decision 10) — ověř, že
agregace na `/ops/api/users` správně seskupí `trigger_type='scheduled'`
+`triggered_by_user_id IS NULL` do jednoho řádku, ne že zmizí nebo spadne
pod NULL group.

**Po dokončení:**
1. `docker compose up -d --build`
2. Pro každý endpoint: ruční `curl`/prohlížeč kontrola proti seed datům,
   porovnat se `SELECT` v `psql`.
3. Ověřit `require_role` — přihlásit se jako viewer, očekávat 403 na všech
   `/ops/api/*`.
4. `pytest -v`
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ops): add ops dashboard aggregation API
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## OPS-3 — Ops Dashboard stránka + Vue3 ostrůvek

Viz `docs/TASKS_OPS_DASHBOARD.md` T3. Shrnutí: `app/templates/ops/index.html`,
shell route, nav odkaz (admin/editor only přes `can_edit()`-styl helper).
Struktura přesně podle odsouhlaseného mockup artefaktu — breadcrumb, KPI
dlaždice, denní graf, graf nákladů podle providera, tabulky Klienti /
Uživatelé / Prompt sety / Prompty / Srovnání modelů / Poslední runy, plus
"Zobrazit všechny runy →" odkaz na existující run-list.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Drill-down mění jen Vue stav, žádný page reload.
- Klient a Uživatel jsou dvě nezávislé osy — kliknutí na klienta v pohledu
  uživatele přepíná do normálního klient-drilldownu (viz mockup).
- Nav odkaz na `/ops` NESMÍ být viditelný vieweru — přes centralizovaný
  `can_edit()`-styl helper v `app/templating.py`, ne inline `role in (...)`.

**Po dokončení:**
1. `docker compose up -d --build`
2. V prohlížeči: projít celý drill-down (globál → klient → prompt set →
   prompt, a odděleně globál → uživatel → klient), ověřit že se nic
   nenačítá s plným reloadem.
3. Přihlásit se jako viewer — ověřit, že `/ops` odkaz v navigaci chybí A
   přímé otevření `/ops` vrací 403.
4. Responsive: ~375px / ~768px / desktop na všech pohledech.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ops): add ops dashboard page and Vue3 island
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## OPS-4 — Testy

Viz `docs/TASKS_OPS_DASHBOARD.md` T4. Shrnutí: `tests/test_ops_dashboard.py`
— agregační správnost, role gate, cross-client/cross-user izolace.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Po dokončení:**
1. `docker compose up -d --build`
2. `pytest -v` — celá sada, ne jen nové testy.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test(ops): add ops dashboard aggregation and access tests
```

### (sem dopiš DONE — commit {hash} až bude hotovo)
