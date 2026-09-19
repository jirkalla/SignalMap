# SignalMap — Claude Code Session Prompts: Pre-Scheduler Data Hygiene

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-prescheduler-data-hygiene (z master)
## 2. Čtyři kódové prompty (PRE-1, PRE-2, PRE-4, PRE-3 — v tomhle pořadí
##    v souboru). PRE-1, PRE-2 a PRE-4 jsou nezávislé, pořadí mezi nimi je
##    libovolné; PRE-3 je závěrečný průchod až po všech třech.
##    PRE-0 není prompt pro agenta — je to ruční SQL zásah na produkci,
##    který děláš ty (viz docs/TASKS_PRE_SCHEDULER.md PRE-0).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle
##    větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická
##    kontrola daného promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_PRE_SCHEDULER.md přepni řádek daného task ID z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. ČÍSLO MIGRACE. Tahle větev si bere 0029. Plánovač (docs/TASKS_SCHEDULER.md)
##    počítá taky s 0029, protože byl napsaný dřív — až přijde na řadu, bude
##    jeho migrace 0030. Neřeš to teď, je to v Completion Checklistu.
## 9. TOHLE JSOU ČÍSLA, PODLE KTERÝCH SE BUDE FAKTUROVAT. Obě vady, které
##    tahle větev opravuje, zkreslovaly /ops — a Philipova cena pro klienta
##    se počítá jako násobek toho, co tam stojí. Když si nebudeš jistý, jestli
##    je změna správná, radši ZASTAV a zeptej se, než abys číslo "opravil"
##    směrem, který vypadá lépe.
## 10. Až je větev hotová a smergnutá: projít Completion Checklist
##     v docs/TASKS_PRE_SCHEDULER.md (hlavně přečíslování migrace plánovače
##     a nastavení is_test u klienta "Test Skoda Auto" na produkci).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-prescheduler-data-hygiene.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_PRE_SCHEDULER.md — CELÉ, včetně design decisions 1-9

KONTEXT: Fáze 1-6 (docs/TASKS.md) a navazující branche jsou hotové a
smergnuté do master. Tahle krátká větev opravuje dvě vady v podkladu pro
čísla na /ops, nalezené 2026-09-18, a jde vědomě PŘED plánovačem
(docs/TASKS_SCHEDULER.md) — ten objem runů znásobí a obě vady by tím jen
narostly.

CO SE OPRAVUJE:
  1. Testovací klient se počítá do souhrnů v /ops (nový clients.is_test).
  2. Formulář ceny modelu neumí zadat "platí od", takže ceny zadané později
     než runy ty runy nikdy neocení (cena runu se řídí cenou platnou
     v okamžiku runu — app/services/cost.py).
  3. Liga citovaných domén na /dashboard seskupuje podle syrové domény,
     takže meag.com a www.meag.com jsou dva řádky s rozdělenými citacemi.
     Změřeno na produkci: 26 domén, 312 citací, unikátních domén 258 místo
     232.

KRITICKÉ:
- is_test skrývá klienta z AGREGACÍ, nikdy ze seznamů (decision 2). Tohle
  není clients.is_active — ten byl vědomě zamítnutý.
- is_test přepíná JEN ADMIN, a to samostatnou toggle route, ne polem ve
  formuláři klienta (decision 14). Příznak působí ZPĚTNĚ na celou historii
  klienta, ne jen na nové runy (decision 15).
- Filtr patří do JEDNOHO místa: ops_scoped_run_ids_query()
  v app/services/ops_dashboard.py (decision 3). Ne do jednotlivých agregací.
- /dashboard se nemění — je vždycky zúžený na jednoho klienta (decision 4).
- Přepínač "včetně testovacích dat" je query parametr, ne uživatelské
  nastavení; výchozí stav je BEZ testovacích dat (decision 5).
- ai_model_price_components je append-only. Zpětná platnost = NOVÝ ŘÁDEK,
  nikdy UPDATE existujícího (decision 7). Historie cen proto musí unést dvě
  stejné ceny s různým "platí od" — na produkci to tak po PRE-0 je.
- Čas se z formuláře převádí na UTC v prohlížeči, ne pevnou zónou na
  serveru (decision 8, stejný princip jako docs/TASKS_LOCAL_TIME.md).
- Výpočet ceny v app/services/cost.py se NEMĚNÍ. Je správný; chybí mu jen
  data se správnou platností. Kdyby se zdálo, že je potřeba ho upravit —
  ZASTAV a zeptej se.
- Doména se normalizuje UVNITŘ SQL agregace, nikdy v Pythonu nad hotovým
  výsledkem (decision 10) — jinak se rozbije run_coverage_pct a LIMIT.
- citations.source_domain se NIKDY nepřepisuje. Je to evidence (NFR-6) a
  export ji vyváží klientovi jako doklad (decision 12).
