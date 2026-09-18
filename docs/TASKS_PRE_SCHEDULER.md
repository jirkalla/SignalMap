# SignalMap — Tasks: Pre-Scheduler Data Hygiene

## v1.0 | Září 2026
## Branch: feature/signalmap-prescheduler-data-hygiene
## Task ID prefix: PRE

> Dvě vady, které se najdou jen tím, že se člověk dívá na `/ops` a nevěří číslům.
> Obě vyplavaly 2026-09-18 při kontrole ops dashboardu před začátkem práce na
> plánovači (`docs/TASKS_SCHEDULER.md`).
>
> 1. **Testovací klient se počítá jako ostatní.** V produkci je klient
>    „Test Skoda Auto"; jeho runy jdou do počtu runů, do úspěšnosti i do
>    odhadu útraty na `/ops`. Neexistuje způsob, jak ho ze souhrnu vyjmout.
> 2. **Ceny zadané později než runy nechávají runy bez ceny.** Cena runu se
>    počítá cenou platnou v okamžiku toho runu (`app/services/cost.py`), ale
>    formulář v `/ai-models` zapisuje `effective_from` vždycky jako „teď".
>    Kdo zadá ceník den po prvních runech, ty runy už nikdy nedocení — a
>    v UI se to projeví jen pomlčkou, kterou si člověk vyloží jako nulu.
>
> **Proč před plánovačem, ne po něm:** obojí jsou vady v podkladu, ze kterého
> se počítá, co klienta stojí provoz (a přes Philipovu 2–4× přirážku i to, co
> se mu vyfakturuje). Plánovač objem runů znásobí — testovací rozvrhy budou
> generovat testovací runy pořád, ne jen když někdo klikne. Opravit vstup do
> čísel dřív, než jich začne přibývat řádově víc, je levnější v tomhle pořadí.
>
> Rozsah je malý (jedna migrace, dva formuláře, jeden `WHERE`), proto vlastní
> krátká větev, ne fáze — stejný precedent jako `docs/TASKS_LOCAL_TIME.md`.

---

## Čísla migrací

Tahle větev si bere **0029**. `docs/TASKS_SCHEDULER.md` byl napsaný dřív a
mluví o „migraci 0029" — pokud se tahle větev smergne první (a to je záměr),
plánovačova migrace bude **0030**. Poznámka je i v obou scheduler
dokumentech; číslo v nich neměň zpětně, řeš to při psaní SCH-0.

---

## Design decisions (rozhodnuto v konverzaci 2026-09-18)

