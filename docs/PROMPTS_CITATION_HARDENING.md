# SignalMap — Claude Code Session Prompts: Citation Hardening (vydání 1)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 0. NEJDŘÍV větev feature/signalmap-client-vision (docs/PROMPTS_CLIENT_VISION.md,
##    VI-1 až VI-4), merge do master BEZ nasazení — nasadí se v CH-7.
## 1. git checkout -b feature/signalmap-citation-hardening (z master po merge Vision)
## 2. Devět promptů (CH-1 až CH-9). CH-1 až CH-5 jsou na sobě nezávislé
##    (pořadí doporučené — CH-1 má největší dopad), CH-6 dokumentuje,
##    CH-7 nasazuje. CH-8 a CH-9 přibyly 2026-09-30 při ověřování CH-1
##    a dělají se PŘED CH-7 (CH-9 nejpozději před backfillem v CH-7).
##    POŘADÍ PROVEDENÍ: CH-1 ✅ → CH-8 ✅ → CH-2 → CH-3 → CH-4 → CH-5 →
##    CH-9 → CH-6 → CH-7. (Čísla promptů jsou stabilní ID, ne pořadí;
##    CH-7 = nasazení je vždy poslední.)
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení). Commit dokončit před dalším promptem.
## 5. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_CITATION_HARDENING.md v "Task Index" ⏳ → ✅.
## 6. Zdůvodnění a design decisions 1-10: docs/TASKS_CITATION_HARDENING.md.
## 7. Lokální testy běží v Dockeru — před CH-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-citation-hardening
(vydání 1, cíl v1.3.0 — nasazuje se společně s už smergnutou
větví client-vision).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_CITATION_HARDENING.md — CELÉ, hlavně „Výchozí stav" a
   design decisions 1-10
3. docs/ROADMAP.md #19
4. Soubory z „Target" aktuálního úkolu

KONTEXT: Pilot Knauf (v1.2.1) ukázal pět nedostatků ověřování citací:
100 % Gemini citací končí jako `robots` (robots.txt se kontroluje na
Googlově přesměrovací bráně), adaptéry nemají timeout (zaseklý Grok
blokoval worker 27 min), výpadek archive.org shodí celý verification job,
NUL byte v PDF shodí insert, a chybí test, že každý důvod
neověřitelnosti má překlad.

KRITICKÉ:
- Tahle větev žádnou migraci nepřidává (jediná ve v1.3.0 je clients.vision
  z větve client-vision). Historické řádky se nepřepisují (NFR-6).
- Router nikdy nevolá SDK přímo; timeouty patří do adaptérů.
- robots.txt se nesmí obejít obecně — výjimka jen pro přesměrovací
  brány z `_REDIRECT_GATEWAYS`, jen jeden hop bez těla (design decision 2).
- Timeout google-genai je v milisekundách.
- Testovací fixture: Skoda Auto, prompt 54, gemini-3.1-flash-lite.
  Žádné placené runy navíc mimo tuhle fixture.
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Bump verze a tag dělá uživatel, ne agent (AI_INSTRUCTIONS.md §4).
```

---

## CH-1 — Přesměrování hop po hopu + robots cíle

### DONE — commit b8c8898

```
Úkol CH-T1 z docs/TASKS_CITATION_HARDENING.md (design decisions 1-3).

1. Přečti app/services/source_capture.py celé a tests/test_source_capture.py.
2. Navrhni mi tvar smyčky (_resolve_and_fetch) a jak se změní capture_url,
   než začneš psát — hlavně: co se uloží do requested_url/final_url při
   zákazu robots na cíli a jak se chová throttle.
3. Implementuj podle T1 bodů 1-4.
4. Testy podle T1 bodu 5 (httpx MockTransport), celá sada pytest.
5. Lokální ověření: ruční run fixture (Skoda Auto, prompt 54,
   gemini-3.1-flash-lite), pak „Verify citations" na run detailu. Ukaž mi
   pro jeho citace requested_url → final_url → error_reason/verdikt.

