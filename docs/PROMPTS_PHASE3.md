# SignalMap — Claude Code Session Prompts: Phase 3 (First Analysis Skill)

## Status: ✅ Done — PR #5, merged 2026-09-10, released in v1.0.0

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-phase3-mention-detection (z aktuálního master)
## 2. Šest kódových promptů (P3-1 až P3-6), POŘADÍ VYNUCENÉ — viz docs/TASKS_PHASE3.md
##    "Task Index" pro odůvodnění (P3-2/P3-3/P3-4 potřebují schéma z P3-1; P3-4 staví
##    na registry z P3-3; P3-5 rozšiřuje P3-3/P3-4 o zvýraznění matchů — přidáno po
##    P3-4 na žádost uživatele, design decision 12; P3-6 testuje všechno).
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 4. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická kontrola
##    daného promptu) než jdeš na další.
## 5. Po každém promptu: git commit (message navržená na konci promptu, commit
##    provádíš ty, ne agent — agent NIKDY nespouští git commit/push sám bez
##    výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 6. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_PHASE3.md přepni řádek daného task ID v tabulce "Task Index"
##       z ⏳ na ✅.
## 7. Nikdy nekombinuj dva prompty do jedné session.
## 8. Kompletní zdůvodnění vč. design decisions 1-11: docs/TASKS_PHASE3.md — přečti
##    si konkrétní task ID před psaním kódu, ideálně celý soubor před P3-1.
## 9. Tenhle skill je záměrně deterministický (žádné LLM volání) — nikde v téhle
##    větvi se nevolá žádný AI provider adaptér navíc. Pokud se během implementace
##    zdá, že je potřeba LLM klasifikace pro cokoliv v P3-1 až P3-6, ZASTAV a
##    zeptej se — to by byl scope creep mimo to, co bylo odsouhlaseno.
## 10. Až je větev hotová a smergnutá: doplnit do docs/TASKS.md odkaz na tuhle větev
##     (viz Completion Checklist v TASKS_PHASE3.md), a odstranit "Analysis skills and
##     structured AI-generated analysis results" z docs/REQUIREMENTS.md §4.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-phase3-mention-detection.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_PHASE3.md — CELÉ, hlavně design decisions 1-11

KONTEXT: Fáze 1 (docs/TASKS.md), fáze 2 (docs/TASKS_PHASE2.md) a runs export
(docs/TASKS_EXPORT.md) jsou hotové a smergnuté do master. Tahle větev přidává
první analysis skill — deterministickou (bez LLM) detekci, jestli se klient
objevuje v textu odpovědi a/nebo mezi citovanými zdroji. Sentiment a
brand-attribute extrakce jsou VĚDOMĚ MIMO ROZSAH téhle větve — vyšší riziko
nekonzistence, přijdou jako další skill až po ověření tohohle v provozu.

KRITICKÉ:
- Žádné LLM volání nikde v týhle větvi. Matching je čistě deterministický
  (regex/string match) — viz design decision 5 v docs/TASKS_PHASE3.md.
- Nový analysis skill = nový soubor v app/analysis/ + nový řádek v
  ANALYSIS_SKILLS registry (app/analysis/__init__.py) — stejný vzor jako
  app/adapters/ pro providery. NIKDY natvrdo zadaná logika mimo tenhle
  registry.
- AnalysisResult se nikdy needituje — nový výpočet je vždy nový řádek
  (stejná evidence-retention disciplína jako raw_responses/citations).
- Selhání analysis enginu NIKDY nesmí shodit/zneplatnit samotný Run — je
  zabalené v try/except s logováním (design decision 7), Run.status a
  uložené RawResponse/Citation řádky zůstávají netknuté.
- clients.domain je nullable — chybějící doména znamená "citation-match se
  přeskočí", ne chybu.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný
JavaScript framework), Alembic migrace, Docker Compose. Backend kód
anglicky vč. komentářů/error_code, UI texty vždy přes t() mechanismus
v app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom
commitu. Formuláře přes existující makra v
app/templates/partials/macros.html — rozšiř je, než píšeš nový markup
od nuly.

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation, AnalysisResult) — NIKDY
  delete/update endpoint.
- Nová migrace pro každou schema změnu, navazující revision ID (poslední
  je 0010 — nová je 0011).
