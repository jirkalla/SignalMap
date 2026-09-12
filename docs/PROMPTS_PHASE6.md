# SignalMap — Claude Code Session Prompts: Phase 6 (Auth + user management)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-phase6-auth (z aktuálního master)
## 2. Osm kódových promptů (P6-1 až P6-8) — STRIKTNĚ V POŘADÍ, na rozdíl od fáze 5 tu není
##    žádná dvojice, co jde souběžně nebo v libovolném pořadí. P6-4/P6-5/P6-6 mají společnou
##    prerekvizitu P6-3, ale mezi sebou nezávisí — pořadí mezi nimi je libovolné, zbytek je pevný.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit provádíš ty,
##    ne agent — agent NIKDY nespouští git commit/push sám bez výslovného potvrzení, a to i
##    přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_PHASE6.md přepni řádek daného task ID v tabulce "Task Index" z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-8 a schema flagu: docs/TASKS_PHASE6.md —
##    přečti si konkrétní task ID před psaním kódu, ideálně celý soubor před P6-1.
## 9. Schema (users tabulka, Run.triggered_by_user_id) je odsouhlasené (viz docs/ROADMAP.md
##    §1 "Rozhodnutí" a docs/TASKS_PHASE6.md schema flag) — P6-1 může začít rovnou, žádné
##    další čekání na potvrzení.
## 10. Tahle větev NEOBSAHUJE API tokeny ani SMTP/emailový reset hesla — obojí je vědomě
##     mimo v1 (viz design decision 1-2 v docs/TASKS_PHASE6.md). Pokud se během implementace
##     zdá lákavé přidat kus jednoho z nich "když už jsme u toho", ZASTAV a zeptej se.
## 11. Multi-tenancy (docs/ROADMAP.md bod 10) NENÍ součástí týhle branch — role řeší "co smí
##     dělat", ne "co vidí kterou organizaci".
## 12. Až je větev hotová a smergnutá: aktualizovat stav bodu 1 v docs/ROADMAP.md (viz
##     Completion Checklist v TASKS_PHASE6.md).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-phase6-auth.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/ROADMAP.md — bod 1 "Auth + user management" (rozhodnutí, schéma, rozšiřitelnost)
4. docs/TASKS_PHASE6.md — CELÉ, hlavně schema flag a design decisions 1-8

KONTEXT: Fáze 1-5 jsou hotové a smergnuté do master. Appka dnes nemá ŽÁDNOU autentizaci —
kdokoliv se znalostí URL vidí všechna data a může spouštět placené API runy. Tahle branch
přidává auth jako JEDEN souvislý pipeline (na rozdíl od fáze 5 tu úkoly jdou striktně po
sobě, ne souběžně):
1. Schema: users tabulka + Run.triggered_by_user_id (P6-1)
2. Auth backend: fastapi-users, cookie session, require_role() (P6-2)
3. Login gate + current_user v šablonách (P6-3)
4. Admin: správa uživatelů (P6-4)
5. Vynucená změna hesla po prvním přihlášení (P6-5)
6. Role-based guardy a skryté UI napříč appkou (P6-6)
7. Audit: kdo spustil run (P6-7)
8. Testy (P6-8)

KRITICKÉ (odsouhlasená rozhodnutí, neměň bez zeptání):
- Admin vytváří účty přímo (jméno/email/role/heslo) — ŽÁDNÁ self-service registrace.
- Login jen email+heslo — ŽÁDNÉ OAuth v1.
- Reset hesla řeší admin ručně (POST /users/{id}/reset-password) — appka NEMÁ SMTP, fastapi-users'
  vestavěný emailový reset-password router se NEMOUNTUJE.
- Jen browser cookie session — ŽÁDNÉ API tokeny v1.
- Tři role: admin (vše) / editor (běžná práce) / viewer (jen čtení, BEZ exportu, BEZ doménové
  klasifikace — obojí bylo explicitně potvrzeno v konverzaci, ne default).
- must_change_password=True při vytvoření i resetu účtu — vynucená změna hesla při dalším
  přihlášení, centrální kontrola (dependency), ne opakovaná na každé routě.
- require_role() je zdroj pravdy pro bezpečnost; skryté UI je jen UX — implementuj OBOJÍ, nikdy
  jen jedno.
