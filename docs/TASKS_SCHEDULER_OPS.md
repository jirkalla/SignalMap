# SignalMap — Tasks: Scheduler Ops (vydání 2)

## v1.0 | Září 2026
## Branch: feature/signalmap-scheduler-ops
## Task ID prefix: SO
## Cílová verze: v1.4.0 (MINOR) — společně s `feature/signalmap-worker-throughput`

Status: navrženo 2026-09-30 jako **vydání 2** z plánu vydání
(`docs/ROADMAP.md` „Plán vydání"). Pokrývá `docs/ROADMAP.md` #24, #21
(první část) a #20. Sedm úkolů, bez migrace.

**Goal:** provoz workerů a scheduleru je čitelný a odolný — z `/schedules`
je vidět, který worker co dělá a který má problém; vyčerpaný kredit
u providera nezahodí dávku po šesti minutách, ale počká na doplnění;
zbývající kvóta a přeskočené runy jsou vidět bez SQL.

**Proč jedno vydání:** všechno sahá na worker a frontu (`app/worker.py`,
`app/services/queue.py`, `/schedules`) — jedna sada testů, jedno
pozorování po nasazení.

---

## Předpoklad: Worker Throughput (WT) jako první větev tohoto vydání

`docs/TASKS_WORKER_THROUGHPUT.md` je hotový plán (WT-T1…T5), zatím
**neimplementovaný** (ověřeno 2026-09-30: `run_forever` spí 5 s po každé
iteraci, heartbeat jen v hlavní smyčce, v compose žádné `deploy.replicas`).
Postup:

1. `feature/signalmap-worker-throughput` → WT-T1…T4 podle jeho vlastních
   PROMPTS, merge do `master` **bez nasazení**.
2. Tahle větev (`feature/signalmap-scheduler-ops`) z aktuálního `master`.
3. **WT-T5 (nasazení a měření) se provede v rámci SO-T7** — jedno
   nasazení v1.4.0 pro obě větve.

SO-T1 staví na WT-T2 (heartbeat vlákno) a WT-T3 (`deploy.replicas`).

---

## Výchozí stav (kód k 2026-09-30, master 7807f7a)

**Jména workerů** — `app/config.py:71` `worker_name = socket.gethostname()`
(hex ID kontejneru), `WORKER_NAME` v compose záměrně bez defaultu. Stavový
pruh `/schedules` (`schedules.py:1108`, `index.html:53`) je jeden barevný
řádek na worker s textem `schedules.worker_running` — **jméno se
nezobrazuje**. Fronta ukazuje `leased_by` (hex) u `leased` položek
(`index.html:286`, `:310`). Heartbeat řádky (`worker_heartbeats`, PK
`worker_name`) po kontejneru ukončeném bez SIGTERM zůstávají navždy.

**Retry a chyby** — `worker.py:93` `_is_retryable_error`: `status_code`
nebo `code`; 429/5xx/bez kódu = retryable. `_BACKOFF_MINUTES = (1, 5, 25)`,
`_MAX_TRANSPORT_ATTEMPTS = 3` → **krok 25 min se nikdy nepoužije**
(3. selhání je terminální). `process_claimed_item` (:225) při retry nastaví
`queued` + backoff **bez `last_error`**; při konci `error` +
`notify("schedule.run_failed")` na **každou** položku. Adaptéry nemají
vlastní výjimky, propadají syrové výjimky SDK; `run_execution` uloží
`str(exc)` do `runs.error_message`. Vyčerpaný kredit: OpenAI 429
`insufficient_quota` (retryable → 3 pokusy za ~6 min → error), Anthropic
400 `credit balance too low` (neretryable → hned error).

**Hloubka fronty** — `_enqueue_one_schedule` (`queue.py:165`) počítá jen
`status='queued'`, ne `deferred`/`leased`.

**Kvóta** — `check_daily_quota` (`run_execution.py:60`) počítá runy
klienta za posledních 24 h proti `daily_run_limit` (default 50); skip
`quota_exceeded` + `quota.exceeded` notifikace (1× denně na klienta).
Detail klienta (`clients.py:249`) **nezobrazuje** limit, využití ani
přeskočené runy.

**History** (`/schedules?view=history`) — filtry jen `status`
(all/errors/skipped) a `q`; `skip_reason` se zobrazuje **nepřeložený**
(`schedule_monitor.py:290`).

**/ops „LLM judge"** — řádek tabulky verification fronty (`ops/index.html:205`)
počítá jen joby `kind='judge'` (ruční tlačítko / retro-verify);
automatické posouzení běží uvnitř capture jobu (`_maybe_auto_judge`,
`verification_queue.py:197`) a v tabulce se neobjeví.

---

## Design decisions

1. **Jméno workeru = číslo repliky Compose, zjištěné reverzním DNS.**
   Při startu worker zjistí vlastní IP a `socket.gethostbyaddr(ip)` —
   vestavěné DNS Dockeru vrací jméno kontejneru (`signalmap-worker-3.<síť>`)
   → `worker-3`. Jméno pak **přesně odpovídá** tomu, co vidí
   `docker compose ps`/`logs` — z „worker-3 visí" je hned jasné, který
   kontejner restartovat. Priorita: `WORKER_NAME` (explicitní) →
   reverzní DNS → hostname (dnešní chování). Rozhoduje se jednou při
   startu, ne v `config.py` při importu. **Ověřit v SO-T1 na Docker
   Desktop i na serveru**; když reverzní DNS nefunguje, zastavit se a
   vrátit k návrhu (záloha: slot `worker-N` claimovaný v DB pod advisory
   lockem).
   - Zamítnuto: slot v DB jako první volba — jméno by se nekrylo se
     jménem kontejneru, což je při restartu přesně ten zmatek, který řešíme.
   - Zamítnuto: čtení labelů přes Docker socket — bezpečnostní riziko.
2. **Úklid heartbeatů:** ticker smaže řádky `worker_heartbeats` starší
   než 1 h (`_STALE_HEARTBEAT_RETENTION`). Stale alarm (60 s) se nemění;
   úklid řeší jen „mrtvé" řádky po zaniklých kontejnerech. Díky
   stabilním jménům z 1. se po redeployi řádky přepisují, ne množí.
3. **Panel workerů na `/schedules`:** jeden řádek na worker — jméno,
   stav (běží / neodpovídá / dry-run), poslední signál, **aktuální práce**:
   `leased` položka `run_queue` (`leased_by = jméno`: klient, model,
   od kdy) nebo `leased` `verification_job` (capture/judge, run id).
   Práce běžící déle než `PROVIDER_TIMEOUT_SECONDS` × 2 (z vydání 1) =
   žlutě „dlouhé volání". Obnova přes HTMX stejně jako fronta (`every 10s`).
4. **Kategorie chyby providera v jedné službě, ne v šesti adaptérech.**
   `app/services/provider_errors.py::classify_provider_error(exc) ->
   ErrorCategory` (`billing` / `rate_limit` / `auth` / `transient` /
   `invalid_request` / `unknown`). Rozpoznává duck-typingem (`status_code`,
   `code`, tělo odpovědi `error.code`/`error.type`, text zprávy) — známé
   tvary: OpenAI 429 `insufficient_quota` → `billing`, 429 jinak →
   `rate_limit`; Anthropic 400 `credit balance` → `billing`; 401/403 →
   `auth`; 5xx/timeout/síť → `transient`; ostatní 4xx → `invalid_request`.
   Adaptéry zůstávají tenké a nové SDK = úprava jedné funkce. Klasifikace
   se testuje s **reálnými** výjimkami SDK postavenými v testu.
5. **Chování workeru podle kategorie:**
   | kategorie | chování |
   |---|---|
   | `billing` | `deferred`, `scheduled_for = now + 30 min`, **nepočítá se** do `transport_attempts`; končí až `grace_expired` (360 min) |
   | `rate_limit`, `transient`, `unknown` | dnešní backoff 1/5/25 min |
   | `auth`, `invalid_request` | hned `error`, bez opakování |
   Každý pokus nastaví `last_error` (i při odložení). Každý pokus dál
   vytváří `Run` řádek s chybou (evidence, NFR-6) — seskupení v UI je
   mimo rozsah.
6. **Retry politika sladěná:** `_MAX_TRANSPORT_ATTEMPTS = len(_BACKOFF_MINUTES) + 1`
   = 4 → použijí se všechny kroky 1/5/25 min. Docstring a komentář:
   „grace = maximální stáří položky; transport attempts = počet pokusů
   při přechodné chybě".
7. **Notifikace billing jednou na providera:** nová událost
   `provider.billing_exhausted` s cooldownem 6 h na providera (stejný vzor
   jako `notify_worker_stale`); `schedule.run_failed` se u `billing`
   neposílá, protože položka neskončila. Překlad `notifications.*` DE/EN.
8. **Hloubka fronty počítá i `deferred`.** Jinak by se při výpadku
   kreditu každé okno přidávalo k desítkám odložených položek bez limitu.
9. **Kvóta na detailu klienta:** read-only helper
   `daily_quota_usage(db, client_id, now) -> (used, limit)` sdílí dotaz
   s `check_daily_quota` (bez advisory locku). Karta: „Dnes 37 / 50
   runů (24 h)", žlutě ≥ 80 %, červeně při vyčerpání.
10. **Přeskočené runy:** banner na `/schedules` a detailu klienta „Za 24 h
    přeskočeno X runů" s rozpadem podle `skip_reason` (přeloženým).
    `dry_run` se do banneru nepočítá (je to záměr, ne problém). V History
    se `skip_reason` zobrazuje přeložený (`schedules.skip_reason_<r>`)
    + test pokrytí překladů jako ve vydání 1.
11. **/ops „LLM judge":** řádek přejmenovat na „Ruční LLM posouzení (joby)"
    a pod tabulku přidat „Automatická LLM posouzení za období: N" —
    počet `citation_verifications` s `check_type='llm'` a
    `created_at` v rozsahu stránky. Obě čísla mají jinou jednotku (joby
    vs. verdikty) — nesčítat, jen v nápovědě vysvětlit rozdíl.
12. **Bez migrace.** Kategorie se neukládá do sloupce — je odvozená
    z výjimky v okamžiku chyby; do `last_error` se zapíše prefix
    `[billing]` apod. pro čitelnost.
13. **Verze MINOR** (nové UI prvky); WT přidává `WORKER_REPLICAS` s
    defaultem 1 → na serveru **nutno nastavit `WORKER_REPLICAS=4`** v `.env`
    (krok navíc, ale ne povinná proměnná bez defaultu → MINOR, ne MAJOR).

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Jméno workeru z čísla repliky + úklid heartbeatů | ⏳ |
| T2 | Panel workerů na `/schedules` (co právě dělá) | ⏳ |
| T3 | Kategorie chyb providera + chování workeru | ⏳ |
| T4 | Billing notifikace, retry politika, hloubka fronty | ⏳ |
| T5 | Kvóta a přeskočené runy v UI, /ops judge popisek | ⏳ |
| T6 | Dokumentace + CHANGELOG | ⏳ |
| T7 | Nasazení v1.4.0 (vč. WT-T5) a měření | ⏳ |

---

## T1 — Jméno workeru z čísla repliky + úklid heartbeatů

**Target:** `app/worker.py`, `app/config.py` (komentář), `app/services/schedule_monitor.py`,
`docker-compose.yaml` (komentář u `WORKER_NAME`), `tests/test_worker_queue.py`

1. **Nejdřív ověřit mechanismus:** v běžícím kontejneru workeru
   `python -c "import socket; ip=socket.gethostbyname(socket.gethostname()); print(socket.gethostbyaddr(ip))"`
   — lokálně (Docker Desktop) se 4 replikami. Výstup ukázat uživateli.
   Nefunguje-li → stop, návrat k design decision 1.
2. `resolve_worker_name(settings) -> str`: `WORKER_NAME` → reverzní DNS
   (regex `-worker-(\d+)` → `worker-N`) → hostname. Výjimky DNS chytit,
   zalogovat, fallback. Volat jednou v `run_forever`, předávat dál místo
   `settings.worker_name`. Při startu zalogovat `worker name=worker-3
   hostname=<hex>`.
3. Úklid heartbeatů v tickeru (design decision 2).
4. Testy: `resolve_worker_name` s mockem `socket` (DNS OK / DNS chyba /
   `WORKER_NAME` nastavené); úklid smaže jen řádky starší než 1 h.

**Done when:** lokálně 4 repliky → `/schedules` a `worker_heartbeats`
ukazují `worker-1`…`worker-4`; `docker compose restart worker` jména
nezmění a nepřibydou řádky.

**Expected commit:** `feat(runs): name workers after their compose replica`

---

## T2 — Panel workerů na `/schedules`

**Target:** `app/services/schedule_monitor.py`, `app/routers/schedules.py`,
`app/templates/schedules/index.html` (+ případně `_workers.html` fragment),
`app/i18n/en.json`, `app/i18n/de.json`, `tests/test_schedule_monitor.py`,
`tests/test_schedules.py`

1. `WorkerStatus` rozšířit o `current_run` (klient, model, `started_at`
   z leased `RunQueueItem` s `leased_by = worker_name`) a `current_job`
   (kind, run id z leased `VerificationJob`). Jeden dotaz na každý typ
   pro všechny workery, ne N+1.
2. Šablona: kompaktní tabulka/karty (desktop tabulka, mobil karty) —
   jméno, stav, poslední signál, aktuální práce + doba, zvýraznění
   „dlouhé volání" (design decision 3). HTMX obnova `every 10s`.
3. Chybové stavy: žádný worker nikdy (dnešní `worker_never_seen`) beze změny.
4. Testy: worker s leased run itemem → `current_run` vyplněné; stale
   worker; worker bez práce.
5. Prohlížeč ~375 / ~768 px / desktop se 4 workery a dávkou v dry-run
   (`SCHEDULER_DRY_RUN=true`) — screenshoty.

**Done when:** testy projdou, screenshoty ukázané.

**Expected commit:** `feat(runs): show what each worker is processing on /schedules`

---

## T3 — Kategorie chyb providera + chování workeru

**Target:** `app/services/provider_errors.py` (nový), `app/worker.py`,
`tests/test_provider_errors.py` (nový), `tests/test_worker_queue.py`

1. `ErrorCategory` + `classify_provider_error` (design decision 4).
   `_is_retryable_error` nahradit voláním klasifikace (nebo nechat jako
   tenkou funkci nad ní — kvůli existujícím testům).
2. `process_claimed_item`: chování podle tabulky v design decision 5;
   `last_error` s prefixem kategorie při každém pokusu.
3. Testy klasifikace s **reálnými třídami výjimek** z nainstalovaných SDK
   (`openai.RateLimitError` s tělem `insufficient_quota`,
   `anthropic.BadRequestError` s „credit balance is too low",
   `openai.AuthenticationError`, `openai.APITimeoutError`,
   `google.genai.errors.APIError`/`ClientError`/`ServerError` s kódy) —
   konstrukce výjimek podle toho, jak je SDK samo vytváří (`response=
   httpx.Response(...)`).
4. Testy workeru: billing → `deferred` +30 min, `transport_attempts`
   beze změny, po grace `grace_expired`; auth → hned `error`; transient →
   backoff.

**Done when:** testy projdou.

**Expected commit:** `feat(runs): classify provider errors and defer billing failures`

---

## T4 — Billing notifikace, retry politika, hloubka fronty

**Target:** `app/services/notifications.py`, `app/worker.py`,
`app/services/queue.py`, `app/routers/schedules.py` (render notifikace),
`app/i18n/*.json`, testy k nim

1. `notify_provider_billing_exhausted(db, provider_code, now)` s cooldownem
   6 h (design decision 7); render textu v seznamu notifikací
   (`schedules.py:~1004` vzor).
2. `_MAX_TRANSPORT_ATTEMPTS` podle design decision 6 + docstringy.
3. `_enqueue_one_schedule`: hloubka = `queued` + `deferred` (design
   decision 8).
4. Testy: dvě billing chyby téhož providera → 1 notifikace; 4. pokus
   transient použije 25 min; odložené položky se započítají do hloubky.

**Done when:** testy projdou.

**Expected commit:** `feat(runs): notify once per provider when credit runs out`

---

## T5 — Kvóta a přeskočené runy v UI, /ops judge popisek

**Target:** `app/services/run_execution.py` (helper), `app/services/schedule_monitor.py`,
`app/routers/clients.py`, `app/routers/schedules.py`, `app/routers/ops_dashboard.py`,
`app/services/ops_dashboard.py`, šablony `clients/detail.html`,
`schedules/index.html`, `ops/index.html`, `app/i18n/*.json`, testy

1. `daily_quota_usage` + karta na detailu klienta (design decision 9).
2. `skipped_summary(db, *, client_id=None, since)` + banner na `/schedules`
   a detailu klienta (design decision 10).
3. History: přeložený `skip_reason`; test pokrytí překladů pro všechny
   hodnoty `skip_reason` použité v kódu (seznam jako konstanta
   `SKIP_REASONS` v `app/models/schedule.py`, pokud tam není).
4. /ops: popisek + počet automatických posouzení (design decision 11).
5. Prohlížeč na třech šířkách: detail klienta (normální / ≥ 80 % /
   vyčerpaná kvóta — nastavit `daily_run_limit` testovacímu klientovi
   dočasně nízko a vrátit), `/schedules` banner, `/ops`.

**Done when:** testy projdou, screenshoty ukázané.

**Expected commit:** `feat(clients): show daily quota and skipped runs`

---

## T6 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/DEPLOYMENT.md` (4.2 worker, `WORKER_REPLICAS`,
jména `worker-N`), `docs/ROADMAP.md` #20, #21, #24, `docs/TASKS_SCHEDULER.md`
(poznámka k retry politice a kategoriím — design decisions tam popisují
dnešní chování)