Na konci: shrnutí, testy, výsledek lokálního ověření, navržený commit:
fix(adapters): resolve grounding redirects before checking robots.txt
```

---

## CH-2 — Timeout na voláních providerů

### DONE — commit 784087f

```
Úkol CH-T2 z docs/TASKS_CITATION_HARDENING.md (design decisions 4-5).

1. Přečti všech šest adaptérů v app/adapters/, app/config.py,
   app/worker.py::_is_retryable_error.
2. V nainstalovaných verzích SDK (requirements.txt) ověř přesný způsob
   nastavení timeoutu a max_retries — hlavně google-genai HttpOptions
   (jednotka, retry). Řekni mi, co jsi zjistil, než začneš.
3. Implementuj T2 body 1-4 (config, adaptéry run i judge, .env.example,
   docker-compose.yaml env s defaultem u app i worker).
4. Testy v tests/test_adapters.py a tests/test_worker_queue.py podle T2
   bodu 3.
5. Lokální ověření s PROVIDER_TIMEOUT_SECONDS=1 (fixture), pak vrátit.

Na konci: shrnutí, testy, navržený commit:
fix(adapters): bound every provider call with an explicit timeout
```

---

## CH-3 — Výpadek archive.org izolovaný na citaci

### DONE — commit 6502257

```
Úkol CH-T3 z docs/TASKS_CITATION_HARDENING.md (design decision 6).

1. Přečti app/services/citation_verification.py (verify_citations_by_quote,
   _try_archive_fallback), app/services/verification_queue.py
   (process_verification_job, job_since) a app/services/archive_lookup.py.
2. Zjisti, jakým check_type/verdict/reason se dnes zapisují neověřitelné
   citace, a navrhni přesně, co se zapíše pro archive_unavailable na
   posledním pokusu. Počkej na můj souhlas.
3. Implementuj T3 body 1-3.
4. Testy podle T3 bodu 4; stávající testy job-level chyb musí projít beze
   změny.

Na konci: shrnutí, testy, navržený commit:
fix(runs): keep one archive.org failure from failing the whole verification job
```

---

## CH-4 — Sanitizace NUL v extrahovaném textu

### DONE — commit aa323ca

```
Úkol CH-T4 z docs/TASKS_CITATION_HARDENING.md (design decision 7).

1. Přečti app/services/source_extract.py a oba inserty SourceText
   (source_capture._store, citation_verification._store_archive_document).
2. Implementuj T4 body 1-2.
3. Testy podle T4 bodu 3, včetně integračního insertu do DB.

Na konci: shrnutí, testy, navržený commit:
fix(runs): strip NUL bytes from extracted source text
```

---

## CH-5 — Test pokrytí překladů důvodů a verdiktů

### DONE — commit 92148b7

```
Úkol CH-T5 z docs/TASKS_CITATION_HARDENING.md (design decision 8).

1. Najdi všechny klíče, které se skládají z UNVERIFIABLE_REASONS a VERDICTS
   (grep v app/templates a app/services), a tests/test_version.py jako vzor.
2. Nový tests/test_i18n_coverage.py podle T5 bodu 1 a 3.
3. ops/index.html: captureReasonLabels generovat z capture_reasons
   předaných routerem (T5 bod 2).
4. Ověř /ops v prohlížeči (desktop + ~375 px) — popisky důvodů stejné
   jako předtím. Screenshot.

Na konci: shrnutí, testy, screenshot, navržený commit:
test(i18n): require a translation for every unverifiable reason
```

---

## CH-6 — Dokumentace + CHANGELOG

```
Úkol CH-T6 z docs/TASKS_CITATION_HARDENING.md.

1. CHANGELOG.md [Unreleased] → ### Fixed podle T6 bodu 1 (uprav znění
   podle toho, co se skutečně implementovalo).
