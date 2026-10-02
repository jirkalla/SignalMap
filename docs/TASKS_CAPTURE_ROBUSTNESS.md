# SignalMap — Tasks: Capture Robustness (vydání 2, první větev)

## v1.0 | Říjen 2026
## Branch: feature/signalmap-capture-robustness
## Task ID prefix: CR
## Cílová verze: v1.4.0 (MINOR) — společně s `feature/signalmap-worker-throughput` a `feature/signalmap-scheduler-ops`

## Status: ⏳ navrženo 2026-10-02, nezačato

Navrženo 2026-10-02 jako **první větev vydání 2** (`docs/ROADMAP.md` #28)
z rozboru chybových `verification_jobs` po prvním nasazení v1.3.0 a backfillu
Knauf. Čtyři úkoly, **bez migrace**.

**Goal:** jeden vadný zdroj nebo jedna vadná odpověď archive.org už nikdy
neshodí celý verification job — poškozené PDF se zapíše jako důvod
neověřitelnosti u té jedné citace a nevalidní odpověď archive.org se
chová jako jeho výpadek (odložení a opakování), ne jako chyba jobu.

**Proč první a samostatně:** je to nejmenší a na workeru nezávislá větev —
sahá jen na extrakci textu (`source_extract.py`) a archivní lookup
(`archive_lookup.py`), ne na smyčku workeru ani frontu (WT, SO). Zároveň
vyčistí statistiku chyb, na které stojí kategorie chyb v `SO-T3`.

**Nasazení:** tahle větev se **nenasazuje samostatně**. Merge do `master`
bez nasazení; nasadí se v rámci `SO-T7` (v1.4.0 pro všechny tři větve).
Výjimka: pokud se po dokončeném backfillu v produkci objeví nové chyby téhle
třídy, jde stejná oprava samostatně jako PATCH v1.3.1 (`docs/DEPLOYMENT.md`
§0: `fix` = PATCH) — rozhodne se podle dotazu z „Výchozího stavu".

---

## Výchozí stav (produkce 2026-10-02, po nasazení v1.3.0 a backfillu Knauf)

`verification_jobs` ve stavu `error`: **26 jobů**, všechny vytvořené před
nasazením v1.3.0 (poslední 2026-10-01 13:59 UTC); po nasazení žádný nový.

| Příčina | Jobů | Opraveno |
|---|---|---|
| `ArchiveUnavailable` (503, timeout, connection error z archive.org) | 20 | v1.3.0 (T3) |
| NUL byte v extrahovaném textu | 1 | v1.3.0 (T4) |
| „gave up after 3 attempts, still incomplete" (rozpočet času) | 2 | ne, mimo tuto větev |
| **`Stream has ended unexpectedly`** | **2** | **CR-T1** |
| **`Expecting value: line 1 column 1 (char 0)`** | **1** | **CR-T2** |

**Poškozené PDF** — `app/services/source_extract.py::extract_pdf`
(`:375`) volá `pypdf.PdfReader(...)` a `page.extract_text()` bez ošetření.
Hypotéza (ověřit v CR-T1 krok 1): „Stream has ended unexpectedly" je zpráva
`pypdf.errors.PdfStreamError` při useknutém nebo poškozeném PDF. Výjimka
unikne z `capture_url` do job-level handleru v
`verification_queue.process_verification_job`: job se třikrát zopakuje
(každé opakování znovu stáhne totéž PDF) a skončí `error`; citace za vadnou
URL téže odpovědi se nezpracují.

**Nevalidní odpověď archive.org** — `app/services/archive_lookup.py::
find_closest_snapshot` (`:128`) volá `response.json()` na těle, které při
výpadku archive.org může být HTML nebo prázdné i s kódem 200. `ValueError`
není `ArchiveUnavailable`, takže unikne z izolace po citaci (T3 z v1.3.0),
která zachytává jen `ArchiveUnavailable`. Navíc `rows[1][1], rows[1][2]`
selže `IndexError`/`TypeError` na JSONu jiného tvaru.

**Další kandidáti na audit (CR-T3):** `SourceDocument.content_type` je
`String(100)` a `_store` do něj ukládá hlavičku `Content-Type` beze změny —
delší hodnota znamená `DataError` při insertu a pád jobu (stejná třída
jako NUL); `Citation.source_domain` je `String(200)` (zapisuje se při
uložení odpovědi providera, ne při capture — ověřit, jestli to může shodit
run).

---

## Design decisions

1. **Ošetření na hranici cizího parseru, ne plošný `except`.** Chyby
   `pypdf` (i `ValueError`/`KeyError`/`RecursionError`, které umí při
   poškozeném vstupu) se zachytí v `extract_pdf` kolem `PdfReader`/
   `extract_text()` (`except Exception` je tady na hranici cizího kódu
   nad nedůvěryhodným vstupem opodstatněné), zalogují s typem výjimky
   a `extract_pdf` vrátí `None`. Plošný `try/except` kolem celé smyčky
   `process_verification_job` se **nedělá** — schoval by skutečné chyby
   (nevíme, jaký důvod by se uložil) a job-level chyba je dnes viditelný
   signál.
2. **Důvod u poškozeného PDF je existující `pdf_no_text`.** Přesnější
   `pdf_unreadable` by vyžadoval migraci (CHECK constraint
   `UNVERIFIABLE_REASONS`), překlady a test pokrytí (T5 z v1.3.0) — a tahle
   větev je „bez migrace". `pdf_no_text` („PDF nemá extrahovatelný text")
   je pro analytika dost blízko; přesnější důvod jen jako případná budoucí
   položka.
