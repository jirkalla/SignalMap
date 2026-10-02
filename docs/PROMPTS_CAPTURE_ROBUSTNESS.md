# SignalMap — Claude Code Session Prompts: Capture Robustness (vydání 2, první větev)

## v1.0 | Říjen 2026
##
## JAK POUŽÍVAT:
## 0. Předpoklad: v1.3.0 je nasazené a uzavřené (backfill hotový, docs
##    „vydání 1 ✅" hotové). Pokud se po backfillu objevily NOVÉ chyby téhle
##    třídy, zvaž PATCH v1.3.1 místo čekání na v1.4.0 (viz TASKS, úvod).
## 1. git checkout -b feature/signalmap-capture-robustness (z aktuálního master)
## 2. Čtyři prompty (CR-1 až CR-4). CR-1 a CR-2 jsou nezávislé, CR-3 (audit)
##    až po nich, CR-4 dokumentuje. NENASAZUJE se — merge do master BEZ
##    nasazení, nasadí se v SO-7 (vydání 2, v1.4.0).
## 3. Další větve vydání 2 po téhle: worker-throughput
##    (docs/PROMPTS_WORKER_THROUGHPUT.md), pak scheduler-ops
##    (docs/PROMPTS_SCHEDULER_OPS.md).
## 4. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 5. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent). Commit dokončit před dalším promptem.
## 6. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_CAPTURE_ROBUSTNESS.md v "Task Index" ⏳ → ✅.
## 7. Zdůvodnění a design decisions 1-5: docs/TASKS_CAPTURE_ROBUSTNESS.md.
## 8. Lokální testy běží v Dockeru — před CR-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-capture-robustness
(vydání 2, první větev, cíl v1.4.0 společně s worker-throughput
a scheduler-ops).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_CAPTURE_ROBUSTNESS.md — CELÉ, hlavně „Výchozí stav"
   a design decisions 1-5
3. docs/ROADMAP.md #28
4. Soubory z „Target" aktuálního úkolu

KONTEXT: Po nasazení v1.3.0 zbyly v produkci tři chybové verification_jobs
z doby před nasazením, které v1.3.0 nezavírá: poškozené PDF
(„Stream has ended unexpectedly", pypdf) a nevalidní odpověď archive.org
(„Expecting value: line 1 column 1"). Jeden vadný vstup tak shodí celý
job a citace za ním se nezpracují.

KRITICKÉ:
- Žádná migrace, žádný nový důvod neověřitelnosti (použij pdf_no_text).
- Ošetření na hranici cizího parseru (pypdf, JSON), NE plošný catch-all
  ve smyčce jobu (design decision 1).
- Nevalidní odpověď archive.org = ArchiveUnavailable, nikdy „žádný
  snapshot" (jinak se výpadek zapíše jako potvrzený záporný nález).
- Nové testy musí selhat na kódu před opravou; poškozené PDF vyrob
  useknutím tests/fixtures/sources/sample.pdf.
- Tahle větev se nenasazuje (nasazení v SO-7); žádné placené runy.
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Commit, push, merge a tag dělá uživatel (AI_INSTRUCTIONS.md §4, §8).
```

---

## CR-1 — Poškozené PDF se zapíše jako `pdf_no_text`

```
Úkol CR-T1 z docs/TASKS_CAPTURE_ROBUSTNESS.md (design decisions 1, 2, 5).

1. Přečti app/services/source_extract.py (extract_pdf) a testy
   tests/test_source_extract.py, tests/test_source_capture.py.
2. Nejdřív REPRODUKUJ: extract_pdf nad useknutým sample.pdf — zapiš mi
   přesný typ a text výjimky (potvrzení nebo vyvrácení hypotézy
   o pypdf PdfStreamError).
3. Řekni mi co/kde/proč (§2) a počkej na souhlas.
4. Implementuj T1 body 2-3, testy podle bodu 4, celá sada pytest.
   Nové testy musí selhat na starém kódu.

Na konci: shrnutí, testy, výsledek reprodukce, navržený commit:
fix(runs): record an unreadable PDF as pdf_no_text instead of failing the job
```

---

## CR-2 — Nevalidní odpověď archive.org je `ArchiveUnavailable`

```
Úkol CR-T2 z docs/TASKS_CAPTURE_ROBUSTNESS.md (design decisions 3, 5).

1. Přečti app/services/archive_lookup.py celé, tests/test_archive_lookup.py
   a v app/services/citation_verification.py to, jak se ArchiveUnavailable
   zachytává po citaci (T3 z v1.3.0).
2. Řekni mi co/kde/proč (§2): které tvary odpovědi CDX se mají chovat
   jako výpadek a které jako „žádný snapshot".
3. Implementuj T2 body 1-2, testy podle bodu 3 včetně integračního
   průchodu verify_citations_by_quote, celá sada pytest. Nové testy musí
   selhat na starém kódu; stávající testy výpadku beze změny.

Na konci: shrnutí, testy, navržený commit:
fix(runs): treat a malformed archive.org CDX response as unavailable
```

---

## CR-3 — Audit dalších cest, kudy jeden vadný vstup shodí job

```
Úkol CR-T3 z docs/TASKS_CAPTURE_ROBUSTNESS.md (design decision 1).
Předpoklad: CR-1 a CR-2 jsou commitnuté.

1. Projdi cestu stažené tělo → SourceDocument/SourceText/
   CitationVerification (source_capture.py, source_extract.py,
   citation_verification.py) a udělej SEZNAM míst, která mohou při vadném
   vstupu vyhodit výjimku do job-level handleru. Začni startovním
   seznamem z T3 bodu 1 (content_type > 100 znaků, source_domain > 200,
   zanořené HTML, šifrované PDF).
2. Pro každé místo: je hrozba reálná (reprodukce testem), nebo nehrozí
   (důvod)? Ukaž mi seznam a navrhni opravy. Počkej na souhlas — plošný
   catch je vyloučen.
3. Implementuj odsouhlasené, každé s testem, který na starém kódu selže.
   Celá sada pytest. Prověřené „nehrozí" cesty zapiš do
   docs/TASKS_CAPTURE_ROBUSTNESS.md.

Na konci: shrnutí, seznam prověřených cest, testy, navržený commit
(tvar podle nálezů, např.):
fix(runs): harden source capture against malformed headers and inputs
```

---

## CR-4 — Dokumentace + CHANGELOG

```
Úkol CR-T4 z docs/TASKS_CAPTURE_ROBUSTNESS.md.

1. CHANGELOG.md [Unreleased] → ### Fixed podle T4 bodu 1 (uprav znění
   podle toho, co se skutečně implementovalo).
2. docs/ROADMAP.md #28 → ✅ s odkazem na tenhle dokument.
3. Výsledky CR-1 (reprodukce) a CR-3 (prověřené cesty) zapsat do
   docs/TASKS_CAPTURE_ROBUSTNESS.md.
Ukaž diff PŘED zápisem.

Navržený commit: docs(docs): document capture robustness fixes
```
