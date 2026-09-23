# SignalMap — Tasks: Oprava extrakce citací (Gemini) + sémantika spanů

## Status: ✅ Done — PR #15, merged 2026-09-16, released in v1.0.0

## v1.0 | Září 2026
## Branch: feature/signalmap-gemini-citation-extraction
## Task ID prefix: GC
## Roadmap: `docs/ROADMAP.md` #11 (blokuje #12 — LLM quote-verification skill)

> Roadmap #11 popisuje jeden defekt: `app/adapters/google.py::_map_citations`
> bere jen **první** `grounding_support`, který odkazuje na daný chunk, a
> zbytek zahodí (`break`) — Gemini přitom vrací many-to-many vazbu mezi
> segmenty odpovědi a zdroji. Při přípravě téhle práce (2026-09-16) se
> proměřením produkčních dat potvrdilo, že defekt je větší, než roadmapa
> odhadovala, a že vedle něj existuje **druhý, nezávislý defekt v sémantice
> sloupce `citations.cited_answer_span`**, který se týká Anthropic adapteru
> a je v přímém rozporu s FR-12. Oba se opravují tady, v jedné větvi —
> důvod viz design decision 6.
>
> Není to jedna z pěti roadmap fází ze skillu `signalmap-conventions`. Je to
> oprava extrakce dat, která `raw_payload` už dnes obsahuje (FR-10), plus
> aditivní rozšíření `citations` o pole, která #12 bude potřebovat.

---

## Naměřený stav (2026-09-16, produkční DB, 52 Gemini odpovědí)

Všechna čísla níže jsou změřená dotazy nad `raw_responses.raw_payload`
v běžící databázi, ne odhad — opakovatelný dotaz je v design decision 1.

| metrika | hodnota |
|---|---|
| `grounding_chunks` celkem (= dnes uložené `citations` řádky) | **551** |
| `grounding_supports` celkem | 740 |
| reálných dvojic (segment odpovědi → zdroj) | **1 354** |
| **ztracených claim-source vazeb** | **803 = 59 %** |
| odpovědí, kde ke ztrátě dochází | **40 z 52** |
| chunků, na které neukazuje žádný support | **0** |
| chunků / unikátních URL (duplicitní chunky existují) | 551 / 514 |
| supports s `segment.start_index = null` | 30 z 740 (vždy první segment, `null` ≡ 0) |
| supports s `segment.end_index = null` nebo `segment.text = null` | 0 |

Druhý defekt — co který provider dnes ukládá do `cited_answer_span`
(měřeno přes `position(cited_answer_span in rendered_text) > 0`):

| provider | řádků se spanem | span nalezen v `rendered_text` | co to reálně je |
|---|---|---|---|
| Google Gemini | 551 | 551 (100 %) | úsek **odpovědi** (`segment.text`) |
| OpenAI ChatGPT | 71 | 71 (100 %) | úsek **odpovědi** (slice přes `start_index`/`end_index`) |
| Anthropic Claude | 158 | **17 (11 %)** | pasáž **ze zdrojové stránky** (`citation.cited_text`) |

Těch 17 shod u Anthropicu je náhoda na krátkých řetězcích, ne signál.
`docs/REQUIREMENTS.md:44` (FR-12) přitom sloupec definuje jako *„the answer
span it supports"* — Anthropic adapter tedy dnes plní jiné pole, než
požadavek popisuje.

Mechanika many-to-many je u Anthropicu i OpenAI v pořádku a nemění se:
oba už dnes emitují jeden řádek na dvojici (tvrzení, zdroj), takže obsahují
duplicitní URL záměrně (Anthropic 158 řádků / 105 unikátních
`(response, url)`, OpenAI 71 / 69). **Gemini je jediný provider s vadnou
extrakcí** — u ostatních dvou se mění jen to, do kterého sloupce se která
část dat ukládá.

---

## ⚠️ Schema flag (AI_INSTRUCTIONS.md §4)

Tahle práce přidává **tři nové sloupce na `citations` nad rámec
`schema_phase1.sql`**: `source_passage`, `answer_span_start`,
`answer_span_end`. Žádná nová tabulka, žádný sloupec se neruší, žádný se
nepřejmenovává — čistě aditivní změna.

**Potvrzeno uživatelem 2026-09-16** (rozhodnutí „A + B", tj. oprava
many-to-many *i* rozdělení sémantiky spanu v jedné větvi).

## ⚠️ Evidence flag (AI_INSTRUCTIONS.md §3)

GC-T3 (backfill) **přepisuje existující `citations` řádky** — u Gemini je
maže a znovu vytváří z `raw_payload`, u Anthropicu přesouvá obsah mezi
sloupci. `citations` je v AI_INSTRUCTIONS.md §3 vyjmenovaná jako evidence
tabulka, takže tohle není rutinní operace.