3. **Nevalidní nebo neočekávaný tvar odpovědi CDX = výpadek archive.org,**
   tedy `ArchiveUnavailable` (odložení a opakování, na posledním pokusu
   `archive_unavailable`), nikdy „žádný snapshot" (`None`) — jinak by se
   neověřený výpadek zapsal jako potvrzený záporný nález (kritické
   pravidlo z `archive_lookup.py`).
4. **Žádná migrace, žádný nový důvod, žádná změna UI.**
5. **Nové testy musí selhat na kódu před opravou** (jako v CITATION_HARDENING).
   Poškozené PDF se v testu vyrobí useknutím existujícího
   `tests/fixtures/sources/sample.pdf` (žádný nový binární soubor).

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Poškozené PDF se zapíše jako `pdf_no_text`, nesrazí job | ⏳ |
| T2 | Nevalidní odpověď archive.org je `ArchiveUnavailable` | ⏳ |
| T3 | Audit dalších cest, kudy jeden vadný vstup shodí job | ⏳ |
| T4 | Dokumentace + CHANGELOG | ⏳ |

**Pořadí:** T1 a T2 jsou na sobě nezávislé, T3 po nich (audit staví na tom, co
T1/T2 odhalí), T4 poslední. Nasazení je v `SO-T7`.

---

## T1 — Poškozené PDF se zapíše jako `pdf_no_text`

**Target:** `app/services/source_extract.py`, `tests/test_source_extract.py`,
`tests/test_source_capture.py`

1. **Reprodukce:** `extract_pdf` nad useknutým `sample.pdf` (např. první
   polovina bajtů) a zapsat přesný typ a text výjimky. Potvrdí nebo vyvrátí
   hypotézu o `PdfStreamError` — výsledek zapsat sem.
2. `extract_pdf`: zachytit chyby čtení (viz design decision 1), zalogovat
   `logger.warning` s typem výjimky a vrátit `None`.
3. Platí i pro archivní PDF: `archive_lookup.fetch_snapshot_content` volá
   `extract_pdf` a `None` už dnes znamená „snapshot nemá použitelný text"
   (ne výpadek) — ověřit testem.
4. Testy: useknuté PDF a PDF s poškozenou strukturou → `None`; přes
   `capture_url` (MockTransport) výsledek `error_reason='pdf_no_text'`
   a job nespadne; archivní větev stejně.

**Done when:** testy + celá sada `pytest` projdou; nové testy selhávají na
starém kódu.

**Expected commit:** `fix(runs): record an unreadable PDF as pdf_no_text instead of failing the job`

---

## T2 — Nevalidní odpověď archive.org je `ArchiveUnavailable`

**Target:** `app/services/archive_lookup.py`, `tests/test_archive_lookup.py`

1. `find_closest_snapshot`: dekódování JSON (`response.json()`) i přístup
   `rows[1][1]`, `rows[1][2]` zabalit tak, aby `ValueError`, `IndexError`,
   `TypeError` (JSON jiného tvaru než seznam seznamů) vyvolaly
   `ArchiveUnavailable` s popisem (design decision 3). Prázdný seznam
   a seznam jen s hlavičkou zůstávají „žádný snapshot" (`None`).