1. `CHANGELOG.md` `[Unreleased]`:
   - Added: worker panel s aktuální prací; zbývající denní kvóta a přehled
     přeskočených runů; jedna notifikace při vyčerpání kreditu providera.
   - Changed: workery pojmenované `worker-1…N`; při vyčerpaném kreditu
     položky čekají (až do konce grace) místo selhání po 3 pokusech;
     chyby autentizace a neplatné požadavky se neopakují; přechodné chyby
     se zkouší 4× (1/5/25 min); přeložené důvody přeskočení; popisek
     „LLM judge" na `/ops`.
   - (WT položky podle `docs/TASKS_WORKER_THROUGHPUT.md` T4, pokud už
     nejsou v `[Unreleased]`.)
2. `DEPLOYMENT.md`: čekaný výstup `docker compose ps` se 4 workery,
   `WORKER_REPLICAS=4` v serverovém `.env`, **odstranit ruční
   `--scale worker=4`** z postupu.

**Done when:** diff ukázaný uživateli a odsouhlasený.

**Expected commit:** `docs(docs): document scheduler ops changes`

---

## T7 — Nasazení v1.4.0 (vč. WT-T5) a měření

**Target:** produkce; end-of-branch docs obou větví (WT i SO)

1. **Před nasazením (uživatel):** v OpenAI konzoli RPM/TPM pro
   gpt-5.6-terra/luna (WT-T5 krok 1).