Zdůvodnění, proč je to přesto legitimní: `raw_payload` je skutečná evidence
(FR-10) a **zůstává nedotčený** — `citations` je z něj odvozený,
dotazovatelný index, ne nezávislý záznam. Backfill nepřidává ani nemění
žádnou informaci, jen přepočítává odvozeninu ze stejného, nezměněného
zdroje. `raw_responses` migrace nečte jinak než `SELECT`em.

**Potvrzeno uživatelem 2026-09-16** (rozhodnutí „Ano, backfillovat").

---

## Design decisions

### 1. Jeden řádek na dvojici (support × chunk index), ne na chunk

Dnešní smyčka jde přes `grounding_chunks` a pro každý hledá první
odpovídající support. Nová jde **přes `grounding_supports`** a pro každý
prochází všechny jeho `grounding_chunk_indices`:

```
for support in grounding_supports:
    for chunk_index in support.grounding_chunk_indices:
        → jedna AdapterCitation (segment support-u × zdroj toho chunku)
```

Tím vznikne 1 354 řádků místo 551 — obě strany many-to-many vazby jsou
zachycené: jeden segment podepřený třemi zdroji dá tři řádky, jeden zdroj
podpírající pět segmentů taky pět řádků.

Dotaz, kterým se čísla ověřila (opakovatelný proti běžící DB):

```sql
with g as (
  select rr.id,
    jsonb_array_length(coalesce(rr.raw_payload->'candidates'->0->'grounding_metadata'->'grounding_chunks','[]'::jsonb)) as chunks,
    (select count(*)
       from jsonb_array_elements(coalesce(rr.raw_payload->'candidates'->0->'grounding_metadata'->'grounding_supports','[]'::jsonb)) s,
            jsonb_array_elements(coalesce(s->'grounding_chunk_indices','[]'::jsonb)) i) as pairs
  from raw_responses rr
  join runs r on r.id = rr.run_id
  join ai_models m on m.id = r.model_id
  where m.model_name like 'gemini%'
)
select sum(chunks) as dnes, sum(pairs) as po_opravě,
       sum(case when pairs > chunks then 1 else 0 end) as dotčených_odpovědí
from g;
```

Tenhle dotaz je zároveň **kontrola správnosti backfillu** (GC-T3): počet
Gemini `citations` řádků po migraci musí sedět na `po_opravě`.

### 2. `citation_position` = pořadí v odpovědi, ne pořadí zdroje

Dnes je u Gemini `citation_position` index chunku, tedy pořadí, ve kterém
provider vrátil zdroje (zhruba pořadí výsledků vyhledávání). U Anthropicu
i OpenAI je to pořadí výskytu v odpovědi. Dvě různé sémantiky v jednom
sloupci.

Po opravě se dvojice **řadí podle `segment.start_index`** (sekundárně podle
`segment.end_index`, terciárně podle indexu chunku, ať je pořadí
deterministické i u shodných offsetů) a `citation_position` je průběžný
index 0..n-1 v tomhle pořadí — stejná sémantika jako u zbylých dvou
providerů.

**Past, na kterou se musí myslet:** `segment.start_index` je u prvního
segmentu `null`, ne `0` (30 ze 740 supports, ověřeno — a vždy jen tam, kde
je `end_index` malý, tedy jde skutečně o začátek odpovědi). Naivní
`sorted(..., key=lambda s: s["start_index"])` na `None` spadne
s `TypeError`. Normalizuj `None → 0` **před** řazením a stejnou
normalizovanou hodnotu ulož do `answer_span_start`.

### 3. Chunk bez supportu se uloží taky, se `cited_answer_span = None`

V dnešních datech je takových chunků **0**, takže to není oprava
existujícího problému — je to pojistka, aby nová smyčka (která jde přes
supports) nemohla zdroj tiše zahodit, kdyby ho Gemini jednou vrátil bez
navázaného segmentu. Takové řádky se zařadí **za** všechny dvojice, ve svém
původním pořadí chunků.

Důvod: `has_citations` a seznam zdrojů jsou evidence (FR-12/FR-13) —
„zdroj, který model našel, ale nepoužil ke konkrétnímu tvrzení" je legitimní
informace, ne šum.

### 4. Žádná deduplikace URL

551 chunků nese 514 unikátních URL, tedy Gemini sám občas vrátí tentýž
zdroj jako dva chunky. Nededuplikuje se — ani chunky mezi sebou, ani
výsledné dvojice. Stejný princip jako u Anthropicu/OpenAI dnes: jeden řádek
= jedna vazba, kterou provider reálně vrátil. Zplošťování evidence je přesně
to, co tahle větev opravuje.

### 5. Mapování ze serializovaného dictu, ne z SDK objektů

Dnešní mappery čtou SDK objekty přes `getattr(...)`. Nově čtou **dict**,
konkrétně tentýž `response.model_dump(mode="json")`, který se stejně už
počítá pro `raw_payload`:

```python
payload = response.model_dump(mode="json")
citations, has_citations = _map_citations(payload)
...
return RawResponsePayload(raw_payload=payload, ...)
```

Důvod je přímočarý: **backfill (GC-T3) musí použít úplně stejnou funkci**
nad `raw_payload` z databáze. Kdyby mapper uměl jen SDK objekty, existovaly
by dvě nezávislé kopie téže logiky (live extrakce a re-extrakce), které se
časem rozejdou — a #12 bude re-extrakci nad `raw_payload` potřebovat znovu.
Jedna funkce, dvě volání.

Tvary dictů jsou **ověřené proti reálným uloženým payloadům v produkční DB**
(2026-09-16), ne odvozené z dokumentace ani z paměti:

Gemini — `candidates[0].grounding_metadata.grounding_supports[i]`:
```json
{
  "segment": {"text": "Die Marke Skoda genießt …", "start_index": null, "end_index": 84, "part_index": null},
  "grounding_chunk_indices": [0, 1],
  "confidence_scores": null,
  "rendered_parts": null
}
```
a `…grounding_chunks[i]` = `{"web": {"uri": …, "title": …, "domain": …}}`
(pozn.: `uri` je vertexaisearch redirect, `domain` v praxi `null`, `title`
je holá doména — logika `_resolve_source_domain` se **nemění**).

Anthropic — `content[i].citations[j]` na `type == "text"` blocích:
```json
{
  "type": "web_search_result_location",
  "url": "https://…", "title": "…",
  "cited_text": "Zweifellos hat sich Skoda …",
  "encrypted_index": "Eo8BCioIExgC…"
}
```
Pozor: `content[i]["citations"]` je na ne-text blocích **skalární `null`**,
ne chybějící klíč ani prázdné pole — `or []` je nutné, jinak iterace spadne.

OpenAI — `output[i].content[j].annotations[k]` na `type == "message"` /
`output_text`:
```json
{"type": "url_citation", "url": "https://…", "title": "…", "start_index": 1538, "end_index": 1690}
```

### 6. Rozdělení sémantiky spanu — tři nové sloupce

| sloupec | význam | Gemini | Anthropic | OpenAI |
|---|---|---|---|---|
| `cited_answer_span` (existuje) | úsek **odpovědi**, který zdroj podpírá | `segment.text` | **NULL** (změna) | slice z `text` |
| `answer_span_start` (nový, INTEGER) | offset začátku spanu v `rendered_text` | `segment.start_index` (`None` → 0) | NULL | `start_index` |
| `answer_span_end` (nový, INTEGER) | offset konce spanu v `rendered_text` | `segment.end_index` | NULL | `end_index` |
| `source_passage` (nový, TEXT) | pasáž **ze zdrojové stránky** | NULL (doplní #12 fetchem) | `citation.cited_text` (změna) | NULL |

Proč to patří do téhle větve, a ne až do #12:

1. **FR-12 rozpor.** Dnešní stav není „jiná, ale platná interpretace" —
   požadavek sloupec definuje jako answer span a Anthropic tam dává zdrojový
   text. To je vada, ne designové rozhodnutí, a opravuje se tam, kde se sahá
   do stejných řádků.
2. **Jedna re-extrakční migrace místo dvou.** GC-T3 stejně přepočítá každý
   `citations` řádek z `raw_payload`. Kdyby se sloupce přidávaly až v #12,
   napíše se a spustí tatáž migrace podruhé nad týmiž daty.
3. **#12 potřebuje obojí zvlášť.** Quote-verification porovnává *claim*
   proti *zdrojové pasáži*. Dnes má u Anthropicu jen pasáž a u Gemini/OpenAI
   jen claim — v jednom sloupci se stejným jménem, takže skill by musel
   odvozovat význam podle providera.

`answer_span_start`/`_end` u Anthropicu zůstávají trvale `NULL` — API tam
žádné offsety v odpovědi nevrací (`web_search_result_location` nese
`encrypted_index`, což je index do výsledků vyhledávání, ne do textu
odpovědi). Není to mezera k pozdějšímu doplnění, je to vlastnost API; patří
to do docstringu `anthropic.py`, ať to nikdo nezkouší „opravit".

### 7. Backfill re-extrakcí, idempotentní, s ověřitelným výsledkem

Migrace `0028` projde všechny `raw_responses` a přepočítá jejich
`citations`:

- **Gemini** — smaže existující `citations` řádky dané raw response
  a vytvoří nové zavoláním `_map_citations` z `google.py` nad uloženým
  `raw_payload` (design decision 5). 551 → očekávaných 1 354 řádků.
- **Anthropic** — stejným způsobem přes `_map_citations` z `anthropic.py`;
  efekt je přesun `cited_answer_span` → `source_passage` a vynulování
  `cited_answer_span`. (Čistý SQL `UPDATE` by stačil, ale re-extrakce
  garantuje, že výsledek je přesně to, co by uložil dnešní adapter — žádná
  druhá, ručně psaná verze téhož mapování.)
- **OpenAI** — stejným způsobem; doplní `answer_span_start`/`_end`
  historickým řádkům.

Vlastnosti, které migrace musí mít:

- **Idempotentní** — opakované spuštění dá stejný výsledek (plyne z toho, že
  vstupem je vždy nedotčený `raw_payload`, ne stávající `citations`).
- **Nesahá na `raw_responses`** ani na `runs` — jen `SELECT`.
- **Přeskočí** raw response bez `citations` řádků a bez rozpoznatelných
  citací v payloadu (žádné prázdné řádky navíc).
- **Nesmí importovat SDK typy** (`google.genai`, `anthropic`, `openai`) —
  mapper po GC-T2 pracuje nad dictem, takže import z `app.adapters.*`
  nestahuje nic, co by v migračním kontextu chybělo. Ověř to spuštěním.
- `downgrade()` **nedělá nic** a říká proč: zpětná cesta z 1 354 dvojic na
  551 zploštělých řádků je ztrátová a neexistuje důvod ji chtít —
  `raw_payload` je pořád k dispozici, takže „rollback" znamená spustit
  starou verzi mapperu, ne obrátit data. (Stejný precedent jako
  `0026_drop_flat_model_prices.py`, jehož `downgrade()` taky vědomě nevrací
  data.)

### 8. Dashboard se nemění, jen se dokumentuje

Gemini citace 551 → 1 354 (+146 %) ovlivní:

- `citation_totals()` (`app/services/dashboard.py:128`) — celkový počet
  citací roste; počet unikátních domén se **nemění**;
- `domain_league_rows()` (`app/services/dashboard.py:269`) — doména
  podpírající 5 segmentů se započítá 5×, ne 1×;
- export CSV/XLSX/JSON — víc řádků v citační sekci.

**Rozhodnutí uživatele (2026-09-16): kód dashboardu se nemění.**
`count(Citation.id)` nově konzistentně napříč providery znamená *„počet
claim-source vazeb"* — což je správný význam a ten, který Anthropic/OpenAI
mají odjakživa. Dnešní stav není „stabilní metrika", je to Gemini
podhodnocené vůči zbytku.

Historická čísla se posunou nahoru — ale díky backfillu (GC-T3)
**konzistentně přes celou historii**, ne skokem v místě nasazení. To je
hlavní praktický důvod, proč backfill a oprava adapteru musí do produkce
společně, ne odděleně.

Zdokumentovat: poznámku k FR-12 v `docs/REQUIREMENTS.md` a k #11
v `docs/ROADMAP.md` (GC-T6).

### 9. Gemini 3 mění tvar odpovědi — mimo rozsah, ale nepadat na tom

Aktuální dokumentace (`ai.google.dev/gemini-api/docs/google-search`,
aktualizace 2026-09-02, ověřeno 2026-09-16) popisuje pro Gemini 3 **jiný
tvar**: `steps` → `model_output.content[].annotations[]` typu `url_citation`
se `start_index`/`end_index` — tedy stejný model jako OpenAI, ne
`grounding_metadata`.

Ověřeno proti reálným datům: přes `google-genai==2.22.0` a
`client.models.generate_content(tools=[GoogleSearch()])` chodí i u
`gemini-3.1-flash-lite` a `gemini-3.5-flash` **pořád starý
`grounding_metadata` tvar** (52 z 52 odpovědí). Oprava v téhle větvi je tedy
správná pro dnešek.

`docs/TASKS_SEARCH_QUERIES.md` design decision 4 si stejného rozporu všiml
už 2026-09-10 a uzavřel ho stejně.

V rozsahu téhle větve: `_map_citations` u Gemini musí na payloadu **bez**
`grounding_metadata` vrátit `([], False)`, ne spadnout (to platí i dnes, jen
to nemá test — GC-T5 ho přidá). **Mimo rozsah:** detekce a mapování nového
`steps`/`url_citation` tvaru — to je samostatná roadmap položka, až SDK/API
tvar reálně přepne. Nezakládej ji v téhle větvi, jen ji navrhni v GC-T6.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| GC-T1 | Schema: `citations` nové sloupce + migrace `0027` | ⏳ |
| GC-T2 | Adapter vrstva: dict-based mapping ve všech třech adapterech + perzistence | ⏳ |
| GC-T3 | Backfill: migrace `0028` — re-extrakce citací z `raw_payload` | ⏳ |
| GC-T4 | Zobrazení: run detail + i18n + export | ⏳ |
| GC-T5 | Testy: pytest pokrytí mappingu, perzistence, exportu | ⏳ |
| GC-T6 | Docs: ROADMAP #11, REQUIREMENTS FR-12, TASKS.md — až po potvrzení uživatelem | ⏳ |

Pořadí vynucené: GC-T2 potřebuje sloupce z GC-T1. GC-T3 potřebuje mappery
z GC-T2 (volá je). GC-T4 zobrazuje to, co GC-T3 dopočítal — proto až po něm,
ať se v prohlížeči kontrolují opravená historická data, ne prázdné sloupce.
GC-T5 testuje všechno předchozí. GC-T6 až úplně na konec, po uživatelově
potvrzení, že to funguje (AI_INSTRUCTIONS.md §7 bod 3).

---

## GC-T1 — Schema: nové sloupce + migrace

**Target:** `app/models/run.py`, `schema_phase1.sql`, nová `alembic/versions/0027_citation_span_semantics.py`

Prerekvizita: schema flag výše potvrzený (je — 2026-09-16).

1. `app/models/run.py`, třída `Citation` — tři nové sloupce za
   `cited_answer_span`:
   ```python
   answer_span_start: Mapped[int | None] = mapped_column(Integer)
   answer_span_end: Mapped[int | None] = mapped_column(Integer)
   source_passage: Mapped[str | None] = mapped_column(Text)
   ```
   Docstring třídy rozšiř o to, co který sloupec znamená a proč jsou
   `cited_answer_span` (claim) a `source_passage` (zdroj) dvě různá pole —
   krátce, s odkazem na design decision 6.
2. `schema_phase1.sql` — stejné tři sloupce do `CREATE TABLE citations`.
3. `alembic heads` — ověř head (očekáváno `0026`, ověř, že mezitím nic
   nepřibylo).
4. Nová migrace `0027_citation_span_semantics.py` — `ADD COLUMN` pro všechny
   tři (všechny nullable, žádný default), `down_revision = "0026"`,
   `downgrade()` je dropne.

Po dokončení:
1. `docker compose exec app alembic upgrade head` — bez chyby.
2. `docker compose exec app python -c "from app.models.run import Citation; print([c.name for c in Citation.__table__.columns])"` —
   nové sloupce v seznamu.
3. Appka nastartuje bez chyby, run detail se pořád načte (nové sloupce se
   zatím nikde nečtou).
4. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
feat(schema): split citation answer span from source passage
```

---

## GC-T2 — Adapter vrstva

**Target:** `app/adapters/base.py`, `app/adapters/google.py`, `app/adapters/anthropic.py`, `app/adapters/openai.py`, `app/routers/runs.py`

Prerekvizita: GC-T1 hotový.

Tvary payloadů jsou ověřené (design decision 5, 2026-09-16, proti reálným
datům v produkční DB) — **žádné další ověřování před psaním kódu není
potřeba**.

1. `app/adapters/base.py` — `AdapterCitation` dostane
   `answer_span_start: int | None = None`, `answer_span_end: int | None = None`,
   `source_passage: str | None = None`. Docstring popíše rozdíl mezi
   `cited_answer_span` a `source_passage` (viz design decision 6) — tenhle
   docstring je to místo, kde se ta hranice definuje jednou pro všechny tři
   adaptery.
2. `app/adapters/google.py` — **přepsat `_map_citations`**, nově přijímá
   `payload: dict` (celý `response.model_dump(mode="json")`), ne
   `types.Candidate`:
   - `chunks` a `supports` z
     `payload["candidates"][0]["grounding_metadata"]` — obojí obranně
     (`.get(...) or []`, prázdné `candidates` → `([], False)`);
   - pro každý support × každý index v `grounding_chunk_indices` jedna
     `AdapterCitation`: `cited_answer_span = segment["text"]`,
     `answer_span_start = segment["start_index"] or 0` (design decision 2 —
     `None` znamená 0!), `answer_span_end = segment["end_index"]`, zdrojová
     pole z odpovídajícího chunku;
   - index mimo rozsah `chunks` (poškozený payload) → dvojici přeskoč,
     nespadni;
   - seřaď podle `(answer_span_start, answer_span_end, chunk_index)`, přiřaď
     `citation_position` 0..n-1;
   - chunky, na které neukázal žádný support, připoj **za** ně, ve svém
     pořadí, se `cited_answer_span`/offsety `None` (design decision 3);
   - `source_passage` zůstává `None` (doplní #12);
   - `has_citations` beze změny: `False`, když nejsou žádné chunky.

   `_resolve_source_domain` a `_map_search_queries` **nepřepisuj** víc, než
   je nutné pro přechod na dict (`web.get("domain")` místo `getattr`) —
   jejich logika je správná a otestovaná.
3. `app/adapters/anthropic.py` — `_map_citations` přijímá `payload: dict`,
   jde přes `payload["content"]`, bloky `type == "text"`, jejich `citations`
   (**pozor: může být skalární `null`**, design decision 5). `cited_text` →
   **`source_passage`**, `cited_answer_span` = `None`, offsety `None`.
   Docstring: proč tam offsety nikdy nebudou (API je nevrací,
   `encrypted_index` je index do výsledků vyhledávání, ne do textu
   odpovědi).
4. `app/adapters/openai.py` — `_map_citations` přijímá `payload: dict`,
   `output` → `message` items → `output_text` bloky → `url_citation`
   anotace. `cited_answer_span` = slice z `text` (jako dnes), nově
   i `answer_span_start`/`answer_span_end` = `start_index`/`end_index`.
   `source_passage` = `None`.
5. Ve všech třech `run()` metodách: spočítej
   `payload = response.model_dump(mode="json")` **jednou**, předej ho
   mapperu i do `RawResponsePayload.raw_payload`. `_map_search_queries`
   převeď na dict taky, ať v jednom souboru nevedle sebe nežijí dva různé
   přístupy k téže odpovědi.
6. `app/routers/runs.py` (~ř. 388) — do `Citation(...)` doplň tři nová pole
   z `AdapterCitation`.

Po dokončení:
1. `pytest` — existující sada zelená. Search-query testy jsou dnes psané
   proti `SimpleNamespace`; **po přechodu na dict budou padat — přepiš je na
   dicty v rámci tohohle tasku**, ne až v GC-T5.
2. Reálný run na Gemini (`gemini-3.1-flash-lite`; klient/prompt si nech
   určit uživatelem) — na run detailu musí být citací **viditelně víc než
   dřív** a `cited_answer_span` musí sedět na úsek odpovědi.
3. Reálný run na Anthropicu — `cited_answer_span` prázdný, pasáž ze zdroje
   uložená (zobrazí ji až GC-T4; do té doby ověř v psql:
   `select cited_answer_span, source_passage from citations where …`).
4. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
fix(adapters): map every Gemini grounding support-chunk pair as its own citation
```

---

## GC-T3 — Backfill historických citací

**Target:** nová `alembic/versions/0028_backfill_citations_from_raw_payload.py`

Prerekvizita: GC-T1 a GC-T2 hotové. Evidence flag výše potvrzený
(je — 2026-09-16).

**Před psaním migrace** si zapiš výchozí stav, ať je co porovnat:
```sql
select p.name, count(*) from citations c
  join raw_responses rr on rr.id = c.raw_response_id
  join runs r on r.id = rr.run_id
  join ai_models m on m.id = r.model_id
  join providers p on p.id = m.provider_id
group by 1 order by 1;
-- očekáváno před migrací: Anthropic 158, Gemini 551, OpenAI 71
```

1. Migrace projde `raw_responses` po dávkách (ne všechno najednou — dnes je
   to 86 řádků, ale migrace přežije i řádově víc), pro každou podle
   providera runu zvolí odpovídající `_map_citations` a přepočítá.
2. Provider se zjišťuje **z DB** (`runs` → `ai_models` → `providers`), ne
   hádáním z tvaru payloadu.
3. `DELETE FROM citations WHERE raw_response_id = :id` + `INSERT` nových
   řádků. Neměň `raw_responses`, `runs` ani nic dalšího.
4. Payload, ze kterého mapper nevytáhne nic (`([], False)`) a který dnes
   nemá žádné `citations` řádky → přeskoč beze změny.
5. Migrace **nesmí importovat SDK** (`google.genai`, `anthropic`, `openai`)
   — po GC-T2 pracují mappery nad dictem, takže
   `from app.adapters.google import _map_citations` nic takového nestáhne.
   Ověř spuštěním, ne úvahou.
6. `downgrade()` — `pass` s vysvětlujícím komentářem (design decision 7).
7. Docstring migrace: co dělá, proč je to legitimní zásah do evidence
   tabulky (odvozenina z nedotčeného `raw_payload`), a naměřená očekávaná
   čísla, ať je za rok poznat, jestli doběhla celá.

Po dokončení:
1. `docker compose exec app alembic upgrade head` — bez chyby.
2. Stejný `count(*)` dotaz jako výše — **očekáváno: Gemini 1 354**,
   Anthropic 158 (počet stejný, obsah přesunutý), OpenAI 71.
3. Křížová kontrola proti payloadu — dotaz z design decision 1 musí dát
   stejné číslo jako reálný počet Gemini řádků v `citations`.
4. Sémantická kontrola — po migraci musí platit pro **všechny** providery:
   ```sql
   select p.name,
          count(*) filter (where c.cited_answer_span is not null) as with_claim,
          count(*) filter (where position(c.cited_answer_span in coalesce(rr.rendered_text,'')) > 0) as claim_in_answer,
          count(*) filter (where c.source_passage is not null) as with_source_passage
   from citations c
     join raw_responses rr on rr.id = c.raw_response_id
     join runs r on r.id = rr.run_id
     join ai_models m on m.id = r.model_id
     join providers p on p.id = m.provider_id
   group by 1;
   ```
   `with_claim` se musí rovnat `claim_in_answer` u Gemini i OpenAI
   (po opravě 1 354/1 354 a 71/71), u Anthropicu musí být `with_claim = 0`
   a `with_source_passage = 158`.
5. Ověř idempotenci — zavolej přepočítávací funkci migrace podruhé proti už
   zmigrované DB (`docker compose exec app python -c …`, ne přes
   `alembic stamp`, ať se neplete revizní historie). Čísla se nesmí změnit.
6. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
fix(adapters): backfill historical citations from stored raw payloads
```

---

## GC-T4 — Zobrazení a export

**Target:** `app/templates/runs/detail.html`, `app/i18n/en.json`, `app/i18n/de.json`, `app/services/export.py`

Prerekvizita: GC-T1 až GC-T3 hotové.

1. `app/templates/runs/detail.html` (citační sekce, ~ř. 146-161) — odděl
   claim od zdrojové pasáže. Dnes je tam jeden kurzívní blok
   `cited_answer_span`; nově:
   - `cited_answer_span` s popiskem `t('run.citation_claim_label')`,
   - `source_passage` s popiskem `t('run.citation_source_passage_label')`,
   - obojí jen `{% if %}` — u každého providera je vyplněná jen jedna větev,
     tak ať prázdná nezabírá místo.

   Offsety (`answer_span_start`/`_end`) **nezobrazuj** — jsou to
   implementační data pro #12, ne informace pro uživatele. Reuse existující
   markup/styl sekce, žádná nová komponenta.
2. i18n (EN+DE, **jeden commit** — AI_INSTRUCTIONS.md §3) —
   `run.citation_claim_label`, `run.citation_source_passage_label`. Vedle
   stávajících `run.citations_label`/`run.citations_empty`
   (`app/i18n/{en,de}.json:343`).
3. `app/services/export.py` — do `CITATION_COLUMNS` (~ř. 63) přidej
   `source_passage`, `answer_span_start`, `answer_span_end`; stejně do
   `_citation_rows()` (~ř. 221) a do JSON větve (~ř. 372). Pořadí sloupců:
   nové za `cited_answer_span`, ať se existující exporty nerozhodí víc, než
   musí.

Po dokončení:
1. `docker compose up -d --build`.
2. V prohlížeči: Gemini run (claim vidět, zdrojová pasáž prázdná), Anthropic
   run (obráceně), starý historický Gemini run (po backfillu musí mít víc
   citací než dřív), run bez citací (prázdný stav, ne chyba).
3. **Ověř na ~375px / ~768px / desktop** — citační sekce má nově dva textové
   bloky na položku, na mobilu se to musí pořád dát číst
   (AI_INSTRUCTIONS.md §3 FRONTEND).
4. Stáhni export ve všech třech formátech, zkontroluj nové sloupce.
5. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
feat(runs): show cited claim and source passage separately on run detail
```

---

## GC-T5 — Testy

**Target:** `tests/test_adapters.py`, `tests/test_runs.py`, `tests/test_export.py`

Prerekvizita: GC-T1 až GC-T4 hotové.

`tests/test_adapters.py` dnes pokrývá **jen `_map_search_queries`** — na
`_map_citations` neexistuje ani jeden test, u žádného providera. To je
vlastní příčina toho, proč #11 mohlo vzniknout a přežít tři fáze. Tenhle
task tu díru zavírá.

1. **Gemini `_map_citations`** (fixtures jako dicty, tvar podle design
   decision 5):
   - jeden support → tři chunky = **tři** řádky se stejným
     `cited_answer_span` a různými zdroji;
   - jeden chunk → dva supports = **dva** řádky se stejným zdrojem a různými
     spany (**tohle je regresní test na původní `break` bug** — bez opravy
     vrátí jeden řádek);
   - `segment.start_index = None` u prvního segmentu → `answer_span_start == 0`
     a nespadne při řazení;
   - pořadí: supports zamíchané na vstupu → `citation_position` odpovídá
     pořadí v odpovědi, ne pořadí na vstupu;
   - chunk, na který neukazuje žádný support → vlastní řádek se
     `cited_answer_span is None`, zařazený poslední;
   - `grounding_chunk_indices` s indexem mimo rozsah → přeskočeno, nespadne;
   - žádné `grounding_metadata` / prázdné `candidates` → `([], False)`
     (design decision 9).
2. **Anthropic `_map_citations`** — `cited_text` jde do `source_passage`,
   `cited_answer_span is None`, offsety `None`; blok s `"citations": null`
   nespadne; víc text bloků → průběžný `citation_position`.
3. **OpenAI `_map_citations`** — offsety uložené, `cited_answer_span`
   nařezaný podle nich, `source_passage is None`.
4. `tests/test_runs.py` — přes `FakeAdapter` ověř, že run-trigger uloží
   všechna tři nová pole a že se na detailu zobrazí správná větev (claim vs.
   zdrojová pasáž).
5. `tests/test_export.py` — nové sloupce ve všech třech formátech.
6. Backfill: **netestuj migraci jako migraci** (existující sada migrace
   nespouští), ale ověř jedním testem nad Gemini fixture, že mapper volaný
   nad `raw_payload` dictem dá totéž co v live cestě — což je přesně tvrzení
   design decision 5.

Po dokončení:
1. `pytest` — všechno zelené, staré i nové.
2. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
test(adapters): cover citation mapping for all three providers
```

---

## GC-T6 — Dokumentace

**Target:** `docs/ROADMAP.md`, `docs/REQUIREMENTS.md`, `docs/TASKS.md`

Prerekvizita: GC-T1 až GC-T5 hotové **a uživatelem potvrzené, že to
funguje** (AI_INSTRUCTIONS.md §7 bod 3 — docs se aktualizují až po
potvrzení, ne po vlastním ověření).

1. `docs/ROADMAP.md` #11 — označ jako hotové, doplň, co se reálně ukázalo
   (59 % ztracených vazeb, ne jen „zploštělá 1:1 citace") a že součástí bylo
   i rozdělení sémantiky spanu.
2. `docs/ROADMAP.md` #12 — oprav větu *„Anthropic runy: zdrojová pasáž už je
   uložená (`citations[].cited_text` přes dnešní `cited_answer_span` pole)"*:
   nově je to `source_passage`, a u Anthropicu naopak chybí claim (offsety
   API nevrací). Změna vstupních předpokladů #12, ne kosmetika.
3. `docs/ROADMAP.md` — **nová položka**: podpora nového Gemini 3
   `steps`/`url_citation` tvaru odpovědi (design decision 9). Nezakládej pro
   ni větev ani dokumenty, jen ji popiš.
4. `docs/REQUIREMENTS.md` FR-12 (ř. 44) — rozšíř o rozdělení claim /
   zdrojová pasáž a o to, že jedna odpověď může mít víc vazeb na tentýž
   zdroj a víc zdrojů k témuž tvrzení.
5. `docs/REQUIREMENTS.md` — poznámka k významu počtu citací (design
   decision 8): počet `citations` řádků = počet claim-source vazeb, ne počet
   zdrojů; pro počet zdrojů slouží `count(distinct source_domain)`.
6. `docs/TASKS.md` — nová sekce v „Beyond phase 1 scope" s odkazem na tuhle
   větev, stejný formát jako ostatní.

**NEPIŠ nic do `/findings`** — to je editorial uživatele, i kdyby se
zjištění z téhle větve do jeho rozsahu trefovalo.

Po dokončení:
1. Ukaž diff dokumentace.
2. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
docs(requirements): record claim/source-passage split in citation model
```

---

## Completion Checklist

- [x] Schema flag potvrzený před GC-T1 (2026-09-16)
- [x] Evidence flag (backfill přepisuje `citations`) potvrzený před GC-T3 (2026-09-16)
- [ ] `citations` má `source_passage`, `answer_span_start`, `answer_span_end` (migrace `0027`)
- [ ] Gemini `_map_citations` emituje jeden řádek na dvojici support × chunk,
      řazeno podle pozice v odpovědi, `start_index=None` ošetřené
- [ ] Anthropic ukládá `cited_text` do `source_passage`, ne do `cited_answer_span`
- [ ] OpenAI ukládá offsety odpovědi
- [ ] Všechny tři mappery čtou dict, ne SDK objekty — jedna cesta pro live i backfill
- [ ] Backfill (`0028`) doběhl: Gemini 551 → 1 354, Anthropic 158 přesunutých,
      OpenAI 71 s offsety; ověřeno oběma kontrolními dotazy z GC-T3
- [ ] Run detail rozlišuje claim a zdrojovou pasáž, i18n EN+DE v jednom commitu
- [ ] Export obsahuje tři nové sloupce ve všech formátech
- [ ] `pytest` zelený, `_map_citations` pokryté u všech tří providerů
      (včetně regrese na původní `break` bug)
- [ ] Ověřeno v prohlížeči na ~375px / ~768px / desktop, na živých datech
- [ ] Docs (GC-T6) aktualizované — **až po uživatelově potvrzení**
- [ ] `docs/TASKS.md` — nová sekce po smergnutí
