# SignalMap — Claude Code Session Prompts: Production-Readiness Hardening

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-phase1-hardening (z aktuálního master)
## 2. Šest kódových promptů (HD-1 až HD-6), POŘADÍ VYNUCENÉ pro HD-4→HD-6→HD-5 (HD-6 navazuje
##    migrací na HD-4's 0007 a potřebuje prompt_sets edit z HD-4; HD-5 testuje delete
##    endpointy z HD-4 i audit sloupce z HD-6). HD-1/HD-2/HD-3 jsou vzájemně nezávislé, ale
##    drž se pořadí v souboru.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit provádíš ty,
##    ne agent — agent NIKDY nespouští git commit/push sám bez výslovného potvrzení, a to i
##    přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_HARDENING.md přepni řádek daného task ID v tabulce "Task Index"
##       z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-6 (včetně re-analýzy rozsahu, decision 4):
##    docs/TASKS_HARDENING.md — přečti si konkrétní task ID před psaním kódu, ideálně celý
##    soubor před HD-1.
## 9. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md krátkou poznámku/odkaz na
##    tenhle branch (viz Completion Checklist v TASKS_HARDENING.md).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-phase1-hardening.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_HARDENING.md — CELÉ, hlavně design decisions 1-6

KONTEXT: Fáze 1 (docs/TASKS.md, Tasks 1-6) je hotová a ověřená end-to-end
proti reálnému Gemini API. Tahle větev nepřidává žádnou user-facing
featuru — je to production-readiness hardening z code review (2026-09-09):
chybové hlášky prozrazují interní detaily klientovi, appka nikam neloguje,
requirements.txt nemá zamčené verze, delete/edit chování je napříč
entitami nekonzistentní (Market má plné CRUD, Client/PromptSet/Prompt ne),
a appka je ověřená jen ručně — nulové automatizované testy.

KRITICKÉ: rozsah byl před založením téhle větve ověřený (design decision 4
v TASKS_HARDENING.md) — DB už dnes implicitně blokuje smazání promptu
s běhy (chybějící `ondelete` na `runs_prompt_id_fkey` = Postgres default
`NO ACTION`, funkčně stejné jako RESTRICT). Skutečná mezera je v aplikační
vrstvě (chybějící endpointy), ne v datech. Neobjevuj scope znovu, drž se
přesně HD-T1 až HD-T6 podle TASKS souboru.

HD-T6 (audit columns) vznikl jako dodatečný nález (ne z původního code
review) — `created_at`/`updated_at` na `markets`/`prompt_sets` jen tam,
kde je entita skutečně editovatelná; `created_by`/`updated_by` se
nepřidává vůbec (žádný auth v týhle fázi) — viz design decision 7 v
TASKS_HARDENING.md.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný
JavaScript framework), Alembic migrace, Docker Compose. Backend kód
anglicky vč. komentářů/error_code, UI texty vždy přes t() mechanismus
v app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom
commitu.

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation) — NIKDY nepřidávej delete
  endpoint pro tyhle tři. To je jádro produktu (evidence-retention),
  ne detail k diskuzi.
- Nová migrace pro každou schema změnu, navazující revision ID
  (poslední je 0006 — nová je 0007).
- Každá route funkce dostane docstring; každé netriviální Form/Field
  pole `description=...`.
- Nikdy `git commit` ani `git push` bez tvého výslovného potvrzení —
  i po tom, co agent sám navrhne commit message, čeká na "ano, commitni"
  než cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message.
Nikdy nespouštěj git add/commit/push sám bez výslovného pokynu — a to
i tehdy, když jsi zprávu sám navrhl v předchozí větě.
```

---
---

## PROMPT HD-1 — Strukturované logování + oprava úniku chybových detailů

```
Task: Prompt HD-1 — logging + fix exception detail leak

Přečti docs/TASKS_HARDENING.md úkol HD-T1 CELÝ.

1. Nový app/logging_config.py — configure_logging(): stdlib logging na
   StreamHandler(sys.stdout), vlastní JSONFormatter (timestamp, level,
   logger name, message, volitelná extra data). Žádná nová závislost
   (ne structlog, ne loguru).
2. app/main.py — zavolej configure_logging() při importu modulu, před
   vytvořením FastAPI app instance.