2. docs/ROADMAP.md #19 — body ✅ s odkazem na tenhle dokument.
3. .env.example / docs/DEPLOYMENT.md — nové PROVIDER_*_TIMEOUT_SECONDS,
   pokud tam env proměnné popisuje.
Ukaž diff PŘED zápisem.

Navržený commit: docs(docs): document citation hardening fixes
```

---

## CH-8 — Průběh „Verify citations" a ochrana proti duplicitním jobům

### DONE — commit 0ec458d

```
Úkol CH-T8 z docs/TASKS_CITATION_HARDENING.md.

1. Přečti app/routers/runs.py (verify_run_citations, run_detail),
   app/services/verification_queue.py, app/templates/runs/detail.html
   a vzor pollingu v app/templates/schedules/index.html.
2. Řekni mi co/kde/proč (§2) a jak budou vypadat stavy průběhu.
3. Implementuj podle T8 bodů 1-4; texty přes t() v en/de.
4. Testy podle T8 bodu 5, celá sada pytest.
5. Lokální ověření v prohlížeči na run 401: průběh po kliknutí, sama
   se obnovující stránka po dokončení, druhá záložka bez tlačítka,
   640/1024 px bez horizontálního posuvníku. Pozor: soudce může narazit
   na limit účtu Anthropic — job pak zůstane `deferred`, to není chyba
   kódu.

Na konci: shrnutí, testy, výsledek ověření, navržený commit:
fix(runs): show verification progress and ignore duplicate Verify citations clicks
```

---

## CH-9 — Hromadné ověření u klienta nestackuje aktivní judge joby

### DONE — commit cf2cae9

```
Úkol CH-T9 z docs/TASKS_CITATION_HARDENING.md. Předpoklad: CH-8 je
commitnutý (používá ACTIVE_JOB_STATUSES z verification_queue.py).

1. Přečti app/routers/clients.py (_bulk_verify_candidate_raw_response_ids,
   náhled i potvrzení) a testy hromadného ověření v tests/test_clients.py.
2. Řekni mi co/kde/proč (§2) — hlavně jak se vyloučení aktivního jobu
   projeví v náhledu vs. v potvrzení.
3. Implementuj podle T9 bodů 1-2.
4. Testy podle T9 bodu 3, celá sada pytest.
5. Lokální ověření: po „Verify citations" na run 401 (nebo ručním
   zařazení judge jobu) ukaž, že náhled u klienta tuhle odpověď nenabízí,
   dokud job neskončí.

Na konci: shrnutí, testy, výsledek ověření, navržený commit:
fix(clients): skip responses with a judge job in progress in bulk verify
```

---

## CH-7 — Nasazení v1.3.0 (vč. Vision), backfill Gemini citací, měření

```
Úkol CH-T7 z docs/TASKS_CITATION_HARDENING.md.

Vede mě krok za krokem podle T7 a docs/DEPLOYMENT.md kapitol 0-5.
Příkazy na serveru spouštím já — ty je připravíš a vyhodnotíš výstup.

1. Uprav SQL z T7 kroku 1 podle skutečných modelů/joinů a dej mi ho.
   Výstup ulož do TASKS dokumentu jako výchozí čísla.
2. Připomeň merge PR, bump v1.3.0, přesun [Unreleased] (vč. Vision), tag
   (dělám já).
3. Nasazení podle DEPLOYMENT.md — migrace clients.vision jde vrátit
   downgradem; worker se pouští s --scale worker=4.
3a. Ověření Vision podle docs/TASKS_CLIENT_VISION.md „Ověření po nasazení".
4. Ověření T7 krok 4.
5. Backfill T7 krok 5 — nejdřív malý vzorek, vyhodnotit, pak zbytek.
6. Druhý den T7 krok 6.
7. End-of-branch docs obou větví — citation-hardening i client-vision
   (T7 krok 7).

Navržený commit na konci:
docs(docs): record citation hardening deploy and close the branch
```
