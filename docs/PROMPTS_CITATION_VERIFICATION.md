# SignalMap — Claude Code Session Prompts: Citation Verification

## Status: 🔜 Merged (PR #24, 2026-09-30) — production deploy and verification (T18) pending

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-citation-verification (z aktuálního master)
## 2. Osmnáct promptů (CV-1 až CV-18), pořadí vynucené etapami:
##    A (CV-1–2) → B (CV-3–6) → C (CV-7–9) → D (CV-10) → E (CV-11–15)
##    → F (CV-16) → docs + nasazení (CV-17–18).
##    Po CV-6 je checkpoint: uživatel rozhodne, jestli A+B zmergovat a
##    nasadit hned (backfill snímků historie je časově kritický).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 5. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_CITATION_VERIFICATION.md přepni řádek daného task ID
##       v tabulce "Task Index" z ⏳ na ✅.
## 6. Kompletní zdůvodnění vč. měření, prototypů a design decisions 1–30:
##    docs/TASKS_CITATION_VERIFICATION.md — přečti si ho celý před CV-1.
## 7. Lokální testy běží v Dockeru — před CV-1 zapni Docker Desktop.
## 8. Migrace (CV-3, CV-7, CV-13, CV-14) jsou schema flagy: návrh schématu
##    ukázat a nechat schválit PŘED napsáním migrace.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-citation-verification.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_CITATION_VERIFICATION.md — CELÉ, hlavně „Výchozí stav",
   „Zjištění, která mění návrh" a design decisions 1–30
3. app/models/run.py (Citation, RawResponse), app/adapters/base.py,
   app/services/run_execution.py, app/worker.py, app/services/queue.py
   (claim_next), app/templates/runs/detail.html

KONTEXT: Každé tvrzení v „Rendered answer" má být propojené se zdrojem a
ověřené proti skutečnému obsahu stránky. Dva kroky: zajištění (stáhnout,
extrahovat, uložit snímek — vždy, zdarma, nesnese odklad) a posouzení
(doslovná kontrola citátu — vždy; LLM u parafrází — podle přepínače
u klienta). Provideři vracejí různé poloviny vazby: Anthropic citát
≤150 zn. bez offsetů, OpenAI jen značku odkazu, Gemini segment +
přesměrovací odkaz Googlu, Perplexity Markdown úryvek bez vazby na
věty, xAI jen seznam URL, DeepSeek nic.

KRITICKÉ:
- citations a historické runy se NEPŘEPISUJÍ (NFR-6). Každé stažení =
  nový source_documents řádek, každé ověření = nový
  citation_verifications řádek s verifier_version.
- Stahování a LLM NIKDY v requestu — jen ve workeru přes
  verification_jobs. Runy z run_queue mají přednost.
- Ochranu proti botům NIKDY neobcházet (žádný headless na challenge,
  žádné CAPTCHA). HTTP 200 se stránkou „Verifying your browser"
  (Radware) je bot_challenge, ne obsah.
- Poctivý User-Agent SignalMapVerifier/…, respektovat robots.txt.
- Normalizace mění formu, NIKDY slova ani čísla. Podobnost ≥ 0,9 jen
  s pravidlem čísel (všechna čísla beze změny).
- Gemini: zobrazovat PŮVODNÍ odkaz, rozbalenou adresu jen jako text
  (podmínky Google).
- LLM jen přes adaptér (judge() bez nástrojů), nikdy SDK ze služby.
- Testy BEZ sítě (httpx.MockTransport, fixtures). Žádné placené volání
  v testech. Lokální ruční ověření: fixture Skoda Auto, prompt 54,
  gemini-3.1-flash-lite; runy 108/311/422/290/291/288 z dev DB.