2. Totéž pro `fetch_snapshot_content`, pokud má podobnou křehkou cestu
   (ověřit; tělo snapshotu není JSON, tam jde jen o obsah).
3. Testy: tělo HTML s kódem 200, prázdné tělo, JSON objekt místo seznamu,
   řádek s příliš málo sloupci → `ArchiveUnavailable`; průchod
   `verify_citations_by_quote` (citace se odloží / na posledním pokusu
   `archive_unavailable`) jako integrační test.

**Done when:** testy + celá sada `pytest` projdou; nové testy selhávají na
starém kódu; stávající testy výpadku archive.org beze změny.

**Expected commit:** `fix(runs): treat a malformed archive.org CDX response as unavailable`

---

## T3 — Audit dalších cest, kudy jeden vadný vstup shodí job

**Target:** `app/services/source_capture.py`, `app/services/source_extract.py`,
případně `app/services/citation_verification.py`; testy u dotčených modulů

Cíl: dokončit práci z T3 a T4 z `docs/TASKS_CITATION_HARDENING.md` — projít
cestu od staženého těla po zápis `SourceDocument`/`SourceText`/
`CitationVerification` a pro **každé** místo, které může při vadném vstupu
vyhodit výjimku do job-level handleru, buď přidat ošetření na hranici
(design decision 1), nebo zapsat, proč nehrozí.

1. Startovní seznam: `content_type` > 100 znaků v `_store` (`String(100)`)
   → ořezat při ukládání; `Citation.source_domain` (200) při ukládání
   odpovědi; pathologicky zanořené HTML v `extract_html`; šifrované PDF
   (pokryje T1); jiné `Content-Length`/kódování, které `httpx` propustí.
2. Pro každé potvrzené místo: reprodukce (test, který dnes selže), oprava,
   test. Co se potvrdit nepodaří, zapsat do tohoto dokumentu jako „nehrozí"
   s důvodem.
3. **Rozhodnutí před kódem:** pokud audit najde cestu, kterou nejde
   ošetřit na hranici, navrhnout řešení a počkat na souhlas (plošný catch
   je vyloučen, design decision 1).

**Done when:** seznam prověřených cest zapsán sem; každá potvrzená
má test a opravu; testy + celá sada `pytest` projdou.

**Expected commit:** `fix(runs): harden source capture against malformed headers and inputs`
(tvar commitu se upřesní podle nálezů; při žádném nálezu jen docs v T4)

---

## T4 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/ROADMAP.md` #28, tento dokument

1. `CHANGELOG.md` → `## [Unreleased]` → `### Fixed` (uprav podle toho, co
   se skutečně implementovalo):
   - A corrupted or truncated PDF no longer fails a verification job: the
     citation is recorded as "PDF has no extractable text" and the other
     citations of the response are still verified.
   - A malformed response from archive.org (an error page instead of data)
     is treated like an outage and retried, instead of failing the job.
2. `docs/ROADMAP.md` #28 → ✅ s odkazem na tenhle dokument.
3. Zapsat výsledky T1 krok 1 a T3 (prověřené cesty) sem.

**Done when:** diff ukázaný uživateli a odsouhlasený.

**Expected commit:** `docs(docs): document capture robustness fixes`

---

## Co tahle větev vědomě nedělá

- **Nový důvod `pdf_unreadable`** — vyžaduje migraci, překlady a test
  pokrytí (design decision 2); případně samostatná položka.
- **Ochrana proti přesměrování na interní adresy (SSRF)** — `docs/ROADMAP.md`
  #27 (vyžaduje nový důvod, tedy migraci).
- **Stavový řádek průběhu u automatického ověření** (uvnitř capture jobu
  není vidět „běží"; `docs/TASKS_CITATION_HARDENING.md` T8 sleduje jen
  `judge` joby) — drobná UX položka mimo tuto větev.
- **„gave up after 3 attempts, still incomplete"** (rozpočet času pro velkou
  odpověď) — jiná příčina (délka práce, ne vadný vstup), řeší se podle
  dat po `WT`.
- **Plošný catch-all ve smyčce jobu** — design decision 1.