3. app/errors.py — v handle_unexpected_error: zaloguj skutečnou výjimku
   (logger.exception(...), celý traceback) PŘED vrácením odpovědi.
   Odpověď klientovi přestane obsahovat str(exc) v poli detail — jen
   generická hláška. AppError handler nech beze změny.
4. app/routers/runs.py — log INFO při triggeru běhu (prompt_id, model,
   market), INFO při úspěchu (latency_ms), ERROR při selhání (s
   výjimkou) v trigger_run.
5. docker-compose.yaml — app service dostane logging.driver: json-file
   s options max-size: "10m", max-file: "3".

Po dokončení:
1. docker compose up -d --build — appka nastartuje bez chyby
2. Spusť reálný běh, ověř docker compose logs app ukazuje strukturované
   řádky
3. Vyvolej chybu (např. POST na /prompts/1/runs se špatným model_id) a
   ověř, že odpověď klientovi neobsahuje syrový text výjimky, ale log
   ukazuje celý traceback
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(infra): add structured stdout logging, stop leaking exception details to clients
```

### DONE

---
---

## PROMPT HD-2 — Dockerfile: non-root uživatel

```
Task: Prompt HD-2 — run app container as non-root

Přečti docs/TASKS_HARDENING.md úkol HD-T2 CELÝ.

1. dockerfile — po COPY krocích přidej:
   RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /code
   USER appuser

Po dokončení:
1. docker compose up -d --build
2. docker compose exec app whoami → appuser (ne root)
3. curl http://localhost:58000/health → 200
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
chore(infra): run app container as non-root user
```

### DONE

---
---

## PROMPT HD-3 — Zamknout verze v requirements.txt

```
Task: Prompt HD-3 — pin dependency versions

Přečti docs/TASKS_HARDENING.md úkol HD-T3 CELÝ.

1. requirements.txt — nahraď každou nepinnutou závislost přesnou verzí
   podle seznamu v HD-T3 (fastapi==0.141.1, uvicorn[standard]==0.52.4,
   sqlalchemy==2.0.52, psycopg[binary]==3.3.5, alembic==1.19.2,
   python-dotenv==1.2.3, pydantic-settings==2.15.0, jinja2==3.1.6,
   python-multipart==0.0.32, google-genai==2.22.0).

Po dokončení:
1. docker compose build --no-cache
2. docker compose exec app pip list --format=freeze — odpovídá přesně
   pinnutým verzím
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
chore(deps): pin all dependency versions for reproducible builds
```

### DONE

---
---

## PROMPT HD-4 — Delete politika + chybějící CRUD (Client/PromptSet/Prompt)

```
Task: Prompt HD-4 — delete policy + missing CRUD

Přečti docs/TASKS_HARDENING.md úkol HD-T4 CELÝ, hlavně design decision 3
a 4 (proč je delete politika JEDNO pravidlo, ne jedno chování, a proč
DB už dnes implicitně chrání data).

1. Nová migrace 0007 — explicitně ON DELETE RESTRICT na
   runs_prompt_id_fkey (drop_constraint + create_foreign_key). Funkčně
   stejné jako dnešní implicitní NO ACTION — jde o čitelnost schématu.
2. Sdílený vzor "má tahle entita pod sebou běh?" v každém routeru,
   analogicky in-use kontrole z markets.py.
3. app/routers/clients.py — POST /clients/{id}/delete: blokovat
   (strukturovaná chyba s počtem běhů), pokud existuje jakýkoliv Run
   pod libovolným PromptSet/Prompt klienta; jinak smazat.
4. app/routers/prompt_sets.py — doplň CHYBĚJÍCÍ GET/POST
   /prompt-sets/{id}/edit (nový templates/prompt_sets/form.html, stejný
   vzor jako clients/form.html) a POST /prompt-sets/{id}/delete
   (blokovat, pokud kterýkoliv prompt pod ním má běh).
5. app/routers/prompts.py — POST /prompts/{id}/delete: maže CELOU
   verzovací linii (kořen + všechny řádky se stejným root_prompt_id),
   blokovat, pokud KTERÁKOLIV verze v linii má běh.
