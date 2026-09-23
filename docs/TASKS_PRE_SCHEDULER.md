# SignalMap — Tasks: Pre-Scheduler Data Hygiene

## Status: ✅ Done — PR #17, merged 2026-09-19, released in v1.0.0

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
> 3. **Liga citovaných domén seskupuje podle syrové domény.** `meag.com` a
>    `www.meag.com` jsou v tabulce dva řádky s rozděleným počtem citací.
>    Změřeno na produkci 2026-09-18: 26 domén se takhle tříští, dotýká se to
>    312 citací (~38 % archivu) a počet unikátních domén je nafouknutý z 232
>    na 258. Není to jen špatné číslo — je to špatné **pořadí** v tabulce,
>    která má říkat, koho si AI bere jako zdroj.
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
10. **Doména se normalizuje v SQL, uvnitř agregace — nikdy v Pythonu nad
    hotovým výsledkem.** Sloučit řádky až po dotazu vypadá lákavě a je to
    špatně ze tří důvodů:
    - `run_coverage_pct` stojí na `count(DISTINCT Run.id)`. Run, který
      cituje `meag.com` i `www.meag.com`, je v obou řádcích — sečtením
      vyjde pokrytí přes 100 %. Správný výsledek dá jen `DISTINCT` nad
      sloučenou skupinou.
    - `LIMIT` je dnes **před** sloučením. Dvě varianty se 4 + 3 citacemi
      můžou obě spadnout pod hranici top N a v tabulce vůbec nebýt, i když
      sloučené na 7 by se tam vešly. Sloučení po `limit` tenhle případ
      nikdy nenajde.
    - `app/services/ops_dashboard.py` má zapsanou disciplínu „agregace
      v SQL, nikdy Python smyčka nad řádky" a tohle je přesně ten případ.
11. **SQL dvojče `normalize_domain()` bydlí vedle něj v `app/utils.py`.**
    Komentář na `app/services/dashboard.py` (domain_league_rows) správně
    varuje, že druhá, SQL-side kopie normalizačního pravidla je drift
    riziko. Řeší se to stejně jako u ceny runu: obě implementace v jednom
    souboru vedle sebe (`estimate_run_cost` / `run_cost_sql_expr`
    v `app/services/cost.py`) plus tabulkový test, který stejnou sadu
    vstupů požene oběma cestami a porovná výsledky. Testy běží proti
    reálnému Postgresu (`signalmap_test`), takže `regexp_replace` je
    k dispozici a test je skutečný, ne simulovaný.
    **Zvažováno a odloženo:** generovaný sloupec
    `citations.source_domain_normalized GENERATED ALWAYS AS (...) STORED`
    s indexem. Elegantnější, až se bude podle domény seskupovat na víc
    místech (export, gap score) — na dvě volající místa je to nadbytek a
    pravidlo by se přestěhovalo do DDL, kde se mění migrací.
12. **`citations.source_domain` se nikdy nepřepisuje.** Je to evidence
    (NFR-6), je to přesně to, co provider vrátil, a `app/services/export.py`
    ji vyváží klientovi jako doklad. Normalizace je **pohled na data**, ne
    data — proto výraz v dotazu, ne UPDATE.
13. **Boolean `is_test`, ne enum `client_type` — zvažováno a odloženo.**
    Nabízelo se místo příznaku zavést typ klienta
    (`production` / `test` / `demo` / `internal`), protože klientský záznam
    už tak sbírá pole (po plánovači k němu přibude `priority`,
    `daily_run_limit`, `monthly_budget_usd`). Odloženo: dnes existuje
    **jedna** otázka — počítat ho do souhrnů, nebo ne — a enum by si
    vynutil rovnou vymyslet, čím se `demo` liší od `internal`. To je přesně
    „design for hypothetical future requirements" (AI_INSTRUCTIONS §5).
    Levným ho dělá decision 3: rozhodnutí je na jednom místě, takže pozdější
    přechod na enum je jedna migrace a jeden `WHERE`, ne dvacet volajících
    míst — stejné pravidlo, jaké si repo zapsalo u `can_edit()`.
    Pozor na rozlišení: „testovací klient" je vlastnost **našeho používání
    appky**; „prospect / aktivní / bývalý klient" je životní cyklus
    **obchodu** a do stejného pole nepatří.
