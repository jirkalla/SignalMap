# SignalMap — Tasks: Worker Throughput

## v1.0 | Září 2026
## Branch: feature/signalmap-worker-throughput
## Task ID prefix: WT
## Cílová verze: v1.4.0 (MINOR) — společně s `feature/signalmap-capture-robustness` a `feature/signalmap-scheduler-ops`

## Status: ✅ Implementováno 2026-10-02 (T1–T4); čeká na merge do `master`, nasazení (T5) až po `feature/signalmap-scheduler-ops`

Navrženo v konverzaci 2026-09-26, z analýzy doby běhu rozvrhů
klienta Knauf nad produkčními daty (nahranými do dev DB přes
`tools/local/refresh_dev_db.py`). Pět úkolů.

**Goal:** denní dávka Knaufu (100 runů) doběhne za ~12–13 min místo
~42 min a appka zvládne ~10 klientů stejné velikosti bez toho, aby
položky propadaly přes `grace_period` (6 h).

Tahle větev dělá jen **A** (worker nespí, když má ve frontě práci) a
**B** (4 repliky workeru). Souběžnost podle poskytovatele (**C1/C2**) je
v `docs/ROADMAP.md` #18 — vědomě ne tady (design decision 5).

---

## Výchozí stav (naměřeno 2026-09-26)

Knauf: 3 rozvrhy (schedule 2, 3, 4) zařazené v 10:00 / 10:05 / 10:10,
25 promptů × 4 modely = **100 runů denně**, 23.–26. 9. bez chyb.

| Den | Start | Konec | Trvání |
|---|---|---|---|
| 2026-09-23 | 10:00 | 10:42 | 42 min |
| 2026-09-24 | 10:00 | 10:43 | 43 min |
| 2026-09-25 | 10:00 | 10:52 | 52 min |
| 2026-09-26 | 10:00 | 10:42 | 42 min |

Rozpad 2026-09-26: **34 min** čekání na providery (součet `latency_ms`),
**8,3 min** pevná pauza workeru (100 × 5 s, naměřená průměrná mezera
mezi runy 5,05 s). Rozvrh 4 čekal ve frontě 21 min, než začal.

Latence podle modelu (100 runů na model):

| Model | Průměr | p95 | Max |
|---|---|---|---|
| gpt-5.6-terra | 36 s | 62 s | 73 s |
| gpt-5.6-luna | 30 s | 46 s | 55 s |
| claude-haiku-4-5 | 12 s | 17 s | 20 s |
| gemini-3.1-flash-lite | 7 s | 8 s | 8 s |

**Příčina** (`app/worker.py::run_forever`): jeden worker, jedna položka
na iteraci, po **každé** iteraci `time.sleep(5)` — i když ve frontě čekají
další položky. Fronta sama je na víc workerů připravená (`claim_next`
s `FOR UPDATE SKIP LOCKED`, heartbeat per `worker_name`, advisory lock
v `check_daily_quota`), jen se nevyužívá.

Kapacitní výpočet v `docs/TASKS_SCHEDULER.md` T10 bod 3 počítal s ~15 s
na run a bez pauzy; reálně je to 20,4 s + 5 s.

---

## Očekávaný efekt (simulace nad reálnými latencemi z 2026-09-26)

Simulace fronty se stejným pořadím claimu jako `claim_next`; 10 klientů =
10 kopií Knaufu. Nemodeluje zpomalení providera pod souběžnou zátěží ani
429 — reálná čísla budou o něco horší.

| | Knauf dnes | 10 klientů, všichni v 10:00 | 10 klientů po 15 min |
|---|---|---|---|
| Dnes (1 worker + sleep) | 42 min | 7 h 03 min ❌ grace | 7 h 03 min ❌ grace |
| A (1 worker) | 34 min | 5 h 40 min | 5 h 40 min |
| **A + B (4 workery)** | **12,5 min** | **85 min** | **~12 min na klienta**, max čekání 3 min |

Počet workerů — Knauf dnes (s A):

