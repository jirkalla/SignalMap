# SignalMap — Claude Code Session Prompts: Client Vision (vydání 1, první větev)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-client-vision (z aktuálního master)
## 2. Čtyři prompty (VI-1 až VI-4), v tomhle pořadí.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent). Commit dokončit před dalším promptem.
## 5. PROGRESS TRACKING — po každém commitnutém promptu:
##    a) V TOMTO souboru pod nadpis promptu `### DONE — commit {hash}`.
##    b) V docs/TASKS_CLIENT_VISION.md v "Task Index" ⏳ → ✅.
## 6. Po VI-4: merge do master BEZ nasazení. Nasazení proběhne v CH-7
##    (docs/PROMPTS_CITATION_HARDENING.md) jako v1.3.0.
## 7. Zdůvodnění a design decisions 1-9: docs/TASKS_CLIENT_VISION.md.
## 8. Lokální testy běží v Dockeru — před VI-1 zapni Docker Desktop.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-client-vision
(vydání 1, první větev; nasazuje se až s citation-hardening jako v1.3.0).
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_CLIENT_VISION.md — CELÉ, hlavně „Výchozí stav" a design
   decisions 1-9
3. docs/ROADMAP.md #23
4. Soubory z „Target" aktuálního úkolu

KONTEXT: Nové pole Vision u klienta — jak chce klient, aby ho AI
asistenti popisovali. Zadává se ve formuláři nad Notes, zobrazuje se
jako první karta na detailu klienta a sbalitelně na dashboardu. Jiný
účel než Notes (interní poznámky).

KRITICKÉ:
- Jedna migrace (clients.vision TEXT NULL), jen přidává — flagovaná
  v TASKS dokumentu.
- Rozšiřuj makro textarea_field v partials/macros.html, nepiš markup
  od nuly.
- Vision neovlivňuje runy, prompty ani export.
- UI texty přes t() v DE i EN; UI ověřit na ~375 / ~768 px / desktop.
- Před kódem mi řekni co/kde/proč (AI_INSTRUCTIONS.md §2).
- Tahle větev se nenasazuje samostatně; bump verze a tag dělá uživatel.
```

---

## VI-1 — Migrace + model

```
Úkol VI-T1 z docs/TASKS_CLIENT_VISION.md (design decisions 1, 9).

1. Zjisti poslední číslo migrace v alembic/versions/ a přečti poslední
   migraci jako vzor formátu.
2. Implementuj T1 body 1-2.
3. Ověř upgrade → downgrade -1 → upgrade, pak pytest.

Navržený commit: feat(clients): add a vision column to clients
```

---

## VI-2 — Formulář

```
Úkol VI-T2 z docs/TASKS_CLIENT_VISION.md (design decisions 3-4, 7).

1. Přečti app/routers/clients.py (create_client, update_client a jak
   vrací inline chyby), clients/form.html, macros.html (textarea_field
   a všechna jeho volání v šablonách).
2. Implementuj T2 body 1-4.
3. Testy T2 bod 5.

Navržený commit: feat(clients): edit client vision in the client form
```

---

## VI-3 — Detail klienta, dashboard, nápověda

```
Úkol VI-T3 z docs/TASKS_CLIENT_VISION.md (design decisions 5-6).

1. Přečti clients/detail.html, dashboard.py (~238-272),
   dashboard/index.html (Vue island, selectedClient), help.html (~43).
2. Krátce mi popiš vzhled karty na detailu a pruhu na dashboardu, než
   začneš.
3. Implementuj T3 body 1-3, testy T3 bod 4.
4. Prohlížeč T3 bod 5 — screenshoty na třech šířkách, i jako viewer.

Navržený commit: feat(clients): show client vision on the detail page and dashboard
```

---

## VI-4 — Dokumentace + CHANGELOG

```
Úkol VI-T4 z docs/TASKS_CLIENT_VISION.md.

1. CHANGELOG.md [Unreleased] → Added (znění podle skutečné implementace).
2. docs/REQUIREMENTS.md — pole Vision u klienta, navrhni doplnění.
3. docs/ROADMAP.md #23.
Ukaž diff PŘED zápisem.

Navržený commit: docs(docs): document the client vision field

Pak se mě zeptej na merge do master (bez nasazení) a připomeň, že
další je feature/signalmap-citation-hardening.
```