14. **Příznak smí přepnout jen admin, a to samostatnou akcí, ne polem ve
    formuláři klienta.** Editor klienta založí i edituje (`_editor_or_admin`
    v `app/routers/clients.py`), ale `is_test` mění, co říkají **všechna**
    čísla v `/ops`, a to zpětně (decision 15) — rozhodnuto 2026-09-18, že
    tohle je admin.
    Forma je **vlastní toggle route** (`POST /clients/{id}/toggle-test`)
    s odznakem na detailu klienta, podle vzoru `toggle_ai_model_active`
    (`app/routers/ai_models.py`) a aktivace uživatele (`app/routers/users.py`)
    — **ne checkbox ve sdíleném formuláři**. Důvod je konkrétní past:
    neodškrtnutý checkbox se v HTML formuláři neodesílá vůbec, takže kdyby
    se pole editorovi jen skrylo, jeho první uložení klienta by `is_test`
    tiše shodilo na false. Samostatná akce tuhle třídu chyby ruší, ne
    ošetřuje.
    V šablonách se ptej přes nový helper `can_flag_test_client(user)`
    v `app/templating.py` vedle `can_edit()`, nikde ne `role == "admin"`
    natvrdo. Skrytí v UI **nenahrazuje** serverovou kontrolu — route má
    `require_role("admin")` nezávisle.
    Praktický důsledek, který je třeba přijmout: dnes je admin **jediný
    účet** (Philip i Andrew jsou editoři), takže testovacího klienta založí
    editor a označit ho musí přijít admin. U věci, která se děje párkrát za
    rok, je to přijatelná cena; kdyby začala vadit, levnější než měnit role
    je dát `can_flag_test_client()` jinou podmínku — na jednom místě.
15. **Příznak působí zpětně, ne „od teď".** Filtr se vyhodnocuje při každém
    dotazu a nikam se nic neukládá, takže označením klienta zmizí z `/ops`
    **celá jeho historie**, ne jen runy od té chvíle — a odškrtnutím se celá
    vrátí. Je to správné chování (testovací runy do souhrnů nikdy nepatřily,
    ani ty včerejší), ale je netriviální a musí být vidět v UI: u přepínače
    krátká nápověda, že se to týká i minulých runů.

---

## Nové schéma (migrace 0029)

Jeden sloupec.

```
clients
  + is_test            bool not null default false   -- decision 1
```

Cenové komponenty ani nic dalšího se **nemění** — PRE-2 mění jen to, co do
existující tabulky zapisuje formulář, a PRE-4 nemění schéma vůbec
(decision 12).

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| PRE-0 | Jednorázové zpětné doplnění platnosti cen (ruční SQL na produkci) | ⏳ |
| PRE-1 | `clients.is_test` + vyloučení testovacích dat z `/ops` | ✅ |
| PRE-2 | Historie cen modelu: seskupení po komponentách, sloučení beze změny — **zpětná platnost zamítnuta**, viz níže | ✅ |
| PRE-4 | Normalizace domén v lize citovaných domén a v počtu unikátních domén | ✅ |
| PRE-3 | Závěrečný průchod: i18n, responsive, docs | ✅ |

PRE-1, PRE-2 a PRE-4 jsou nezávislé, pořadí mezi nimi je libovolné. PRE-3 je
až po všech třech.

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
2. **Samostatná admin akce, ne pole ve formuláři** (decision 14):
   `POST /clients/{client_id}/toggle-test` s `require_role("admin")`,
   přepínač na detailu klienta, podle vzoru `toggle_ai_model_active`
   v `app/routers/ai_models.py`. Formulář klienta (create i edit) se
   **nemění** — a nesmí `is_test` nijak zapisovat ani nulovat.
   Nápověda u přepínače řekne obojí: že se runy klienta nepočítají do
   souhrnů v `/ops`, a že to platí **i zpětně** (decision 15).
2b. Helper `can_flag_test_client(user)` v `app/templating.py` vedle
   `can_edit()` + registrace do `templates.env.globals`. V šablonách nikde
   `role == "admin"` natvrdo (decision 14).
3. Odznak „Test" v `/clients` a na detailu klienta — vizuálně stejný jazyk
   jako `Active`/`Inactive` u uživatelů v `/users`, ať se to nemusí učit
   podruhé. Odznak vidí **všichni** včetně viewera; přepínat smí jen admin.
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
   nastavit `is_test = true` (přes UI pod admin účtem, ne SQL — je to jedno
   kliknutí).
8. Testy: agregace bez parametru testovacího klienta nevidí; s
   `include_test=true` ho vidí; součet runů obou variant sedí na celkový
   počet; osa „podle klienta" testovacího klienta v základním stavu
   nevypíše; **editor dostane na toggle route 403** a **editorova editace
   klienta příznak nezmění** (regrese na past z decision 14).

**Done when:** `/ops` bez přepínače neobsahuje runy testovacího klienta
v žádném z pohledů (souhrn, graf, providerři, klienti, uživatelé), s
přepínačem ano; `/clients` klienta pořád normálně ukazuje s odznakem;
přepínač vidí a použije jen admin, editor na něj dostane 403 a jeho editace
klienta příznak nezmění; `pytest` zelený; responsive ~375 / 768 / desktop
ověřené v prohlížeči.

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