- UI text jen přes t() (DE/EN), backend anglicky.
- Bump verze a tag dělá uživatel (AI_INSTRUCTIONS.md §4).
```

---
---

## CV-1 — Odvození tvrzení podle providera

Viz `docs/TASKS_CITATION_VERIFICATION.md` T1 a design decisions 4–5.
Shrnutí: čistá funkce `derive_claim` v `app/services/claims.py` —
Anthropic blok z `raw_payload`, OpenAI text před značkou, Gemini segment
rozšířený na větu.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T1) a počkej na potvrzení.

**Kritické:**
- Nic se neukládá do DB (design decision 5).
- Anthropic: když pořadí citací a bloků nesedí (kontrola `url`), vrátit
  `None` a zalogovat — nehádat.
- Offsety: OpenAI znaky, Gemini bajty → Gemini hledat jako text.
- Fixtures zkopírovat z dev DB do testů (runy 108, 311, 422), testy
  nesmí číst DB s produkčními daty.

**Po dokončení:**
1. Nové testy projdou; pak celá sada:
   ```bash
   COPYFILE_DISABLE=1 tar -cf - tests requirements-dev.txt | docker compose run --rm --no-deps -T app sh -c 'tar -xf - && pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -p no:cacheprovider -q'
   ```
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): derive the cited claim per provider
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-2 — Tvrzení v detailu runu a v exportu

Viz T2. Shrnutí: „Cited claim“ z `derive_claim`; export dostane
`claim_text` + `claim_method` vedle ponechaného `cited_answer_span`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Existující sloupce exportu neměnit ani nepřejmenovávat.
- Nové texty DE/EN přes `t()`.
- UI ověřit v prohlížeči na ~640 / ~1024 / desktop (runy 108, 311, 422).

**Po dokončení:**
1. Testy + celá sada `pytest` (příkaz z CV-1).
2. Implementation summary + validation checklist + navrhni commit
   message (nespouštěj git)

**Expected commit:**
```
fix(runs): show the real cited claim instead of the link marker
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-3 — Migrace 0036: zdroje a fronta úloh

Viz T3. **Schema flag:** nejdřív ukaž návrh tabulek `source_texts`,
`source_documents`, `verification_jobs` (sloupce, typy, CHECK, indexy)
a počkej na schválení. Teprve pak migrace a modely.

**Kritické:**
- Číslo migrace ověř podle `alembic/versions/` (poslední je 0035).
- `downgrade` musí fungovat.
- Žádná datová migrace.

**Po dokončení:**
1. `alembic upgrade head` + `downgrade -1` + `upgrade head` lokálně.
2. Celá sada `pytest`.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): add source snapshot and verification job tables
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-4 — Služba pro stažení a extrakci zdroje

Viz T4 a design decisions 6–13. Shrnutí: `capture_url` (cache 24 h,
robots, poctivý UA, limity, redirecty, detekce challenge vč. 200
interstitial, PDF přes `pypdf`), extraktor HTML s umístěním (nadpisy,
sbalené sekce), deduplikace textu podle sha256.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Extraktor bere i skrytý (sbalený) text — citáty z něj prokazatelně
  pocházejí. Žádná `trafilatura` (design decision 10).
- Detekce Radware/Cloudflare/Incapsula/Akamai i při HTTP 200.
- Testy výhradně přes `httpx.MockTransport` + fixtures v
  `tests/fixtures/sources/`.
- `requirements.txt`: pin `httpx` a `pypdf` (verze shodné s
  `requirements-dev.txt`, kde už jsou).
- Měření UA (T4 bod 6) jen lokálně, mimo testy; výsledek do TASKS
  dokumentu. Rozdíl > 10 p. b. → zastav se a nech rozhodnout uživatele.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): capture and extract cited source pages
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-5 — Fronta úloh a zpracování ve workeru

Viz T5. Shrnutí: `enqueue_capture` po úspěšném runu (best effort),
`claim_next_job` se SKIP LOCKED a leasem, worker bere úlohy ověření jen
když `run_queue` nic nevrátí.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Chyba při zařazení úlohy nesmí shodit run (stejně jako analýzy
  v `execute_run`).
- Runy mají přednost; ověřování nesmí prodloužit dávku Knaufu.
- Pokud už je smergovaná větev worker-throughput (WT), respektovat její
  smyčku (`run_iteration`) a heartbeat vlákno — nerozbít je.
- 429/5xx → `deferred` s backoffem, ne trvalá chyba.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Lokálně jeden run fixture → během minuty snímky jeho zdrojů v DB
   (`select count(*) from source_documents`).
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): queue source capture after each run
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-6 — Backfill snímků historie

Viz T6. Shrnutí: `python -m app.cli.backfill_sources [--client] [--since]
[--dry-run]`, idempotentní, od nejstarších, Gemini první.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- CLI jen zařazuje úlohy, stahuje worker (stejné limity na doménu).
- `--dry-run` nic nezapisuje.

