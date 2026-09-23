# SignalMap — Tasks: Production-Readiness Hardening

## Status: ✅ Done — PR #1, merged 2026-09-09, released in v1.0.0

## v1.0 | Září 2026
## Branch: feature/signalmap-phase1-hardening
## Task ID prefix: HD

> Vzniklo z code review a bezpečnostní/architektonické analýzy appky (2026-09-09), na žádost
> uživatele. Review neodhalilo žádný blokující bug ve funkčnosti — fáze 1 je hotová a ověřená
> (`docs/TASKS.md`) — ale několik věcí, které jsou v pořádku pro lokální demo a nebezpečné/
> nespolehlivé kdekoliv dál: chybové hlášky prozrazují interní detaily klientovi, appka
> nikam neloguje, `requirements.txt` nemá jedinou verzi zamčenou, delete/edit chování je napříč
> doménovými entitami nekonzistentní (Market má plné CRUD, Client/PromptSet/Prompt ne), a celá
> appka je ověřená jen ručně (manuální browser testy, curl) — nulové automatizované pokrytí.
>
> Tenhle dokument a `PROMPTS_HARDENING.md` řeší tohle jako samostatnou práci, ne jako součást
> `docs/TASKS.md`'s fázového seznamu — nejde o novou produktovou featuru, jde o technický dluh
> napříč už hotovou fází 1. Struktura podle vzoru z PersonalHUB
> (`TASKS_COACH_SCHEDULE_TIMEZONE.md`).

**Goal:** appka zůstává funkčně stejná (žádná nová user-facing feature) — cílem je zvýšit
produkční připravenost: bezpečné chybové hlášky + strukturované logování, reprodukovatelný
build, jedna konzistentní delete politika napříč doménovými entitami (s chybějícím CRUD
doplněným), a základní automatizovaná testovací sada nahrazující dosavadní čistě manuální
ověřování.

---

## Design decisions (rozhodnuto v diskuzi před psaním kódu)

1. **Logging: stdlib `logging` → stdout, JSON formát, žádná nová závislost.** Log soubor se
   uvnitř kontejneru nikdy nepíše — rotaci řeší Docker (`docker-compose.yaml` `logging.driver:
   json-file` + `max-size`/`max-file`), ne appka. `structlog`/`loguru` zváženo a zamítnuto —
   zbytečná váha pro současnou velikost projektu.
2. **Verze balíčků: přesné piny (`==`), ne rozsahy.** Ověřeno přes PyPI k 2026-09-09 — všechny
   aktuálně nainstalované verze jsou už nejnovější dostupné. Jde čistě o zamčení
   reprodukovatelnosti, ne o upgrade. Lockfile (`uv`) zvážen jako budoucí vylepšení, mimo scope
   týhle větve.
3. **Jedna delete politika, ne jedno chování všude:** smazání je povolené všude, kde nezničí
   evidenci; kde by ji zničilo, je zablokované strukturovanou chybou (stejný vzor, co už
   funguje u `Market`). `Run`/`RawResponse`/`Citation` **nikdy** nejdou smazat — to je evidence,
   ne konfigurace. `Client`/`PromptSet`/`Prompt`/`Market` dostávají plné CRUD včetně delete.