| Workery | Konec | Max čekání ve frontě | Max souběžně na OpenAI |
|---|---|---|---|
| 1 | 34,0 min | 23,2 min | 1 |
| 2 | 17,1 min | 7,1 min | 2 |
| 3 | 13,1 min | 4,4 min | 3 |
| **4** | **12,5 min** | **3,3 min** | **4** |
| 8 | 11,6 min | 1,5 min | 8 |
| 16 | 10,9 min | 0,6 min | 14 |

Nad 4 workery Knauf nezrychlí: poslední rozvrh se zařadí až v 10:11,
takže ~11 min je podlaha daná časy rozvrhů, ne kapacitou.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Spát jen když je fronta prázdná.** `claim_next` vrátí položku →
   další iterace hned; vrátí `None` → `sleep(5)` jako dnes. Stejný vzor
   jako Celery/RQ/pg-boss/Procrastinate/Oban — poll jen v nečinnosti.
   Ticker, reconcile a notifikační kontroly zůstávají na svém
   minutovém rytmu (`_TICKER_INTERVAL`), takže zrušení pauzy je nezrychlí.
2. **Žádný `LISTEN/NOTIFY`.** Zkrátil by jen čekání první položky po
   zařazení (≤ 5 s). U denních rozvrhů je to nepodstatné a přidalo by to
   druhé DB spojení a reconnect logiku.
3. **B = `deploy.replicas` v `docker-compose.yaml`, řízené z `.env`**
   (`WORKER_REPLICAS`, výchozí `1`), ne `docker compose up --scale`.
   Příznak `--scale` se nikde neuloží — další `docker compose up` podle
   runbooku by tiše vrátil jeden worker. Výchozí `1` drží dev PC levný a
   stejný jako dnes; produkce si nastaví `4` ve vlastním `.env`.
   Že compose v2 bere interpolaci v `replicas`, se ověří ve WT-T3
   (`docker compose config`), ne předpokládá.
4. **Proč 4.** Viz tabulka výše: 4 je poslední krok, který Knaufu
   znatelně pomůže; zároveň drží max 4 souběžné požadavky na OpenAI
   (polovina položek jsou dva GPT modely) — pod rate limity běžného
   tieru, ale **ověřit RPM/TPM pro gpt-5.6 v OpenAI konzoli** před
   nasazením (WT-T5). Náklady: ~180 MB RAM na worker (naměřeno
   `docker stats`), 1 DB spojení na worker z `max_connections = 100`.
5. **Žádný limit podle poskytovatele v téhle větvi.** Při 4 workerech
   není potřeba; při ~10+ klientech se stejným startem ano. Návrh
   (C1 lanes podle providera, C2 limit v DB) je v `docs/ROADMAP.md` #18.
   `SCHEDULER_PROVIDER_CONCURRENCY` (`app/config.py`) zůstává nečtený a
   neodstraňuje se — C2 ho použije jako výchozí hodnotu.
6. **Heartbeat se odpojí od volání providera** (WT-T2). Heartbeat se dnes
   zapisuje jednou za iteraci, tedy **před** voláním providera; práh
   `WORKER_STALE_THRESHOLD_SECONDS = 60` (`app/services/schedule_monitor.py`)
   počítal s voláním ≤ 28 s. Reálně 7 runů ze 420 trvalo > 60 s
   (max 73 s) a **už to způsobilo falešný alarm**: `worker.stale`
   2026-09-25 10:37:37 (`seconds_since: 70`) přesně během runu 375
   (gpt-5.6-terra, 73,4 s). Se 4 workery by falešných alarmů bylo ~4×
   víc. Řešení: daemon vlákno v procesu workeru zapisuje touchfile + DB
   heartbeat každých 10 s nezávisle na hlavní smyčce.
   - **Zamítnuto: zvednout práh.** SDK mají výchozí timeout ~600 s;
     práh by musel být přes 10 min a detekce mrtvého workeru by ztratila
     smysl.
   - **Co tím heartbeat přestane hlídat:** zaseknuté volání uvnitř živého
     procesu. To už hlídá lease (design decision 14 v
     `docs/TASKS_SCHEDULER.md`, 15 min) — `release_expired_leases`
     jiného workeru, a omezuje ho timeout SDK. Heartbeat = „proces žije",
     lease = „položka se hýbe". Zapsat do komentáře u vlákna.