1. **`clients.is_test` jako příznak, ne oddělené prostředí — ale obojí platí.**
   Správná odpověď na testovací data je testovací prostředí a lokální
   `docker compose` stack jím je. Jenže část ověřování se dá udělat jen proti
   reálným providerům s reálnými klíči, a ta poběží na produkci i nadále.
   Pro ten případ je standardní řešení v multi-tenant SaaS příznak na
   záznamu (`is_internal` / `is_demo` / `is_test`) a analytika, která ho ve
   výchozím stavu nezapočítává. Konvence v názvu („Test …" a `LIKE`) je
   zamítnutá: rozbije se při prvním přejmenování.
2. **Testovací klient se skrývá z agregací, nikdy ze seznamů.** V `/clients`,
   ve výběru klienta i na svém detailu zůstává úplně normálně viditelný, jen
   s odznakem „Test". Skrývat ho ze seznamů by vyrobilo otázku „kam se poděl
   klient, kterého jsem včera založil" — přesně ten důvod, proč byl
   v `docs/TASKS_SCHEDULER.md` (decision 31) zamítnutý `clients.is_active`.
   Tenhle příznak sahá jen na to, co se sčítá.
3. **Filtr se vkládá do jednoho místa: `ops_scoped_run_ids_query()`.**
   `app/services/ops_dashboard.py` má jedno hrdlo, kterým prochází každý
   ops dotaz — tam patří i tenhle `WHERE`, ne do patnácti agregací zvlášť.
4. **`/dashboard` se nemění.** Klientský dashboard je vždycky zúžený na
   jednoho konkrétního klienta (`scoped_run_ids_query(client_id, ...)`
   v `app/services/dashboard.py` má `client_id` povinný), takže testovací
   klient tam nemá co znečistit — člověk si ho vybral vědomě.
5. **Přepínač „včetně testovacích dat" je query parametr, ne uživatelské
   nastavení.** `?include_test=1` vedle existujících `range`/`client_id`.
   Stav je tím pádem v URL: odkaz na konkrétní pohled jde poslat kolegovi a
   uvidí totéž. Výchozí stav je **bez** testovacích dat.
6. **`effective_from` je nepovinné pole ve formuláři ceny, ne skryté chování.**
   Prázdné = teď (dnešní chování beze změny). Vyplněné = platnost od zadaného
   okamžiku, minulého i budoucího. Budoucí datum je legitimní případ
   (provider oznámí změnu ceny dopředu) a `prices_at` si s ním poradí bez
   úprav — bere poslední řádek s `effective_from <= čas runu`.
7. **Zpětné doplnění ceny je INSERT, nikdy UPDATE.** `ai_model_price_components`
   je append-only (viz docstring modelu). Řádek zadaný 18. 9. je pravdivý
   záznam o tom, co tehdy někdo zadal; doplnění dřívější platnosti je **nový
   řádek**, ne přepis starého. Důsledek: v historii cen se stejná cena může
   objevit dvakrát s různým „platí od". To je správně a UI to musí unést.
8. **Vstup času se převádí v prohlížeči, ne pevnou zónou na serveru.**
   `datetime-local` pole + převod na UTC v existujícím inline JS bloku
   v `base.html` (skryté pole s ISO UTC hodnotou) — stejný princip jako
   `docs/TASKS_LOCAL_TIME.md` decision 1 pro zobrazení. Bez JS server
   naivní hodnotu interpretuje jako UTC a nápověda pod polem to říká
   nahlas. Žádná nová závislost, žádný `/static`.
9. **Jednorázová oprava historických dat se dělá SQL, ne migrací.** Je to
   oprava dat konkrétní instalace, ne změna schématu — v migraci by se
   pokoušela proběhnout i na prázdné vývojářské databázi a v testech.
   Zapsaná je v PRE-0 kvůli dohledatelnosti, protože vysvětluje, proč
   některé modely mají dvě ceny se stejnou hodnotou.

---

## Nové schéma (migrace 0029)

Jeden sloupec.

```
clients
  + is_test            bool not null default false   -- decision 1
```

Cenové komponenty ani nic dalšího se **nemění** — PRE-2 mění jen to, co do
existující tabulky zapisuje formulář.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| PRE-0 | Jednorázové zpětné doplnění platnosti cen (ruční SQL na produkci) | ⏳ |
| PRE-1 | `clients.is_test` + vyloučení testovacích dat z `/ops` | ⏳ |
| PRE-2 | Pole „platí od" ve formuláři ceny modelu | ⏳ |
| PRE-3 | Závěrečný průchod: i18n, responsive, docs | ⏳ |

PRE-1 a PRE-2 jsou nezávislé, pořadí je libovolné. PRE-3 je až po obou.

---

## PRE-0 — Zpětné doplnění platnosti cen (ruční zásah na produkci)

**Target:** žádný soubor v repu — zápis zásahu do produkční databáze,
připraveno 2026-09-18. Až proběhne, dopiš sem datum a počet vložených řádků
(`INSERT 0 N`) a přepni řádek v Task Indexu na ✅.

**Co se stalo:** `/ops` ukazoval u OpenAI ChatGPT pomlčku místo ceny,
přestože modely měly zadané ceny od 14. 9. Příčina: `input`/`output` byly od
14. 9., ale `cache_read`/`cache_write` až od 18. 9., a
`app/services/cost.py` (design decision 14) vrací `NULL`, když run vykázal
**nenulový** počet cache tokenů bez ceny platné v jeho čase — nikdy
podhodnocený odhad. U OpenAI mělo 9 z 12 ověřovaných řádků nenulové
`cache_write_tokens`, takže 19 z 20 runů zůstalo bez ceny. Google Gemini
mělo ze stejného důvodu 18 runů úplně bez ceníku (runy od 13. 9., první
ceny od 15. 9.).

**Zásah** (po `signalmap-backup.sh`, potvrzeno uživatelem, že se ceník
providerů mezi 13. a 18. 9. neměnil): každé nejstarší cenové komponentě se
vložil sourozenec se stejnou cenou a `effective_from = ai_models.created_at`.

```sql
INSERT INTO ai_model_price_components
       (ai_model_id, component_type, price_per_unit_usd, unit, effective_from, changed_by_user_id)
SELECT c.ai_model_id, c.component_type, c.price_per_unit_usd, c.unit,
       m.created_at, c.changed_by_user_id
FROM ai_model_price_components c
JOIN ai_models m ON m.id = c.ai_model_id
WHERE c.effective_from = (SELECT min(x.effective_from)
                          FROM ai_model_price_components x
                          WHERE x.ai_model_id = c.ai_model_id
                            AND x.component_type = c.component_type)
  AND c.effective_from > m.created_at;
```

Idempotentní (podruhé nevloží nic), append-only (decision 7). Kontrola před
i po — po zásahu musí vrátit prázdný výsledek:

```sql
SELECT p.name AS provider, count(*) AS runs_bez_ceny
FROM runs r
JOIN ai_models m ON m.id = r.model_id
JOIN providers p ON p.id = m.provider_id
WHERE NOT EXISTS (SELECT 1 FROM ai_model_price_components c
                  WHERE c.ai_model_id = m.id AND c.effective_from <= r.started_at)
GROUP BY 1 ORDER BY 1;
```

**Proč je to tady zapsané:** bez téhle poznámky bude za půl roku v historii
cen u každého modelu dvakrát stejná cena a nikdo nebude vědět proč.

---

## PRE-1 — `clients.is_test` + vyloučení testovacích dat z `/ops`

**Target:** nová `alembic/versions/0029_clients_is_test.py`,
`app/models/client.py`, `app/routers/clients.py`,
`app/templates/clients/list.html`, `app/templates/clients/form.html`,
`app/templates/clients/detail.html`, `app/services/ops_dashboard.py`,
`app/routers/ops_dashboard.py`, `app/templates/ops/index.html`,
`app/i18n/en.json`, `app/i18n/de.json`, `tests/`

1. Migrace: `clients.is_test BOOLEAN NOT NULL DEFAULT false`. Downgrade
   sloupec zahodí. Model `Client` dostane pole + docstring, který říká, co
   příznak dělá **a co nedělá** (decision 2) — jinak ho někdo za rok použije
   na skrývání klienta ze seznamu.
2. Formulář klienta (create i edit): checkbox „Testovací klient" s nápovědou
   „runy tohoto klienta se nezapočítávají do souhrnů v Ops". Reuse maker
   z `app/templates/partials/macros.html`.
3. Odznak „Test" v `/clients` a na detailu klienta — vizuálně stejný jazyk
   jako `Active`/`Inactive` u uživatelů v `/users`, ať se to nemusí učit
   podruhé.
4. `ops_scoped_run_ids_query()` (`app/services/ops_dashboard.py`) dostane
   keyword-only `include_test: bool = False` a při `False` přidá
   `WHERE clients.is_test IS FALSE`. Query dnes joinuje `Prompt` a
   `PromptSet` — přidej join na `Client` (decision 3). Výchozí hodnota
   `False` znamená, že každé volající místo je chráněné, i kdyby na parametr
   zapomnělo.
5. Router `/ops` (`app/routers/ops_dashboard.py`): nový query parametr
   `include_test` s `description=...` (AI_INSTRUCTIONS §3), protažený přes
   `_ops_scope` do všech `/ops/api/*` endpointů. **Nesmí zůstat endpoint,
   který parametr ignoruje** — dva pohledy na téže stránce s různým
   filtrem jsou horší než žádný filtr.
6. Přepínač v `app/templates/ops/index.html` vedle přepínačů rozsahu
   (7d/30d/90d/All): „včetně testovacích dat", HTMX, stav v URL
   (decision 5). Když je zapnutý, musí to být na stránce **vidět** i po
   scrollu k tabulkám — ne jen podle stavu jednoho checkboxu nahoře.
7. Datový krok při nasazení: existujícímu klientovi „Test Skoda Auto"
   nastavit `is_test = true` (přes UI, ne SQL — je to jedno kliknutí).
8. Testy: agregace bez parametru testovacího klienta nevidí; s
   `include_test=true` ho vidí; součet runů obou variant sedí na celkový
   počet; osa „podle klienta" testovacího klienta v základním stavu
   nevypíše.

**Done when:** `/ops` bez přepínače neobsahuje runy testovacího klienta
v žádném z pohledů (souhrn, graf, providerři, klienti, uživatelé), s
přepínačem ano; `/clients` klienta pořád normálně ukazuje s odznakem;
`pytest` zelený; responsive ~375 / 768 / desktop ověřené v prohlížeči.

**Expected commit:** `feat(clients): exclude test clients from ops aggregates`

---

## PRE-2 — Pole „platí od" ve formuláři ceny modelu

**Target:** `app/routers/ai_models.py`, `app/templates/ai_models/form.html`,
`app/templates/base.html` (inline JS),
`app/templates/partials/macros.html`, `app/i18n/en.json`, `app/i18n/de.json`,
`tests/`

1. Formulář ceny modelu dostane **jedno** nepovinné pole `effective_from`
   (`datetime-local`), platné pro všechny komponenty zapsané tímhle
   odesláním. Prázdné = `now()`, tedy dnešní chování (decision 6). Ne pole
   na každou komponentu zvlášť — ceník se mění naráz a pět datumů v jednom
   formuláři je pozvánka k překlepu.
2. Převod na UTC v prohlížeči (decision 8): `datetime-local` + skryté pole
   s ISO UTC hodnotou, převod v existujícím inline `<script>` bloku
   v `base.html`. Bez JS se naivní hodnota interpretuje jako UTC a nápověda
   pod polem to říká.
3. Router: parsování a validace přes `AppError` se srozumitelnou hláškou
   (ne 500 z `ValueError`), zápis do `AIModelPriceComponent.effective_from`
   v `create` i `update` cestě — obě dnes spoléhají na server default.
4. Historie cen (`_price_history_rows`) musí unést dva řádky se stejnou
   cenou a různým „platí od" (decision 7) — ověř na datech z PRE-0, kde
   přesně tohle je.
5. Dokumentace pole: `Form(..., description=...)` podle AI_INSTRUCTIONS §3,
   plus docstring routy, která vysvětlí, **proč** pole existuje (cena runu
   se počítá cenou platnou v jeho čase).
6. Testy: prázdné pole → `effective_from` ≈ teď; vyplněné minulé datum →
   uloží se přesně; run staršího data se tou cenou ocení (nejlevnější test
   celé větve a přesně ten, co chytí regresi); nesmyslný vstup → `AppError`,
   ne 500.

**Done when:** cenu jde v UI zadat se zpětnou platností, historie cen ji
ukáže správně, a run z doby před zadáním ceny má v `/ops` číslo místo
pomlčky; `pytest` zelený; responsive ověřené.

**Expected commit:** `feat(ai-models): allow backdating a model price change`

---

## PRE-3 — Závěrečný průchod

**Target:** `app/i18n/en.json`, `app/i18n/de.json`, šablony, `tests/`,
`docs/REQUIREMENTS.md`

1. i18n úplnost — grep přes nové a změněné šablony na natvrdo psanou prosu
   mimo `t()` (AI_INSTRUCTIONS §3).
2. Responsive ~375 / 768 / desktop: `/ops` s přepínačem i bez, formulář
   klienta, seznam klientů, formulář modelu s novým polem. Skutečně
   v prohlížeči, ne „mělo by fungovat" (AI_INSTRUCTIONS §7).
3. `docs/REQUIREMENTS.md`: zaznamenat, že ops čísla ve výchozím stavu
   testovací klienty nezahrnují, a že cena runu se řídí cenou platnou
   v jeho čase (to druhé dnes není nikde napsané a je to netriviální
   chování, na kterém stojí fakturace). Ukázat diff, neměnit potichu.
4. Celý `pytest` zelený.

**Done when:** vše výše ověřené a odškrtnuté s uživatelem.

**Expected commit:** `docs(requirements): record test-client exclusion and price validity`

---

## Co tahle větev VĚDOMĚ nedělá

- **Neodděluje testovací prostředí od produkce.** Příznak je doplněk, ne
  náhrada (decision 1). Lokální stack zůstává správným místem pro
  experimenty, tohle řeší jen to, co se na produkci testovat musí.
- **Neskrývá testovací klienty ze seznamů ani z `/dashboard`**
  (decisions 2 a 4).
- **Nepřidává `clients.is_active`** — samostatné téma, zamítnuto
  2026-09-18 (`docs/TASKS_SCHEDULER.md` decision 31).
- **Neřeší import ceníků od providerů automaticky.** Ceny se zadávají
  ručně; tahle větev jen dovolí zadat je se správným datem.
- **Nemění výpočet ceny** (`app/services/cost.py`) — chování je správné,
  chybí mu jen data se správnou platností.

---

## Completion Checklist (až je branch hotová a smergnutá)

- V `docs/TASKS_SCHEDULER.md` a `docs/PROMPTS_SCHEDULER.md` přečíslovat
  plánovačovu migraci z 0029 na 0030 (poznámka je v obou už teď).
- Doplnit do `docs/TASKS.md` odkaz na tuhle větev, stejný vzor jako ostatní.
- Nastavit „Test Skoda Auto" `is_test = true` na produkci a ověřit, že
  `/ops` čísla spadla o jeho runy.
- Zkontrolovat `/ops` po nasazení: Cost estimate musí být vyšší než před
  PRE-0 (poprvé započítané OpenAI a Gemini runy) a zároveň nižší o runy
  testovacího klienta.