- Role jako ROLES tuple + CHECK constraint (stejný vzor jako DOMAIN_TYPES ve fázi 5), ne DB enum.
- Run.triggered_by_user_id je nullable (budoucí scheduler run nemá člověka za sebou).
- Multi-tenancy NENÍ součástí týhle branch.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX + jeden Vue3 ostrůvek na
dashboardu, Alembic migrace, Docker Compose. Nová závislost: fastapi-users[sqlalchemy] (ověř
aktuální stable verzi proti PyPI před zápisem do requirements.txt, neodhaduj číslo z paměti).
Backend kód anglicky vč. komentářů/error_code, UI texty vždy přes t() mechanismus v
app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom commitu. Tailwind CDN pro
styling.

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation, AnalysisResult) — appka je jen ČTE tam, kde to
  není explicitně úkol zápisu. Uživatelské účty se NIKDY nemažou (DELETE) — jen deaktivují
  (is_active=False) — Run.triggered_by_user_id na ně může odkazovat, smazání by porušilo audit.
- Nová migrace pro každou schema/index změnu, navazující revision ID (ověř `alembic heads`
  před vytvořením další).
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

## PROMPT P6-1 — Schema: `users` tabulka + `Run.triggered_by_user_id`

```
Task: Prompt P6-1 — users table and Run audit column

Přečti docs/TASKS_PHASE6.md úkol P6-T1 CELÝ a schema flag (přesný tvar User modelu).
Žádná prerekvizita — schéma je odsouhlasené, začni rovnou.

1. requirements.txt — přidej fastapi-users[sqlalchemy] (ověř aktuální stable verzi proti
   PyPI, neodhaduj z paměti).
2. Nová migrace — CREATE TABLE users (fastapi-users' SQLAlchemyBaseUserTable sloupce: id,
   email, hashed_password, is_active, is_verified + name, role, must_change_password,
   created_at, updated_at dle schema flagu). CHECK constraint na role
   (admin/editor/viewer). ALTER TABLE runs ADD COLUMN triggered_by_user_id (nullable FK na
   users.id).
3. app/models/user.py — User model + ROLES tuple dle schema flagu.
4. app/models/run.py — Run dostane triggered_by_user_id sloupec + triggered_by:
   Mapped["User | None"] relationship.
5. app/models/__init__.py — zaregistruj User.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověř: users tabulka existuje s očekávanými sloupci a CHECK constraintem; runs má
   nový nullable sloupec triggered_by_user_id.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add users table and Run.triggered_by_user_id for auth
```

### DONE — commit 4db1f11

---
---

## PROMPT P6-2 — Auth backend: fastapi-users wiring

```
Task: Prompt P6-2 — fastapi-users cookie authentication backend

Přečti docs/TASKS_PHASE6.md úkol P6-T2 CELÝ, hlavně design decision 1 (cookie+JWT, ne
databázové session) a design decision 6 (401 vs. 403 chování).
Prerekvizita: P6-1 hotový.

1. app/config.py — nový setting secret_key: str (JWT signing key) z .env. Žádný defaultní
   literál pro produkční hodnotu — appka má selhat při chybějící hodnotě.
2. .env.example — nová SECRET_KEY= položka s komentářem (generovat náhodný string, nikdy
   nesdílet mezi prostředími).
3. Nový app/auth.py:
   - UserManager (fastapi-users) — minimalistický, ŽÁDNÉ on_after_* hooky (register/
     forgot-password se nemountují).
   - CookieTransport (cookie_name="signalmap_session", cookie_max_age 14 dní) + JWTStrategy
     (secret=settings.secret_key) -> jeden AuthenticationBackend.
   - FastAPIUsers instance, current_active_user dependency export.
   - require_role(*allowed_roles: str) — dependency factory, AppError("forbidden", ...,
     status_code=403), když user.role not in allowed_roles.
4. app/main.py — namountuj JEN fastapi_users.get_auth_router(auth_backend) (login/logout),
   NE register/reset-password routery. Zaregistruj custom exception handler pro
   HTTPException(401) -> RedirectResponse na /login, jen pro non-/api/-prefixované cesty —
   OVĚŘ přesné chování fastapi-users' výchozí 401 výjimky, neodhaduj ji.

Po dokončení:
1. docker compose up -d --build
2. Ruční ad-hoc skript v kontejneru: vytvoř User přímo v DB session (bcrypt hash přes
   UserManager), zavolej POST /auth/login (curl/httpie) -> dostaneš Set-Cookie. Zavolej
   chráněnou routu s tím cookie -> projde. Bez cookie -> 401/redirect dle design decision 6.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(auth): wire fastapi-users cookie authentication and require_role dependency
```

### DONE — commit 8090256

---
---

## PROMPT P6-3 — Login gate + `current_user` v šablonách

