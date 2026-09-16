# SignalMap — Claude Code Session Prompts: Oprava extrakce citací (Gemini)

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. Schema flag i evidence flag v docs/TASKS_GEMINI_CITATIONS.md jsou už
##    POTVRZENÉ (2026-09-16) — tři nové sloupce na `citations` a backfill,
##    který přepisuje existující `citations` řádky. Nemusíš se na ně ptát
##    znovu, ale přečti si obě sekce, ať víš, co přesně bylo odsouhlaseno.
## 2. git checkout -b feature/signalmap-gemini-citation-extraction (z master)
##    — větev zakládá UŽIVATEL, ne agent, i když ji AI_INSTRUCTIONS.md §9
##    vyžaduje.
## 3. Šest promptů (GC-1 až GC-6), POŘADÍ VYNUCENÉ — viz
##    docs/TASKS_GEMINI_CITATIONS.md "Task Index" pro odůvodnění.
## 4. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 5. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická
##    kontrola daného promptu) než jdeš na další.
## 6. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
##    Commit dotáhni PŘED začátkem dalšího promptu, neřetěz necommitnutou
##    práci přes dva tasky.
## 7. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_GEMINI_CITATIONS.md přepni řádek daného task ID
##       v tabulce "Task Index" z ⏳ na ✅.
## 8. Nikdy nekombinuj dva prompty do jedné session.
## 9. Kompletní zdůvodnění vč. design decisions 1-9 a naměřených dat:
##    docs/TASKS_GEMINI_CITATIONS.md — přečti si konkrétní task ID před
##    psaním kódu, ideálně celý soubor před GC-1.
## 10. Tvary payloadů pro GC-2 jsou už ověřené (2026-09-16, design decision 5)
##     přímo proti reálným uloženým raw_payload v produkční DB, u všech tří
##     providerů. Žádné další ověřování před psaním kódu není potřeba.
## 11. GC-6 (docs) se NESMÍ spustit dřív, než uživatel potvrdí, že GC-1 až
##     GC-5 reálně fungují (AI_INSTRUCTIONS.md §7 bod 3).
## 12. Až je větev hotová a smergnutá: v docs/TASKS.md přidat sekci
##     "Citation extraction fix" do "Beyond phase 1 scope".

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-gemini-citation-extraction.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md (hlavně FR-10 až FR-14)
3. docs/TASKS_GEMINI_CITATIONS.md — CELÉ, hlavně sekci "Naměřený stav",
   oba flagy a design decisions 1-9

KONTEXT: Fáze 1-6 jsou hotové a smergnuté. Tahle větev řeší roadmap
položku #11 (docs/ROADMAP.md) — opravu extrakce citací u Gemini — a spolu
s ní druhý defekt, který se našel při přípravě: sloupec
citations.cited_answer_span dnes znamená u každého providera něco jiného.
Blokuje roadmap #12 (LLM quote-verification skill).

CO JE ROZBITÉ (změřeno na produkčních datech 2026-09-16, ne odhad):
- app/adapters/google.py::_map_citations bere jen PRVNÍ grounding_support,
  který odkazuje na daný chunk, a zbytek zahodí (break). Gemini vrací
  many-to-many vazbu: 551 uložených citací vs. 1 354 reálných dvojic
  (segment odpovědi → zdroj) = 59 % claim-source vazeb se ztrácí, ve 40
  z 52 odpovědí.
- cited_answer_span drží u Gemini a OpenAI úsek ODPOVĚDI, ale u Anthropicu
  pasáž ZE ZDROJE (citation.cited_text). Ověřeno: span se najde
  v rendered_text u Gemini 551/551, OpenAI 71/71, Anthropic jen 17/158.
  FR-12 přitom sloupec definuje jako answer span — to je vada, ne
  interpretace.

CO SE MĚNÍ:
- Gemini mapper se přepíše na iteraci přes grounding_supports (jeden řádek
  na dvojici support × chunk), řazenou podle pozice v odpovědi.