4. **Re-analýza rozsahu (proveden před psaním tasků) — upřesnění, ne jen potvrzení:**
   - DB už dnes **implicitně** blokuje smazání promptu s existujícími běhy —
     `runs_prompt_id_fkey` nemá `ON DELETE` klauzuli, takže Postgres defaultně použije
     `NO ACTION` (funkčně stejné jako `RESTRICT` bez odloženého vyhodnocení, které tu
     nepoužíváme). Skutečná mezera **není v datech**, je v aplikační vrstvě: žádný delete
     endpoint pro `Client`/`PromptSet`/`Prompt` vůbec neexistuje (jen `Market` ho má), a
     `PromptSet` nemá dokonce ani edit formulář. Bez app-level ošetření by první pokus o delete
     spadl na neošetřený `IntegrityError` → obecná 500 (po HD-T1 už bez uniklého detailu, ale
     pořád bez užitečné hlášky "tenhle prompt má N běhů").
   - `Prompt.root_prompt_id` (self-referencing FK, žádný `ondelete`) znamená, že smazání
     promptu musí řešit celou verzovací linii najednou — smazat jen aktuální verzi a nechat
     osiřelé staré verze by vytvořilo matoucí částečný stav. Rozhodnuto: delete promptu maže
     **celou linii** (kořen + všechny verze sdílející `root_prompt_id`), zablokované, pokud
     **kterákoliv** verze v linii má běh.
   - `AIModel.provider_id`, `SystemInstructionTemplate.provider_id` nemají `ondelete` taky, ale
     admin UI pro providery/modely je vědomě odložené na fázi 2 (spolu s Anthropic adaptérem) —
     mimo scope týhle větve.
5. **Testovací DB: druhá databáze na existujícím compose Postgresu, ne SQLite, zatím ne
   testcontainers.** SQLite nevěrně simuluje `JSONB`/GIN index, které appka reálně používá
   (`raw_responses.raw_payload`, `runs.request_payload`) — testy by procházely a reálné chování
   by se lišilo. Testcontainers je "správnější" pro CI, ale žádné CI zatím neexistuje — zbytečná
   komplexita teď, zvážit až při zavedení CI pipeline.
6. **Vědomě mimo scope týhle větve** (probráno a odloženo dřív, tahle větev to neřeší):
   CSRF ochrana (čeká na auth fázi — bez session není co unést), pagination, rate limiting,
   skutečná ISO 3166/639 validace proti reálnému seznamu (zůstává jen formátová kontrola),
   Anthropic/Claude adaptér, admin UI pro providery/modely.
7. **Audit columns (`created_at`/`updated_at`): přidat jen tam, kde je entita skutečně
   editovatelná, ne plošně.** Nález mimo původní code review (2026-09-09, diskuze s
   uživatelem) — napříč tabulkami je to dnes nekonzistentní čistě organickým driftem
   (`Client` je měl od začátku, protože měl mít plný CRUD; `Market` edit UI přibylo později
   a audit sloupce dodané nebyly; `PromptSet`/`Prompt`/`Run` jsou navržené kolem immutable
   historie, takže `updated_at` u nich chybí záměrně). Řešení: `created_at`/`updated_at` na
   `markets` (má plné CRUD včetně edit) a `updated_at` na `prompt_sets` (edit přibývá v
   HD-T4). `providers`/`ai_models` audit sloupce nedostávají — nemají v týhle větvi žádnou
   cestu k editaci (admin UI odložené na fázi 2, viz decision 6), přidávat je teď by bylo
   mrtvé/neověřitelné schema. `created_by`/`updated_by` (user attribution) se **nepřidává
   vůbec** — fáze 1 nemá auth ani uživatelské účty (`docs/REQUIREMENTS.md` §4), takový
   sloupec by musel být buď natvrdo vyplněný, nebo nepoužívaný nullable — obojí horší než
   sloupec nemít. Patří to do fáze s autentizací (`signalmap-conventions` build-sequencing
   krok 5), ne sem.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| HD-T1 | Strukturované logování + oprava úniku chybových detailů | ✅ |
| HD-T2 | Dockerfile: non-root uživatel | ✅ |
| HD-T3 | Zamknout verze v requirements.txt | ✅ |
| HD-T4 | Delete politika + chybějící CRUD (Client/PromptSet/Prompt) | ✅ |
| HD-T6 | Audit columns: `created_at`/`updated_at` na `markets`/`prompt_sets` | ✅ |
| HD-T5 | Testovací infrastruktura + základní sada | ✅ |

---

