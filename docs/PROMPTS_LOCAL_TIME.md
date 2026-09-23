# SignalMap — Claude Code Session Prompts: Local Timezone Display for Timestamps

## Status: ✅ Done — PR #11, merged 2026-09-14, released in v1.0.0

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-local-time-display (z aktuálního master)
## 2. Dva kódové prompty (LT-1, LT-2), POŘADÍ VYNUCENÉ (LT-2 potřebuje makro z LT-1).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola daného
##    promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit provádíš ty,
##    ne agent — agent NIKDY nespouští git commit/push sám bez výslovného potvrzení, a to i
##    přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_LOCAL_TIME.md přepni řádek daného task ID v tabulce "Task Index"
##       z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-6: docs/TASKS_LOCAL_TIME.md — přečti si
##    konkrétní task ID před psaním kódu, ideálně celý soubor před LT-1.
## 9. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md krátkou poznámku/odkaz na
##    tenhle branch (viz Completion Checklist v TASKS_LOCAL_TIME.md) — teprve po tom, co
##    uživatel v prohlížeči potvrdí, že oprava funguje (AI_INSTRUCTIONS.md §7).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-local-time-display.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_LOCAL_TIME.md — CELÉ, hlavně design decisions 1-6

KONTEXT: appka ukládá časy v UTC (TIMESTAMPTZ, správně), ale všech 14 míst napříč
7 šablonami, co čas zobrazují, dělá `.strftime(...)` přímo na uloženou hodnotu bez
převodu na jakoukoliv místní časovou zónu. Nalezeno 2026-09-14 při manuálním ověřování
appky na produkci (run zobrazoval čas o 2 hodiny posunutý oproti reálnému — UTC vs. CEST).
Tahle větev to řeší jako samostatnou práci, mimo fázový seznam docs/TASKS.md.

KRITICKÉ: převod se dělá na KLIENTOVI (JS, podle časové zóny prohlížeče každého
uživatele), ne pevnou zónou na serveru — appka může mít uživatele mimo střední Evropu.
Appka dnes nemá žádný /static adresář ani FastAPI StaticFiles mount — veškerý JS je
inline v jednom <script> bloku v app/templates/base.html. Rozšiř tenhle stávající blok,
NEZAVÁDĚJ novou static-file infrastrukturu (viz design decision 2 v TASKS_LOCAL_TIME.md
proč). Použij nativní Intl.DateTimeFormat, žádnou JS knihovnu.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný JavaScript framework),
Docker Compose. Backend kód anglicky vč. komentářů, UI texty přes t() mechanismus
v app/i18n/{en,de}.json.

KRITICKÁ PRAVIDLA:
- Fallback bez JS musí fungovat — appka pořád ukáže čitelný UTC čas, i když JS selže.
- Musí fungovat i po HTMX swapu (htmx:afterSettle), ne jen při prvním načtení stránky.
- Nikdy `git commit` ani `git push` bez tvého výslovného potvrzení — i po tom, co agent
  sám navrhne commit message, čeká na "ano, commitni" než cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message. Nikdy nespouštěj
git add/commit/push sám bez výslovného pokynu — a to i tehdy, když jsi zprávu sám navrhl
v předchozí větě.
```

---
---

## PROMPT LT-1 — Sdílené makro + klientský JS převod časové zóny

```
Task: Prompt LT-1 — local_time() macro + client-side timezone conversion

Přečti docs/TASKS_LOCAL_TIME.md úkol LT-T1 CELÝ, hlavně design decisions 1-4
(proč klientský převod, proč žádný nový /static mount, proč Intl.DateTimeFormat,
proč <time> element s UTC fallbackem).

1. app/templates/partials/macros.html — nové makro local_time(dt, seconds=True):
   - Prázdný řetězec, pokud dt je None.
   - Jinak:
     <time datetime="{{ dt.isoformat() }}" data-local-time>{{ dt.strftime('%Y-%m-%d %H:%M:%S' if seconds else '%Y-%m-%d %H:%M') }} UTC</time>
2. app/templates/base.html — rozšiř STÁVAJÍCÍ <script> blok (nezakládej nový,
   nezaváděj /static soubor) o:
   - funkci localizeTimes(root), co najde [data-local-time] uvnitř root a přepíše
     textContent přes new Intl.DateTimeFormat(undefined, {dateStyle: 'medium',
     timeStyle: 'medium'}).format(new Date(...))
   - zavolej localizeTimes(document) při načtení
   - zavěš localizeTimes(event.target) na document.addEventListener('htmx:afterSettle', ...)

Po dokončení:
1. docker compose up -d --build — appka nastartuje bez chyby
2. Dočasně přidej testovací {{ local_time(nějaký_datetime) }} výskyt do libovolné
   šablony (nebo počkej na LT-2), ověř v prohlížeči DevTools, že se zobrazený čas
   liší od UTC podle časové zóny tvého prohlížeče
3. Vypni JS v DevTools, obnov stránku — ověř, že se pořád zobrazuje čitelný UTC čas
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(ui): add shared local_time() macro with client-side timezone conversion
```

---
---

## PROMPT LT-2 — Nahradit všech 14 `.strftime(...)` výskytů makrem

```
Task: Prompt LT-2 — replace all .strftime(...) calls with local_time() macro

Přečti docs/TASKS_LOCAL_TIME.md úkol LT-T2 CELÝ, hlavně přesný seznam 14 výskytů
k nahrazení.
Prerekvizita: LT-1 hotový (makro local_time musí existovat).

1. V každé z těchto 7 šablon rozšiř {% from "partials/macros.html" import ... %}
   o local_time:
   - app/templates/runs/detail.html
   - app/templates/prompts/detail.html
   - app/templates/clients/detail.html
   - app/templates/clients/list.html
   - app/templates/providers/list.html
   - app/templates/ai_models/list.html
   - app/templates/ai_models/form.html
2. Nahraď X.strftime('%Y-%m-%d %H:%M:%S') → {{ local_time(X) }} a
   X.strftime('%Y-%m-%d %H:%M') → {{ local_time(X, seconds=False) }} přesně podle
   seznamu v LT-T2 (neměň úroveň detailu, jen přidej převod zóny).

Po dokončení:
1. docker compose up -d --build
2. Projdi v prohlížeči VŠECHNY dotčené obrazovky (run detail, prompt detail, client
   detail/list, providers list, ai-models list/form) — čas musí sedět s reálným
   místním časem
3. Ověř HTMX swap scénář — na stránce, kde HTMX mění obsah bez plného reloadu, že
   se nově vložený čas taky správně převede
4. pytest — žádný test by se neměl rozbít
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
refactor(ui): use local_time() macro for all displayed timestamps
```