- Každá route funkce dostane docstring; každé netriviální Form/Field
  pole description=....
- Nikdy git commit ani git push bez tvého výslovného potvrzení — i po
  tom, co agent sám navrhne commit message, čeká na "ano, commitni" než
  cokoliv spustí.

Po každém promptu ukaž implementation summary a navrhni commit message.
Nikdy nespouštěj git add/commit/push sám bez výslovného pokynu — a to
i tehdy, když jsi zprávu sám navrhl v předchozí větě.
```

---
---

## PROMPT P3-1 — Schema: nové tabulky + clients.domain, seed skillu

```
Task: Prompt P3-1 — analysis schema + mention_visibility seed

Přečti docs/TASKS_PHASE3.md úkol P3-T1 CELÝ, hlavně design decisions 1, 3, 4,
6, 8 (execution_type framework, proč nový sloupec domain místo heuristiky,
proč samostatná tabulka client_aliases, doménová normalizace, proč
skill_version je prostý integer, ne row-lineage jako u Prompt).

1. Nová migrace 0011:
   - clients — ADD COLUMN domain VARCHAR(200) (nullable).
   - CREATE TABLE client_aliases — id SERIAL PK, client_id INTEGER NOT NULL
     REFERENCES clients(id) ON DELETE CASCADE, alias VARCHAR(200) NOT NULL,
     created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (client_id, alias).
   - CREATE TABLE analysis_skills — id SERIAL PK, key VARCHAR(50) NOT NULL
     UNIQUE, name VARCHAR(150) NOT NULL, version INTEGER NOT NULL DEFAULT 1,
     execution_type VARCHAR(20) NOT NULL, prompt_template TEXT (nullable),
     output_schema JSONB (nullable), is_active BOOLEAN NOT NULL DEFAULT TRUE,
     created_at/updated_at TIMESTAMPTZ NOT NULL DEFAULT now().
   - CREATE TABLE analysis_results — id SERIAL PK, raw_response_id INTEGER NOT
     NULL REFERENCES raw_responses(id) ON DELETE CASCADE, analysis_skill_id
     INTEGER NOT NULL REFERENCES analysis_skills(id), skill_version INTEGER
     NOT NULL, output JSONB NOT NULL, computed_at TIMESTAMPTZ NOT NULL DEFAULT
     now(). Index idx_analysis_results_raw_response ON analysis_results
     (raw_response_id).
   - INSERT do analysis_skills: key='mention_visibility',
     name='Mention & Visibility Detection', version=1,
     execution_type='rule_based', is_active=TRUE, output_schema jako JSONB
     popisující pole text_mentioned/mention_count/first_mention_position/
     matched_terms/cited/cited_domains (viz P3-T1 v docs/TASKS_PHASE3.md pro
     přesný text).