## HD-T1 — Strukturované logování + oprava úniku chybových detailů

**Target:** nový `app/logging_config.py`, `app/main.py`, `app/errors.py`,
`app/routers/runs.py`, `docker-compose.yaml`

1. `app/logging_config.py` — nová funkce `configure_logging()`: stdlib `logging` na
   `StreamHandler(sys.stdout)`, malý vlastní `JSONFormatter` (timestamp, level, logger name,
   message, volitelná `extra` data) — žádná nová závislost.
2. `app/main.py` — zavolat `configure_logging()` při importu modulu, před vytvořením `app`.
3. `app/errors.py` — v `handle_unexpected_error`: zalogovat skutečnou výjimku
   (`logger.exception(...)`, celý traceback) **před** vrácením odpovědi klientovi. Odpověď
   klientovi přestává obsahovat `str(exc)` v `detail` — jen generická hláška. `AppError`
   handler zůstává beze změny (tam `detail` je vždy zamýšlený, ne uniklý interní stav).
4. `app/routers/runs.py` — log INFO při triggeru běhu (prompt_id, model, market), INFO při
   úspěchu (latency_ms), ERROR při selhání (s výjimkou) v `trigger_run`.
5. `docker-compose.yaml` — `app` service dostane:
   ```yaml
   logging:
     driver: json-file
     options:
       max-size: "10m"
       max-file: "3"
   ```

Po dokončení:
1. `docker compose up -d --build` — appka nastartuje bez chyby
2. Spustit reálný běh → `docker compose logs app` ukazuje strukturované log řádky (start/úspěch)
3. Vyvolat chybu (např. `curl -X POST .../prompts/1/runs -d model_id=999 -d market_id=1`) →
   odpověď klientovi neobsahuje syrový text výjimky, ale `docker compose logs app` ukazuje
   celý traceback
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(infra): add structured stdout logging, stop leaking exception details to clients
```

### DONE — commit 90fb22c

---

## HD-T2 — Dockerfile: non-root uživatel

**Target:** `dockerfile`

1. Po `COPY` krocích přidat:
   ```dockerfile
   RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /code
   USER appuser
   ```

Po dokončení:
1. `docker compose up -d --build`
2. `docker compose exec app whoami` → `appuser` (ne `root`)
3. `curl http://localhost:58000/health` → `200`
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
chore(infra): run app container as non-root user
```

### DONE — commit 2136fc8

---

## HD-T3 — Zamknout verze v requirements.txt

**Target:** `requirements.txt`

1. Nahradit každou nepinnutou závislost přesnou verzí (ověřeno k 2026-09-09 jako aktuálně
   nejnovější):
   ```
   fastapi==0.141.1
   uvicorn[standard]==0.52.4
   sqlalchemy==2.0.52
   psycopg[binary]==3.3.5
   alembic==1.19.2
   python-dotenv==1.2.3
   pydantic-settings==2.15.0
   jinja2==3.1.6
   python-multipart==0.0.32
   google-genai==2.22.0
   ```

Po dokončení:
1. `docker compose build --no-cache`
2. `docker compose exec app pip list --format=freeze` — odpovídá přesně pinnutým verzím
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
chore(deps): pin all dependency versions for reproducible builds
```

### DONE — commit cfa9aba

---

## HD-T4 — Delete politika + chybějící CRUD (Client/PromptSet/Prompt)

**Target:** nová migrace `alembic/versions/0007_*.py`, `app/models/run.py`,
`app/routers/clients.py`, `app/routers/prompt_sets.py`, `app/routers/prompts.py`,
nový `app/templates/prompt_sets/form.html`, `app/templates/clients/detail.html`,
`app/templates/prompt_sets/detail.html`, `app/templates/prompts/detail.html`,
`app/i18n/en.json`, `app/i18n/de.json`