- citations dostane tři nové sloupce: source_passage (pasáž ze zdroje),
  answer_span_start, answer_span_end.
- Všechny tři mappery přejdou z SDK objektů na dict (model_dump), aby
  backfill mohl volat tutéž funkci nad uloženým raw_payload.
- Migrace 0028 přepočítá historické citace z raw_payload.

KRITICKÉ:
- Schema flag (3 nové sloupce nad rámec schema_phase1.sql) i evidence flag
  (backfill přepisuje citations řádky) JSOU potvrzené uživatelem
  2026-09-16 — viz obě ⚠️ sekce v docs/TASKS_GEMINI_CITATIONS.md. Neptej
  se na ně znovu, ale nerozšiřuj je: žádná nová tabulka, žádný zrušený
  nebo přejmenovaný sloupec, raw_responses se nepřepisuje NIKDY.
- Anthropic a OpenAI adaptery mají many-to-many mechaniku V POŘÁDKU —
  emitují jeden řádek na dvojici už dnes. NEPŘEPISUJ jejich smyčky. Mění
  se u nich jen (a) čtení z dictu místo SDK objektu a (b) do kterého
  sloupce jde který text.
- segment.start_index je u prvního segmentu null, ne 0 (30 ze 740 případů).
  Řazení i ukládání to musí normalizovat na 0, jinak TypeError.
- content[i]["citations"] je u Anthropicu na ne-text blocích skalární null,
  ne prázdné pole — bez `or []` iterace spadne.
- Dashboard kód se NEMĚNÍ. Počet citací u Gemini vzroste o 146 % a je to
  správně: count(Citation.id) nově konzistentně znamená "počet
  claim-source vazeb", stejně jako to u Anthropicu/OpenAI platí odjakživa.
- Nic nepiš do /findings.

STACK: FastAPI + SQLAlchemy 2.0 + PostgreSQL, Jinja2 + HTMX (žádný
JavaScript framework), Alembic migrace, Docker Compose. Backend kód
anglicky vč. komentářů/error_code, UI texty přes t() mechanismus
v app/i18n/{en,de}.json — nikdy natvrdo v šabloně, oba jazyky v jednom
commitu.

KRITICKÁ PRAVIDLA:
- Evidence řádky (Run, RawResponse, Citation, SearchQuery) se nepřepisují.
  Backfill v GC-3 je jediná, výslovně odsouhlasená výjimka, a je legitimní
  jen proto, že citations je odvozenina z nedotčeného raw_payload —
  nepoužívej ji jako precedens pro nic dalšího.
- Router zůstává tenký — mapování dat žije v adapterech (app/adapters/),
  ne v routeru.
- Každá route funkce dostane docstring; každé netriviální pole popis.
- Nikdy git commit, git checkout -b ani git push bez mého výslovného
  potvrzení — i po tom, co si sám navrhneš commit message, čekáš na
  "ano, commitni", než cokoliv spustíš.

Po každém promptu ukaž implementation summary a navrhni commit message.
```

---
---

## PROMPT GC-1 — Schema: nové sloupce + migrace

```
Task: Prompt GC-1 — citations dostane source_passage a offsety odpovědi

Přečti docs/TASKS_GEMINI_CITATIONS.md úkol GC-T1 CELÝ, plus design
decision 6 (proč jsou claim a zdrojová pasáž dvě různá pole).

Schema flag je potvrzený (2026-09-16) — tři nové sloupce na citations,
všechny nullable, nic se neruší ani nepřejmenovává.

1. app/models/run.py, třída Citation — tři nové sloupce hned za
   cited_answer_span:
   - answer_span_start: Mapped[int | None] = mapped_column(Integer)
   - answer_span_end: Mapped[int | None] = mapped_column(Integer)
   - source_passage: Mapped[str | None] = mapped_column(Text)
   Rozšiř docstring třídy: cited_answer_span = úsek ODPOVĚDI, který zdroj
   podpírá; source_passage = pasáž ZE ZDROJOVÉ STRÁNKY. Krátce zmiň, který
   provider plní co (Gemini/OpenAI claim + offsety, Anthropic jen pasáž —
   jeho API offsety v odpovědi nevrací).