6. Šablony — delete tlačítko s confirm() (stejný vzor jako
   markets/list.html) na clients/detail.html, prompt_sets/detail.html,
   prompts/detail.html; Edit odkaz na prompt_sets/detail.html.
7. i18n (EN i DE zároveň, ve stejném commitu) — nové klíče pro delete
   button/confirm na client/prompt_set/prompt, edit_title pro prompt
   set, errors.client_in_use/prompt_set_in_use/prompt_in_use se
   stejným {count} placeholder formátem jako errors.market_in_use.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V prohlížeči: prázdný klient → smazat → funguje. Klient s promptem
   a během → pokus o smazání klienta/prompt setu/promptu je zablokovaný
   se správným počtem běhů v hlášce.
3. /prompt-sets/{id}/edit — formulář se předvyplní a uloží
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(crud): add delete to clients/prompt-sets/prompts, blocked when evidence exists; add missing prompt-set edit
```

### DONE

---
---

## PROMPT HD-6 — Audit columns: created_at/updated_at na markets/prompt_sets

```
Task: Prompt HD-6 — created_at/updated_at audit columns

Přečti docs/TASKS_HARDENING.md úkol HD-T6 CELÝ, hlavně design decision 7
(proč zrovna markets/prompt_sets, proč ne providers/ai_models, proč se
nepřidává created_by/updated_by).
Prerekvizita: HD-4 hotový (migrace navazuje na jeho 0007; prompt_sets
edit endpoint z HD-4 je to, co updated_at bude reálně měnit).

1. Nová migrace 0008 — ADD COLUMN created_at/updated_at (TIMESTAMPTZ NOT
   NULL DEFAULT now()) na markets; ADD COLUMN updated_at na prompt_sets
   (created_at už existuje).
2. app/models/market.py — Market dostává created_at/updated_at
   (server_default=func.now(), updated_at navíc onupdate=func.now()),
   stejný vzor jako app/models/client.py.
3. app/models/prompt.py — PromptSet dostává updated_at stejným vzorem.
   Žádná změna routeru — onupdate se uplatní automaticky při stávajícím
   edit UPDATE.
4. Žádná UI/šablonová změna teď není potřeba.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověřit, že existující řádky markets/prompt_sets mají vyplněné
   nové sloupce
3. Upravit market přes /markets/{id}/edit → updated_at se změní,
   created_at zůstane
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add created_at/updated_at audit columns to markets and prompt_sets
```

### DONE

---
---

## PROMPT HD-5 — Testovací infrastruktura + základní sada

```
Task: Prompt HD-5 — pytest infrastructure + initial test suite

Přečti docs/TASKS_HARDENING.md úkol HD-T5 CELÝ.
Prerekvizita: HD-4 a HD-6 hotové (testy pokrývají i nové delete endpointy
a audit sloupce).

1. Nový requirements-dev.txt: -r requirements.txt + pytest + httpx.
2. tests/conftest.py:
   - fixture pro engine/session na druhé databázi signalmap_test
     (stejný Postgres kontejner, jiná DB) — Base.metadata.create_all()
     na začátku, truncate mezi testy
   - fixture TestClient s app.dependency_overrides[get_db] přepnutým
     na testovací session
   - fixture minimálních seed dat (market, provider google_gemini,
     ai_model)
   - FakeAdapter implementující ProviderAdapter protokol — vrací
     připravený RawResponsePayload nebo vyhazuje výjimku podle
     parametru; NIKDY nevolá reálné Gemini API
3. tests/test_health.py — /health → 200
4. tests/test_clients.py — create → list → edit → detail; delete
   zablokovaný s existujícím během; delete projde na prázdném klientovi
5. tests/test_markets.py — neplatný language/country odmítnutý; delete
   zablokovaný, když market používá prompt
6. tests/test_runs.py — celý flow s FakeAdapter: úspěšný běh uloží
   Run+RawResponse+Citation; FakeAdapter vyhazující výjimku →
   Run.status == 'error' s uloženou hláškou (FR-16)
7. README.md — nová sekce "Running tests": CREATE DATABASE
   signalmap_test, pip install -r requirements-dev.txt, pytest

Po dokončení:
1. pytest — všechny testy zelené
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: add pytest infrastructure and initial test suite (health, clients, markets, runs)
```

### DONE