2. Merge PR, bump **v1.4.0**, přesun `[Unreleased]`, tag — uživatel.
3. Serverový `.env`: `WORKER_REPLICAS=4`, **`WORKER_NAME` nesmí být
   nastavený**. Nasazení podle `docs/DEPLOYMENT.md` 1–5 (už bez
   `--scale`). Migrace žádné → rollback = návrat kódu (ale pozor: při
   rollbacku na v1.3.x vrátit ruční `--scale worker=4`).
4. Ověření: `docker compose ps` → `signalmap-worker-1…4`; `/schedules`
   → `worker-1…4`, jména odpovídají kontejnerům; `docker compose restart
   worker` → stejná jména, žádné nové řádky v `worker_heartbeats`.
5. Měření WT-T5 kroky 4–6 (SQL tam) po doběhnutí Knaufu — výsledek sem.
6. Kontrola kategorií: týden po nasazení
   `select left(last_error, 20), status, count(*) from run_queue where
   finished_at > now() - interval '7 days' and last_error is not null
   group by 1, 2;` — žádné `[unknown]` bez vysvětlení; každý `[unknown]`
   prozkoumat a doplnit klasifikaci.
7. End-of-branch docs: `## Status: ...` v `*_SCHEDULER_OPS.md` i
   `*_WORKER_THROUGHPUT.md`, dva řádky v `docs/00_INDEX.md`,
   `docs/ROADMAP.md` „Plán vydání" → vydání 2 ✅, #18 (A+B hotové).

**Done when:** kroky 4–6 ověřené a uživatel potvrdil.

**Expected commit:** `docs(docs): record scheduler ops deploy and close the branch`

---

## Co tohle vydání vědomě nedělá

- **Circuit breaker na provideru** (#21) — až po týdnu dat z kategorií
  (T7 krok 6); bez nich nevíme, jaký práh nastavit.
- **History filtry + hromadný retry podle filtru** (#21) — vydání 4.
- **Seskupení opakovaných pokusů v Runs** (#21) — mimo plán.
- **Kontrola ceny a dostupnosti před dávkou** (#21) — mimo plán.
- **Souběžnost podle poskytovatele C1/C2** (#18) — až při 429 nebo ~10+
  klientech.
- **Ukládání kategorie chyby do sloupce** — bez migrace (design decision 12).
