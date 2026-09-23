# SignalMap — Tasks: Phase 6 (Auth + user management)

## Status: ✅ Done — PR #8, merged 2026-09-12, released in v1.0.0

## v1.0 | Září 2026
## Branch: feature/signalmap-phase6-auth
## Task ID prefix: P6

> Fáze 1 (`docs/TASKS.md`), fáze 2 (`docs/TASKS_PHASE2.md`), production-readiness hardening
> (`docs/TASKS_HARDENING.md`), runs export (`docs/TASKS_EXPORT.md`), fáze 3
> (`docs/TASKS_PHASE3.md`), search query capture (`docs/TASKS_SEARCH_QUERIES.md`), fáze 4
> (`docs/TASKS_PHASE4.md`) a fáze 5 (`docs/TASKS_PHASE5.md`) jsou hotové a smergnuté. Tenhle
> dokument pokrývá `docs/ROADMAP.md` bod 1 — appka dnes nemá žádnou autentizaci
> (`docs/REQUIREMENTS.md` §4), takže blokuje jakékoliv veřejné nasazení (`docs/ROADMAP.md` bod
> 2-3).
>
> Na rozdíl od fáze 5 (tři nezávislé kusy práce bundlované do jedné branch) je tahle fáze
> **jeden souvislý pipeline** — auth backend musí existovat, než dává smysl stavět cokoliv na
> něm. Úkoly P6-T1 až P6-T7 jdou **striktně v pořadí**, žádné souběžné větvení jako u P5-T1/T2.

---

## ⚠️ Schema flag (AI_INSTRUCTIONS.md §4)

Návrh byl **výslovně odsouhlasený v konverzaci** (provisioning, login metoda, role, audit,
vynucená změna hesla, žádné API tokeny/SMTP v1 — viz `docs/ROADMAP.md` §1 "Rozhodnutí"). Přesto
je tu zopakovaný jako kód, ne jen prózou, stejně jako u fáze 5 — schema se nezačíná stavět bez
toho, že přesný tvar je černé na bílém.