1. Migrace 0007 — explicitně nastavit `ON DELETE RESTRICT` na `runs_prompt_id_fkey`
   (`op.drop_constraint` + `op.create_foreign_key(..., ondelete="RESTRICT")`). Funkčně stejné
   jako dnešní implicitní `NO ACTION` — jde o čitelnost/self-dokumentaci schématu, ne o změnu
   chování.
2. Sdílený vzor pro "má tahle entita pod sebou nějaký běh?" — helper funkce v každém routeru
   (analogicky `_market_rows`/in-use kontrola z `markets.py`), počítá `Run` přes join řetězec.
3. `clients.py` — `POST /clients/{id}/delete`: blokovat (strukturovaná chyba s počtem běhů),
   pokud existuje jakýkoliv `Run` pod libovolným `PromptSet`/`Prompt` klienta; jinak smazat
   (DB cascade smaže prázdné `prompt_sets`/`prompts` pod ním).
4. `prompt_sets.py` — doplnit **chybějící** `GET/POST /prompt-sets/{id}/edit` (nový
   `templates/prompt_sets/form.html`, stejný vzor jako `clients/form.html`) a
   `POST /prompt-sets/{id}/delete` (blokovat, pokud kterýkoliv prompt pod ním má běh).
5. `prompts.py` — `POST /prompts/{id}/delete`: maže **celou verzovací linii** (kořen + všechny
   řádky se stejným `root_prompt_id`), blokovat, pokud **kterákoliv** verze v linii má běh.
6. Šablony — přidat delete tlačítko s `confirm()` (stejný vzor jako `markets/list.html`) na
   `clients/detail.html`, `prompt_sets/detail.html`, `prompts/detail.html`; přidat "Edit" odkaz
   na `prompt_sets/detail.html`.
7. i18n — nové klíče `client.delete_button`/`delete_confirm`, `prompt_set.*` (create_title už
   existuje, doplnit edit_title, delete_button/confirm), `prompt.delete_button/delete_confirm`,
   `errors.client_in_use`/`prompt_set_in_use`/`prompt_in_use` (stejný formát jako
   `errors.market_in_use`, s `{count}` placeholderem) — EN i DE zároveň.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V prohlížeči: založit prázdného klienta → smazat → funguje. Založit klienta s promptem
   a spustit na něm běh → pokus o smazání klienta/prompt setu/promptu je zablokovaný se
   správným počtem běhů v hlášce.
3. `prompt_sets/{id}/edit` — formulář se předvyplní a uloží změnu.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(crud): add delete to clients/prompt-sets/prompts, blocked when evidence exists; add missing prompt-set edit
```

### DONE — commits 83bfad3, 35230bc, 7f99456 (confirm() apostrophe bugfix found during manual verification)

---

## HD-T6 — Audit columns: `created_at`/`updated_at` na `markets`/`prompt_sets`

**Target:** nová migrace `alembic/versions/0008_*.py`, `app/models/market.py`,
`app/models/prompt.py`

Prerekvizita: HD-T4 hotový (migrace navazuje na jeho `0007`; `prompt_sets.updated_at` dává
smysl až s edit endpointem, který HD-T4 přidává).

Viz design decision 7 výše — proč zrovna tyhle dvě tabulky a proč se nepřidává
`created_by`/`updated_by`.

1. Migrace 0008:
   - `markets` — `ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()`,
     `ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
   - `prompt_sets` — `ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
     (`created_at` už existuje od fáze 1).
2. `app/models/market.py` — `Market` dostává `created_at: Mapped[datetime]` a
   `updated_at: Mapped[datetime]` (`server_default=func.now()`, `updated_at` navíc
   `onupdate=func.now()`), stejný vzor jako `app/models/client.py`.
3. `app/models/prompt.py` — `PromptSet` dostává `updated_at` stejným vzorem. Žádná změna
   routeru není potřeba — `onupdate` se uplatní automaticky při stávajícím edit UPDATE
   (market edit dnes, prompt-set edit po HD-T4).
4. Žádná UI/šablonová změna není vyžadována — jde o audit data pro budoucí použití
   (troubleshooting, případně pozdější "last updated" v UI), ne o novou featuru teď.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověřit, že existující řádky `markets`/`prompt_sets` mají vyplněné nové sloupce
   (default se uplatní i na existující data).
3. Upravit market přes `/markets/{id}/edit` → `updated_at` se změní; `created_at` zůstane.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add created_at/updated_at audit columns to markets and prompt_sets
```