**Po dokončení:**
1. Test idempotence + celá sada `pytest`.
2. Lokálně `--dry-run` nad dev DB (čekáno ~3 000 URL), pak ostrý běh
   pro jednoho klienta.
3. Implementation summary + navrhni commit message (nespouštěj git)
4. **Checkpoint:** zeptej se uživatele, jestli A+B zmergovat a nasadit
   hned, nebo pokračovat etapou C na téže větvi.

**Expected commit:**
```
feat(runs): add a backfill command for source snapshots
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-7 — Migrace 0037: `citation_verifications`

Viz T7. **Schema flag:** nejdřív ukaž návrh tabulky a číselníku verdiktů
(design decision 28) a počkej na schválení.

**Kritické:**
- Tabulka je append-only; žádný UPDATE existujících řádků v kódu.
- `verdict` a `reason` jako CHECK constraint, stejný vzor jako
  `domain_classifications`.

**Po dokončení:**
1. Upgrade/downgrade lokálně, celá sada `pytest`.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): add the citation verification table
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-8 — Ověření citátu

Viz T8 a design decisions 15–18. Shrnutí: normalizace s mapou offsetů,
kusy (`...`, `…`, `·`; Perplexity řádky, `|`, Markdown), podobnost ≥ 0,9
s **pravidlem čísel**, umístění. Anthropic `cited_text`, Perplexity
`snippet`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Negativní test: „150 bis 400 Wohnungen“ vs. „50 Wohnungen“ nesmí
  projít.
- Fixtures z prototypu: „Techno- logien“ (PDF), „·“, `&amp;`, Markdown
  ADAC, akordeon „Bauwesen“.
- Výsledek nad runy 108 a 290 lokálně musí odpovídat prototypu
  (108: 11/2/1, 290: 9/1/5) — odchylku vysvětli, nepřizpůsobuj test.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): verify cited quotes against captured sources
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-9 — UI: souhrn, zvýraznění tvrzení, panel s důkazem

Viz T9 a prototypy (odkazy v hlavičce TASKS dokumentu). Shrnutí: karta
se souhrnem a filtrem, podtržená tvrzení s čísly citací, panel
„Evidence“, verdikty v seznamu citací.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Jinja + malý vanilla JS, žádný Vue ostrůvek.
- Mobil: panel pod textem; ověřit ~640 / ~1024 / desktop.
- Gemini odkaz původní (design decision 6).
- Sbalená sekce: upozornění, že ji prohlížeč sám neotevře.
- Všechny texty přes `t()`, DE i EN.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Ruční průchod runy 108, 290, 291, 288 v prohlížeči na třech šířkách.
3. Implementation summary + validation checklist + navrhni commit
   message (nespouštěj git)

**Expected commit:**
```
feat(runs): show citation verification on the run detail
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-10 — Záloha přes archive.org

Viz T10 a design decision 19. Shrnutí: Wayback CDX `closest` k datu
runu, jen při 404/410/přesměrování jinam/nenalezeném citátu; 429/5xx →
odložit a zopakovat.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- 1 požadavek / 3 s na archive.org, timeout 60 s.
- „archive nedostupný“ ≠ „citát neexistuje“.
- Testy přes `MockTransport`.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Ruční ověření na karriere-familienunternehmen.de (snímek 2026-03-03).
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): fall back to archived snapshots for changed pages
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-11 — Adaptér: `judge()` bez nástrojů

Viz T11 a design decision 20. Shrnutí: nová metoda adaptéru bez web
search, teplota 0; nejdřív Anthropic; FakeAdapter pro testy.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- `run()` se nemění.
- Ostatní adaptéry: `NotImplementedError` s jasnou zprávou.
- Žádné placené volání v testech.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(adapters): add a tool-free judge call
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-12 — LLM posouzení

Viz T12 a design decisions 21–24. Shrnutí: BM25 top-5 pasáží (≤ ~6 000
znaků), prompt z T12, verdikt + zdůvodnění + věta, věta se dohledá na
snímku, cena do `citation_verifications`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Ne ořezaná stránka, ale vybrané pasáže (zjištění 6).
- Odolné parsování (zjištění 7) — test s neescapovanými uvozovkami.
- Věta nenalezena na snímku → `needs_review = true`.
- Lokální ověření nad runy 311 a 422 stojí peníze (~0,25 USD) — před
  spuštěním se zeptej uživatele.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Po souhlasu uživatele lokální běh nad 311 a 422, porovnání
   s prototypem (311: 3/3, 422: 7/14/5), skutečná cena.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): judge paraphrased claims against source passages
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-13 — Přepínač u klienta, ruční a hromadné ověření, rozpočet