```python
# app/models/user.py
from fastapi_users.db import SQLAlchemyBaseUserTable
from app.models.base import Base

# Stejný vzor jako DOMAIN_TYPES (app/models/domain_classification.py) — přidání role =
# rozšířit tuhle tuple, žádná změna frameworku.
ROLES = ("admin", "editor", "viewer")

class User(SQLAlchemyBaseUserTable[int], Base):
    """Login account. email/hashed_password/is_active/is_verified přichází z
    SQLAlchemyBaseUserTable (fastapi-users) — nepřidávej je znovu ručně.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")  # CHECK constraint, viz níže
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

Plus rozšíření existujícího `Run`:

```python
# app/models/run.py — přidat na existující Run
triggered_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
```

`triggered_by_user_id` je nullable — budoucí scheduler-run (`docs/ROADMAP.md` bod 5) nemá
žádného člověka za sebou; `trigger_type='scheduled'` + `triggered_by_user_id=None` dohromady
jsou jednoznačné, žádný speciální "systémový" uživatelský účet není potřeba.

CHECK constraint na `role` (ne Python enum) — konzistentní s tím, jak `domain_type` na
`domain_classifications` (fáze 5) a `execution_type` na `analysis_skills` (fáze 3) jsou taky
prostý `VARCHAR` bez DB enum typu.

---

## Design decisions (navrženo před psaním kódu)

1. **Cookie + JWT strategie, ne databázové session.** `CookieTransport` + `JWTStrategy`
   (fastapi-users) — stavový podpis session žije v samotném cookie, appka nemusí spravovat
   ani čistit samostatnou `sessions` tabulku. Vědomý kompromis: nejde silou odhlásit jedno
   konkrétní zařízení bez revokačního seznamu — pro malý interní tým přijatelné, jde přidat
   později (`DatabaseStrategy` je stejná knihovna, jiná implementace `get_strategy`).
2. **Žádné self-service registrace, žádný emailový reset hesla v v1.** fastapi-users má
   vestavěné routery pro obojí (`get_register_router()`, `get_reset_password_router()`) — v
   téhle fázi se **vůbec nemountují**. Jediná cesta k účtu je admin (`POST /users`); reset
   hesla řeší admin ručně (`POST /users/{id}/reset-password`).
3. **Admin nastavuje počáteční heslo ručně na create-user formuláři** (ne auto-generované
   zobrazené jednou) — v kombinaci s `must_change_password=True` (bod 5 níž) je to heslo
   jednorázové, takže jeho síla nehraje velkou roli; jednodušší na admina než kopírovat
   vygenerovaný string.
4. **`require_role()` je jediný zdroj pravdy pro autorizaci; skryté UI prvky v šabloně jsou
   jen UX.** Stejný princip, co appka nikde jinde explicitně nezapsala, ale řídila se jím
   implicitně — tady je to poprvé bezpečnostně kritické, takže stojí za explicitní zápis.
5. **`must_change_password` se vynucuje na úrovni celé appky** (dependency na
   `include_router(..., dependencies=[...])` v `app/main.py`, ne opakovaně na každé routě) —
   jedna centrální kontrola, žádné riziko, že se na ni zapomene u nové routy přidané později.
6. **401 (nepřihlášený) vs. 403 (přihlášený, ale špatná role) se chovají jinak.** 403 jde přes
   existující `AppError` → JSON (stejně jako každá jiná chyba appky dnes — i 404 na neexistujícím
   klientovi se dnes zobrazí jako JSON, ne hezká chybová stránka; tahle fáze to nemění). 401
   ale musí u plnohodnotné navigace prohlížečem přesměrovat na `/login`, ne vrátit JSON — appka
   by jinak byla nepoužitelná (každá stránka by ukázala syrový JSON místo login formuláře).
   `/dashboard/api/*` endpointy (volané přes `fetch`, ne navigací) naopak JSON 401 vracet mají,
   ať to frontend JS může rozeznat. **Ověř přesné chování fastapi-users' `current_active_user`
   defaultní `HTTPException`, neodhaduj ji** — stejná opatrnost jako design decision 11 ve fázi 5
   u JSONB dotazu.
7. **Multi-tenancy (`docs/ROADMAP.md` bod 10) není součástí týhle fáze.** `require_role()`
   a `current_active_user` jsou navržené tak, aby šla tenant-scoping dependency přidat později
   navrstvením nad ně, ne přestavbou.
8. **Viewer role nemá žádnou zápisovou ani stahovací akci** — potvrzeno v konverzaci: ani
   export (CSV/XLSX/JSON), ani doménová klasifikace. Jen čtení.

---

## Task Index

| ID | Name | Závislost | Status |
|----|------|-----------|--------|
| P6-T1 | Schema: `users` tabulka + `Run.triggered_by_user_id` | žádná | ✅ |
| P6-T2 | Auth backend: fastapi-users wiring, login/logout, `require_role()` | P6-T1 | ✅ |
| P6-T3 | Login gate + `current_user` v šablonách | P6-T2 | ✅ |
| P6-T4 | Admin: správa uživatelů (UI) | P6-T3 | ✅ |
| P6-T5 | Vynucená změna hesla po prvním přihlášení | P6-T3 | ✅ |
| P6-T6 | Role-based guardy a skryté UI napříč appkou | P6-T3 | ✅ |
| P6-T7 | Audit: `triggered_by_user_id` v `trigger_run` | P6-T2 | ✅ |
| P6-T8 | Testy | P6-T1 až P6-T7 | ✅ |

Striktně sekvenční — na rozdíl od fáze 5 tu není žádná dvojice úkolů, co jde souběžně. P6-T4,
P6-T5, P6-T6 mají společnou prerekvizitu P6-T3, ale mezi sebou na sobě nezávisí — jdou v
libovolném pořadí mezi sebou, číslování jen odráží logické seskupení (admin nástroje →
vynucené heslo → zbytek appky).

---

## P6-T1 — Schema: `users` tabulka + `Run.triggered_by_user_id`

**Target:** nová migrace, `app/models/user.py`, `app/models/run.py`, `app/models/__init__.py`,
`requirements.txt`

1. `requirements.txt` — přidat `fastapi-users[sqlalchemy]` (pinned verze, ověř aktuální stable
   proti PyPI, neodhaduj číslo z paměti).
2. Nová migrace — `CREATE TABLE users` (fastapi-users' `SQLAlchemyBaseUserTable` sloupce: `id`,
   `email`, `hashed_password`, `is_active`, `is_verified` + `name`, `role`, `must_change_password`,
   `created_at`, `updated_at` dle schema flagu). CHECK constraint na `role`. `ALTER TABLE runs ADD
   COLUMN triggered_by_user_id` (nullable FK na `users.id`).
3. `app/models/user.py` — `User` model + `ROLES` tuple dle schema flagu.
4. `app/models/run.py` — `Run` dostane `triggered_by_user_id` sloupec + `triggered_by:
   Mapped["User | None"]` relationship.
5. `app/models/__init__.py` — registrace `User`.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověř: `users` tabulka existuje s očekávanými sloupci a CHECK constraintem; `runs` má
   nový nullable sloupec `triggered_by_user_id`.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add users table and Run.triggered_by_user_id for auth
```

---

## P6-T2 — Auth backend: fastapi-users wiring

**Target:** nový `app/auth.py`, `app/config.py`, `app/main.py`, `.env.example`

Prerekvizita: P6-T1 hotový.

1. `app/config.py` — nový setting `secret_key: str` (JWT signing key), načtený z `.env`. Žádný
   defaultní literál v kódu pro produkční hodnotu — appka má selhat při chybějící hodnotě, ne
   tiše použít slabý default.
2. `.env.example` — nová `SECRET_KEY=` položka s komentářem (generovat náhodný string, nikdy
   nesdílet mezi prostředími).
3. Nový `app/auth.py`:
   - `UserManager` (fastapi-users) — minimalistický, žádné vlastní `on_after_*` hooky v v1
     (register/forgot-password se nemountují, viz design decision 2).
   - `CookieTransport` (`cookie_name="signalmap_session"`, `cookie_max_age` 14 dní) +
     `JWTStrategy` (`secret=settings.secret_key`) → jeden `AuthenticationBackend`.
   - `FastAPIUsers` instance, `current_active_user` dependency export.
   - `require_role(*allowed_roles: str)` — dependency factory, `AppError("forbidden", ...,
     status_code=403)`, když `user.role not in allowed_roles` (design decision 4).
4. `app/main.py` — namountovat jen `fastapi_users.get_auth_router(auth_backend)` (login/logout),
   **ne** register/reset-password routery (design decision 2). Zaregistrovat custom exception
   handler pro `HTTPException(401)` → `RedirectResponse` na `/login` (design decision 6) — jen
   pro non-`/api/`-prefixované cesty, ověř přesné chování fastapi-users' výchozí 401 výjimky.

Po dokončení:
1. `docker compose up -d --build`
2. Ruční ad-hoc skript v kontejneru: vytvoř `User` přímo v DB session (bcrypt hash ručně přes
   `UserManager`), zavolej `POST /auth/login` (`curl`/httpie) → dostaneš `Set-Cookie`. Zavolej
   chráněnou routu s tím cookie → projde. Bez cookie → 401/redirect dle design decision 6.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(auth): wire fastapi-users cookie authentication and require_role dependency
```

---

## P6-T3 — Login gate + `current_user` v šablonách

**Target:** `app/templating.py`, nový `app/templates/auth/login.html`, `app/templates/base.html`,
`app/main.py`, i18n

Prerekvizita: P6-T2 hotový.

1. `app/templating.py` — `render()` dostane volitelný `current_user` parametr/injekci (mirror
   dnešního `t`/`locale` vzoru), dostupný v každé šabloně jako `current_user` (`None`, když
   nepřihlášený — ale díky login gate (bod 3) se to na chráněných routách nestane).
2. Nový `app/templates/auth/login.html` — email + heslo formulář, `POST /auth/login` (fastapi-users'
   endpoint), chybová hláška na neplatné přihlášení. Veřejná route, žádný `require_role`.
3. `app/main.py` — `app.include_router(..., dependencies=[Depends(current_active_user)])` na
   každém existujícím routeru **kromě** `locale` (jazyk musí fungovat i na login stránce) a
   nového `auth`/`login` routeru (design decision 5 — centrální vynucení, ne opakovaně).
4. `app/templates/base.html` — v hlavičce: jméno přihlášeného uživatele + odkaz "Odhlásit"
   (`POST /auth/logout`).
5. i18n (EN+DE, jeden commit) — `auth.login_title`, `auth.email_label`, `auth.password_label`,
   `auth.login_button`, `auth.invalid_credentials`, `auth.logout_button`.

Po dokončení:
1. `docker compose up -d --build`
2. Nepřihlášený → jakákoliv chráněná stránka přesměruje na `/login`. Přihlášení platným účtem
   (vytvořeným ručně v P6-T2) → přesměruje zpět, jméno se zobrazuje v hlavičce.
3. Odhlášení → cookie zneplatní, další request na chráněnou stránku zase přesměruje na `/login`.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(auth): add login page, logout, and app-wide login gate
```

---

## P6-T4 — Admin: správa uživatelů

**Target:** nový `app/routers/users.py`, nové `app/templates/users/list.html` a
`app/templates/users/form.html`, `app/main.py`, i18n

Prerekvizita: P6-T3 hotový.

1. Nový `app/routers/users.py`, celý router s `dependencies=[Depends(require_role("admin"))]`:
   - `GET /users` — seznam (jméno, email, role, aktivní/neaktivní).
   - `GET /users/new`, `POST /users` (Form `name`, `email`, `role`, `password`) — vytvoří
     `User` s `must_change_password=True`. Duplicitní email → strukturovaná `AppError`, ne 500
     (stejný pre-check + `IntegrityError` fallback vzor jako `create_client_alias`).
   - `POST /users/{id}/edit` — změna `name`/`role`.
   - `POST /users/{id}/reset-password` (Form `password`) — nastaví nový hash,
     `must_change_password=True`.
   - `POST /users/{id}/deactivate` — `is_active=False`. **Nikdy `DELETE`** — `Run.
     triggered_by_user_id` na něj může odkazovat, mazání by porušilo audit trail (stejný
     instinkt jako NFR-6 u evidence řádků).
2. `app/main.py` — zaregistrovat router.
3. Šablony `users/list.html`, `users/form.html` — mirror stylu `clients/list.html`/`clients/
   form.html`.
4. i18n (EN+DE, jeden commit) — `user.list_title`, `user.create_title`, `user.name_label`,
   `user.email_label`, `user.role_label`, `user.password_label`, `user.reset_password_button`,
   `user.deactivate_button`, `user.deactivate_confirm`, `errors.user_email_duplicate`,
   `errors.user_not_found`.

Po dokončení:
1. `docker compose up -d --build`
2. Přihlášen jako admin: vytvoř nového uživatele s rolí `editor` → objeví se v seznamu. Zkus
   duplicitní email → strukturovaná chyba. Resetuj mu heslo, deaktivuj ho.
3. Přihlášen jako `editor`/`viewer` (vytvořený výše) → `/users` vrátí 403, odkaz na `/users`
   není v hlavičce vidět (P6-T6 dořeší zbytek appky, tady jen tahle sekce).
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(users): add admin-only user management CRUD
```

---

## P6-T4b — Bootstrap skript pro první admin účet (dodatečně přidáno)

Nebyl to samostatný task v původním plánu — objevilo se to jako mezera až po dokončení P6-T4:
`/users` je admin-only, takže čerstvá appka nemá žádný způsob, jak vytvořit úplně prvního
admina. `scripts/create_admin.py` řeší přesně tenhle bootstrap moment — spouští se ručně
(`docker compose exec app python -m scripts.create_admin --email ... --name ...`), heslo se
zadává interaktivně (nikdy jako CLI argument, aby nezůstalo v shell historii), stejný
synchronní `PasswordHelper` vzor jako `app/routers/users.py`. Zdokumentováno v `README.md` jako
krok 4 ("Run it").

**Expected commit:**
```
feat(users): add scripts/create_admin.py to bootstrap the first admin account
```

---

## P6-T4c — `ENVIRONMENT` setting + `--from-env` bootstrap + dev test users (dodatečně přidáno)

Taky mezera objevená až po P6-T4b, tentokrát z konverzace o tom, jak tohle řeší jiné projekty
(Django `createsuperuser --noinput`, Rails/Laravel seed scripty). Rozlišuje dvě různé věci, co
vypadají podobně, ale nemají stejnou povahu:

- **Admin bootstrap** — skutečný operační krok, může běžet i v produkci → env proměnné dávají
  smysl (`DEV_ADMIN_EMAIL`/`DEV_ADMIN_NAME`/`DEV_ADMIN_PASSWORD`, čteno přes
  `python -m scripts.create_admin --from-env`, idempotentní na rozdíl od interaktivní cesty).
- **Testovací editor/viewer účty** — čistě lokální dev fixture, nikdy nic skutečného nechrání
  (existují jen na jednorázovém lokálním Postgresu) → **natvrdo v kódu**
  (`scripts/seed_dev_users.py`, `editor@local.dev`/`viewer@local.dev`, pevné heslo), ne přes env
  — stejný vzor jako Rails/Laravel seed data. Env proměnná na tohle by neřešila žádné skutečné
  riziko, jen přidávala vyplňování.
- **`ENVIRONMENT=development/production`** (`app/config.py`, default `development`) — nová
  appka dosud neměla žádný koncept "jsem v produkci". Umožňuje `seed_dev_users.py` **skutečně
  technicky odmítnout** běh v produkci (`sys.exit(1)`), ne jen mít varování v docstringu.

Zdokumentováno v `README.md` krok 4 (alternativy k základnímu bootstrapu).

**Expected commit:**
```
feat(config): add ENVIRONMENT setting, --from-env admin bootstrap, and dev test-user seeding
```

---

## P6-T5 — Vynucená změna hesla po prvním přihlášení

**Target:** nový `app/routers/auth.py` (nebo rozšíření `app/routers/users.py` o `/change-password`),
`app/main.py`, nová šablona `app/templates/auth/change_password.html`, i18n

Prerekvizita: P6-T3 hotový.

1. Nová `POST /change-password` (Form `new_password`) — vyžaduje jen přihlášení
   (`current_active_user`, žádná konkrétní role), nastaví nový hash, `must_change_password=False`.
2. `app/main.py` (nebo dependency v `app/auth.py`) — pokud `current_user.must_change_password`
   a request nesměřuje na `/change-password` nebo `/auth/logout`, přesměruj (303) na
   `/change-password`. Funguje napříč celou appkou bez ohledu na roli (design decision 5).
3. Nová šablona `app/templates/auth/change_password.html` — jednoduchý formulář, žádná
   navigace/sidebar (uživatel je "uvězněný", dokud heslo nezmění).
4. i18n (EN+DE, jeden commit) — `auth.change_password_title`, `auth.new_password_label`,
   `auth.change_password_button`, `auth.change_password_required_note`.

Po dokončení:
1. `docker compose up -d --build`
2. Admin vytvoří nový účet (P6-T4) → přihlášení tím účtem přesměruje na `/change-password`
   bez ohledu na to, kam se uživatel pokusil jít. Po změně hesla → normální přístup, opětovné
   přihlášení už nepřesměrovává.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(auth): force password change on first login after admin-created account
```

---

## P6-T6 — Role-based guardy a skryté UI napříč appkou

**Target:** `app/routers/clients.py`, `prompt_sets.py`, `prompts.py`, `runs.py`, `dashboard.py`,
`providers.py`, `ai_models.py`, `settings.py` + odpovídající šablony

Prerekvizita: P6-T3 hotový. Aplikuje tabulku rolí z `docs/ROADMAP.md` §1 "Co je vidět komu".

1. Route-level (zdroj pravdy, design decision 4):
   - Všechny mutující routy (create/edit/delete) na `clients.py`, `prompt_sets.py`,
     `prompts.py` → `Depends(require_role("admin", "editor"))`.
   - `trigger_run` (`runs.py`) → `Depends(require_role("admin", "editor"))`.
   - Export routy (`export_run`, `export_prompt_runs`, `export_client_runs`) → `Depends(
     require_role("admin", "editor"))` — **viewer nesmí exportovat** (potvrzeno v konverzaci).
   - `classify_domain` (`dashboard.py`) → `Depends(require_role("admin", "editor"))` —
     **viewer nesmí klasifikovat domény**.
   - Celé routery `providers.py`, `ai_models.py`, `settings.py` →
     `dependencies=[Depends(require_role("admin"))]` na úrovni `APIRouter`/`include_router`.
2. Šablona-level (jen UX, ne bezpečnost — skryté tlačítko pro roli, co na to nemá route
   oprávnění): tlačítka "Spustit run", "Smazat", "Upravit", export skupina, inline select pro
   doménovou klasifikaci, odkazy na `/providers`/`/ai-models`/`/settings`/`/users` v
   `base.html` navigaci — všude `{% if current_user.role in (...) %}`.

Po dokončení:
1. `docker compose up -d --build`
2. Přihlášen jako `viewer`: dashboard/run detail/client detail se zobrazí normálně (čtení),
   ale tlačítko "Spustit run", editační/mazací akce, export tlačítka a doménová klasifikace
   nejsou vidět. Přímé `POST` na tyhle routy (curl/DevTools) i tak vrátí 403 — UI schování
   nenahrazuje route-level check.
3. Přihlášen jako `editor`: totéž funguje jako dřív (žádná regrese), ale `/providers`,
   `/ai-models`, `/settings`, `/users` vrátí 403 a nejsou v navigaci.
4. Přihlášen jako `admin`: appka se chová jako dřív, žádné omezení.
5. Ověř na ~640px/~1024px/desktop šířce.
6. Implementation summary + navrhni commit message (nespouštěj git)

> **Pozn. (2026-09-12, dodatečně):** bod 3 výše a řádek `{% if current_user.role in (...) %}`
> v bodě 2 popisují původní plán, ne aktuální stav — viz `docs/ROADMAP.md` §1 "Dodatečná úprava"
> pro skutečné chování (`/ai-models`/`/settings` teď editor vidí, šablony používají sdílenou
> funkci `can_edit(current_user)`, ne inline `role in (...)`). Neverifikuj podle tohohle checklistu
> doslovně, drž se `ROADMAP.md`.

**Expected commit:**
```
feat(auth): apply role-based route guards and hide UI by role across the app
```

---

## P6-T7 — Audit: `triggered_by_user_id` v `trigger_run`

**Target:** `app/routers/runs.py`, `app/templates/runs/detail.html`, i18n

Prerekvizita: P6-T2 hotový (potřeba `current_active_user`). Nezávislé na P6-T4/T5/T6, jde
udělat kdykoliv po P6-T2 — číslo jen odráží logické zařazení za zbytek auth práce.

1. `app/routers/runs.py` — `trigger_run` dostane `user: User = Depends(current_active_user)`,
   nastaví `Run.triggered_by_user_id = user.id` při vytvoření řádku.
2. `app/templates/runs/detail.html` — malé pole v METADATA sekci: "Spustil: {{ run.
   triggered_by.name }}" (nebo "—" pro runy z doby před touhle fází, kde je sloupec `None`).
3. i18n (EN+DE, jeden commit) — `run.triggered_by_label`.

Po dokončení:
1. `docker compose up -d --build`
2. Spusť run přihlášený jako konkrétní uživatel → run detail ukazuje jeho jméno. Existující
   runy z před touhle fází → "—", ne chyba/pád.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): record which user triggered each run
```

---

## P6-T8 — Testy

**Target:** nový `tests/test_auth.py`, nový `tests/test_users.py`, `tests/conftest.py`
(rozšíření), rozšíření existujících testů o autentizovaného klienta

Prerekvizita: P6-T1 až P6-T7 hotové.

1. `tests/conftest.py` — nové fixtures: `admin_user`/`editor_user`/`viewer_user` (seedované
   přes `UserManager`, ne přímý SQL insert — ať heslo je vždy validně hashované), `authed_client`
   (TestClient přihlášený jako `editor_user`, výchozí pro existující testy, co dřív žádnou
   auth nepotřebovaly).
2. `tests/test_auth.py` — login s platnými/neplatnými credentials, logout zneplatní cookie,
   nepřihlášený požadavek na chráněnou routu → redirect na `/login`, `/dashboard/api/*`
   nepřihlášený → JSON 401 (ne redirect, design decision 6).
3. `tests/test_users.py` — admin CRUD uživatelů, duplicitní email → strukturovaná chyba,
   `editor`/`viewer` na `/users` → 403, `must_change_password` flow (vynucený redirect,
   zmizí po změně hesla).
4. Rozšíření existujících testů (`test_clients.py`, `test_runs.py`, `test_dashboard.py`, ...) —
   přepnout na `authed_client` fixture tam, kde routy teď vyžadují přihlášení; přidat aspoň
   jeden test na `viewer` roli u mutujících endpointů (403) a exportu (403) v `test_runs.py`.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover authentication, roles, and user management
```

---

## Completion Checklist

- [x] Schema (`users`, `Run.triggered_by_user_id`) potvrzené před P6-T1 (viz konverzace +
      tenhle dokument)
- [x] Login/logout funguje, cookie session, žádné API tokeny
- [x] Žádná self-service registrace, žádný emailový reset hesla — obojí vestavěné v
      fastapi-users, ale nezapojené
- [x] Admin může vytvářet/editovat/deaktivovat uživatele a resetovat hesla přes UI
- [x] Bootstrap prvního admin účtu (`scripts/create_admin.py`) existuje a je zdokumentovaný v README
- [x] Nový/resetovaný účet vynutí změnu hesla při prvním přihlášení
- [x] Role `admin`/`editor`/`viewer` vynucené na úrovni routy (403), ne jen v UI
- [x] Viewer nemá export ani doménovou klasifikaci, jen čtení
- [x] `Run.triggered_by_user_id` se zapisuje při každém `trigger_run` a zobrazuje na run detailu
- [x] `pytest` sada zelená, pokrývá auth, role, user CRUD i regresi existujících routerů (124 testů)
- [ ] Ověřeno v prohlížeči na ~375px/~768px/desktop, všechny nové obrazovky — **stále chybí
      systematický průchod**, jen bodové kontroly (account stránka na mobilu, menu/error page na
      desktopu)
- [x] `docs/ROADMAP.md` — stav bodu 1 aktualizován po dokončení