### Skutečný výsledek (2026-09-19) — zpětná platnost zamítnuta

Pole „platí od" bylo implementováno podle zadání výše, ověřeno na reálných
datech (69 ze 72 neoceněných runů dostalo cenu), a pak **zrušeno** —
rozhodnutí uživatele po ukázání historie cen v UI.

**Důvod:** `ai_model_price_components` je append-only a nemá mazání ani
`voided_at`. Dokud formulář uměl zapsat jen `now()`, minulost se nedala
pokazit. Pole „platí od" tenhle bezpečnostní plot samo o sobě odstranilo:
překlep v datu (např. rok 2020 místo 2026) by vytvořil trvalý řádek, který
by nešlo opravit ani smazat přes UI — jen ručním zásahem do databáze. To je
nová expozice, kterou zadání výše nezohlednilo, a riziko převážilo přínos.

**Co zůstalo:** oprava zobrazení historie cen (commit `ebc2ddb`) —
seskupení `_price_history_rows()` po komponentách místo jednoho
plochého seznamu řazeného napříč typy (tři ceny platné najednou dřív
vypadaly jako tři si odporující odpovědi), a slučování sousedních řádků se
shodnou cenou do jednoho úseku (backfill kvůli PRE-0 zapisuje řádek i
tehdy, když se cena nemění — zobrazený jako samostatný řádek to vytvářelo
předěl v místě, kde se nic nezměnilo). Komponenta dostane tabulku jen když
má víc než jeden úsek; jinak je řádek pod „Unchanged since entered" — u
modelu s pěti komponentami (typicky Anthropic) tak nevzniká pět
prázdných tabulek.

**Dopad:** cena zadaná později než run tenhle run **navždy** neocení —
formulář to už neumožní opravit. Lokálně se to týká 3 runů u
`gemini-3.5-flash` (8.–9. 9. 2026), které v `/ops` zůstávají s pomlčkou.
Na produkci žádný další zásah po PRE-0 nebyl potřeba, protože ten už byl
proveden ručním SQL právě proto, že UI cestu tehdy nemělo.

**Případná budoucí oprava** (mimo tuhle větev): buď `voided_at` na
existujícím řádku (oprava = supersede, ne smazání), nebo bitemporální
`recorded_at` vedle `effective_from` — obojí je migrace a vlastní úkol,
ne oprava zobrazení.

---

## PRE-4 — Normalizace domén v lize a v počtu unikátních domén

**Target:** `app/utils.py`, `app/services/dashboard.py`, `tests/`

**Nález (2026-09-18):** `domain_league_rows()`
(`app/services/dashboard.py`, ~ř. 269–321) seskupuje přes
`.group_by(Citation.source_domain)`, tedy podle syrové hodnoty sloupce.
`normalize_domain()` tam už je, ale používá se **jen** na dohledání
`domain_type` a `is_own_domain`, ne na seskupení. Stejnou vadu má
`citation_totals()` (~ř. 128–138): `count(distinct Citation.source_domain)`
nad syrovým sloupcem.

**Obě se musí opravit najednou.** Router `/dashboard` vrací na téže stránce
KPI dlaždici z `citation_totals()` i tabulku z `domain_league_rows()`.
Opravit jen jednu znamená vyrobit novou nesrovnalost — dlaždice by hlásila
jiný počet domén, než kolik řádků ukazuje tabulka pod ní. Dnes jsou obě
„stejně špatně", proto si toho nikdo nevšiml.

**Symptom, který to prozradil:** ve screenshotu z produkce nesou oba řádky
(`meag.com` i `www.meag.com`) stejný odznak „Corporate". `domain_classifications`
je klíčovaná normalizovanou doménou (`app/models/domain_classification.py`)
a `/api/classify` ukládá `normalize_domain(...)` — klasifikační vrstva ty
dvě hodnoty tedy dávno považuje za jednu doménu, zatímco seskupení ne.
Tabulka si odporuje sama v sousedních řádcích.

**Změřený dopad na produkci** (2026-09-18, přes všechny klienty):

| | |
|---|---|
| domén tříštěných na víc variant | 26 |
| dotčených citací | 312 (~38 % archivu) |
| unikátních domén dnes → po opravě | 258 → 232 |
| jiný rozdíl než `www.` | **žádný** |

Nejvyšší dotčená doména je `skoda-media.de` s 86 citacemi, druhá
`skoda-storyboard.com` s 36 — obě klientovy vlastní zdroje, obě rozdělené.
Že jsou všechny rozdíly jen `www.` (žádná velká písmena, port ani kořenová
tečka) je samostatný závěr: **normalizační pravidlo nepotřebuje rozšířit**,
`lower()` + strip `www.` na reálná data stačí. Nevymýšlej složitější.