2. schema_phase1.sql — stejné tři sloupce do CREATE TABLE citations.

3. Zjisti aktuální alembic head (očekávám 0026, ale ověř).

4. Nová migrace alembic/versions/0027_citation_span_semantics.py:
   - upgrade(): ADD COLUMN pro všechny tři, nullable, bez defaultu
   - downgrade(): DROP COLUMN pro všechny tři
   - down_revision = zjištěný head

Po dokončení:
- docker compose exec app alembic upgrade head — bez chyby
- ověř sloupce: docker compose exec app python -c "from app.models.run import Citation; print([c.name for c in Citation.__table__.columns])"
- appka nastartuje, /runs/{id} se pořád načte
- implementation summary + navrhni commit message (git nespouštěj)
```

---

## PROMPT GC-2 — Adapter vrstva

```
Task: Prompt GC-2 — oprava Gemini many-to-many + přechod mapperů na dict

Přečti docs/TASKS_GEMINI_CITATIONS.md úkol GC-T2 CELÝ, plus design
decisions 1-6. Prerekvizita: GC-1 hotový a commitnutý.

Tvary payloadů jsou ověřené proti reálným datům v DB (design decision 5) —
neověřuj je znovu, ani proti dokumentaci, ani reálným voláním API.

1. app/adapters/base.py — AdapterCitation dostane answer_span_start,
   answer_span_end (int | None = None) a source_passage (str | None = None).
   Docstring definuje hranici claim vs. zdrojová pasáž JEDNOU pro všechny
   tři adaptery — ostatní docstringy na něj odkazují, neopakují ho.

