# SignalMap — Tasks: Citation Hardening (vydání 1)

## v1.0 | Září 2026
## Branch: feature/signalmap-citation-hardening
## Task ID prefix: CH
## Cílová verze: v1.3.0 (MINOR) — společně s `feature/signalmap-client-vision`

Status: navrženo 2026-09-30 jako **vydání 1** z plánu vydání
(`docs/ROADMAP.md` „Plán vydání"). Pokrývá `docs/ROADMAP.md` #19 celé.
Devět úkolů (T8 a T9 přibyly 2026-09-30 při lokálním ověřování T1), bez migrace.

**Nasazuje se společně s Vision u klienta** (`docs/TASKS_CLIENT_VISION.md`,
#23): větev `feature/signalmap-client-vision` se smerguje do `master`
první (bez nasazení), tahle větev z ní vychází a CH-T7 nasadí obě
najednou jako **v1.3.0**. Vision je malá a runy neovlivňuje, takže oprava
citací kvůli ní čeká jen 1–2 dny.

**Goal:** ověřování citací přestane systémově ztrácet data — Gemini
citace jdou ověřit, zaseknuté volání providera neblokuje worker desítky
minut, výpadek archive.org ani jeden vadný PDF neshodí celý job, a nový
důvod neověřitelnosti bez překladu neprojde testy.

**Proč první a samostatně:** každý den bez opravy jsou další Gemini
citace, jejichž zdroj už později nemusí vypadat stejně — snímek zdroje má
hodnotu hlavně v čase vzniku odpovědi. Backfill snímků historie
(`docs/TASKS_CITATION_VERIFICATION.md` T18) **má smysl spustit až po
tomhle vydání**, jinak skončí všechny Gemini citace jako `robots`.

---

## Výchozí stav (kód k 2026-09-30, master 7807f7a)

**Gemini + robots.txt** — `app/services/source_capture.py::capture_url`
(:305): cache (:323) → `_robots_allowed(client, url)` (:327) na
**původní** URL → throttle podle domény původní URL (:330) → `_fetch`
(:196) přes `httpx.Client(follow_redirects=True, max_redirects=10)`
(`build_capture_client`, :85). Gemini citace jsou
`vertexaisearch.cloud.google.com/grounding-api-redirect/...`, jejíž
robots.txt má `Disallow: /grounding-api-redirect` → `error_reason="robots"`
u 100 % Gemini citací. Robots cílové domény se po přesměrování
nekontroluje vůbec (ani u ne-Gemini URL). `final_url` se ukládá
z `response.url` po přesměrování.

**Timeouty providerů** — žádný adaptér nepředává `timeout=`:
`google.py:194` `genai.Client(api_key=...)`, `anthropic.py:149`,
`openai.py:107`, `grok.py:102`, `perplexity.py:102`, `deepseek.py:61`
(poslední čtyři `openai.OpenAI(base_url=...)`). Platí pro `run` i `judge`.
`app/config.py` žádné timeout nastavení nemá. Výchozí timeout SDK je
~600 s (OpenAI/Anthropic) × jejich vlastní retry. Worker s `run_id` na
položce lease neuvolní (`release_expired_leases`, `queue.py:355`, design
decision 13); `reconcile_interrupted_runs` (`queue.py:376`) čeká
natvrdo 30 min a pak jen označí `error`.

**archive.org** — `ArchiveUnavailable` (`archive_lookup.py:56`) vzniká
v `_request` (:88) při timeoutu/`RequestError`/429/5xx; volá se
z `citation_verification._try_archive_fallback` (:134) uvnitř
`verify_citations_by_quote` (:276, :305) a **nikde se nechytá po
citacích** — propadne do job-level `except Exception` ve
`verification_queue.process_verification_job` (:302), který job odloží
(`_BACKOFF_MINUTES = (1, 5, 25)`) a po `_MAX_ATTEMPTS = 3` dá `error`.
Citace commitnuté před výjimkou zůstanou (commit per citace), retry je
přeskočí (`job_since`, :270), ale citace **za** tou vadnou se po třetím
pokusu už nezpracují nikdy. Důvod `archive_unavailable` je
v `UNVERIFIABLE_REASONS`, ale **žádný kód ho nezapisuje**.

**NUL byte** — `SourceText.text` (`Text`, `verification.py:111`) se
vkládá na dvou místech: `source_capture._store` (:274, sha256 na :276)
a `citation_verification._store_archive_document` (:110). Sanitizace
`\x00` neexistuje nikde. Extrakce: `app/services/source_extract.py`
(`extract_html`, `extract_pdf`).

**i18n důvodů** — `UNVERIFIABLE_REASONS` (`verification.py:94`, 13
hodnot) se překládá dvojím způsobem: `run.reason_<r>` (šablona
`partials/verification.html:52`, `t()` na chybějícím klíči vyhodí
`KeyError` → 500 z v1.2.1) a `ops.capture_reason_<r>` — ten je v
`ops/index.html:458` **natvrdo** jako JS mapa `captureReasonLabels`
s fallbackem na surový kód. Parita klíčů en/de se hlídá už při importu
(`app/i18n/__init__.py:28`), ale pokrytí „každý důvod má klíč" ne.
Vzor pro test: `tests/test_version.py` (čistý text, bez DB).

---

## Design decisions

1. **Přesměrování se řeší ručně, hop po hopu, robots.txt u každého
   hostitele.** `follow_redirects=False` a vlastní smyčka do
   `MAX_REDIRECTS`; před každým požadavkem `_robots_allowed` pro daný
   hop. Stejně to dělají slušní crawleři (Googlebot kontroluje robots.txt
   cílového hostitele po přesměrování). Oproti dnešku je to **přísnější**
   pro běžné weby (dnes se cíl nekontroluje vůbec) a opravuje Gemini.
2. **Známé přesměrovací brány jsou výjimka z robots.txt, ale jen pro
   jeden hop bez těla.** Seznam `_REDIRECT_GATEWAYS` (host + prefix cesty;
   dnes jen `vertexaisearch.cloud.google.com/grounding-api-redirect/`).
   Na bránu se pošle jen požadavek pro hlavičku `Location` (tělo se
   nečte), pak normální flow na cíl. Zdůvodnění do komentáře: robots.txt
   brány brání indexaci brány, ne rozkliknutí konkrétního odkazu, který
   provider dal uživateli jako citaci — appka tady dělá přesně to, co
   by udělal prohlížeč uživatele. Jde o URL vzor, ne seznam providerů
   (§3 `AI_INSTRUCTIONS.md` se netýká).
3. **Throttle a cache podle cílové domény.** Throttle se počítá pro
   hostitele každého hopu (ne jen původní URL — jinak by všechny Gemini
   citace sdílely jeden slot `vertexaisearch...`). Cache zůstává podle
   `requested_url` (tak se dnes hledá; Gemini odkazy jsou unikátní).
4. **Timeout providera jako nastavení, ne konstanta.**
   `PROVIDER_TIMEOUT_SECONDS` (výchozí **120**) a
   `PROVIDER_JUDGE_TIMEOUT_SECONDS` (výchozí **60**) v `app/config.py`.
   Naměřené maximum runu je 73 s (gpt-5.6-terra, `docs/TASKS_WORKER_THROUGHPUT.md`),
   120 s dává rezervu. Předává se při konstrukci klienta SDK. **Pozor na
   jednotky:** `google-genai` bere `HttpOptions(timeout=...)`
   v **milisekundách**. Horní mez jednoho volání = timeout × (retry SDK + 1)
   musí zůstat pod lease (15 min) i pod reconcile (30 min) — `max_retries`
   SDK nastavit explicitně na **1** (dnes default 2 u OpenAI/Anthropic),
   opakování řeší worker vlastním backoffem; zapsat do komentáře.
5. **Timeout je retryable.** Timeout výjimky SDK nemají `status_code` →
   `_is_retryable_error` (`worker.py:93`) je už dnes vrací jako retryable.
   Ověřit testem, neměnit.
6. **Výpadek archive.org se izoluje na citaci, ale retry se zachová.**
   `ArchiveUnavailable` se chytí per citace; citace bez verdiktu se
   poznamená a smyčka pokračuje. Po smyčce: pokud nějaká citace skončila
   na `ArchiveUnavailable` a job má ještě pokusy, job se odloží (dnešní
   backoff) — retry díky `job_since` zpracuje **jen** tyhle citace. Na
   posledním pokusu se pro ně zapíše verdikt `unverifiable` s důvodem
   `archive_unavailable` (konečně se použije) a job skončí `done`.
7. **NUL se odstraňuje na jednom místě, před hashem.** Helper
   `sanitize_extracted_text(text)` v `source_extract.py` odstraní **jen**
   `\x00` (PostgreSQL odmítá pouze NUL; ostatní řídicí znaky jsou legální
   a mohou být pro porovnání citátů potřeba). Volá se na **vstupu**
   `extract_html` a u každé stránky v `extract_pdf`, tedy dřív, než se
   spočítají `locations`/`page_starts` — odstranění až z hotového textu
   by všechny offsety za NUL posunulo. Navíc jako pojistka těsně před
   oběma inserty, takže sha256 i uložený text jsou konzistentní. Test i na
   úrovni `_store`, že NUL projít nemůže.
8. **Test pokrytí překladů je obecný.** Pro každou hodnotu
   `UNVERIFIABLE_REASONS` existuje `run.reason_<r>` i `ops.capture_reason_<r>`
   (en i de); pro každou hodnotu `VERDICTS` existuje klíč, který šablony
   používají (ověřit v `verification_display.py`). JS mapa v `ops/index.html`
   se místo ručního výčtu **vygeneruje** v Jinja z `UNVERIFIABLE_REASONS`
   (předaných z routeru) — test pak hlídá jediný zdroj.
9. **Žádná migrace, žádná změna historických řádků** (NFR-6). Staré
   `robots` záznamy u Gemini zůstanou; nové ověření vznikne backfillem
   (T7) jako nové řádky.
10. **Verze MINOR (v1.3.0)** — tahle větev sama by byla PATCH (`fix`),
    ale nasazuje se spolu s Vision (nová funkce + migrace, jen přidává;
    `docs/DEPLOYMENT.md` §0). Nová env proměnná má default → nasazení je
    obyčejný deploy.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Přesměrování hop po hopu + robots cíle (Gemini) | ✅ |
| T2 | Timeout na voláních providerů | ✅ |
| T3 | Výpadek archive.org izolovaný na citaci | ✅ |
| T4 | Sanitizace NUL v extrahovaném textu | ✅ |
| T5 | Test pokrytí překladů důvodů a verdiktů | ✅ |
| T6 | Dokumentace + CHANGELOG | ✅ |
| T7 | Nasazení v1.3.0 (vč. Vision), backfill Gemini citací, měření | ⏳ |
| T8 | Průběh „Verify citations“ a ochrana proti duplicitním jobům (nasazuje se s T7) | ✅ |
| T9 | Hromadné ověření u klienta nestackuje aktivní judge joby (před backfillem v T7) | ✅ |

**Pořadí provedení** (ID úkolů jsou stabilní a nemění se, pořadí se od nich
liší, protože T8 a T9 přibyly dodatečně):

`T1 ✅ → T8 ✅ → T2 → T3 → T4 → T5 → T9 → T6 → T7`

T2–T5 jsou na sobě nezávislé. T9 musí být hotový před backfillem v T7
(krok 5), T6 dokumentuje až hotové opravy a T7 (nasazení) je vždy poslední.

---

## T1 — Přesměrování hop po hopu + robots cíle

**Target:** `app/services/source_capture.py`, `tests/test_source_capture.py`

1. `build_capture_client`: `follow_redirects=False`.
2. Nová funkce `_resolve_and_fetch(db, client, url, *, sleep)` —
   smyčka max `MAX_REDIRECTS`: pro každý hop (a) je-li to brána
   z `_REDIRECT_GATEWAYS` → jen přečíst `Location` (stream, tělo
   nečíst), bez robots; (b) jinak `_robots_allowed` → při zákazu
   `error_reason="robots"` s `final_url` = hop, kde to skončilo;
   `_throttle_domain` pro hostitele hopu; `_fetch`; 3xx s `Location` →
   další hop (relativní `Location` přes `urljoin`); jinak konec.
   Překročení limitu → stejné chování jako dnešní `TooManyRedirects`.
3. `capture_url` volá novou funkci místo `_robots_allowed` + `_fetch`;
   pořadí v docstringu (:315) aktualizovat.
4. Komentář u `_REDIRECT_GATEWAYS` podle design decision 2.
5. Testy (httpx `MockTransport`):
   - Gemini brána s `Disallow` → cíl povolený → uloženo `method=live`,
     `final_url` = cíl, `requested_url` = brána;
   - Gemini brána → cíl s `Disallow` → `robots`;
   - běžný web 301 → jiná doména s `Disallow` → `robots` (nové chování);
   - relativní `Location`; smyčka přesměrování → limit;
   - throttle se volá pro hostitele cíle, ne brány.

**Done when:** testy + celá sada `pytest` projdou; lokálně jedna reálná
Gemini citace (Skoda Auto, prompt 54, gemini-3.1-flash-lite) přes
tlačítko „Verify citations" skončí jinak než `robots`.

**Implementováno (upřesnění):** `final_url` je poslední dosažený hop,
kdykoli se liší od `requested_url` — i při `robots`, `timeout` a `too_large`
uprostřed řetězce (u zákazu hned na prvním hopu zůstává prázdné). Brána
bez přesměrování (404/200) uloží svůj stavový kód s prázdným tělem. Brána
se neškrtí, škrtí se až hostitel cíle. `robots.txt` se stahuje s
přesměrováním (`http→https`, `www`), jinak by prázdné 3xx tělo znamenalo
„vše povoleno“; přesměrování na jiné schéma než `http`/`https` končí
chybou. Mimo rozsah zůstává ochrana proti přesměrování na interní adresy
(SSRF) — kandidát do `docs/ROADMAP.md`.

**Expected commit:** `fix(adapters): resolve grounding redirects before checking robots.txt`

---

## T2 — Timeout na voláních providerů

**Target:** `app/config.py`, `app/adapters/{google,anthropic,openai,grok,perplexity,deepseek}.py`,
`.env.example`, `docker-compose.yaml` (env u `app` i `worker`, s defaultem),
`tests/test_adapters.py`, `tests/test_worker_queue.py`

1. `provider_timeout_seconds: float = 120` (`Field(ge=10)`, viz Done when),
   `provider_judge_timeout_seconds: float = 60` (`gt=0`) + komentář
   (design decision 4).
2. Každý adaptér: timeout a `max_retries=1` při konstrukci klienta
   (OpenAI-kompatibilní: `openai.OpenAI(..., timeout=, max_retries=)`;
   Anthropic obdobně; Google `http_options=types.HttpOptions(timeout=ms)`
   — ověřit v nainstalované verzi `google-genai`, jestli podporuje i retry
   nastavení; když ne, zapsat). Pro `judge` per-call override
   (`with_options(timeout=...)` u OpenAI/Anthropic; u Google per-request
   `http_options` v `config`, pokud to verze umí — jinak druhý klient).
3. Testy: každý adaptér postaví klienta s očekávaným timeoutem (inspekce
   atributu klienta nebo mock konstruktoru); `_is_retryable_error` vrací
   `True` pro `openai.APITimeoutError`, `anthropic.APITimeoutError` a
   timeout z `google-genai`.
4. Komentář u `reconcile_interrupted_runs`: 30 min je teď s rezervou nad
   timeout × (retry + 1); neměnit.

**Done when:** testy projdou; chování SDK ověřeno proti lokálnímu serveru,
který nikdy neodpoví (timeout i jediný retry u openai/anthropic, jediný
pokus u google, všechno retryable); nastavení vráceno. ✅ 2026-10-01.
**Původní krok (ruční Gemini run s `PROVIDER_TIMEOUT_SECONDS=1`) nejde
provést:** `google-genai` posílá timeout serveru jako deadline a Gemini API
pod 10 s odmítne s HTTP 400 (terminální, worker ji neopakuje) — proto má
`provider_timeout_seconds` `ge=10` a appka se s menší hodnotou nespustí.

**Implementováno (upřesnění):** `judge()` implementuje jen Anthropic
adaptér, ostatních pět vyhazuje `NotImplementedError` — judge timeout je
per-request `timeout=` u `messages.create`, žádný druhý klient. Google
nemá `retry_options` (SDK bez nich dělá jediný pokus), `max_retries=1`
dostaly jen openai-kompatibilní adaptéry a Anthropic. HTTP 408 se ve
workeru dál nepovažuje za retryable (decision 5 se nemění).

**Expected commit:** `fix(adapters): bound every provider call with an explicit timeout`

---

## T3 — Výpadek archive.org izolovaný na citaci

**Target:** `app/services/citation_verification.py`,
`app/services/verification_queue.py`, `tests/test_citation_verification.py`,
`tests/test_verification_queue.py`

1. `verify_citations_by_quote`: `try/except ArchiveUnavailable` kolem
   archivní zálohy **per citace** (u obou spouštěčů: živé 404/410 i živé
   `not_found`); funkce vrací `QuoteCheckOutcome(complete, archive_deferred)`
   místo `bool`.
2. Nový parametr `final_attempt: bool` — při `True` se pro odložené citace
   uloží `CitationVerification(check_type='quote', verdict='unverifiable',
   reason='archive_unavailable', source_document_id=<živý dokument>)`
   (stejný tvar jako ostatní neověřitelné řádky téhle cesty; živé
   404 / `not_found` se do verdiktu nekopíruje, zůstává na dokumentu).
3. `process_verification_job`: když jsou odložené citace a
   `job.attempts < _MAX_ATTEMPTS` → job `deferred` s dnešním backoffem
   (bez výjimky, bez `logger.error` — je to očekávaný stav, `warning`);
   jinak `done`.
4. Testy: 3 citace, prostřední hodí `ArchiveUnavailable` → první a třetí
   mají verdikt, job `deferred`; retry zpracuje jen prostřední; na
   posledním pokusu vznikne `unverifiable/archive_unavailable` a job
   je `done`. Stávající testy job-level chyb beze změny.

**Done when:** testy projdou.

**Expected commit:** `fix(runs): keep one archive.org failure from failing the whole verification job`

---

## T4 — Sanitizace NUL v extrahovaném textu

**Target:** `app/services/source_extract.py`, `app/services/source_capture.py`,
`app/services/citation_verification.py`, `tests/test_source_extract.py`

1. `sanitize_extracted_text` podle design decision 7, volaná na vstupu
   `extract_html` a u každé stránky v `extract_pdf` — před výpočtem offsetů.
2. Pojistka v obou insertech (`_store`, `_store_archive_document`):
   `assert "\x00" not in text` není vhodné pro produkci → místo toho
   znovu zavolat helper (idempotentní, levné) — jeden řádek, komentář proč.
3. Testy: PDF/HTML text s `\x00` → uložený text bez NUL, sha256 odpovídá
   uloženému textu; insert do DB projde (integrační test s `db_session`).

**Done when:** testy projdou.

**Expected commit:** `fix(runs): strip NUL bytes from extracted source text`

---

## T5 — Test pokrytí překladů důvodů a verdiktů

**Target:** `tests/test_i18n_coverage.py` (nový), `app/templates/ops/index.html`,
`app/routers/ops_dashboard.py`

1. Test podle design decision 8: `UNVERIFIABLE_REASONS` × {`run.reason_`,
   `ops.capture_reason_`} × {en, de}; `VERDICTS` × klíče, které šablony
   reálně skládají (najít `grep -rn "verdict_" app/templates app/services`);
   srozumitelná chybová hláška s chybějícím klíčem.
2. `ops/index.html`: mapu `captureReasonLabels` generovat v Jinja smyčkou
   přes `capture_reasons` (předané z routeru), ne ručně vypsanou.
3. Test ověří, že test sám selže: dočasně přidat fiktivní důvod do kopie
   n-tice přes `monkeypatch` a zkontrolovat, že funkce kontroly vrátí
   chybějící klíč (test testu, jeden řádek).

**Done when:** test projde; `/ops` v prohlížeči ukazuje stejné popisky
jako před změnou.

**Implementováno (upřesnění):** seznam hodnot pro `/ops` je
`CAPTURE_REASONS` (`ops_dashboard.py`) = `success`, `not_captured` +
`UNVERIFIABLE_REASONS` (stránka zobrazuje i dvě syntetické hodnoty). Test
navíc kryje `HUMAN_VERDICTS` (`verification.human_verdict_<v>`); „test
testu“ předává kontrolní funkci seznam s fiktivním důvodem místo
`monkeypatch`. Ostatní složené klíče (`account.role_…`, `queue_status_…`
aj.) zůstávají mimo.

**Expected commit:** `test(i18n): require a translation for every unverifiable reason`

---

## T6 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/ROADMAP.md` #19, `docs/DEPLOYMENT.md`
(jen pokud popisuje env proměnné), `.env.example` (z T2)

1. `CHANGELOG.md` → `## [Unreleased]` → `### Fixed`:
   - Gemini citations can be verified again: grounding redirect links are
     resolved before robots.txt is checked, and robots.txt is now checked
     for the page the link actually leads to.
   - A hanging AI provider call no longer blocks a worker for up to half
     an hour — every call has a timeout (`PROVIDER_TIMEOUT_SECONDS`).
   - An archive.org outage no longer stops verification of a run's other
     citations.
   - A source document containing NUL bytes no longer fails verification.
2. `docs/ROADMAP.md` #19: každý bod → ✅ s odkazem na tenhle dokument.

**Done when:** diff ukázaný uživateli a odsouhlasený.

**Expected commit:** `docs(docs): document citation hardening fixes`

---

## T7 — Nasazení v1.3.0 (vč. Vision), backfill Gemini citací, měření

**Target:** produkce; end-of-branch docs

1. **Výchozí čísla před nasazením** (produkce, `psql`):
   ```sql
   select p.code provider, sd.error_reason, count(*)
   from source_documents sd
   join citations c on c.source_url = sd.requested_url
   join raw_responses rr on rr.id = c.raw_response_id
   join runs r on r.id = rr.run_id
   join ai_models m on m.id = r.model_id join providers p on p.id = m.provider_id
   where sd.fetched_at > now() - interval '7 days'
   group by 1, 2 order by 1, 3 desc;
   ```
   (Sloupce ověřit proti modelům — join citace → dokument jde přes URL,
   viz `verification_display.latest_source_document`.)
2. Merge PR, bump **v1.3.0**, přesun `[Unreleased]`, tag — dělá uživatel.
   `[Unreleased]` obsahuje i položky Vision (`docs/TASKS_CLIENT_VISION.md` VI-T4).
3. Nasazení podle `docs/DEPLOYMENT.md` 1–5; **worker se pouští
   `--scale worker=4`** (do vydání 2 platí ruční scale). Jediná migrace
   je `clients.vision` (jen `ADD COLUMN`) → rollback zálohou **i**
   downgradem (`DEPLOYMENT.md` 1.3).
3a. Ověření Vision podle `docs/TASKS_CLIENT_VISION.md` „Ověření po
   nasazení" (detail klienta, formulář, dashboard).
4. Ověření: ruční „Verify citations" u jednoho čerstvého Gemini runu
   (Skoda Auto, prompt 54, gemini-3.1-flash-lite) → verdikty jiné než
   `robots`; `final_url` míří na cílový web.
5. **Backfill Gemini citací** (`python -m app.cli.backfill_sources`,
   `docs/TASKS_CITATION_VERIFICATION.md` T18) — nejdřív
   `--client <Knauf> --since <7 dní>`, změřit, pak zbytek. Sledovat
   podíl `http_404` u starých Gemini odkazů (brána nemá zdokumentovanou
   životnost, `app/cli/backfill_sources.py:16`) — výsledek zapsat sem.
6. Den po nasazení: stejný SQL jako v kroku 1 → podíl `robots` u Gemini
   ≈ 0; žádný job `error` s `ArchiveUnavailable`/`NUL` v `verification_jobs.error`;
   žádný `Run` s `worker interrupted` (reconcile).
7. End-of-branch docs: `## Status: ...` v `*_CITATION_HARDENING.md`
   i `*_CLIENT_VISION.md`, dva řádky v `docs/00_INDEX.md`, `docs/ROADMAP.md` „Plán vydání" → vydání 1 ✅.

**Done when:** kroky 4–6 ověřené a uživatel potvrdil.

**Expected commit:** `docs(docs): record citation hardening deploy and close the branch`

---

## T8 — Průběh „Verify citations“ a ochrana proti duplicitním jobům

Přibylo 2026-09-30 při lokálním ověřování T1: tlačítko „Verify citations“
po kliknutí nic neukázalo a server ho nechránil — každý klik zařadil další
placený `judge` job (nad runem 401 jich vznikly tři). Nejde o opravu citací
jako T1–T5, ale o stejné riziko zbytečných nákladů, a mění jen UI a router,
takže patří do téhož vydání. **Musí být hotové před T7.**

**Target:** `app/services/verification_queue.py`, `app/routers/runs.py`,
`app/templates/runs/detail.html`, `app/templates/runs/verify_status.html` (nový),
`app/i18n/{en,de}.json`, `tests/test_runs.py`

1. `verification_queue.py`: `ACTIVE_JOB_STATUSES = ("queued", "leased", "deferred")`
   a `latest_judge_job(db, raw_response_id)`. Jen `judge` — `capture` má
   vlastní stav „čeká na capture“ (`verification_display.is_capture_pending`).
2. `verify_run_citations`: když poslední `judge` job je aktivní, nový se
   nezařadí (odpověď je dál přesměrování, stránka ukáže průběh). Podmínku
   způsobilosti sdílí s tlačítkem (`_can_verify_citations`).
3. Nová route `GET /runs/{id}/verify-status` (HTML fragment): tlačítko, nebo
   průběh jobu; s `?poll=true` a dokončeným jobem vrací `HX-Refresh: true`,
   takže se stránka jednou sama obnoví a ukáže nové verdikty. Fragment se
   při aktivním jobu sám dotazuje každé 4 s (vzor `schedules/index.html`).
4. `runs/verify_status.html` + pět klíčů `run.verify_status_*` (en/de). Stavy:
   čeká / běží / opakuje se po chybě / selhalo (+ tlačítko) / „Last verified“
   (+ tlačítko). Viewer vidí průběh, ale ne tlačítko. Čas se vykresluje přes
   makro `local_time` mimo `str.format` (jinak by se escapoval).
5. Testy: druhý POST při `queued`/`leased`/`deferred` nevytvoří job, po
   `done`/`error` ano; fragment pro každý stav, `HX-Refresh` po dokončení,
   409 pro run bez citací, viewer bez tlačítka.

**Done when:** testy + celá sada `pytest` projdou; v prohlížeči (run 401) se
průběh objeví po kliknutí, po dokončení se stránka sama obnoví, druhá záložka
tlačítko nevidí; 640/1024 px bez horizontálního posuvníku. ✅ ověřeno
2026-09-30.

**Vědomě mimo rozsah:** hromadné ověření na detailu klienta
(`clients.py::verify_retroactively_confirm`) dál zařazuje joby bez téhle
kontroly — řeší T9. Během ověřování narazil soudce na limit
útraty účtu Anthropic (job zůstal `deferred` a po navýšení limitu doběhl) —
kategorizace takových chyb je vydání 2 (#21).

**Expected commit:** `fix(runs): show verification progress and ignore duplicate Verify citations clicks`

---

## T9 — Hromadné ověření u klienta nestackuje aktivní judge joby

Přibylo 2026-09-30 jako dotažení T8. Hromadné zpětné ověření na detailu
klienta (`/clients/{id}`: rozsah dat → náhled → potvrzení) vybírá odpovědi
přes `clients.py::_bulk_verify_candidate_raw_response_ids`, která vynechá
jen odpovědi **s už zapsaným** LLM verdiktem. Odpověď s `judge` jobem, který
je teprve ve frontě nebo běží, verdikt ještě nemá — druhé spuštění nebo klik
na „Verify citations“ během dávky ji proto zařadí znovu a za stejné citace
se zaplatí dvakrát. U malé dávky jde o centy (run 401 ≈ 0,03 USD), po
backfillu v T7 (desítky až stovky odpovědí) o jednotky dolarů a dlouhou
frontu. **Musí být hotové před T7 krokem 5 (backfill).**

**Target:** `app/routers/clients.py`, `tests/test_clients.py`

1. `_bulk_verify_candidate_raw_response_ids`: vyloučit odpovědi, které mají
   `judge` job ve stavu z `ACTIVE_JOB_STATUSES` (`verification_queue.py`,
   z T8) — stejná definice „běží“ jako u tlačítka na run detailu. Náhled
   i potvrzení používají tutéž funkci, takže se počty nerozejdou. Docstring
   doplnit o tohle pravidlo.
2. Žádná změna šablon ani překladů. Potvrzení dál jen zařadí méně jobů než
   náhled, nikdy více (beze změny oproti dnešku).
3. Testy: odpověď s `judge` jobem `queued`/`leased`/`deferred` mimo náhled
   i potvrzení; po `done`/`error` bez verdiktu znovu způsobilá; druhé
   potvrzení hned po prvním nezařadí nic.

**Done when:** testy + celá sada `pytest` projdou; lokálně po „Verify
citations“ na runu z fixture (Skoda Auto, prompt 54) náhled u klienta tuhle
odpověď nenabízí, dokud job neskončí.

**Ověřeno 2026-10-01:** testy + celá sada; lokálně u klienta Kings&Queens
náhled po simulovaném běžícím jobu ukázal 1 odpověď / 2 citace místo
2 / 38. Známé omezení: kontrola před zápisem (check-then-insert) bez
unikátního omezení v DB — dvě potvrzení ve stejné milisekundě by teoreticky
obě prošla.

**Expected commit:** `fix(clients): skip responses with a judge job in progress in bulk verify`

---

## Co tohle vydání vědomě nedělá

- **Žádná kategorizace chyb providerů / billing** — vydání 2 (#21).
- **Nemění 30min `reconcile_interrupted_runs`** ani lease — timeout to
  řeší u zdroje.
- **Nepřepisuje staré `robots` záznamy** — nové ověření je nový řádek.
- **Nemění cache klíč** snímků (`requested_url`).