```
Task: Prompt P6-3 — login page, logout, app-wide login gate

Přečti docs/TASKS_PHASE6.md úkol P6-T3 CELÝ.
Prerekvizita: P6-2 hotový.

1. app/templating.py — render() dostane current_user dostupný v každé šabloně (mirror
   dnešního t/locale vzoru).
2. Nový app/templates/auth/login.html — email + heslo formulář, POST /auth/login (fastapi-users'
   endpoint), chybová hláška na neplatné přihlášení. Veřejná route, žádný require_role.
3. app/main.py — app.include_router(..., dependencies=[Depends(current_active_user)]) na
   KAŽDÉM existujícím routeru KROMĚ locale (jazyk musí fungovat i na login stránce) a
   nového auth routeru.
4. app/templates/base.html — v hlavičce: jméno přihlášeného uživatele + odkaz "Odhlásit"
   (POST /auth/logout).
5. i18n (EN+DE, jeden commit) — auth.login_title, auth.email_label, auth.password_label,
   auth.login_button, auth.invalid_credentials, auth.logout_button.

Po dokončení:
1. docker compose up -d --build
2. Nepřihlášený -> jakákoliv chráněná stránka přesměruje na /login. Přihlášení platným
   účtem (z P6-2) -> přesměruje zpět, jméno se zobrazuje v hlavičce.
3. Odhlášení -> cookie zneplatní, další request na chráněnou stránku zase přesměruje na
   /login.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(auth): add login page, logout, and app-wide login gate
```

### DONE — commit 7f1b502

---
---

## PROMPT P6-4 — Admin: správa uživatelů

```
Task: Prompt P6-4 — admin-only user management CRUD

Přečti docs/TASKS_PHASE6.md úkol P6-T4 CELÝ.
Prerekvizita: P6-3 hotový.

1. Nový app/routers/users.py, CELÝ router s dependencies=[Depends(require_role("admin"))]:
   - GET /users — seznam (jméno, email, role, aktivní/neaktivní).
   - GET /users/new, POST /users (Form name, email, role, password) — vytvoří User s
     must_change_password=True. Duplicitní email -> strukturovaná AppError, ne 500
     (pre-check + IntegrityError fallback, stejný vzor jako create_client_alias).
   - POST /users/{id}/edit — změna name/role.
   - POST /users/{id}/reset-password (Form password) — nový hash, must_change_password=True.
   - POST /users/{id}/deactivate — is_active=False. NIKDY DELETE.
2. app/main.py — zaregistruj router.
3. Šablony app/templates/users/list.html, app/templates/users/form.html — mirror stylu
   clients/list.html a clients/form.html.
4. i18n (EN+DE, jeden commit) — user.list_title, user.create_title, user.name_label,
   user.email_label, user.role_label, user.password_label, user.reset_password_button,
   user.deactivate_button, user.deactivate_confirm, errors.user_email_duplicate,
   errors.user_not_found.

Po dokončení:
1. docker compose up -d --build
2. Přihlášen jako admin: vytvoř nového uživatele s rolí editor -> objeví se v seznamu.
   Zkus duplicitní email -> strukturovaná chyba. Resetuj mu heslo, deaktivuj ho.
3. Přihlášen jako editor/viewer (vytvořený výše) -> /users vrátí 403.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(users): add admin-only user management CRUD
```

### DONE — commit be39e1f (+ follow-ups 7f193f0 "P6-T4b bootstrap script", 386aabb "P6-T4c ENVIRONMENT + dev test users", both dodatečně přidáno — viz `docs/TASKS_PHASE6.md`)

---
---

## PROMPT P6-5 — Vynucená změna hesla po prvním přihlášení

```
Task: Prompt P6-5 — force password change on first login

Přečti docs/TASKS_PHASE6.md úkol P6-T5 CELÝ.
Prerekvizita: P6-3 hotový.

1. Nová POST /change-password (Form new_password) — vyžaduje jen přihlášení
   (current_active_user, žádná konkrétní role), nastaví nový hash,
   must_change_password=False.
2. app/main.py (nebo dependency v app/auth.py) — pokud current_user.must_change_password a
   request nesměřuje na /change-password nebo /auth/logout, přesměruj (303) na
   /change-password. Funguje napříč celou appkou bez ohledu na roli.
3. Nová šablona app/templates/auth/change_password.html — jednoduchý formulář, žádná
   navigace/sidebar.
4. i18n (EN+DE, jeden commit) — auth.change_password_title, auth.new_password_label,
   auth.change_password_button, auth.change_password_required_note.

Po dokončení:
1. docker compose up -d --build
2. Admin vytvoří nový účet (P6-4) -> přihlášení tím účtem přesměruje na /change-password bez
   ohledu na to, kam se uživatel pokusil jít. Po změně hesla -> normální přístup, opětovné
   přihlášení už nepřesměrovává.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(auth): force password change on first login after admin-created account
```

### DONE — commit ad447a2

---
---

## PROMPT P6-6 — Role-based guardy a skryté UI napříč appkou

