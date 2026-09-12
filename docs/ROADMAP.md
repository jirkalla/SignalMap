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
| `/providers`, `/ai-models`, `/settings` | ✅ | ❌ | ❌ |
| `/users` | ✅ | ❌ | ❌ |

Viewer je čistě read-only — žádná akce, co appku nebo data mění, ani je
nestahuje.

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

## 4. Cost/ops dashboard

**Proč před schedulerem, ne po něm:** chcete vidět náklady dřív, než appka
začne spouštět runy automaticky a nehlídaně.

- Data už existují, nejde o nové schéma: `runs.latency_ms`, `token_usage`
  (JSON na `RawResponse`), `cost_per_1k_input_usd`/`cost_per_1k_output_usd`
  (na `ai_models`, z fáze 2 admin UI).
- Nová agregace v `app/services/dashboard.py` (nebo samostatný
  `app/services/cost.py`) + dashboard sekce: náklady podle klienta/promptu/
  modelu za období, trend v čase.

## 5. Scheduler

Automatické, opakované spouštění runů (`FR-9` bylo ve fázi 1 explicitně mimo
scope — teď dává smysl to odemknout).

- Rozšíření `Run.trigger_type` o `'scheduled'` (dnes jen `'manual'`).
- `triggered_by_user_id` (#1) rozliší, kdo/co run spustil.
- UI koncept z mockupu: přepínač "Repeat" (weekly/monthly) na úrovni promptu
  nebo prompt setu.
- Bez #1 (auth) a #4 (cost dashboard) tohle nejde bezpečně pustit ven —
  nehlídané opakované volání placených API bez viditelnosti nákladů je riziko.

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

## 9. Frontend rebrand — ExpressYourself.AI

Vizuální redesign appky podle nového brandu. **Návrh design systému a
prototyp jsou hotové** (artefakt: https://claude.ai/code/artifact/579b74dd-1be1-424d-a54a-52a40060865c)
— zbývá promítnout do skutečných Jinja2 šablon. Čistě kosmetická/frontendová
práce, žádné nové schéma, může jet paralelně s kterýmkoliv krokem výše.

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

## 10. Multi-tenancy

Izolace dat po organizacích (ne po rolích — role řeší "co smí dělat", tohle
řeší "co vidí"). Dává smysl, až appku bude reálně používat víc oddělených
organizací na jedné instanci — pro současný malý tým s auth (#1) to zatím
stačí. Až přijde na řadu: `org_id` FK na `User` + tenant-scoping dependency
navrstvená nad `require_role` (viz #1 rozšiřitelnost).

## Mimo tuhle roadmapu, zaznamenáno pro pořádek

Z diskuze o vzorovém mockupu (peec.ai-style konkurent) vyplynuly dvě další
myšlenky, které nejsou v žádném z kroků výše — nejsou to jen kosmetika, jde o
novou funkcionalitu:

- **Hromadný import promptů** (CSV/XLSX upload) — dnes se prompty přidávají
  jednotlivě přes formulář.
- **"Study" koncept** — jeden setup krok spustí dávku runů najednou (více
  promptů × více modelů), místo dnešního jednoho runu (prompt × model ×
  market) najednou. Největší architektonická změna z celé diskuze — vlastní
  budoucí fáze, ne součást žádného kroku výše.

---

## Stav

| # | Krok | Status |
|---|------|--------|
| 1 | Auth + user management | Návrh hotový (tenhle dokument), implementace čeká |
| 2 | Deploy hardening | Neimplementováno |
| 3 | Jít online | Čeká na 1–2 |
| 4 | Cost/ops dashboard | Neimplementováno |
| 5 | Scheduler | Čeká na 1, 4 |
| 6 | Brand-attribute tagging | Neimplementováno |
| 7 | Sentiment | Odemčeno, neimplementováno |
| 8 | Gap/opportunity score | Čeká na 7 |
| 9 | Frontend rebrand | Design hotový, implementace čeká |
| 10 | Multi-tenancy | Čeká na 1, mimo dohled |