```

---
---

## PRE-1 — `clients.is_test` + vyloučení testovacích dat z `/ops`

Viz `docs/TASKS_PRE_SCHEDULER.md` PRE-1. Shrnutí: migrace 0029 s jedním
sloupcem, checkbox ve formuláři klienta, odznak v seznamu, `include_test`
napříč `/ops` a přepínač v UI.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS §2: přesné cesty souborů
+ zdůvodnění) a počkej na potvrzení, než začneš psát kód.

**Kritické:**
- **`is_test` se přepíná vlastní admin akcí** `POST /clients/{id}/toggle-test`
  s `require_role("admin")`, podle vzoru `toggle_ai_model_active`
  v `app/routers/ai_models.py`. **Do formuláře klienta pole NEPŘIDÁVEJ** —
  neodškrtnutý checkbox se v HTML neodesílá, takže skryté pole by při první
  editorově editaci klienta příznak tiše shodilo na false (decision 14).
  V šablonách se ptej přes nový `can_flag_test_client()` v
  `app/templating.py`, nikde `role == "admin"` natvrdo.
- Nápověda u přepínače musí říct, že filtr platí **i zpětně** — označením
  klienta zmizí z `/ops` celá jeho historie, ne jen nové runy (decision 15).
- `ops_scoped_run_ids_query()` dostane **keyword-only `include_test: bool =
  False`**. Výchozí `False` znamená, že volající místo, které na parametr
  zapomene, je pořád chráněné — ne naopak.
- Query dnes joinuje `Prompt` a `PromptSet`; pro `clients.is_test` přidej
  join na `Client`. Ověř, že to nerozbije žádnou agregaci, která tenhle
  select používá jako poddotaz.
- **Žádný `/ops/api/*` endpoint nesmí parametr ignorovat.** Dva pohledy na
  jedné stránce s různým filtrem jsou horší než žádný filtr — projdi je
  všechny, je jich devět.
- Odznak „Test" použij ve stejném vizuálním jazyce jako `Active`/`Inactive`
  v `/users`, ať se to člověk neučí podruhé.
- Sloupec v modelu `Client` dostane docstring, co příznak dělá **a co
  nedělá** (neskrývá klienta ze seznamů) — jinak ho někdo za rok použije na
  archivaci.
- i18n DE/EN pro checkbox, nápovědu, odznak i přepínač.

**Po dokončení:**
1. `docker compose up -d --build`
2. `docker compose exec app alembic upgrade head`, pak `downgrade -1`, pak
   znovu `upgrade head` — obojí bez ruční opravy.
3. V prohlížeči: založit testovacího klienta, označit ho pod **admin**
   účtem, spustit na něm run (model `gemini-3.1-flash-lite`, klienta a
   prompt podle mého zadání), pak na `/ops` ověřit, že se v souhrnu
   **neobjeví**, a s přepínačem že **objeví**. Projdi všech pět pohledů, ne
   jen KPI dlaždice.
3b. Pod editorem: přepínač není vidět, POST na toggle route vrátí 403, a po
   editaci klienta zůstane `is_test` beze změny.
4. `/clients` — klient je pořád vidět, s odznakem.
5. `pytest -v`
6. Responsive ~375 / 768 / desktop na `/ops` (s přepínačem i bez) a na
   formuláři klienta.
7. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(clients): exclude test clients from ops aggregates
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## PRE-2 — Pole „platí od" ve formuláři ceny modelu

Viz `docs/TASKS_PRE_SCHEDULER.md` PRE-2. Shrnutí: jedno nepovinné pole
`effective_from` ve formuláři ceny modelu, převod na UTC v prohlížeči,
zápis v create i update cestě.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- **Jedno pole pro celé odeslání formuláře, ne pole na každou komponentu.**
  Ceník se mění naráz; pět datumů v jednom formuláři je pozvánka k překlepu.
- Prázdné pole = `now()`, tedy **dnešní chování beze změny**. Tenhle prompt
  nesmí změnit, co se stane, když uživatel nic nevyplní.
- Budoucí datum je legitimní (provider oznámí změnu dopředu) — neblokuj ho.
  `prices_at` i `run_cost_sql_expr` si s ním poradí bez úprav.
- Převod na UTC patří do existujícího inline `<script>` bloku v `base.html`.
  **Žádný nový `/static` adresář, žádná JS knihovna** — přesně ten důvod,
  který si zapsal `docs/TASKS_LOCAL_TIME.md` decision 2 a 3.
- Nesmyslný vstup → `AppError` se srozumitelnou hláškou, nikdy 500
  z neodchyceného `ValueError`.
- Zápis musí být v **obou** cestách routeru — create i update. Dnes obě
  spoléhají na server default a je snadné opravit jen jednu.
- Historie cen (`_price_history_rows`) musí unést dva řádky se stejnou cenou
  a různým „platí od" — na produkci to tak po PRE-0 je, takže to nejde
  odbýt úvahou, že taková data nevzniknou.

**Po dokončení:**
1. `docker compose up -d --build`
2. V prohlížeči: zadat modelu novou cenu se zpětnou platností, ověřit
   v historii cen; pak ověřit, že starší run tou cenou **dostane číslo**
   místo pomlčky.
3. Zadat prázdné pole → uloží se s dnešním časem (nezměněné chování).
4. `pytest -v` — včetně testu, že run staršího data se zpětně zadanou cenou
   ocení.
5. Responsive ~375 / 768 / desktop na formuláři modelu.
6. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ai-models): allow backdating a model price change
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## PRE-4 — Normalizace domén v lize a v počtu unikátních domén

Viz `docs/TASKS_PRE_SCHEDULER.md` PRE-4. Shrnutí: SQL dvojče
`normalize_domain()` v `app/utils.py`, seskupení i `count(distinct)`
v `app/services/dashboard.py` podle normalizovaného výrazu, testy proti
driftu obou implementací.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- **Obě funkce najednou** — `citation_totals()` i `domain_league_rows()`.
  Router `/dashboard` je vrací na téže stránce; opravit jednu znamená
  vyrobit novou nesrovnalost mezi dlaždicí a tabulkou pod ní.
- **Seskupení patří do SQL, před `LIMIT`.** Sloučení v Pythonu nad hotovým
  výsledkem rozbije `run_coverage_pct` (run citující obě varianty by se
  počítal dvakrát, pokrytí přes 100 %) a nikdy nenajde domény, které se do
  top N dostanou až po sloučení.
- **Nepřepisuj `citations.source_domain`.** Evidence se nemění (NFR-6),
  export ji vyváží klientovi jako doklad. Normalizace je pohled, ne data.
- **Nerozšiřuj normalizační pravidlo.** Na produkci je změřeno, že všech 26
  případů je jen `www.` — žádná velká písmena, port ani kořenová tečka.
  `lower()` + strip `www.` stačí. Složitější pravidlo by byl kód pro
  hypotetická data (AI_INSTRUCTIONS §5).
- **Subdomény se NESJEDNOCUJÍ.** `blog.meag.com` zůstává samostatný řádek.
- Obě implementace (Python i SQL) bydlí v `app/utils.py` vedle sebe, podle
  precedentu `estimate_run_cost` / `run_cost_sql_expr` v `cost.py`, a drží
  je v souladu tabulkový test — ne dobrá vůle.

**Po dokončení:**
1. `docker compose up -d --build`
2. `pytest -v` — včetně testu, že run citující obě varianty se do
   `run_coverage_pct` započítá jednou, a testu dvojčat Python vs. SQL.
3. V prohlížeči na `/dashboard` klienta podle mého zadání: v lize je
   `meag.com` **jeden** řádek, KPI dlaždice „unikátní domény" sedí na
   počet skupin v tabulce.
4. Responsive ~375 / 768 / desktop na `/dashboard`.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
fix(dashboard): group cited domains by their normalized form
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## PRE-3 — Závěrečný průchod: i18n, responsive, docs

Viz `docs/TASKS_PRE_SCHEDULER.md` PRE-3. Shrnutí: kontrola úplnosti i18n,
responsive průchod v prohlížeči, doplnění `docs/REQUIREMENTS.md`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** responsive se **ověřuje v prohlížeči** na ~375 / 768 /
desktop, ne konstatuje (AI_INSTRUCTIONS §7). Projdi: `/ops` s přepínačem i
bez, formulář klienta, seznam klientů, formulář modelu s novým polem a
`/dashboard` s ligou domén.

`docs/REQUIREMENTS.md` doplnit o tři věci, které dnes nejsou nikde napsané a
všechny jsou netriviální: (a) čísla v `/ops` ve výchozím stavu nezahrnují
testovací klienty, (b) cena runu se řídí cenou platnou v okamžiku runu, a
chybějící cena znamená „neznámá", nikdy „nula" — na tom stojí fakturace,
(c) doména se ve všech agregacích porovnává a seskupuje v normalizovaném
tvaru, kdežto v evidenci a exportu zůstává syrová. Ukaž mi diff, neměň to
potichu.

**Po dokončení:**
1. Grep přes změněné šablony na natvrdo psanou prosu mimo `t()`.
2. Responsive průchod, výsledek popiš konkrétně (co jsi kde viděl).
3. `pytest -v` — celá sada.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(requirements): record test-client exclusion and price validity
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---
---

## PO DOKONČENÍ VŠECH PROMPTŮ

1. Projít Completion Checklist v `docs/TASKS_PRE_SCHEDULER.md`.
2. Na produkci nastavit klientovi „Test Skoda Auto" `is_test = true` (přes
   UI, jedno kliknutí) a ověřit, že `/ops` čísla spadla právě o jeho runy.
3. Zkontrolovat Cost estimate: po PRE-0 má být **vyšší** (poprvé započítané
   OpenAI a Gemini runy), po nastavení `is_test` **nižší** o testovacího
   klienta. Když to nesedí, nespoléhej na to, že se to „nějak srovná" —
   najdi proč.
3b. Ověřit, že počet unikátních domén spadl z 258 na 232 (PRE-4), a říct to
   Philipovi **dopředu** — je to druhý přepočet čísel po citacích
   565 → 820, tentokrát dolů a se změnou pořadí v lize.
4. Teprve pak začít plánovač (`docs/PROMPTS_SCHEDULER.md`), a v jeho SCH-0
   použít číslo migrace **0030**.