7. **Kolize a kvóta se nemění.** Guard proti souběhu stejného
   prompt+model (design decision 17) i advisory lock v `check_daily_quota`
   fungují přes procesy, ne jen v rámci jednoho. Položky jednoho rozvrhu
   budou běžet paralelně; pořadí podle `priority` platí pořád při claimu.
8. **Import race (OIR) se workeru netýká ani se 4 replikami** — každý
   proces je dál jednovláknový pro volání providerů; heartbeat vlákno
   z WT-T2 žádné SDK nevolá.
9. **Verze** se určí při releasu podle `docs/DEPLOYMENT.md` kapitola 0;
   bump a tag dělá uživatel (`AI_INSTRUCTIONS.md` §4).

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | A — smyčka nespí, když zpracovala položku | ✅ |
| T2 | Heartbeat nezávislý na délce volání providera | ✅ |
| T3 | B — repliky workeru v compose (`WORKER_REPLICAS`) | ✅ |
| T4 | Dokumentace + CHANGELOG | ✅ |
| T5 | Nasazení (`WORKER_REPLICAS=4`) a měření na produkci | ⏳ |

---

## T1 — Smyčka nespí, když zpracovala položku

**Target:** `app/worker.py`, `tests/test_worker_queue.py`

1. Vytáhnout tělo `while` smyčky v `run_forever` do funkce (např.
   `run_iteration(db, *, settings, now, last_ticker_run) -> (processed:
   bool, last_ticker_run)`), aby rozhodnutí „spát / nespát" šlo testovat
   bez skutečného `time.sleep`.
2. `time.sleep(_ITEM_POLL_INTERVAL_SECONDS)` jen když `processed` je
   `False` a nebyl požadován stop.