### DONE — commit 65b6c7a

---

## HD-T5 — Testovací infrastruktura + základní sada

**Target:** nový `requirements-dev.txt`, nový `tests/` adresář (`conftest.py`,
`test_health.py`, `test_clients.py`, `test_markets.py`, `test_runs.py`), drobná úprava
`app/adapters/__init__.py` (testovací seam pro nahrazení adaptéru), `README.md`

Prerekvizita: HD-T4 a HD-T6 hotové (testy pokrývají i nové delete endpointy a audit sloupce).

1. `requirements-dev.txt`: `-r requirements.txt` + `pytest` + `httpx` (potřeba pro
   `fastapi.testclient.TestClient`).
2. `tests/conftest.py`:
   - fixture pro engine/session na druhé databázi `signalmap_test` (stejný Postgres kontejner,
     jiná DB) — `Base.metadata.create_all()` na začátku session, truncate mezi testy pro izolaci
   - fixture `TestClient` s `app.dependency_overrides[get_db]` přepnutým na testovací session
   - fixture minimálních seed dat (jeden market, provider `google_gemini`, jeden `ai_model`) —
     potřeba kvůli FK constraintům
   - `FakeAdapter` implementující `ProviderAdapter` protokol, vrací připravený
     `RawResponsePayload` nebo vyhazuje výjimku podle parametru — registrovaný do `ADAPTERS` jen
     pro test run, ne reálné volání Gemini
3. `tests/test_health.py` — `/health` → `200`
4. `tests/test_clients.py` — create → list → edit → detail; delete zablokovaný s existujícím
   během; delete projde na prázdném klientovi
5. `tests/test_markets.py` — neplatný `language`/`country` odmítnutý; delete zablokovaný,
   když market používá prompt
6. `tests/test_runs.py` — celý flow s `FakeAdapter`: úspěšný běh uloží `Run`+`RawResponse`+
   `Citation`; `FakeAdapter` vyhazující výjimku → `Run.status == 'error'` s uloženou hláškou
   (FR-16)
7. `README.md` — nová sekce "Running tests": `CREATE DATABASE signalmap_test`,
   `pip install -r requirements-dev.txt`, `pytest`

Po dokončení:
1. `pytest` — všechny testy zelené
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: add pytest infrastructure and initial test suite (health, clients, markets, runs)
```

### DONE — commits 4e51159, b52b16e (httpx2 + pytest 9.1.1 bump, README pytest invocation fix)

---

## Completion Checklist

- [x] Chybové odpovědi klientovi neobsahují syrový text výjimky; server-side log ukazuje
      celý traceback
- [x] `docker compose exec app whoami` → `appuser`
- [x] `requirements.txt` — všechny závislosti pinnuté, `pip freeze` v kontejneru odpovídá
- [x] Client/PromptSet/Prompt mají plné CRUD (create/list/edit/delete), delete zablokovaný,
      kde by zničil evidenci
- [x] `prompt_sets/{id}/edit` existuje a funguje
- [x] `markets`/`prompt_sets` mají `created_at`/`updated_at`; `updated_at` se mění při edit
- [x] `pytest` sada zelená, pokrývá health/clients/markets/runs (úspěch i chybová cesta)
- [x] `docs/TASKS.md` — poznámka, že hardening větev existuje a co pokrývá (odkaz na tenhle
      soubor)