2. app/models/client.py — Client dostává domain: Mapped[str | None] a
   relationship aliases: Mapped[list["ClientAlias"]] (cascade="all,
   delete-orphan", stejný vzor jako prompt_sets).
3. Nový app/models/client_alias.py — ClientAlias model (id, client_id FK,
   alias, created_at, relationship client back_populates="aliases").
4. Nový app/models/analysis.py — AnalysisSkill (id, key, name, version,
   execution_type, prompt_template, output_schema jako JSONB, is_active,
   created_at/updated_at) a AnalysisResult (id, raw_response_id FK,
   analysis_skill_id FK, skill_version, output jako JSONB, computed_at;
   relationship analysis_skill).
5. app/models/__init__.py — zaregistruj ClientAlias, AnalysisSkill,
   AnalysisResult do importů a __all__.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. V DB ověř: clients.domain existuje; client_aliases prázdná ale existuje;
   analysis_skills má jeden řádek mention_visibility s execution_type=
   'rule_based', is_active=true; analysis_results existuje a je prázdná.
3. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(schema): add analysis_skills/analysis_results/client_aliases + clients.domain
```

---
---

## PROMPT P3-2 — Client aliasy + doména v UI

```
Task: Prompt P3-2 — client domain field and alias management UI

Přečti docs/TASKS_PHASE3.md úkol P3-T2 CELÝ.
Prerekvizita: P3-1 hotový (domain sloupec a client_aliases tabulka existují).

1. app/routers/clients.py:
   - create_client/update_client — nové Form pole domain: str = Form("",
     description="Client's own primary domain, e.g. 'acme.com' — used to
     detect when the client's own site is among a run's cited sources."),
     uloží se .strip().lower() or None.
   - POST /clients/{client_id}/aliases — přidá ClientAlias řádek (alias:
     str = Form(..., description="Alternate name/spelling to match against,
     e.g. 'Acme Corp'."), .strip()). Duplicitní alias (UNIQUE violation) →
     AppError("client_alias_duplicate", ...), ne 500.
   - POST /clients/{client_id}/aliases/{alias_id}/delete — smaže alias, žádná
     in-use kontrola (aliasy nejsou evidence).
   Docstring na každé nové route funkci.
2. app/templates/clients/form.html — nové pole domain (text_field makro), pod
   industry.
3. app/templates/clients/detail.html — nová sekce "Aliases": seznam
   existujících aliasů s delete_button makrem, inline formulář na přidání
   dalšího. Zobraz i client.domain (nebo "—" když prázdné).
4. i18n (EN+DE, jeden commit) — client.domain_label, client.domain_placeholder,
   client.aliases_title, client.alias_add_placeholder,
   client.alias_delete_confirm, client.aliases_empty_state,
   errors.client_alias_duplicate, errors.client_alias_not_found.

Po dokončení:
1. docker compose up -d --build
2. V prohlížeči na existujícím klientovi: vyplň domain, ulož, znovu otevři
   edit formulář → hodnota zůstala. Přidej 2 aliasy, smaž jeden → seznam se
   aktualizuje. Zkus přidat duplicitní alias → strukturovaná chyba, ne 500.
3. Ověř na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(clients): add domain field and alias management for analysis matching
```

---
---

## PROMPT P3-3 — Analysis engine + mention_visibility skill

```
Task: Prompt P3-3 — rule-based mention/visibility analysis skill

Přečti docs/TASKS_PHASE3.md úkol P3-T3 CELÝ, hlavně design decisions 5 a 6
(přesný matching algoritmus a doménová normalizace) — implementuj přesně
podle nich, nevymýšlej vlastní variantu.
Prerekvizita: P3-1 hotový.

1. Nový app/analysis/base.py — AnalysisSkillRunner (Protocol nebo ABC),
   analogické ProviderAdapter u providerů: metoda run(rendered_text: str |
   None, citations: list[Citation], client: Client) -> dict vracející
   JSON-serializovatelný dict.
2. Nový app/analysis/mention_visibility.py — MentionVisibilityRunner:
   - Kandidáti = [client.name] + [a.alias for a in client.aliases], prázdné
     přeskoč.
   - Pro každého kandidáta re.finditer(r'(?<!\w)' + re.escape(candidate) +
     r'(?!\w)', rendered_text, re.IGNORECASE) — pozice matchů ze všech
     kandidátů sjednoť do jedné množiny (dedupe podle start pozice).
     mention_count = len(pozice), first_mention_position = min(pozice) nebo
     None, matched_terms = kandidáti co aspoň jednou zamatchovali,
     text_mentioned = mention_count > 0. Pokud rendered_text je None/prázdný
     → text_mentioned=False, mention_count=0, first_mention_position=None,
     matched_terms=[], žádná chyba.
   - Citation match: normalizuj client.domain a každou citation.source_domain
     (lowercase, ořízni 'www.' prefix). cited=True pokud normalizovaná
     doména klienta odpovídá normalizované doméně citace přesně, NEBO
     normalizovaná doména citace končí na '.' + normalizovaná doména
     klienta (subdoména). cited_domains = originální (nenormalizované)
     source_domain hodnoty, co zamatchovaly. Pokud client.domain je None →
     cited=False, cited_domains=[], bez další logiky.
   - Vrať dict přesně s klíči: text_mentioned, mention_count,
     first_mention_position, matched_terms, cited, cited_domains.
3. Nový app/analysis/__init__.py — ANALYSIS_SKILLS: dict[str,
   type[AnalysisSkillRunner]] = {"mention_visibility": MentionVisibilityRunner},
   get_runner(skill_key: str) -> AnalysisSkillRunner, has_runner(skill_key:
   str) -> bool — stejný registry vzor jako app/adapters/__init__.py.

Po dokončení:
1. Ad-hoc ověření (REPL v kontejneru nebo krátký scratch skript, nekomituj
   ho): zavolej MentionVisibilityRunner().run(...) s ručně sestavenými
   objekty na pár případů — žádná zmínka; zmínka jménem; zmínka jen
   aliasem; zmínka uvnitř jiného slova (NESMÍ matchnout kvůli word-boundary).
   Ověř, že výstup odpovídá očekávání.
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(analysis): add rule-based mention/visibility skill and analysis registry
```

---
---

## PROMPT P3-4 — Zapojení do trigger_run + zobrazení na run detailu

### DONE — commit 3fc9313 (spolu s P3-5, viz poznámka tam — nebyl commitnutý zvlášť)

```
Task: Prompt P3-4 — wire analysis engine into run trigger, show results

Přečti docs/TASKS_PHASE3.md úkol P3-T4 CELÝ, hlavně design decision 7
(proč selhání analýzy nikdy nesmí shodit run) a app/routers/runs.py
trigger_run jako referenční místo (success větev, po db.commit() ze
RawResponse/Citation/SearchQuery).
Prerekvizita: P3-1 a P3-3 hotové.

1. app/routers/runs.py:
   - Nová privátní funkce _run_active_analysis_skills(db, raw_response,
     client) — načte AnalysisSkill kde is_active=true; pro každý s
     execution_type=='rule_based' a has_runner(skill.key) zavolej
     get_runner(skill.key).run(raw_response.rendered_text,
     raw_response.citations, client) [nebo ekvivalentní přístup k citacím
     daného raw_response], ulož AnalysisResult (analysis_skill_id=skill.id,
     raw_response_id=raw_response.id, skill_version=skill.version,
     output=výsledek). execution_type=='llm_prompt' řádky v týhle fázi
     přeskoč (zatím žádný takový skill neexistuje).
   - Zavolej tuhle funkci v success větvi trigger_run, po commitu
     RawResponse/Citation/SearchQuery, ZABALENOU v try/except s
     logger.error(..., exc_info=True) — selhání nikdy nezmění run.status
     ani nezpůsobí rollback už uložené evidence.
   - client získej přes prompt.prompt_set.client.
   - Docstring na nové funkci.
2. app/templates/runs/detail.html — nová sekce "Analysis" (jen když
   analysis_results neprázdné): text_mentioned/cited jako badge (reuse
   status_badge nebo obdobný vizuální jazyk už na stránce), mention_count,
   matched_terms jako seznam, cited_domains jako seznam.
3. i18n (EN+DE, jeden commit) — analysis.section_title,
   analysis.mentioned_yes, analysis.mentioned_no,
   analysis.mention_count_label, analysis.matched_terms_label,
   analysis.cited_yes, analysis.cited_no, analysis.cited_domains_label.

Po dokončení:
1. docker compose up -d --build
2. Na klientovi s vyplněnou doménou a aspoň jedním aliasem (z P3-2) spusť
   reálný run, kde je klient v odpovědi zmíněný a/nebo citovaný → run detail
   ukazuje sekci Analysis se správnými hodnotami.
3. Spusť run, kde klient zmíněný není → sekce ukazuje mentioned=No,
   cited=No, bez chyby.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): compute and display mention/visibility analysis after each run
```

---
---

## PROMPT P3-5 — Zvýraznění zmínek v textu odpovědi

### DONE — commit 3fc9313 (spojeno s P3-4 do jednoho commitu — P3-4 nebyl
commitnutý zvlášť před tím, než na stejných souborech začala práce na P3-5,
čisté rozdělení by vyžadovalo `git add -p`, který tenhle nástroj nepodporuje;
odsouhlaseno s uživatelem)

```
Task: Prompt P3-5 — highlight matched mention terms in the rendered answer

Přečti docs/TASKS_PHASE3.md úkol P3-T5 CELÝ a design decision 12 — přidáno
po P3-T4 na žádost uživatele. KRITICKÉ: zvýraznění se opírá výhradně o
pozice uložené v AnalysisResult.output v době výpočtu, NIKDY o přepočet
matchingu proti aktuálním aliasům klienta při zobrazení — jinak by se
porušila neměnnost evidence (design decision 10).
Prerekvizita: P3-3 a P3-4 hotové.

1. app/analysis/mention_visibility.py:
   - Uprav interní match-hledání tak, aby si pro každý start pamatovalo i
     konec matche (dict start -> end; při shodném startu z různých
     kandidátů ponech delší end).
   - V MentionVisibilityRunner.run() z toho postav match_spans:
     list[list[int]] — seřazené podle startu, přeskoč rozsah, jehož start
     je menší než end posledního zachovaného rozsahu (obrana proti
     překryvu). mention_count = len(match_spans), first_mention_position =
     match_spans[0][0] nebo None — beze změny chování oproti dnešku.
   - Přidej "match_spans": match_spans do výstupního dictu.
2. Nová migrace 0012 — POUZE datová (op.execute UPDATE), doplní
   "match_spans": "array of [start, end) pairs into rendered_text, used to
   highlight matches" do analysis_skills.output_schema JSON pro řádek
   mention_visibility. Žádná ALTER TABLE, jen UPDATE dokumentačního JSONu.
3. app/routers/runs.py:
   - Nová privátní funkce _highlight_matches(text: str, spans:
     list[list[int]]) -> Markup (from markupsafe import Markup, escape) —
     escape() na text mimo rozsahy, <mark class="bg-yellow-200 rounded
     px-0.5"> obalí escape() na text uvnitř rozsahu. Prázdné/None spans →
     jen escape(text).
   - run_detail — najdi match_spans z AnalysisResult.output pro
     mention_visibility výsledek (pokud existuje v analysis_results), postav
     rendered_text_html = _highlight_matches(raw_response.rendered_text,
     spans) a přidej do kontextu šablony.
4. app/templates/runs/detail.html — sekce "Rendered answer" použije
   rendered_text_html (Markup, Jinja neescapuje podruhé) místo prostého
   raw_response.rendered_text, fallback na t('common.none'), když
   rendered_text chybí.

Po dokončení:
1. docker compose exec app alembic upgrade head
2. docker compose up -d --build
3. Na existujícím runu s text_mentioned=true znovu otevři run detail →
   matchnuté výrazy v "Rendered answer" jsou vizuálně zvýrazněné, zbytek
   textu beze změny.
4. Ověř, že text není rozbitý/dvakrát escapovaný a že žádný text z AI
   odpovědi se nevykreslí jako živé HTML.
5. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
feat(runs): highlight matched mention terms in the rendered answer
```

---
---

## PROMPT P3-6 — Testy

```
Task: Prompt P3-6 — test coverage for aliases, matching, and wiring

Přečti docs/TASKS_PHASE3.md úkol P3-T6 CELÝ.
Prerekvizita: P3-1 až P3-5 hotové.

1. Nový tests/test_client_aliases.py — přidání/smazání aliasu přes
   /clients/{id}/aliases; duplicitní alias vrací strukturovanou chybu, ne
   500; domain pole se uloží přes create/edit formulář.
2. Nový tests/test_analysis_mention_visibility.py — přímé jednotkové testy
   MentionVisibilityRunner.run() (bez HTTP/DB): žádná zmínka; zmínka jménem;
   zmínka jen aliasem; víc zmínek (počet/pozice sedí); word-boundary
   (řetězec uvnitř jiného slova nematchne); rendered_text=None;
   client.domain=None → cited=False bez chyby; citace přesně na doméně
   matchne; citace na subdoméně matchne; citace na jiné doméně nematchne;
   match_spans odpovídá skutečným pozicím/délkám matchů v testovacím textu.
3. tests/test_runs.py — rozšiř run-trigger test (přes FakeAdapter) o
   ověření, že po úspěšném běhu existuje odpovídající AnalysisResult řádek
   se skill_version=1 a očekávaným output (vč. match_spans); a že selhání
   uvnitř analysis enginu (monkeypatch get_runner tak, aby vyhodil výjimku)
   run nezhroutí — Run.status zůstává 'success', RawResponse existuje.

Po dokončení:
1. pytest — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)
```

**Expected commit:**
```
test: cover client aliases, mention/visibility matching, and analysis wiring
```