Viz T13 a design decisions 25–26. **Schema flag** pro
`clients.auto_verify_citations` — ukázat a počkat na schválení.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Výchozí vypnuto; přepínač řídí jen LLM, ne capture a doslovnou
  kontrolu.
- Tlačítko „Ověřit citace“: HTMX POST + `HX-Redirect` podle vzoru
  `trigger_run` (AI_INSTRUCTIONS.md §5).
- Hromadné ověření: náhled s počtem a odhadem ceny PŘED zařazením.
- Cena ověření v `client_month_to_date_spend` a v `/ops`.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Ruční průchod v prohlížeči (formulář klienta, detail runu, hromadné
   ověření) na třech šířkách.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(clients): add per-client automatic citation verification
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-14 — Ruční verdikty

Viz T14 a design decision 27. **Schema flag** pro `verification_labels`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Slepé hodnocení: verdikt LLM se hodnotiteli NEZOBRAZUJE.
- Vzorek stratifikovaně podle providera a verdiktu LLM.
- Jen editor/admin; stránka použitelná na telefonu.

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Ruční průchod na mobilu i desktopu.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): collect human verdicts for citation checks
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-15 — Měření shody a rozhodnutí o zapnutí

Viz T15. **Tenhle prompt nepíše kód** — provází uživatele hodnocením
slepé sady a vyhodnotí shodu.

**Postup:**
1. Připrav slepou sadu (~100 citací) přes CV-14.
2. Po ohodnocení: shoda, matice záměn, falešné „supported“, rozpad podle
   providera, shoda mezi lidmi (pokud hodnotili dva).
3. Uživatel potvrdí práh a rozhodne o zapnutí pro Knauf.
4. Výsledky do TASKS dokumentu.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(citation-verification): record judge evaluation results
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-16 — Agregace

Viz T16 a design decision 29. Shrnutí: dashboard (podíly verdiktů podle
providera, domény, typu zdroje; vlastní domény klienta zvlášť), `/ops`
(úspěšnost capture podle důvodu, fronta, cena).

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- xAI jako „prohlédnuté zdroje“ zvlášť, DeepSeek mimo.
- „Nelze ověřit“ se nikdy nepočítá jako „nepodloženo“.
- Počítat z nejnovějšího ověření každé citace (append-only tabulka).

**Po dokončení:**
1. Testy + celá sada `pytest`.
2. Ruční průchod v prohlížeči na třech šířkách.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): add citation verification rates to dashboards
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-17 — Dokumentace + CHANGELOG

Viz T17.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Jen `[Unreleased]` — sekci s verzí nezakládej, číslo verze neměň.
- Diff dokumentace ukaž, netlač ho potichu (AI_INSTRUCTIONS.md §3).

**Po dokončení:**
1. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(docs): document citation verification
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## CV-18 — Nasazení, backfill, end-of-branch

Viz T18. **Tenhle prompt nepíše kód** — provází nasazení podle
`docs/DEPLOYMENT.md` a backfill.

**Kritické:**
- Před nasazením si vyžádej potvrzení, že Knauf dostal žádost
  o allowlist (UA + IP).
- Merge, bump verze, přesun CHANGELOGu a tag dělá **uživatel**.
- Migrace 0036–0039; zálohu DB před migrací podle runbooku.
- Příkazy pro server ve tvaru pro otevřenou SSH session, lokální pro
  **PowerShell**; u každého kroku označ, kde běží.
- Produkce běží se 4 workery (ruční `--scale worker=4`, dokud není
  nasazené WT-T3) — nasazení to nesmí vrátit na 1.

**Postup:**
1. Runbook `docs/DEPLOYMENT.md`.
2. Backfill `--dry-run`, pak ostře; sledovat `/ops`.
3. Druhý den: úspěšnost capture podle důvodu, `bot_challenge` u
   knauf.com.
4. End-of-branch docs: `## Status: ...` v obou souborech, řádek v
   `docs/00_INDEX.md`, stav #12 v `docs/ROADMAP.md`.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(citation-verification): record deploy and close the branch
```

### (sem dopiš DONE — commit {hash} až bude hotovo)