```
Task: Prompt P6-6 — apply role-based guards and hide UI by role

Přečti docs/TASKS_PHASE6.md úkol P6-T6 CELÝ a docs/ROADMAP.md §1 tabulku "Co je vidět komu".
Prerekvizita: P6-3 hotový.

1. Route-level (zdroj pravdy):
   - Všechny mutující routy (create/edit/delete) na clients.py, prompt_sets.py, prompts.py ->
     Depends(require_role("admin", "editor")).
   - trigger_run (runs.py) -> Depends(require_role("admin", "editor")).
   - Export routy (export_run, export_prompt_runs, export_client_runs) -> Depends(
     require_role("admin", "editor")) — viewer NESMÍ exportovat.
   - classify_domain (dashboard.py) -> Depends(require_role("admin", "editor")) — viewer
     NESMÍ klasifikovat domény.
   - Celé routery providers.py, ai_models.py, settings.py ->
     dependencies=[Depends(require_role("admin"))].
2. Šablona-level (jen UX): tlačítka "Spustit run", "Smazat", "Upravit", export skupina,
   inline select pro doménovou klasifikaci, odkazy na /providers, /ai-models, /settings,
   /users v base.html navigaci — všude {% if current_user.role in (...) %}.

Po dokončení:
1. docker compose up -d --build
2. Přihlášen jako viewer: dashboard/run detail/client detail se zobrazí normálně (čtení),
   ale tlačítko "Spustit run", editační/mazací akce, export tlačítka a doménová klasifikace
   nejsou vidět. Přímý POST na tyhle routy (curl/DevTools) i tak vrátí 403.
3. Přihlášen jako editor: totéž funguje jako dřív (žádná regrese), ale /providers,
   /ai-models, /settings, /users vrátí 403 a nejsou v navigaci.
4. Přihlášen jako admin: appka se chová jako dřív, žádné omezení.
5. Ověř na ~640px/~1024px/desktop šířce.
6. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(auth): apply role-based route guards and hide UI by role across the app
```

### DONE — commit e1d8d4d (+ follow-ups z konverzace, ne v původním promptu: c2635a5 "HTML error page pro AppError", d002f66 "self-deactivate guard + toggle-active sjednocení", 0c74bef "self-service display name" — viz `docs/ROADMAP.md` §1 "Dodatečná úprava" pro roli-viditelnost odchylky od plánu)

---
---

## PROMPT P6-7 — Audit: `triggered_by_user_id` v `trigger_run`

```
Task: Prompt P6-7 — record who triggered each run

Přečti docs/TASKS_PHASE6.md úkol P6-T7 CELÝ.
Prerekvizita: P6-2 hotový.

1. app/routers/runs.py — trigger_run dostane user: User = Depends(current_active_user),
   nastaví Run.triggered_by_user_id = user.id při vytvoření řádku.
2. app/templates/runs/detail.html — pole v METADATA sekci: "Spustil: {{
   run.triggered_by.name }}" (nebo "—" pro runy z doby před touhle fází).
3. i18n (EN+DE, jeden commit) — run.triggered_by_label.

Po dokončení:
1. docker compose up -d --build
2. Spusť run přihlášený jako konkrétní uživatel -> run detail ukazuje jeho jméno. Existující
   runy z před touhle fází -> "—", ne chyba/pád.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): record which user triggered each run
```

### DONE — commit 47104d6

---
---

## PROMPT P6-8 — Testy

```
Task: Prompt P6-8 — test coverage for auth, roles, and user management

Přečti docs/TASKS_PHASE6.md úkol P6-T8 CELÝ.
Prerekvizita: P6-1 až P6-7 hotové.

1. tests/conftest.py — nové fixtures: admin_user/editor_user/viewer_user (seedované přes
   UserManager, ne přímý SQL insert), authed_client (TestClient přihlášený jako editor_user,
   výchozí pro existující testy).
2. Nový tests/test_auth.py — login s platnými/neplatnými credentials, logout zneplatní
   cookie, nepřihlášený požadavek na chráněnou routu -> redirect na /login,
   /dashboard/api/* nepřihlášený -> JSON 401 (ne redirect).
3. Nový tests/test_users.py — admin CRUD uživatelů, duplicitní email -> strukturovaná
   chyba, editor/viewer na /users -> 403, must_change_password flow (vynucený redirect,
   zmizí po změně hesla).
4. Rozšíř existující testy (test_clients.py, test_runs.py, test_dashboard.py, ...) — přepni
   na authed_client fixture tam, kde routy teď vyžadují přihlášení; přidej aspoň jeden test
   na viewer roli u mutujících endpointů (403) a exportu (403) v test_runs.py.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover authentication, roles, and user management
```

### DONE — commit bda7a67 (plus a follow-up code-review fix batch, uncommitted at time of writing — self-role-change guard, current-password check, role validation, shared `build_user()`/`can_edit()` helpers; see git log for the eventual commit(s))