3. Upravit docstring modulu (krok 5 „Sleep 5 seconds" → „Sleep 5 seconds
   only if the queue had nothing due") a komentář u
   `_ITEM_POLL_INTERVAL_SECONDS`.
4. Testy:
   - položka ve frontě → iterace vrátí `processed=True`;
   - prázdná fronta → `processed=False`;
   - `run_forever` se `sleep` nahrazeným mockem a dvěma položkami ve
     frontě + stop po třetí iteraci → `sleep` zavolán jen jednou (po
     prázdné iteraci).
   - položka ve stavu `skipped`/`deferred` se počítá jako zpracovaná
     (worker na ní strávil claim) — jinak by fronta plná odložených
     položek točila smyčku naprázdno; ověřit, že `deferred` s budoucím
     `scheduled_for` už `claim_next` nevrátí (existující
     `test_claim_next_ignores_a_future_item`), takže smyčka nemůže běžet
     naprázdno.

**Done when:** testy projdou; celá sada `pytest` projde;
`docker compose up -d --build --wait` a log workeru ukazuje normální start.

**Expected commit:** `feat(runs): skip the worker poll sleep while the queue has work`

---

## T2 — Heartbeat nezávislý na délce volání providera

**Target:** `app/worker.py`, `app/services/schedule_monitor.py`
(komentář), `docker-compose.yaml` (komentář u healthchecku),
`tests/test_worker_queue.py`

1. Daemon vlákno spuštěné v `run_forever` před smyčkou: každých 10 s
   `write_heartbeat` s **vlastní** `SessionLocal()` session (session se
   mezi vlákny nesdílí) + touch `/tmp/worker-alive`. Zastavuje se přes
   `threading.Event` při SIGTERM, **před** `deregister_heartbeat`, aby
   po čistém vypnutí nezůstal řádek zapsaný vláknem.
2. Chyba DB ve vlákně → zalogovat a zkusit znovu příští tik, vlákno
   nesmí umřít (jinak by se vrátil falešný `stale`, jen obráceně).
3. Hlavní smyčka heartbeat dál zapisovat může (neškodí), ale zdrojem
   pravdy je vlákno — rozhodnout v implementaci, zdůvodnit v komentáři.
4. Komentáře: `WORKER_STALE_THRESHOLD_SECONDS` (60 s = 6× interval
   vlákna, ne „sleep + volání") a healthcheck v `docker-compose.yaml`
   („written every ~10 s by a background thread"). Doplnit poznámku
   z design decision 6 (heartbeat = proces žije, lease = položka se
   hýbe).
5. Testy: vlákno zapíše heartbeat, i když hlavní „práce" (mock
   `process_claimed_item`) trvá déle než interval; po nastavení stop
   eventu vlákno skončí a `deregister_heartbeat` řádek smaže.

**Done when:** testy projdou; lokálně s `SCHEDULER_DRY_RUN=false` a
FakeAdapterem, který spí 80 s (nebo dočasná pauza v debug režimu), zůstane
`/schedules` na „běží" a `docker ps` na `healthy`.

**Expected commit:** `fix(runs): keep the worker heartbeat alive during long provider calls`

---

## T3 — Repliky workeru v compose

**Target:** `docker-compose.yaml`, `.env.example`

1. U služby `worker`:
   ```yaml
   deploy:
     replicas: ${WORKER_REPLICAS:-1}
   ```
   Komentář: proč `replicas` a ne `--scale` (design decision 3), proč
   výchozí 1, odkaz na tenhle dokument.
2. Ověřit, že compose interpolaci bere: `WORKER_REPLICAS=4 docker compose
   config` ukáže `replicas: 4`. Když ne, **zastavit se** a vrátit se
   k návrhu (alternativa: dvě pojmenované služby nebo `--scale` v
   runbooku).
3. Ověřit, že nic nepoužívá `container_name` ani pevný název kontejneru
   `signalmap-worker-1` (scale by selhal / skripty by mířily jen na
   první repliku): `grep -rn "worker-1" tools docs README.md`.
4. `.env.example`: `# WORKER_REPLICAS=1` se vysvětlením (produkce 4,
   odkaz na tabulku v tomhle dokumentu). U `WORKER_NAME` zdůraznit, že
   při `WORKER_REPLICAS > 1` **nesmí** být nastavený — všechny repliky
   by sdílely jméno (heartbeat, lease).
5. Lokální ověření: `WORKER_REPLICAS=4 docker compose up -d --wait
   worker` → 4 kontejnery `healthy`, `/schedules` ukazuje 4 workery
   s různými jmény; pak zpět na 1.
6. Test souběhu s reálnými 4 workery **lokálně bez placených volání**:
   `SCHEDULER_DRY_RUN=true` + ruční „spustit teď" jednoho setu →
   všechny položky `skipped/dry_run`, každá právě jednou, rozdělené mezi
   víc `leased_by`.

**Done when:** body 2–6 ověřené; `pytest` projde.

**Expected commit:** `feat(infra): make the worker replica count configurable`

---

## T4 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/DEPLOYMENT.md`, `docs/ROADMAP.md`,
`app/config.py` (komentář), `docs/TASKS.md` (jen pokud popisuje worker
jako sekvenční)

1. `CHANGELOG.md` pod `## [Unreleased]` → `### Changed`, zhruba:
   ```
   - Scheduled runs are processed several times faster: the worker no
     longer pauses between queue items, and more than one worker can run
     at once (`WORKER_REPLICAS`).
   ```
   `### Fixed`:
   ```
   - A provider call longer than 60 seconds no longer triggers a false
     "worker not responding" notification.
   ```
2. `docs/DEPLOYMENT.md` 4.2: čekaný výstup s N kontejnery
   (`signalmap-worker-1` … `-4`), kontrola `dry_run`/`enabled` u **každé**
   repliky, `WORKER_NAME` nesmí být v serverovém `.env`.
3. Komentář u `scheduler_provider_concurrency` v `app/config.py`:
   aktualizovat kapacitní úvahu (reálně ~20 s na run, horizontální škálování
   přes `WORKER_REPLICAS` je teď skutečná cesta; per-provider limit =
   `docs/ROADMAP.md` #18).
4. `docs/ROADMAP.md` #18: poznámka, že A + B jsou hotové (odkaz na tenhle
   dokument), C1/C2 zůstávají.

**Done when:** diff dokumentace ukázaný uživateli a odsouhlasený.

**Expected commit:** `docs(docs): document worker replicas and throughput changes`

---

## T5 — Nasazení a měření na produkci

**Target:** produkce, žádné soubory (kromě end-of-branch docs)

1. **Před nasazením (uživatel):** v OpenAI konzoli ověřit RPM/TPM limit
   pro `gpt-5.6-terra` a `gpt-5.6-luna` — 4 souběžné požadavky s
   `web_search`. Totéž zběžně pro Anthropic.
2. Merge, bump verze, přesun `[Unreleased]`, tag — **dělá uživatel**.
3. Nasazení podle `docs/DEPLOYMENT.md` kapitoly 1–5; v serverovém `.env`
   přidat `WORKER_REPLICAS=4`. Migrace žádné.
4. **Měření** — první den po nasazení, po doběhnutí Knaufu (server,
   `psql` v kontejneru `postgres`):
   ```sql
   select coalesce(rq.batch_id::text, rq.schedule_id::text) grp,
          count(*) items,
          count(*) filter (where rq.status='done') done,
          count(*) filter (where rq.status='error') err,
          min(rq.queued_at) queued, max(r.finished_at) last_fin,
          max(r.finished_at) - min(rq.queued_at) wall,
          count(distinct rq.leased_by) workers
   from run_queue rq left join runs r on r.id = rq.run_id
   where rq.client_id = (select id from clients where name = 'Knauf')
     and rq.queued_at > now() - interval '1 day'
   group by 1 order by min(rq.queued_at);
   ```
   Cíl: poslední run Knaufu do ~10:15 (dřív ~10:42), `workers` = 4,
   `err` = 0.
5. **429 a chyby:**
   ```sql
   select p.name, count(*) filter (where r.status='error') err,
          count(*) filter (where r.error_message ilike '%429%') rate_limited,
          count(*)
   from runs r join ai_models m on m.id=r.model_id join providers p on p.id=m.provider_id
   where r.started_at > now() - interval '1 day' group by 1;
   ```
   Když `rate_limited` > 0 u OpenAI: snížit `WORKER_REPLICAS` na 3 a
   přesunout ROADMAP #18 (C1) výš.
6. `/schedules`: 4 workery, žádný `worker.stale` v notifikacích za den.
7. End-of-branch docs: `## Status: ...` v obou souborech, řádek v
   `docs/00_INDEX.md`.

**Done when:** měření ukazuje zrychlení, žádné 429, žádný falešný stale.

**Expected commit:** `docs(worker-throughput): record deploy and close the branch`

---

## Co tahle větev vědomě nedělá

- **Žádný limit souběhu podle poskytovatele** (C1/C2) — `docs/ROADMAP.md`
  #18, design decision 5.
- **Žádné vlákna/async pro volání providerů.** Horizontální škálování
  procesy stačí; vlákna/async by přepsaly smyčku workeru (resp. celý
  adaptérový řetězec) pro zisk, který při desítkách souběžných volání
  nemají (rozebráno v konverzaci 2026-09-26, shrnuto v ROADMAP #18).
- **Žádné Batch API** — jiný problém (cena, ne rychlost), ROADMAP #18.
- **Nemění časy rozvrhů klientů.** Rozložení startů po klientech je
  provozní rozhodnutí uživatele v UI, ne kód.
- **Nenastavuje explicitní timeouty v adaptérech.** Výchozí timeout SDK
  (~600 s) je pod leasem (15 min); samostatné téma.
- **Nepřepisuje historické runy ani notifikaci z 2026-09-25** (NFR-6).