1. Do `app/utils.py` přidat SQL dvojče vedle `normalize_domain()`, např.
   `normalized_domain_sql(col)` vracející
   `func.regexp_replace(func.lower(col), '^www\.', '')`. Docstring vysvětlí,
   proč existují dvě implementace a co je drží v souladu (decision 11).
2. `citation_totals()` — `count(distinct <výraz>)` místo syrového sloupce.
   Komentář o NULL semantice zůstává platný (`count(DISTINCT ...)` NULL
   vynechává i nad výrazem).
3. `domain_league_rows()` — `group_by`, tie-break v `order_by` i vracený
   `domain` na normalizovaný výraz. **Seskupení musí být před `LIMIT`**
   (decision 10), tedy v SQL, ne nad hotovým výsledkem.
4. Lookup klasifikace se tím **zjednoduší**: klíč skupiny je už
   normalizovaný, takže `normalize_domain()` na obou místech odpadá — i
   s komentářem o drift riziku, který se stěhuje k novému dvojčeti.
5. `is_own_domain(row.domain, client.domain)` nech být — normalizuje si
   vstup sám, změna group key mu nevadí.
6. Testy:
   - tabulkový test dvojčat: stejná sada vstupů (`www.X.com`, `X.COM`,
     `x.com`, `www2.x.com`, `blog.x.com`, prázdný řetězec) přes
     `normalize_domain()` i přes SQL výraz, výsledky se musí shodovat;
   - liga: citace na `www.x.com` a `x.com` ze **dvou různých runů** dají
     jeden řádek se správným počtem citací **a** `run_coverage_pct` ≤ 100;
   - run, který cituje obě varianty, se do `run_coverage_pct` započítá
     **jednou** (regrese na decision 10, první odrážka);
   - `citation_totals()` počítá domény sloučeně;
   - řádek pod hranicí `limit`, který se sloučením do top N dostane, tam
     skutečně je.

**Vědomě mimo rozsah:** subdomény se **nesjednocují** — `blog.meag.com`
zůstává samostatným řádkem, protože je to jiný zdroj. Důsledek, který je
třeba znát: `is_own_domain()` bere subdoménu jako vlastní doménu, takže
v tabulce můžou stát dva různé řádky, oba označené jako „vlastní". To je
záměr, ne nedodělek.

**Done when:** v lize na produkčních datech je `meag.com` **jeden** řádek se
14 citacemi; KPI dlaždice u téhož klienta sedí na počet skupin v tabulce;
`pytest` zelený včetně nových testů; responsive ověřené na `/dashboard`.

**Expected commit:** `fix(dashboard): group cited domains by their normalized form`

---

## PRE-3 — Závěrečný průchod

**Target:** `app/i18n/en.json`, `app/i18n/de.json`, šablony, `tests/`,
`docs/REQUIREMENTS.md`

1. i18n úplnost — grep přes nové a změněné šablony na natvrdo psanou prosu
   mimo `t()` (AI_INSTRUCTIONS §3).
2. Responsive ~375 / 768 / desktop: `/ops` s přepínačem i bez, formulář
   klienta, seznam klientů, formulář modelu s novým polem, `/dashboard`
   s ligou domén. Skutečně v prohlížeči, ne „mělo by fungovat"
   (AI_INSTRUCTIONS §7).
3. `docs/REQUIREMENTS.md`: zaznamenat tři věci, které dnes nejsou nikde
   napsané a všechny jsou netriviální: (a) ops čísla ve výchozím stavu
   nezahrnují testovací klienty, (b) cena runu se řídí cenou platnou
   v jeho čase a chybějící cena znamená „neznámá", nikdy „nula" — na tom
   stojí fakturace, (c) doména se ve všech agregacích porovnává
   a seskupuje v normalizovaném tvaru, kdežto v evidenci a exportu
   zůstává syrová. Ukázat diff, neměnit potichu.
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
- **Nesjednocuje subdomény** s doménou druhého řádu (PRE-4) a **nepřidává
  generovaný sloupec** pro normalizovanou doménu (decision 11) — obojí je
  vědomé odložení, ne opomenutí.

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
- Ověřit na produkci, že počet unikátních domén spadl z 258 na 232 (PRE-4)
  — a **říct to Philipovi dopředu**. Je to druhý přepočet čísel po citacích
  565 → 820, tentokrát směrem dolů a se změnou pořadí v tabulce. Formulace,
  která nesvádí k panice: data se nemění, opravuje se způsob, jakým se
  sčítají — `www.meag.com` a `meag.com` je jedna firma, ne dvě.