2. app/adapters/google.py — PŘEPIŠ _map_citations: přijímá payload: dict
   (celý response.model_dump(mode="json")), ne types.Candidate.
   - chunks/supports z payload["candidates"][0]["grounding_metadata"],
     obranně (.get(...) or []); prázdné candidates → ([], False)
   - iteruj PŘES SUPPORTS, a pro každý přes všechny jeho
     grounding_chunk_indices → jedna AdapterCitation na dvojici
     (tohle je jádro opravy — dnešní kód jde přes chunks a breakne)
   - cited_answer_span = segment["text"]
   - answer_span_start = segment["start_index"] or 0  ← null znamená 0!
   - answer_span_end = segment["end_index"]
   - zdrojová pole (url/title/domain) z odpovídajícího chunku
   - chunk index mimo rozsah → dvojici přeskoč, nespadni
   - seřaď podle (answer_span_start, answer_span_end, chunk_index),
     citation_position = průběžný index 0..n-1
   - chunk, na který neukázal žádný support → vlastní řádek se span/offsety
     None, zařazený ZA všechny dvojice, v původním pořadí chunků
   - source_passage zůstává None (doplní roadmap #12)
   - has_citations beze změny: False, když nejsou žádné chunky
   _resolve_source_domain a _map_search_queries převeď na dict, ale jinak
   je nech být — jejich logika je správná.

3. app/adapters/anthropic.py — _map_citations přijímá payload: dict, jde
   přes payload["content"], bloky type == "text". POZOR: block["citations"]
   může být skalární null, ne pole. Smyčku ani pořadí NEMĚŇ — mění se jen:
   cited_text → source_passage, cited_answer_span = None, offsety None.
   Do docstringu napiš, proč tam offsety nikdy nebudou (API je nevrací,
   encrypted_index je index do výsledků vyhledávání, ne do textu odpovědi).

4. app/adapters/openai.py — _map_citations přijímá payload: dict
   (output → message items → output_text bloky → url_citation anotace).
   Smyčku NEMĚŇ, jen doplň answer_span_start/answer_span_end ze
   start_index/end_index. cited_answer_span dál slice z textu bloku,
   source_passage = None.

5. Ve všech třech run() metodách: payload = response.model_dump(mode="json")
   spočítej JEDNOU a předej ho mapperu i do RawResponsePayload.raw_payload.
   _map_search_queries převeď na dict taky — ať v jednom souboru nevedle
   sebe nežijí dva různé přístupy ke stejné odpovědi.

6. app/routers/runs.py (~ř. 388) — do Citation(...) doplň tři nová pole.

7. tests/test_adapters.py — existující search-query testy jsou psané proti
   SimpleNamespace a po tomhle přechodu budou padat. Přepiš je na dicty
   TEĎ, ne až v GC-5.

Po dokončení:
- pytest zelený (staré testy včetně přepsaných)
- reálný Gemini run (gemini-3.1-flash-lite) — ZEPTEJ SE MĚ, na kterém
  klientovi/promptu ho spustit, nevybírej sám. Na run detailu musí být
  citací viditelně víc než dřív a cited_answer_span musí odpovídat úseku
  odpovědi.
- reálný Anthropic run — ověř v psql, že cited_answer_span je NULL
  a source_passage vyplněný (zobrazení přijde až v GC-4)
- implementation summary + navrhni commit message (git nespouštěj)
```

---

## PROMPT GC-3 — Backfill historických citací

```
Task: Prompt GC-3 — migrace 0028, re-extrakce citací z raw_payload

Přečti docs/TASKS_GEMINI_CITATIONS.md úkol GC-T3 CELÝ, plus design
decision 7 a sekci "⚠️ Evidence flag". Prerekvizita: GC-2 hotový
a commitnutý.

Evidence flag je potvrzený (2026-09-16): tahle migrace SMÍ mazat a znovu
vytvářet citations řádky, protože jsou odvozenina z raw_payload, který
zůstává nedotčený. raw_responses a runs se nepřepisují — jen SELECT.

1. Nejdřív mi ukaž výchozí stav (dotaz je v GC-T3) — očekávám
   Anthropic 158, Gemini 551, OpenAI 71.

2. alembic/versions/0028_backfill_citations_from_raw_payload.py:
   - projdi raw_responses po dávkách, ne všechno najednou
   - provider zjisti z DB (runs → ai_models → providers), NE hádáním
     z tvaru payloadu
   - podle providera zavolej odpovídající _map_citations z app/adapters/*
     nad uloženým raw_payload (tatáž funkce jako live cesta — to je celý
     smysl přechodu na dict v GC-2)
   - DELETE citations pro danou raw_response + INSERT nových řádků
   - raw response, ze které mapper nevytáhne nic a která dnes nemá žádné
     citations řádky → přeskoč beze změny
   - migrace NESMÍ importovat google.genai / anthropic / openai SDK.
     Ověř spuštěním, ne úvahou.
   - downgrade(): pass + komentář proč (zpětná cesta je ztrátová
     a zbytečná, raw_payload je pořád k dispozici — stejný precedent jako
     0026_drop_flat_model_prices.py)
   - docstring: co dělá, proč je legitimní sáhnout na evidence tabulku,
     a naměřená očekávaná čísla

3. docker compose exec app alembic upgrade head

4. Ověř OBĚMA kontrolními dotazy z GC-T3:
   a) počty per provider — Gemini 1 354, Anthropic 158, OpenAI 71
   b) sémantická kontrola — with_claim == claim_in_answer u Gemini
      i OpenAI; u Anthropicu with_claim = 0 a with_source_passage = 158
   c) křížová kontrola proti payloadu (dotaz z design decision 1) musí dát
      stejné číslo jako reálný počet Gemini řádků

5. Ověř idempotenci — zavolej přepočítávací funkci podruhé proti už
   zmigrované DB přes docker compose exec app python -c (NE přes
   alembic stamp, ať se neplete revizní historie). Čísla se nesmí změnit.

Po dokončení:
- appka nastartuje, run detail starého Gemini runu ukazuje víc citací
- implementation summary + navrhni commit message (git nespouštěj)
```

---

## PROMPT GC-4 — Zobrazení a export

```
Task: Prompt GC-4 — run detail rozliší claim a zdrojovou pasáž

Přečti docs/TASKS_GEMINI_CITATIONS.md úkol GC-T4 CELÝ. Prerekvizita:
GC-3 hotový a commitnutý.

1. app/templates/runs/detail.html, citační sekce (~ř. 146-161) — dnes je
   tam jeden kurzívní blok s cited_answer_span. Nově dva bloky, každý pod
   svým {% if %}:
   - cited_answer_span s popiskem t('run.citation_claim_label')
   - source_passage s popiskem t('run.citation_source_passage_label')
   U každého providera je vyplněná jen jedna větev, takže prázdná nesmí
   zabírat místo. answer_span_start/_end NEZOBRAZUJ — to jsou
   implementační data pro roadmap #12, ne informace pro uživatele.
   Reuse existující markup a styl sekce, nedělej novou komponentu.

2. app/i18n/en.json + de.json (JEDEN commit, oba jazyky):
   run.citation_claim_label, run.citation_source_passage_label.
   Dej je vedle stávajících run.citations_label / run.citations_empty.

3. app/services/export.py — do CITATION_COLUMNS (~ř. 63) přidej
   source_passage, answer_span_start, answer_span_end (za cited_answer_span,
   ať se existující exporty nerozhodí víc než musí); stejně do
   _citation_rows() (~ř. 221) a do JSON větve (~ř. 372).

Po dokončení:
- docker compose up -d --build
- v prohlížeči zkontroluj: Gemini run (claim ano, pasáž prázdná),
  Anthropic run (obráceně), starý historický Gemini run (po backfillu víc
  citací), run bez citací (prázdný stav, ne chyba)
- OVĚŘ NA ~375px / ~768px / DESKTOP a napiš mi, že jsi to reálně proklikal
  — sekce má nově dva textové bloky na položku, na mobilu se to musí dát
  číst
- stáhni export ve všech třech formátech, zkontroluj nové sloupce
- implementation summary + navrhni commit message (git nespouštěj)
```

---

## PROMPT GC-5 — Testy

```
Task: Prompt GC-5 — pokrytí _map_citations u všech tří providerů

Přečti docs/TASKS_GEMINI_CITATIONS.md úkol GC-T5 CELÝ. Prerekvizita:
GC-4 hotový a commitnutý.

Kontext: tests/test_adapters.py dnes testuje JEN _map_search_queries. Na
_map_citations neexistuje ani jeden test, u žádného providera — to je
vlastní příčina, proč tenhle bug přežil tři fáze. Tenhle prompt tu díru
zavírá.

1. Gemini _map_citations (fixtures jako dicty, tvar viz design decision 5):
   - jeden support → tři chunky = TŘI řádky, stejný span, různé zdroje
   - jeden chunk → dva supports = DVA řádky, stejný zdroj, různé spany
     ← tohle je regresní test na původní break bug, pojmenuj ho tak
   - segment.start_index = None u prvního segmentu → answer_span_start == 0
     a řazení nespadne
   - supports zamíchané na vstupu → citation_position odpovídá pořadí
     v odpovědi, ne pořadí na vstupu
   - chunk bez jakéhokoliv supportu → vlastní řádek, cited_answer_span
     is None, zařazený poslední
   - grounding_chunk_indices s indexem mimo rozsah → přeskočeno, nespadne
   - chybějící grounding_metadata / prázdné candidates → ([], False)

2. Anthropic _map_citations: cited_text → source_passage,
   cited_answer_span is None, offsety None; blok s "citations": null
   nespadne; víc text bloků → průběžný citation_position.

3. OpenAI _map_citations: offsety uložené, cited_answer_span nařezaný
   podle nich, source_passage is None.

4. tests/test_runs.py — přes FakeAdapter ověř, že run-trigger uloží
   všechna tři nová pole a že se na detailu zobrazí správná větev.

5. tests/test_export.py — nové sloupce ve všech třech formátech.

6. Jeden test, který volá Gemini mapper nad realisticky tvarovaným
   raw_payload dictem a ověří shodu s live cestou — to je přesně tvrzení
   design decision 5, na kterém stojí backfill.

Po dokončení:
- pytest zelený, staré i nové
- napiš mi, kolik testů celkem a kolik z toho je nových
- implementation summary + navrhni commit message (git nespouštěj)
```

---

## PROMPT GC-6 — Dokumentace

```
Task: Prompt GC-6 — docs update

NEJDŘÍV SE ZEPTEJ, jestli jsem potvrdil, že GC-1 až GC-5 reálně fungují.
Pokud jsem to v týhle konverzaci nepotvrdil, ZASTAV a zeptej se —
AI_INSTRUCTIONS.md §7 bod 3: docs se aktualizují až po mém potvrzení, ne
po tvém vlastním ověření.

Přečti docs/TASKS_GEMINI_CITATIONS.md úkol GC-T6 CELÝ.

1. docs/ROADMAP.md #11 — označ jako hotové. Doplň, co se reálně ukázalo:
   ne "zploštělá 1:1 citace", ale 59 % ztracených claim-source vazeb
   (551 z 1 354) ve 40 z 52 odpovědí, a že součástí opravy bylo
   i rozdělení sémantiky spanu.

2. docs/ROADMAP.md #12 — oprav větu "Anthropic runy: zdrojová pasáž už je
   uložená (citations[].cited_text přes dnešní cited_answer_span pole)".
   Nově: pasáž je v source_passage, a u Anthropicu naopak chybí claim,
   protože API offsety v odpovědi nevrací. Je to změna vstupních
   předpokladů #12, ne kosmetika — napiš to tak.

3. docs/ROADMAP.md — nová položka: podpora nového Gemini 3
   steps/url_citation tvaru odpovědi (design decision 9). Jen popis,
   nezakládej pro ni větev ani vlastní dokumenty.

4. docs/REQUIREMENTS.md FR-12 (ř. 44) — rozšíř o rozdělení claim /
   zdrojová pasáž a o to, že jedna odpověď může mít víc vazeb na tentýž
   zdroj a víc zdrojů k témuž tvrzení.

5. docs/REQUIREMENTS.md — poznámka k významu počtu citací: počet citations
   řádků = počet claim-source vazeb, ne počet zdrojů; pro počet zdrojů je
   count(distinct source_domain).

6. docs/TASKS.md — nová sekce v "Beyond phase 1 scope" s odkazem na tuhle
   větev, stejný formát jako ostatní sekce.

NEPIŠ NIC DO /findings — to je moje editorial rozhodnutí, ne tvoje, i kdyby
se zjištění z téhle větve do jeho rozsahu trefovalo.

Po dokončení:
- ukaž mi diff dokumentace
- implementation summary + navrhni commit message (git nespouštěj)
```

---
---

## Po dokončení všech promptů

1. Projdi Completion Checklist v docs/TASKS_GEMINI_CITATIONS.md — všechny
   položky odškrtnuté.
2. Zeptej se mě na merge do master (AI_INSTRUCTIONS.md §9 — merge ani push
   bez mého výslovného pokynu).
3. Po merge: docs/TASKS.md sekce "Citation extraction fix" (poslední
   nezaškrtnutá položka checklistu).
4. Roadmap #12 (LLM quote-verification skill) je tímhle odblokovaná —
   ale má vlastní docs/TASKS_QUOTE_VERIFICATION.md a vlastní větev,
   nezačínej ji jako pokračování téhle.
