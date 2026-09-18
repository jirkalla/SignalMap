# SignalMap — Roadmap (post phase 5)

## v1.0 | Září 2026

Phases 1–5 (`docs/TASKS.md`) jsou hotové a smergnuté. Tenhle dokument sbírá, co
přijde dál — sestaveno z konverzace o nasazení appky na server a produktovém
směřování. Pořadí odráží **závislosti**, ne jen prioritu podle přání: appka
nejde bezpečně vystavit online bez auth, scheduler nedává smysl bez viditelnosti
nákladů, gap/opportunity score potřebuje sentiment i konkurenční data.

Každá položka je zatím jen **rámec** — teprve až se na ni dojde, dostane vlastní
`docs/TASKS_PHASEn.md`/`docs/PROMPTS_PHASEn.md` pár se schváleným schématem a
design decisions, stejně jako fáze 1–5.

**Dodatečná úprava (2026-09-15):** Rozsáhlá konverzace o Philipově "AI
Perception Analysis Platform" functional spec dokumentu, o komerčním
směřování appky (potvrzeno — SignalMap se plánuje jako komerční produkt,
Knauf je reálné, probíhající jednání) a o konkrétním technickém návrhu pro
#4/#5/#10. Plné bod-po-bodu srovnání spec dokumentu s touhle roadmapou:
[Evidence Gap Ledger artefakt](https://claude.ai/artifact/2d5A52xq28j49bmmT9nScu).
Výsledky promítnuty do #4, #5, #9, #10 níže a do nových položek #11–16.

---

## 1. Auth + user management (admin / editor / viewer)

**Proč první:** appka dnes nemá žádnou autentizaci (`docs/REQUIREMENTS.md` §4)
— kdokoliv se znalostí URL může vidět všechna data a hlavně **spouštět placené
runy**. Blokuje jakékoliv veřejné nasazení.

### Rozhodnutí (potvrzeno v konverzaci)

- **Provisioning:** admin vytváří účty přímo (jméno/email/role/heslo) — žádné
  self-service registrace, žádná závislost na emailu pro onboarding.
- **Login:** email + heslo. Žádné OAuth v1.
- **Reset hesla:** appka zatím nemá SMTP — admin nastaví nové heslo přímo
  (mimo appku ho sdělí uživateli). Žádný emailový reset-link flow v1.
- **API tokeny:** ne v v1 — jen browser session (cookie).
- **Role:** `admin` (správa uživatelů, providers, settings, cokoliv) /
  `editor` (běžná práce — klienti, prompty, runy, dashboard) / `viewer` (jen
  čtení, nemůže spouštět runy ani nic editovat).
- **Audit:** ano, od v1 — `triggered_by_user_id` na `Run`.
- **Vynucená změna hesla:** ano — `must_change_password` flag na `User`,
  nastavený při vytvoření účtu i při ručním resetu adminem. Zajistí, že heslo,
  co admin sdělí mimo appku, funguje jen jednou.

### Co je vidět komu (UI)

`current_user` se injektuje do každého `render()` volání (stejný vzor jako
dnešní `get_t(request)`), takže libovolná šablona umí `{% if
current_user.role in (...) %}`. Route-level `require_role()` je zdroj pravdy
pro bezpečnost; skryté UI prvky jsou jen UX — obojí potřeba.

| Prvek UI | admin | editor | viewer |
|---|---|---|---|
| Dashboard, Run detail (čtení) | ✅ | ✅ | ✅ |
| Tlačítko "Spustit run" | ✅ | ✅ | ❌ |
| Vytvořit/editovat/mazat (klient, prompt, tracked entity...) | ✅ | ✅ | ❌ |
| Export (CSV/XLSX/JSON) | ✅ | ✅ | ❌ |
| Doménová klasifikace (dashboard league table) | ✅ | ✅ | ❌ |
| `/providers`, `/users` | ✅ | ❌ | ❌ |
| `/ai-models`, `/settings` | ✅ | ✅ | ❌ |
| `/help`, `/findings` (Guide/Findings) | ✅ | ✅ | ❌ |
| `/docs` (API dokumentace) | ✅ | ❌ | ❌ |

Viewer je čistě read-only — žádná akce, co appku nebo data mění, ani je
nestahuje.

**Dodatečná úprava (2026-09-12):** `/ai-models` a `/settings` byly původně
plánované jako admin-only (spolu s `/providers`), ale na výslovný požadavek v
konverzaci byly otevřené i editorovi — editor smí upravovat modely a
system-instruction šablony, ne ale spravovat providery/uživatele. `/help` a
`/findings` byly naopak zavřené viewerovi (ten čte jen data klientů, ne interní
dokumentaci/log). Tabulka výše odráží skutečný stav; `docs/TASKS_PHASE6.md`
P6-T6 pořád popisuje původní (admin-only) plán jako historický záznam
rozhodnutí v době psaní promptu, ne jako aktuální zdroj pravdy.

### Architektura

Knihovna: **fastapi-users** — zavedená, udržovaná knihovna pro hashování
hesel a session logiku, místo vlastního hand-rolled řešení (méně custom
bezpečnostního kódu, který je potřeba sami hlídat a patchovat).

```python
# app/models/user.py
from fastapi_users.db import SQLAlchemyBaseUserTable
from app.models.base import Base

# Stejný vzor jako DOMAIN_TYPES (app/models/domain_classification.py) —
# přidání role = rozšířit tuhle tuple, žádná změna frameworku.
ROLES = ("admin", "editor", "viewer")

class User(SQLAlchemyBaseUserTable[int], Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # email / hashed_password / is_active / is_verified přichází z SQLAlchemyBaseUserTable
```

Auth backend — cookie session (ne JWT vydávaný klientovi jako API token,
appka je server-rendered Jinja2+HTMX, ne API-first SPA):

```python
# app/auth.py
cookie_transport = CookieTransport(cookie_name="signalmap_session", cookie_max_age=60*60*24*14)
auth_backend = AuthenticationBackend(name="cookie", transport=cookie_transport, get_strategy=get_jwt_strategy)
```

Jeden centrální dependency pro autorizaci, používaný stejně jako dnešní
`_get_client_or_404`-style helpery v routerech:

```python
def require_role(*allowed_roles: str):
    def dependency(user: User = Depends(current_active_user)) -> User:
        if user.role not in allowed_roles:
            raise AppError("forbidden", t("errors.forbidden"), status_code=403)
        return user
    return dependency

# použití: @router.post(...) def x(user: User = Depends(require_role("admin"))): ...
```

Správa uživatelů — vlastní admin-only CRUD routy (`GET/POST /users`,
`POST /users/{id}/edit`, `POST /users/{id}/reset-password`,
`POST /users/{id}/deactivate`), stejný vzor jako existující `app/routers/
clients.py`. fastapi-users' vestavěné `/auth/register` a emailový
reset-password router se **vůbec nemountují** — nejsou v v1 potřeba a
nechceme veřejnou registrační cestu do appky.

`current_user` se injektuje do každého `render()` volání (stejný vzor jako
dnešní `get_t(request)` pro jazyk) — šablony pak dělají `{% if
current_user.role == 'admin' %}`. Route-level `require_role()` je zdroj
pravdy pro bezpečnost; skryté UI prvky v šabloně jsou jen UX — obojí potřeba.

Audit: `Run.triggered_by_user_id` (nullable FK na `users.id` — nullable,
protože budoucí scheduler-run nemá žádného člověka za sebou;
`trigger_type='scheduled'` + `triggered_by_user_id=None` dohromady jsou
jednoznačné).

### Rozšiřitelnost — proč to půjde přidávat bez přepisování základu

- **Nová role** → rozšířit `ROLES` tuple + doplnit `require_role(...)` volání,
  co ji mají zahrnovat. Žádná změna frameworku.
- **API tokeny později** → přidat `BearerTransport` jako druhý transport na
  **stejný** `AuthenticationBackend` — nesahá na cookie login flow, přesně
  rozšiřovací bod, pro který je fastapi-users navržený.
- **Email/SMTP později** → vybrat SMTP/emailovou službu, přidat credentials do
  `.env` (stejný vzor jako `GOOGLE_API_KEY`/`ANTHROPIC_API_KEY`), napsat malý
  `app/email.py` helper, připojit fastapi-users' vestavěný
  `get_reset_password_router()` (existuje v knihovně, teď se jen nemountuje) a
  napojit jeho `on_after_forgot_password` callback na ten helper. Žádná změna
  schématu — reset-password flow pracuje nad stejnou `users` tabulkou.
  Přechod na "admin pozve emailem" místo dnešního "admin vytvoří účet přímo"
  by byla samostatná následná úprava provisioning flow, ne automatický
  důsledek zapnutí SMTP.
- **Multi-tenancy později** (samostatná, pozdější položka — viz níže) → přidat
  `org_id` FK na `User` + tenant-scoping dependency navrstvenou nad
  `require_role` — ortogonální k rolím (viewer v organizaci A zůstane
  odříznutý od dat organizace B bez ohledu na roli).
- **Audit na dalších tabulkách** → stejný `_by_user_id` sloupec pattern
  (např. `deleted_by_user_id`), pokud bude potřeba později.

### Proč je to snadné na údržbu

- fastapi-users řeší hashování hesel a session bezpečnost — méně vlastního
  bezpečnostního kódu, co je potřeba sami hlídat.
- Jeden centrální `require_role()` dependency místo roztroušených ad-hoc
  kontrol po routerech.
- Admin user-management routy kopírují přesně stejný CRUD vzor, jaký appka
  už má u Client/Prompt/TrackedEntity — žádný nový mentální model.
- Bez SMTP závislosti v v1 — méně pohyblivých částí pro malý tým.

## 2. Deploy hardening

**Proč druhé:** appka dosud běžela jen lokálně (`docker compose up` na
vývojářově PC). Nic z tohohle ještě není řešené.

- Doména + DNS A záznam na IP serveru.
- Reverse proxy — **Caddy** (ne nginx+certbot, zbytečná komplikace pro první
  nasazení) — automatický Let's Encrypt certifikát, `reverse_proxy app:8000`
  přes interní Docker síť.
- Appka nesmí být přímo dostupná zvenku — dnešní `docker-compose.yaml`
  (`"58000:8000"` bez bind adresy) naslouchá na `0.0.0.0`; po přidání proxy buď
  mapping úplně zrušit (Caddy mluví s `app:8000` po interní síti), nebo omezit
  na `127.0.0.1:58000:8000`.
- Firewall na serveru — jen 80/443 (+ SSH) navenek.
- Produkční `.env` (secrets) na serveru, mimo git.
- Zálohování `pgdata` volume — evidence data (`raw_responses`, `citations`) se
  nikdy nepřepisují (NFR-6), ztráta bez zálohy = nevratná ztráta historie.
- Repo bylo prověřené na secrets/citlivá data před sdílením přístupu
  kolegovi — čisté (žádný `.env`, žádné API klíče v historii).

## 3. Jít online

Ověřit HTTPS funguje, auth blokuje neautorizovaný přístup, appka běží
identicky jako lokálně (Docker Compose portabilita, NFR-8).

## 4. Ops dashboard (interní)

**Proč před schedulerem, ne po něm:** chcete vidět náklady dřív, než appka
začne spouštět runy automaticky a nehlídaně. Přejmenováno z "Cost/ops
dashboard" (2026-09-15) — odděleno od budoucího Billingu (#16), který je
jiná věc (klientské účtování, ne interní viditelnost).

- Data už existují, nejde o nové schéma: `runs.latency_ms`, `token_usage`
  (JSON na `RawResponse`), `cost_per_1k_input_usd`/`cost_per_1k_output_usd`
  (na `ai_models`, z fáze 2 admin UI), `runs.triggered_by_user_id` (#1).
- Dvě nezávislé osy pohledu: Klient → Prompt Set → Prompt (vnořené), a
  odděleně Uživatel (napříč klienty — kdo runy spouští, včetně pseudo-
  uživatele "Scheduler" pro `trigger_type='scheduled'`).
- Agregace vždy v SQL (`GROUP BY`/`SUM`/`COUNT`), nikdy Python nad plným
  seznamem `Run` řádků — jediný způsob, jak zůstat rychlý i při tisících
  runů. Časový bucket u grafu se mění podle rozsahu (den/týden/měsíc),
  stejný princip jako existující `/dashboard` bucket-switching logika.
- `/ops` je **admin/editor only, nikdy viewer, nikdy client-facing** —
  jiná access-control hranice než klientský `/dashboard`.
- **Implementováno:** `docs/TASKS_OPS_DASHBOARD.md` + `docs/PROMPTS_OPS_DASHBOARD.md`
  (design odsouhlasený na mockup artefaktu) — T0–T4 hotové a otestované na
  branch `feature/signalmap-ops-dashboard` (2026-09-15), čeká na merge.

## 5. Scheduler

Automatické, opakované spouštění runů (`FR-9` bylo ve fázi 1 explicitně mimo
scope — teď dává smysl to odemknout).

- Rozšíření `Run.trigger_type` o `'scheduled'` (dnes jen `'manual'`).
- `triggered_by_user_id` (#1) rozliší, kdo/co run spustil.
- UI koncept z mockupu: přepínač "Repeat" (weekly/monthly) na úrovni promptu
  nebo prompt setu.
- Bez #1 (auth) a #4 (ops dashboard) tohle nejde bezpečně pustit ven —
  nehlídané opakované volání placených API bez viditelnosti nákladů je riziko.

### Technický návrh (2026-09-15)

- **Fronta oddělená od `runs`**, ne rozšíření `Run` samotné — appka má
  dnes zdokumentovaný dvoufázový životní cyklus runu (`pending` vloženo
  těsně před voláním adaptéru) a partiální unique index proti duplicitě
  (`app/models/run.py`); scheduler rozhoduje **kdy** spustit, `Run` vznikne
  až při skutečném spuštění, přesně jako dnes.
- Dvě nové tabulky: `scheduled_run_definitions` (pravidlo opakování —
  `prompt_id`, `frequency`, `next_run_at`, `is_active`) a `run_queue`
  (konkrétní položka k vykonání — `priority`, `status`, `queued_at`,
  `run_id` po vzniku).
- Worker: `SELECT ... FROM run_queue WHERE status='queued' ORDER BY
  priority DESC, queued_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED` — standardní
  Postgres vzor, žádný Celery/Redis (drží NFR-8 portabilitu, menší provozní
  zátěž pro malý tým).
- Exekuční logika (sestav request → zavolej adapter → ulož Run/RawResponse/
  Citations) se vytáhne z `trigger_run` do sdíleného `app/services/
  run_execution.py`, volaného jak z HTTP routeru, tak z workeru — žádná
  duplikace.
- Guardrail proti nehlídanému rozpočtu od prvního dne: denní/týdenní limit
  počtu runů na klienta (jednoduché počítadlo, ne celý billing engine).

**Dodatečná úprava (2026-09-15):** #11 (oprava extrakce citací) a #12 (LLM
quote-verification skill) by měly jít před #6/#7/#8 — využívají stejný
`execution_type='llm_prompt'` hák na `AnalysisSkill`, co čeká nevyužitý od
fáze 3, a #12 je levnější, konkrétnější první krok směrem k LLM-based
analýze než rovnou sentiment/attribute klasifikace.

## 6. Brand-attribute tagging

Další "generace 2/3" analysis skill, stejný vzor jako `competitive_visibility`
(fáze 5) — deterministická extrakce zmíněných vlastností/atributů značky
z `rendered_text`, ne LLM klasifikace (stejná opatrnost jako u sentimentu).
Nezávislé na zbytku roadmapy, může jet paralelně.

## 7. Sentiment

Odemčeno fází 5 (`docs/TASKS_PHASE5.md`: "sentiment čeká, až tahle práce běží
ověřená v provozu — stejný důvod, proč fáze 3 sama odložila sentiment") —
competitive visibility teď běží v produkci (PR #7, merged 2026-09-11), takže
podmínka je splněná. Implementace zatím neexistuje.

## 8. Gap / opportunity score

Čeká na sentiment (#7) i na reálná konkurenční data (fáze 5) — bez obojího
nemá skóre z čeho počítat.

## 9. Nové UI — ExpressYourself.AI

**Přehodnoceno 2026-09-18.** Tahle položka se dřív popisovala jako „čistě
kosmetická práce, design hotový, zbývá promítnout do šablon". **Obojí bylo
nepřesné** a při plánování to svádělo k podcenění:

- **Hotový je screenshot a nápad**, ne design systém. Tokeny, typografie a
  komponenty zapsané níž jsou *návrh odvozený ze screenshotu*, ne
  odsouhlasený a odzkoušený systém. Než se začne stavět, potřebuje to
  vlastní návrhovou fázi.
- **Není to rebrand, je to nové UI.** Cílem není přebarvit stávající
  obrazovky, ale udělat rozhraní, které jde **skutečně ovládat na mobilu a
  tabletu** — ne desktopová stránka, která se na telefonu jakžtakž vejde.
  Dnešní obrazovky jsou stavěné primárně pro desktop; responzivita se u nich
  ověřuje, ale nebyla výchozím požadavkem.

Velikost je tedy **velká, ne malá**, a rozpadne se minimálně na: návrh a
odsouhlasení UI → design systém v kódu → přestavba layoutu → převedení
jednotlivých obrazovek. Žádné nové schéma to nepotřebuje a může jet
paralelně s čímkoliv jiným, ale nejde o výplňovou práci mezi dvěma úkoly.

Původní artefakt se screenshotem:
https://claude.ai/code/artifact/579b74dd-1be1-424d-a54a-52a40060865c

**Dodatečná úprava (2026-09-15):** Potvrzeno v konverzaci — appka **zůstává**
na Jinja2 + HTMX + Vue3 ostrůvcích, žádný přechod na plnou SPA kvůli
komerčnímu směřování. Spouštěcí podmínka na revizi zůstává přesně ta, co je
zapsaná v `docs/TASKS_PHASE4.md` design decision 1 (víc obrazovek sdílí
stav / client-side routing / interaktivních obrazovek víc než CRUD) —
nejpravděpodobnější moment je **Visual Signal Map** (viz "Zaznamenáno,
vědomě odloženo" níže), pokud bude sdílet filtry s dashboardem.

### Design tokeny (odsouhlasené, CSS custom properties)

```css
:root {
  --color-yellow: #fcf038;
  --color-pink: #fa65bf;
  --color-turquoise: #47ebfc;
  --color-black: #101016;

  --color-background: #f8f7f4;
  --color-sidebar: #f1f0eb;
  --color-surface: #ffffff;
  --color-surface-muted: #f0eff1;
  --color-border: #dddce2;

  --color-text-primary: #17151a;
  --color-text-secondary: #5d5d68;
  --color-text-muted: #9e9da5;

  --color-positive: #35be78;
  --color-negative: #e95454;

  --color-turquoise-soft: #c7f7fc;
  --color-pink-soft: #fbd1e8;
  --color-yellow-soft: #fff8a8;

  --header-height: 80px;
  --sidebar-width: 248px;
  --radius-control: 8px;
  --radius-card: 12px;
}
```

Zatím jen světlý režim — dark mode nebyl zadaný, nevymýšlel jsem ho, čeká na
zadání.

### Typografie

- **Inter** (variabilní, `wght@100..900`) — běžný UI text. `page-title` 32px/750,
  `card-title` 17px/650, `nav-section-label` 11px/650 uppercase.
- **Anton** — jen wordmark "ExpressYourself.ai" v headeru (poster-bold,
  `-webkit-text-stroke` obrys), nikde jinde — na hustém dashboardu by to bylo
  křiklavé.
- **IBM Plex Mono** — tabulková čísla (KPI hodnoty, sloupce v tabulkách) —
  zachovává existující konvenci appky.

### Komponenty

- **Primary button** — neobrutalistický tvrdý stín (`box-shadow: 4px 5px 0
  black`), stiskový efekt při hoveru/kliknutí. Jediné místo v appce s touhle
  grafikou — "spend your boldness in one place".
- **Card** — bílý povrch, 1px border, jemný stín (`0 1px 2px rgba(16,16,22,.04)`).
- **Nav item (aktivní)** — 4px žlutá lišta vlevo (`::before`), ne barevné
  pozadí celé položky.
- **Badge (own/competitor)** — tyrkysová/růžová jako kategorické barvy, jediné
  místo, kde nesou skutečný význam (ne rozházené po appce).

### Ikony

Sdílený SVG sprite (`<symbol>`+`<use>`), ne externí ikonový font/balíček —
appka nemá build krok (Tailwind + Vue3 island přes CDN), takže npm ikonové
balíčky (jak je user používá v jiných Vue projektech) se sem nehodí. 10 ikon
navržených pro sidebar/bottom nav (Clients, Prompts, Runs, Dashboard,
Sentiment, Attributes, Providers, Markets, Settings, Plus) — přenositelné do
Jinja2 makra (`app/templates/partials/macros.html`), stejné SVG cesty fungují
i v HTML šabloně i ve Vue3 dashboard islandu.

### Responzivní navigace

- **Desktop (≥1024px):** boční menu, **collapsible** (248px → 64px, ikony bez
  textu v sbaleném stavu), přepínač nahoře v sidebaru.
- **Tablet/mobil (<1024px):** boční menu zmizí, nahradí ho **spodní tab bar**
  (fixed, 5 hlavních položek s ikonami) — jako u mobilní appky, šetří místo.

### Co zbývá udělat

Pořadí je podstatné — první dva body nejsou implementace, ale rozhodování,
a bez nich se ostatní dělat nedají.

- [ ] **Navrhnout UI**, ne jen barvy: jak se appka ovládá na telefonu, co je
      na první obrazovce, co se na malém displeji schová. Screenshot na tohle
      neodpovídá.
- [ ] **Ověřit tokeny a komponenty níž** — jsou odvozené ze screenshotu, ne
      odzkoušené na reálných obrazovkách appky
- [ ] Promítnout tokeny do `app/templates/base.html` (Tailwind config nebo
      CSS custom properties vedle Tailwind utility tříd)
- [ ] Ikonové makro v `app/templates/partials/macros.html`
- [ ] Přestavět `base.html` layout na header + sidebar (dnes appka nemá boční
      menu vůbec — jen top nav, viz `base.html`)
- [ ] Responsive chování (collapsible sidebar JS, bottom nav media query)
- [ ] Ověřit v prohlížeči na ~375px/~768px/desktop na všech existujících
      obrazovkách, ne jen na dashboardu
- [ ] Rozhodnout název — "ExpressYourself.AI" je zatím pracovní název převzatý
      ze screenshotu, ne potvrzené obchodní rozhodnutí (doména, ochranná
      známka nekontrolované)

## 10. Client-scoped access (dřív "Multi-tenancy")

Izolace dat po klientech (ne po rolích — role řeší "co smí dělat", tohle
řeší "která data vidí"). Design probraný a kriticky zhodnocený v konverzaci
2026-09-13 (porovnáno i s alternativou "feature/module entitlements" —
viz "Mimo tuhle roadmapu" níže, ta je odložená zcela).

**Dodatečná úprava (2026-09-15) — rozšířeno o smíšený model:** Původní
rozsah (jen interní account manageři, appku nikdy neotvírá externí klient)
předpokládal, že appka zůstává čistě agenturní nástroj. Teď je potvrzeno,
že SignalMap se plánuje jako komerční produkt, a Knauf (reálné, probíhající
jednání, appka ho už má jako klienta) je konkrétní blízký případ —
předpoklad "žádný externí přístup" už neplatí obecně, i když pro Knauf
konkrétně se billing řeší fakturou mimo appku (#16), ne přes appku.

- **Client = hranice tenantu**, ne nová `workspace_id` entita nad Client
  (jak navrhoval Philipův spec dokument) — Project/Brand entita (#13) řeší
  "víc scopů pod jedním klientem", tenant-izolace řeší "klient nikdy
  nevidí jiného klienta", to jsou dvě různé osy.
- **`users.user_type`** (`internal` / `external`) + **`home_client_id`**
  (nullable FK, jen pro `external`) — interní uživatelé zůstávají na
  `user_clients` M2M scopingu (návrh níže, beze změny), externí mají
  `home_client_id` jako jedinou a neměnnou hranici, žádný `user_clients`
  řádek. Smíšený model (někteří klienti self-serve, jiní agenturně
  spravovaní) vyjde přirozeně z toho, jestli danému klientovi appka vůbec
  vytvoří externí účty, ne z jiného datového modelu.
- **Nová závislost:** externí přístup (Knauf "Client-view" portál, #14)
  vyžaduje #2 (deploy hardening) + #3 (jít online) hotové dřív — externí
  uživatel se nepřipojí na appku běžící lokálně na vývojářově PC.

### Návrh (odsouhlaseno v konverzaci, čeká na vlastní branch)

- **M2M tabulka `user_clients`** (`user_id`, `client_id`), ne jeden
  `client_id` sloupec na `User` — člověk typicky spravuje portfolio
  klientů, ne přesně jednoho (stejný pattern jako agenturní nástroje typu
  HubSpot/Sprout Social client-access). Levné teď, drahé dodělávat později.
- **`users.client_scoped: bool NOT NULL DEFAULT FALSE`** — explicitní
  fail-closed přepínač, stejná disciplína jako `AIModel.is_free`
  ("explicit fact, not derived"). `FALSE` (default i pro všechny dnešní
  účty) = vidí vše jako dnes, nulová změna chování při migraci. `TRUE` a
  prázdné `user_clients` = vidí **nic**, ne vše — nikdy neodvozovat "je
  omezený" z toho, že mu prostě ještě nikdo nic nepřiřadil (fail-open by
  byla tichá díra, OWASP API3 Broken Object Level Authorization).
- **`admin` je vždy neomezený**, bez ohledu na flag — scoping se týká jen
  `editor`/`viewer`.
- **Jedna centrální dependency** (`get_accessible_client_ids(user) ->
  set[int] | None`, `None` = neomezený) používaná všude, kde appka čte
  cokoliv navěšené na `Client` — `clients.py`, `prompt_sets.py`,
  `prompts.py`, `runs.py` (list/detail/trigger/**export**), `dashboard.py`.
  Přímý lookup mimo scope → **404**, ne 403 (scoped uživatel nemá jak
  ověřit, že klient vůbec existuje).
- **Odhad náročnosti:** srovnatelné s fází 6 (auth), možná větší — dotýká
  se min. 5 routerů + jejich šablon (cross-linky musí dál dávat smysl pro
  scoped uživatele). Vyžaduje vlastní izolační testovou sadu (stejný
  precedent jako fáze 4's "cross-client data-isolation regression test") —
  ne volitelné, chybějící filtr na jediném místě = přímý únik dat klienta.
- Až se na tohle dojde: vlastní `docs/TASKS_CLIENT_SCOPING.md`/
  `docs/PROMPTS_CLIENT_SCOPING.md`, stejný formát jako fáze 2–6, ne
  přílepek k jiné branch.

## 11. Oprava extrakce citací (Gemini) ✅ Hotovo

**Přidáno 2026-09-15, implementováno na `feature/signalmap-gemini-citation-extraction`
(2026-09-16).** `app/adapters/google.py::_map_citations` bral jen **první**
`grounding_support`, co odkazoval na daný chunk, a zbytek zahodil (`break`
v cyklu) — Gemini přitom vrací many-to-many vazbu mezi segmenty odpovědi
a zdroji.

**Rozsah byl větší, než roadmapa odhadovala.** Nešlo o „zploštělou 1:1
citaci": proměření produkčních dat před opravou ukázalo **551 uložených
citací proti 1 354 reálným dvojicím (segment odpovědi → zdroj), tedy 803
ztracených claim-source vazeb = 59 %, ve 40 z 52 odpovědí**. Chunků, na
které by neukazoval žádný support, bylo 0 — ztráta byla výhradně na straně
zahozených supportů.

**Součástí opravy bylo i rozdělení sémantiky spanu**, druhý, nezávislý
defekt nalezený při přípravě: `citations.cited_answer_span` znamenal
u každého providera něco jiného — u Gemini a OpenAI úsek odpovědi, u
Anthropicu pasáž ze zdrojové stránky (`citation.cited_text`), což je
v přímém rozporu s FR-12. Ověřeno: span se v `rendered_text` našel u Gemini
551/551 a u OpenAI 71/71, ale u Anthropicu jen 17/158. `citations` proto
dostaly `source_passage`, `answer_span_start` a `answer_span_end`
(migrace `0027`) a všechny tři mappery přešly z SDK objektů na dict, aby
šla stejná funkce použít i pro re-extrakci.

Migrace `0028` přepočítala celou historii z nedotčeného `raw_payload`
(Gemini 583 → 1 386 řádků, Anthropic 167 s přesunutým textem, OpenAI 71
s doplněnými offsety) — díky tomu se historická čísla posunula konzistentně
přes celý archiv, ne skokem v místě nasazení. Dashboard se neměnil:
`count(citations)` nově napříč providery znamená „počet claim-source vazeb",
což je význam, který u Anthropicu a OpenAI platil odjakživa
(`docs/REQUIREMENTS.md` FR-12 a poznámka za FR-15).

Plný rozpis, naměřená čísla a design decisions:
`docs/TASKS_GEMINI_CITATIONS.md` a `docs/PROMPTS_GEMINI_CITATIONS.md`.

## 12. LLM quote-verification skill

**Přidáno 2026-09-15.** První `execution_type='llm_prompt'` analysis skill
— hák na `AnalysisSkill.prompt_template` čeká nevyužitý od fáze 3
(`docs/TASKS_PHASE3.md`: "the column exists now so adding one later doesn't
need another schema change"). Porovnává claim (segment odpovědi) proti
zdrojové pasáži: lexikální shoda deterministicky (string processing, žádné
LLM), sémantická podpora (verified/partial/weak/contradictory) přes nové,
samostatné LLM volání — existující adapter, jiný system prompt, teplota 0,
žádné search/grounding nástroje.

**Vstupní předpoklady se s #11 změnily (2026-09-16)** — nejde o kosmetiku
názvu sloupce, ale o to, co pro který provider vůbec existuje. Skill dostává
z každé strany vazby jinou polovinu a musí s tím počítat od návrhu:

- **Anthropic runy:** zdrojová pasáž je uložená v `citations.source_passage`
  (ne v `cited_answer_span`, jak tahle položka tvrdila dřív) — žádné
  fetchování, jen klasifikace. **Chybí ale claim:** `cited_answer_span`
  i offsety jsou u Anthropicu trvale `NULL`, protože API žádný úsek odpovědi
  nevrací (`web_search_result_location` nese `encrypted_index`, což je index
  do výsledků vyhledávání, ne do textu odpovědi). Není to mezera k doplnění,
  je to vlastnost API — tvrzení k porovnání bude potřeba odvodit jinak
  (např. z textu bloku, na kterém citace visí).
- **Gemini a OpenAI runy:** claim uložený je, včetně offsetů; chybí zdrojová
  pasáž — potřeba lehký fetch+extract krok (`httpx`+`trafilatura`, žádné
  verzování pro v1).
- **Pozor na jednotku offsetů:** `answer_span_start`/`_end` se ukládají tak,
  jak je provider vrátil, a jednotka se liší — Gemini počítá **UTF-8 byty**
  (změřeno: 1 316 z 1 354 spanů sedí jen jako byte offsety), OpenAI
  **znaky** (71/71). Přenositelné je hledat `cited_answer_span` jako text,
  ne slicovat podle offsetu.
- Závisí na #11 (hotovo — bez opravy extrakce by skill ztrácel 59 % vazeb).
- Vlastní `docs/TASKS_QUOTE_VERIFICATION.md`/`docs/PROMPTS_QUOTE_VERIFICATION.md`,
  až se na to dojde.

## 13. Project/Brand entita

**Přidáno 2026-09-15.** Vrstva mezi `Client` a `PromptSet` — dnes 1 Client
= 1 scope, ale jeden platící účet (Škoda, případně Knauf) potřebuje
sledovat víc scopů zároveň (Škoda DE vs. CZ). Malá, aditivní změna (jedna
tabulka + FK), nezávislá na #10, ale logicky s ním souvisí (Project je
"víc scopů pod klientem", #10 je "klient nikdy nevidí jiného klienta").

## 14. Client-view portál + Executive Summary report

**Přidáno 2026-09-15.** Vázáno na Knauf. Omezený, read-only pohled pro
externí uživatele (#10) + klientsky čitelný jednostránkový report na
existujících dashboard datech — přímo odpovídá spec dokumentu: "první
obrazovka musí odpovědět na strategickou otázku okamžitě". Silnější s
reálně narůstajícími daty ze Scheduleru (#5) než s jedním ručním snímkem —
proto až po #4/#5, ne před nimi. Vyžaduje #2+#3 (viz #10).

## 15. UUID `public_id` na `clients`

**Přidáno 2026-09-15.** Scoped řešení, ne plná migrace primárních klíčů
(appka má ~15-17 tabulek, 40 FK míst, 25 migrací — plná migrace je
invazivní napříč celou appkou a musí se flagovat zvlášť, `AI_INSTRUCTIONS.md`
§4). `public_id` (UUID) sloupec navíc jen na entitách, co uvidí externí
klient (`clients`, případně později `runs`/reporty) — interní joiny
zůstávají na integeru. Řeší konkrétní riziko (enumerace mezi klienty přes
`/clients/8`, `/clients/9`), co vzniká přesně ve chvíli, kdy začnou
existovat externí uživatelé (#10/#14) — sekvenováno spolu s nimi.

## 16. Billing

**Přidáno 2026-09-15.** Potvrzený komerční směr, ale **oddělené** od #4
(ops dashboard zůstává čistě interní). Cenový model (měsíční tarify podle
vzoru Profound/Otterly/Scrunch vs. metered prepay ze spec dokumentu) je
otevřená diskuze, zatím nerozhodnuto. Pro Knauf konkrétně billing engine
**není potřeba** — fakturuje se mimo appku jako běžná konzultační zakázka
(retainer/projekt), appka jen ukazuje Client-view portál (#14). Tahle
položka je blokovaná na **existenci prvního self-serve zákazníka**, ne na
Knauf.

## 17. Nový tvar Gemini odpovědi (`steps` / `url_citation`)

**Přidáno 2026-09-16** při práci na #11 (`docs/TASKS_GEMINI_CITATIONS.md`
design decision 9). Dokumentace Google (`ai.google.dev/gemini-api/docs/google-search`,
aktualizace 2026-09-02) popisuje pro Gemini 3 **jiný tvar odpovědi**:
`steps` → `model_output.content[].annotations[]` typu `url_citation` se
`start_index`/`end_index` — tedy stejný model, jaký dnes vrací OpenAI, ne
`grounding_metadata`.

**Reálně to zatím nechodí.** Ověřeno 2026-09-16 proti produkčním datům:
přes `google-genai==2.22.0` a `client.models.generate_content(tools=[GoogleSearch()])`
chodí i u `gemini-3.1-flash-lite` a `gemini-3.5-flash` pořád starý
`grounding_metadata` tvar, ve 52 z 52 odpovědí. Oprava v #11 je tedy pro
dnešek správná a `_map_citations` na payloadu bez `grounding_metadata` vrátí
`([], False)` místo pádu (pokryto testem).

Až API/SDK tvar reálně přepne, bude potřeba detekce tvaru a druhá mapovací
větev. Do té doby je to jen hlídané riziko: **nezakládat větev ani vlastní
dokumenty**, jen sledovat, jestli se v `raw_payload` nezačne objevovat
`steps`. `docs/TASKS_SEARCH_QUERIES.md` design decision 4 si téhož rozporu
všiml už 2026-09-10 a uzavřel ho stejně.

## Mimo tuhle roadmapu, zaznamenáno pro pořádek

Z diskuze o vzorovém mockupu (peec.ai-style konkurent) vyplynuly dvě další
myšlenky, které nejsou v žádném z kroků výše — nejsou to jen kosmetika, jde o
novou funkcionalitu:

- **Hromadný import promptů** (CSV/XLSX upload) — dnes se prompty přidávají
  jednotlivě přes formulář.

  ✅ **Implementováno mimo pořadí**, branch `feature/signalmap-bulk-import-multi-model`,
  smergnuto 2026-09-15 (PR #12) — CSV/XLSX/JSON upload s preview krokem (duplicate detection,
  per-řádkový trh) před uložením. Spolu s tím i menší, nezávislý kus multi-model run triggeru
  (viz další bod) — oba popsané v `docs/TASKS_BULK_IMPORT_MULTI_MODEL.md`.

- **"Study" koncept** — jeden setup krok spustí dávku runů najednou (více
  promptů × více modelů), místo dnešního jednoho runu (prompt × model ×
  market) najednou. Největší architektonická změna z celé diskuze — vlastní
  budoucí fáze, ne součást žádného kroku výše.

  ⏳ **Pořád čeká na Scheduler infrastrukturu** (viz #5 výše) — appka dnes nemá žádný task
  queue/`BackgroundTasks` mechanismus. Branch výše (2026-09-15) implementovala jen **stavební
  kámen** směrem k tomuhle konceptu, ne "Study" samotný: umí spustit víc modelů paralelně pro
  **jeden** prompt (checkboxy na run-trigger formuláři, `POST /prompts/{id}/runs` volané N-krát
  z klienta) — ne dávku přes víc promptů najednou. Vědomě rozhodnuto v konverzaci 2026-09-14
  nestavět "Study" dřív, než existuje Scheduler, aby se stejná infrastruktura nemusela stavět
  dvakrát nezávisle.

Z konverzace o client-scoped access (2026-09-13, viz #10 výše) vyplynula
ještě jedna myšlenka, porovnaná a **vědomě odložená celá**, ne jen
naplánovaná na později:

- **Feature/module entitlements** — místo/vedle role/client-scope mít
  jednotlivé schopnosti (zobrazení raw JSON, export raw JSON, konkrétní
  dashboard sekce, ...) jako samostatně přiřaditelné/do budoucna placené
  moduly per uživatel. Jiná osa než client-scoping (#10 — "která data",
  tohle je "která schopnost") a jiná než role (bundle výchozích schopností,
  tohle je per-user override). Reálný precedent z praxe: Stripe
  Entitlements API, LaunchDarkly feature flags, Salesforce Permission Sets
  — všechny to řeší jako samostatnou vrstvu nad rolí.

  **Vědomě nestavěno teď** — žádný externí ani placený uživatel dnes
  neexistuje, generická grant-tabulka/admin UI by řešila hypotetickou
  potřebu (přesně to, co `AI_INSTRUCTIONS.md` §5 zakazuje — "don't design
  for hypothetical future requirements"). Místo toho jen jedno pravidlo do
  budoucna: každé nové "kdo tohle smí" rozhodnutí patří do vlastní
  pojmenované `can_*()` funkce (přesně vzor `can_edit()` v
  `app/templating.py`, které samo vzniklo z code review nálezu — ~25
  roztroušených `role in (...)` kontrol sjednocených do jednoho místa),
  nikdy zpátky jako inline `role in (...)` v šabloně/routeru. Díky tomu je
  pozdější přechod "role rozhoduje" → "entitlement rozhoduje" jen lokální
  úprava těla jedné funkce, ne refaktor napříč appkou. Skutečnou
  grant-tabulku a admin UI stavět, až bude reálný obchodní důvod (externí
  uživatel, placený tier) — ne dřív.

**Přidáno 2026-09-15**, z konverzace o Philipově "AI Perception Analysis
Platform" functional spec dokumentu (plné srovnání: [Evidence Gap Ledger
artefakt](https://claude.ai/artifact/2d5A52xq28j49bmmT9nScu)) — zaznamenáno
jako zvážené a vědomě odložené, ne zapomenuté:

- **Plný Desired Perception Claims model** (verzované, vážené, se
  schvalovacím flow) — žádný ze zkoumaných konkurentů v kategorii
  (Profound, Otterly.ai, Scrunch AI) nic takového nemá, prodávají
  viditelnost/sentiment/citace přímo z promptů. Nahrazeno pro v1 prostým
  volným textovým polem, pokud/až bude potřeba (#6/#7/#8 na tom nezávisí).
- **Tři formální scoring modely** (Claim Alignment % / Evidence Quality /
  Observed Source Contribution s konfigurovatelnými váhami) — čeká na
  Desired Claims model výše.
- **Visual Signal Map** (interaktivní graf claimů/zdrojů/evidence) — žádný
  zkoumaný konkurent nevede grafovou vizualizaci jako hlavní hodnotu;
  trh kupuje hlavně čísla, trendy a alerty. Zároveň jediný kandidát na
  revizi rozhodnutí "zůstat na Jinja2+HTMX+Vue ostrůvcích" (#9).
- **Plná canonical data model migrace na UUID** (všechny primární klíče,
  ne jen `public_id` na `clients` z #15) — invazivní napříč celou appkou,
  žádný konkrétní důvod ji dělat teď, když scoped řešení (#15) pokrývá
  reálné riziko (enumerace).
- **Plný billing engine** (rate cards, markup, prepayment, wallet/ledger,
  platební brána) — viz #16, blokováno na existenci self-serve zákazníka.

---

## Stav

| # | Krok | Status |
|---|------|--------|
| 1 | Auth + user management | Hotovo, smergnuto do `master` 2026-09-12 ([PR #8](https://github.com/jirkalla/SignalMap/pull/8)) — chybí jen systematické ověření na ~375/768px/desktop pro všechny obrazovky |
| 2 | Deploy hardening | Neimplementováno — nová závislost: #14 (Knauf Client-view) na tom čeká |
| 3 | Jít online | Čeká na 1–2 |
| 4 | Ops dashboard (interní) | T0–T4 hotové a otestované na `feature/signalmap-ops-dashboard` (2026-09-15), čeká na merge |
| 5 | Scheduler | Technický návrh hotový (2026-09-15), čeká na 4 |
| 6 | Brand-attribute tagging | Neimplementováno, čeká za #11/#12 |
| 7 | Sentiment | Odemčeno, čeká za #11/#12 |
| 8 | Gap/opportunity score | Čeká na 7 |
| 9 | Nové UI | Přehodnoceno 2026-09-18 — hotový je jen screenshot a nápad, ne design systém; nejde o kosmetiku, ale o rozhraní ovladatelné na mobilu/tabletu, tedy velkou položku. Potvrzeno žádný přechod na SPA (2026-09-15) |
| 10 | Client-scoped access | Rozšířeno o smíšený model (2026-09-15) — Client = tenant, `user_type`/`home_client_id`, Knauf jako konkrétní případ; čeká na vlastní branch |
| 11 | Oprava extrakce citací (Gemini) | ✅ Hotovo a otestované na `feature/signalmap-gemini-citation-extraction` (2026-09-16) — 59 % ztracených claim-source vazeb obnoveno (551 → 1 354), plus rozdělení sémantiky spanu; historie přepočítaná migrací `0028`; čeká na merge |
| 12 | LLM quote-verification skill | Navrženo 2026-09-15; #11 hotové, takže odblokované — vstupní předpoklady ale změněné, viz #12 |
| 13 | Project/Brand entita | Navrženo 2026-09-15 |
| 14 | Client-view portál + Executive Summary | Navrženo 2026-09-15, čeká na 2, 3, 4, 5, 10 |
| 15 | UUID `public_id` na `clients` | Navrženo 2026-09-15, spolu s 10/14 |
| 16 | Billing | Navrženo 2026-09-15, blokováno na prvním self-serve zákazníkovi; cenový model zatím otevřený |
| 17 | Nový tvar Gemini odpovědi (`steps`/`url_citation`) | Zaznamenáno 2026-09-16 při #11 — API zatím vrací starý tvar (52/52 odpovědí), jen hlídané riziko |
