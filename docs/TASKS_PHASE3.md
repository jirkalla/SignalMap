# SignalMap — Tasks: Phase 3 (First Analysis Skill — Mention/Visibility Detection)

## v1.0 | Září 2026
## Branch: feature/signalmap-phase3-mention-detection
## Task ID prefix: P3

> Fáze 1 (`docs/TASKS.md`), fáze 2 (`docs/TASKS_PHASE2.md`), production-readiness
> hardening (`docs/TASKS_HARDENING.md`) a runs export (`docs/TASKS_EXPORT.md`) jsou
> hotové a smergnuté. Tenhle dokument pokrývá krok 3 z build sequencing
> (`signalmap-conventions` skill) — první analysis skill běžící nad uloženými
> `raw_responses`.
>
> Cíl větve: nejjednodušší obhajitelná metrika — "objevuje se klient v odpovědi vůbec
> (textem, nebo jako citovaný zdroj)" — počítaná deterministicky (string/regex match),
> bez LLM klasifikace. Sentiment a brand-attribute extrakce jsou vědomě mimo rozsah:
> nesou vyšší riziko halucinace/nekonzistence a v podobných nástrojích (viz `/findings`
> poznámky k Peecu) snadno sklouznou k nesmyslu. Ty přijdou jako **další** skill, až
> bude tenhle ověřený v provozu.
>
> Design byl probraný a odsouhlasený s uživatelem před psaním tohohle dokumentu
> (rozsah, schema přístup, zdroj domény pro citation-match, trigger timing).

---

## Design decisions (rozhodnuto před psaním kódu)

1. **`analysis_skills` dostává `execution_type` (`rule_based` | `llm_prompt`) od
   začátku, i když tenhle skill je jediný a je `rule_based`.** Alternativa (holé
   sloupce jen pro rule-based skill) by při druhém skillu — budoucí sentiment, který
   bude `llm_prompt` — vyžadovala přepracování schématu. `prompt_template` je
   nullable a u `rule_based` skillů zůstává `NULL`; `output_schema` (JSONB) je čistě
   dokumentační popis výstupních polí, nevynucuje se na DB úrovni.
2. **Mention detection zahrnuje text i citace, jako dvě oddělená pole ve výstupu.**
   `text_mentioned`/`mention_count`/`first_mention_position` z `rendered_text`,
   `cited`/`cited_domains` z `citations.source_domain`. Citation-match je bonus
   prakticky zdarma (tabulka `citations` už existuje) a je jednoznačnější signál než
   text match (URL buď v seznamu je, nebo není). Pole se neslévají do jednoho
   booleanu — analytik má vidět, **čím** byl klient vidět.
3. **Nový sloupec `clients.domain` (nullable), ne heuristika na jméno v URL.**
   Bez explicitní domény by citation-match musel hádat shodu jména klienta s
   `source_domain` textovým podřetězcem — křehké (`acme.com` vs. "Acme Corp GmbH" se
   nemusí textově shodovat) a náchylné na false positives/negatives. Analytik
   doménu vyplní ručně u klienta (podobně jako `industry`/`notes` dnes) — pole se
   navíc hodí i mimo tenhle skill (export, později dashboard). Když `domain` chybí
   (`NULL`), citation-match se pro daného klienta jednoduše přeskočí (`cited=false`,
   `cited_domains=[]`), ne chyba.
4. **Nová tabulka `client_aliases`, ne text/array sloupec na `Client`.** Klient se
   v odpovědi může objevit pod víc variantami jména ("Acme", "Acme Corp",
   "Acme GmbH"). Samostatná tabulka (`id`, `client_id`, `alias`, `created_at`,
   `UNIQUE(client_id, alias)`) drží stejný vzor jako `Market` — malá lookup
   tabulka s jednoduchým inline CRUD — místo netypovaného pole bez validace a bez
   UI, což by šlo proti "data not hardcoded lists" duchu projektu.
5. **Matching algoritmus: case-insensitive, word-boundary regex, sjednocené pozice
   napříč kandidáty.** Kandidáti = `client.name` + všechny `client_aliases.alias`.
   Pro každého kandidáta `re.finditer(r'(?<!\w)' + re.escape(candidate) + r'(?!\w)',
   rendered_text, re.IGNORECASE)`, pozice matchů ze všech kandidátů se sjednotí do
   jedné množiny (dedupe podle start pozice, aby se překrývající match jména a
   aliasu nepočítal dvakrát) → `mention_count = len(pozice)`,
   `first_mention_position = min(pozice)` nebo `None`, `matched_terms` = seznam
   kandidátů, co aspoň jednou zamatchovaly (transparentnost — proč byl klient
   označen jako zmíněný). **Známé omezení, vědomě neřešené v téhle fázi:**
   nezachytí koreference ("firma", "výrobce" bez jména), překlepy, ani skloňování v
   češtině/němčině mimo přesný řetězec aliasu — to je přesně ten typ nejednoznačnosti,
   který má fáze 3 vědomě obejít (viz úvod dokumentu). Přidat další alias je levný
   fix pro konkrétní pozorovaný miss, ne stavět fuzzy/lemma matching teď.
6. **Doména se normalizuje (lowercase, `www.` prefix pryč) a porovnává s
   toleranci subdomény.** `cited = True`, pokud normalizovaná `clients.domain`
   odpovídá normalizované `citations.source_domain` přesně, nebo pokud
   `source_domain` končí na `.` + normalizovaná doména klienta (např.
   `blog.acme.com` matchne `acme.com`). `cited_domains` obsahuje originální
   (nenormalizované) `source_domain` hodnoty, co zamatchovaly — pro dohledatelnost
   v UI.
7. **Trigger: automaticky po každém úspěšném Run, synchronně v `trigger_run`.**
   Regex match je řádově mikrosekundy — žádný důvod pro batch/frontu. Volání
   analysis enginu je zabalené v `try/except` s logováním, které **nikdy
   nezpůsobí, že by run samotný skončil jako chyba** — evidence (`Run`,
   `RawResponse`, `Citation`) musí zůstat vidět i kdyby analýza selhala na
   neočekávaném vstupu. To je stejná tolerance k selhání odvozené vrstvy, jakou
   appka dnes má jinde (provider chyba neshodí appku, jen se zaloguje a zapíše
   strukturovaně).
8. **`AnalysisResult.skill_version` je zkopírovaná hodnota `analysis_skills.version`
   v době výpočtu, ne odkaz na verzovaný řádek jako u `Prompt`.** `Prompt` má
   plnou row-versioning lineage (`root_prompt_id`/`is_current_version`), protože
   text promptu edituje analytik přímo v UI. Logika skillu se mění nasazením kódu,
   ne editací v UI — prostý `version` integer na `analysis_skills`, zkopírovaný na
   výsledek, dá stejnou zpětnou dohledatelnost ("jakou verzí logiky byl tenhle
   výsledek spočítaný") bez zbytečné row-lineage konstrukce pro jediný skill.
9. **Žádný re-run/backfill mechanismus v téhle fázi.** Existující (fáze 1/2/export)
   `raw_responses` z doby před touhle větví analýzu nedostanou zpětně — skill běží
   jen na nových runech od merge dál. Tlačítko "přepočítat analýzu" (např. po
   přidání aliasu) i hromadný backfill jsou vědomě odložené — řeší problém, co
   dnes ještě nenastal (žádný skill v provozu), stavět by to bylo predikcí
   budoucí potřeby.
10. **`AnalysisResult` se nikdy needituje/nepřepisuje — nový výpočet je nový řádek**
    (stejná evidence-retention disciplína jako `raw_responses`/`citations`), i když
    v téhle fázi vzniká jen jednou automaticky, takže duplicity dnes reálně
    nenastanou.
11. **Zobrazení výsledku: minimální sekce na `runs/detail.html`, žádná nová
    obrazovka/dashboard.** Fáze 4 (dashboard) je samostatný pozdější krok — cílem
    fáze 3 je dokázat, že výpočet a uložení funguje end-to-end a je vidět bez
    ruční DB inspekce, stejná "thin vertical slice" filozofie jako fáze 1.
12. **Zvýraznění zmínek v textu odpovědi (přidáno po P3-T4, odsouhlaseno s
    uživatelem 2026-09-10): opírá se výhradně o pozice uložené v době výpočtu,
    nikdy o přepočet proti aktuálním aliasům klienta.** `mention_visibility.py`
    dostává nové pole výstupu `match_spans` (seznam `[start, end)` dvojic, po
    stejné dedupe/sjednocovací logice jako `mention_count` — žádná schema
    migrace na `analysis_results` není potřeba, `output` je `JSONB`; jen malá
    data-only migrace na `analysis_skills.output_schema`, ať dokumentace
    neshnije). Render v `runs/detail.html` obalí tyhle rozsahy v
    `raw_response.rendered_text` do `<mark>` — **nikdy nepřepočítává matching
    znovu** při zobrazení, protože kdyby se aliasy klienta mezitím změnily,
    přepočet by ukázal jiný výsledek, než jaký byl skutečně evidovaný v době
    běhu (porušilo by to design decision 10 — `AnalysisResult` je neměnná
    evidence). HTML sestavuje pomocná funkce v `app/routers/runs.py`
    (`markupsafe.escape` na text mimo matche, `<mark>` jen kolem takto
    escapovaného textu) — ne inline stavba HTML v Jinja šabloně, kvůli
    bezpečnému zacházení s AI-generovaným textem. Překrývající se rozsahy
    (teoreticky možné, prakticky téměř nikdy díky word-boundary matchi) se
    řeší jednoduchým "přeskoč, pokud start < konec předchozího zachovaného
    rozsahu" průchodem, ne komplexním interval-merge algoritmem.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| P3-T1 | Schema: `analysis_skills`/`analysis_results`/`client_aliases` + `clients.domain`, seed skillu | ⏳ |
| P3-T2 | Client aliasy + doména v UI (form pole, detail sekce, add/delete) | ⏳ |
| P3-T3 | Analysis engine + `mention_visibility` rule-based skill | ⏳ |
| P3-T4 | Zapojení do `trigger_run` + zobrazení výsledku na run detailu | ✅ |
| P3-T5 | Zvýraznění zmínek v textu odpovědi (`match_spans`, `<mark>`) | ✅ |
| P3-T6 | Testy: alias CRUD, matching logika (vč. `match_spans`), end-to-end přes `FakeAdapter` | ⏳ |

Pořadí je vynucené: P3-T2/P3-T3/P3-T4 potřebují sloupce a tabulky z P3-T1; P3-T2 dává
smysl před P3-T4, protože ruční ověření P3-T4 potřebuje mít aspoň jednoho klienta s
vyplněnou doménou/aliasem přes UI; P3-T3 (čistá logika, žádná UI závislost) může vzniknout
souběžně s P3-T2, ale je zařazený až po něm kvůli lineárnímu čtení dokumentu; P3-T4 staví
na P3-T3; P3-T5 (přidáno po P3-T4, viz design decision 12) rozšiřuje P3-T3/P3-T4 o
zvýraznění, staví na obou; P3-T6 testuje všechno předchozí najednou.

---

## P3-T1 — Schema: nové tabulky + `clients.domain`, seed skillu

**Target:** nová migrace `alembic/versions/0011_*.py`, `app/models/client.py`, nový
`app/models/client_alias.py`, nový `app/models/analysis.py`, `app/models/__init__.py`

1. Migrace 0011:
   - `clients` — `ADD COLUMN domain VARCHAR(200)` (nullable — ne každý klient ho musí
     mít vyplněný hned).
   - `CREATE TABLE client_aliases` — `id SERIAL PK`, `client_id INTEGER NOT NULL
     REFERENCES clients(id) ON DELETE CASCADE`, `alias VARCHAR(200) NOT NULL`,
     `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `UNIQUE (client_id, alias)`.
   - `CREATE TABLE analysis_skills` — `id SERIAL PK`, `key VARCHAR(50) NOT NULL
     UNIQUE` (např. `'mention_visibility'`), `name VARCHAR(150) NOT NULL`,
     `version INTEGER NOT NULL DEFAULT 1`, `execution_type VARCHAR(20) NOT NULL`
     (`'rule_based'` | `'llm_prompt'`), `prompt_template TEXT` (nullable),
     `output_schema JSONB` (nullable, dokumentační), `is_active BOOLEAN NOT NULL
     DEFAULT TRUE`, `created_at`/`updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
   - `CREATE TABLE analysis_results` — `id SERIAL PK`, `raw_response_id INTEGER NOT
     NULL REFERENCES raw_responses(id) ON DELETE CASCADE`, `analysis_skill_id
     INTEGER NOT NULL REFERENCES analysis_skills(id)`, `skill_version INTEGER NOT
     NULL`, `output JSONB NOT NULL`, `computed_at TIMESTAMPTZ NOT NULL DEFAULT
     now()`. Index `idx_analysis_results_raw_response ON analysis_results
     (raw_response_id)`.
   - `INSERT INTO analysis_skills (key, name, version, execution_type,
     output_schema, is_active) VALUES ('mention_visibility', 'Mention &
     Visibility Detection', 1, 'rule_based', '{"text_mentioned": "boolean",
     "mention_count": "integer", "first_mention_position": "integer|null",
     "matched_terms": "array of matched name/alias strings", "cited": "boolean",
     "cited_domains": "array of matching citation source_domain values"}'::jsonb,
     TRUE)`.
2. `app/models/client.py` — `Client` dostává `domain: Mapped[str | None]`, a
   relationship `aliases: Mapped[list["ClientAlias"]]` (`cascade="all,
   delete-orphan"`, stejný vzor jako `prompt_sets`).
3. Nový `app/models/client_alias.py` — `ClientAlias` model (`id`, `client_id` FK,
   `alias`, `created_at`, `client` relationship `back_populates="aliases"`).
4. Nový `app/models/analysis.py` — `AnalysisSkill` (`id`, `key`, `name`, `version`,
   `execution_type`, `prompt_template`, `output_schema` jako `JSONB`,
   `is_active`, `created_at`/`updated_at`) a `AnalysisResult` (`id`,
   `raw_response_id` FK, `analysis_skill_id` FK, `skill_version`, `output` jako
   `JSONB`, `computed_at`; relationship `analysis_skill: Mapped["AnalysisSkill"]`).
5. `app/models/__init__.py` — zaregistrovat `ClientAlias`, `AnalysisSkill`,
   `AnalysisResult` do importů a `__all__`.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověřit: `clients.domain` existuje; `client_aliases` prázdná, ale existuje;
   `analysis_skills` má jeden řádek `mention_visibility`, `execution_type =
   'rule_based'`, `is_active = true`; `analysis_results` existuje a je prázdná.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add analysis_skills/analysis_results/client_aliases + clients.domain
```

---

## P3-T2 — Client aliasy + doména v UI

**Target:** `app/routers/clients.py`, `app/templates/clients/form.html`,
`app/templates/clients/detail.html`, `app/i18n/en.json`, `app/i18n/de.json`

1. `app/routers/clients.py`:
   - `create_client`/`update_client` — nové `Form(...)` pole `domain: str = Form("",
     description="Client's own primary domain, e.g. 'acme.com' — used to detect
     when the client's own site is among a run's cited sources.")`, uloží se
     `.strip().lower() or None`.
   - `POST /clients/{client_id}/aliases` — přidá `ClientAlias` řádek (`alias:
     str = Form(..., description="Alternate name/spelling to match against, e.g.
     'Acme Corp'.")`, `.strip()`). Duplicitní alias (`UNIQUE` violation) →
     strukturovaná chyba `AppError("client_alias_duplicate", ...)`, ne 500.
   - `POST /clients/{client_id}/aliases/{alias_id}/delete` — smaže alias. Aliasy
     nejsou evidence (samotný `AnalysisResult` už má svůj výsledek uložený
     nezávisle) — mazání není blokované, žádná in-use kontrola.
2. `app/templates/clients/form.html` — nové pole `domain` (`text_field` makro),
   pod `industry`.
3. `app/templates/clients/detail.html` — nová sekce "Aliases": tabulka/seznam
   existujících aliasů s `delete_button` makrem u každého, plus inline formulář
   (jedno textové pole + submit) na přidání dalšího. Zobrazit i `client.domain`
   (nebo "—", když prázdné) vedle `industry`.
4. i18n (EN+DE, jeden commit) — `client.domain_label`, `client.domain_placeholder`,
   `client.aliases_title`, `client.alias_add_placeholder`,
   `client.alias_delete_confirm`, `client.aliases_empty_state`,
   `errors.client_alias_duplicate` (`"This alias is already registered for this
   client."` / DE ekvivalent), `errors.client_alias_not_found`.

Po dokončení:
1. `docker compose up -d --build`
2. V prohlížeči na existujícím klientovi: vyplnit `domain`, uložit, znovu otevřít
   edit formulář → hodnota zůstala. Přidat 2 aliasy, smazat jeden → seznam se
   aktualizuje. Zkusit přidat duplicitní alias → strukturovaná chyba, ne 500.
3. Ověřit na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(clients): add domain field and alias management for analysis matching
```

---

## P3-T3 — Analysis engine + `mention_visibility` rule-based skill

**Target:** nový `app/analysis/__init__.py`, nový `app/analysis/base.py`, nový
`app/analysis/mention_visibility.py`

1. Nový `app/analysis/base.py` — `AnalysisSkillRunner` (Protocol nebo ABC) se
   společným rozhraním, analogické `ProviderAdapter` u providerů: metoda
   `run(rendered_text: str | None, citations: list[Citation], client: Client) ->
   dict` vracející JSON-serializovatelný dict odpovídající `output_schema` skillu.
2. Nový `app/analysis/mention_visibility.py` — `MentionVisibilityRunner`:
   - Kandidáti = `[client.name] + [a.alias for a in client.aliases]`, prázdné
     přeskočit.
   - Regex match dle design decision 5 (word-boundary, case-insensitive,
     sjednocené pozice napříč kandidáty) nad `rendered_text` (pokud `None` nebo
     prázdný string → `text_mentioned=False, mention_count=0,
     first_mention_position=None, matched_terms=[]`, žádná chyba).
   - Citation match dle design decision 6 (normalizace domény, tolerance
     subdomény) — pokud `client.domain` je `None`, `cited=False,
     cited_domains=[]` bez další logiky.
   - Vrátí dict přesně podle `output_schema` (viz P3-T1 seed).
3. Nový `app/analysis/__init__.py` — `ANALYSIS_SKILLS: dict[str,
   type[AnalysisSkillRunner]] = {"mention_visibility": MentionVisibilityRunner}`,
   `get_runner(skill_key: str) -> AnalysisSkillRunner`, `has_runner(skill_key:
   str) -> bool` — stejný registry vzor jako `app/adapters/__init__.py`
   (`get_adapter`/`has_adapter`), aby přidání dalšího skillu později znamenalo
   jeden nový soubor + jeden nový řádek v registry, ne hledání volacích míst po
   appce.

Po dokončení (jednotkově, bez UI — pokryto testy v P3-T5, ale ověř ručně teď):
1. Krátký ad-hoc skript/REPL v kontejneru: zavolej `MentionVisibilityRunner().run(...)`
   s ručně sestaveným textem a `Client`/`ClientAlias` objekty (bez DB, jen
   instance v paměti) — ověř, že `mention_count`/`first_mention_position`/
   `matched_terms` odpovídají očekávání na pár ručně vymyšlených případech
   (žádná zmínka, jedna zmínka jménem, zmínka jen aliasem, zmínka uvnitř jiného
   slova co NEMÁ matchnout kvůli word-boundary).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(analysis): add rule-based mention/visibility skill and analysis registry
```

---

## P3-T4 — Zapojení do `trigger_run` + zobrazení na run detailu

**Target:** `app/routers/runs.py`, `app/templates/runs/detail.html`,
`app/i18n/en.json`, `app/i18n/de.json`

1. `app/routers/runs.py` (`trigger_run`, success větev, po `db.commit()` z
   uložení `RawResponse`/`Citation`/`SearchQuery` řádků):
   - Nová privátní funkce `_run_active_analysis_skills(db, raw_response, client)`
     — načte `analysis_skills` kde `is_active = true`, pro každý s
     `execution_type == 'rule_based'` a `has_runner(skill.key)` zavolá
     `get_runner(skill.key).run(raw_response.rendered_text, citations, client)`,
     uloží `AnalysisResult` (`skill_version=skill.version`). `execution_type ==
     'llm_prompt'` řádky v téhle fázi přeskoč (žádný takový skill zatím
     neexistuje — připraveno pro budoucí sentiment skill, ne implementováno teď).
   - Volání zabalené v `try/except` s `logger.error(..., exc_info=True)` (design
     decision 7) — selhání analýzy nikdy nezmění `run.status` ani nezpůsobí, že
     by se `RawResponse`/`Citation` řádky nezapsaly (ty jsou už commitnuté před
     tímhle voláním).
   - `client` se získá přes `prompt.prompt_set.client` (relationship chain už
     existuje).
   - Docstring na nové funkci.
2. `app/templates/runs/detail.html` — nová sekce "Analysis" (jen když
   `analysis_results` neprázdné): pro `mention_visibility` výsledek zobrazit
   `text_mentioned`/`cited` jako badge (reuse `status_badge` nebo podobný vizuální
   jazyk), `mention_count`, `matched_terms` jako seznam, `cited_domains` jako
   seznam odkazů. Žádná nová vizuální komponenta od nuly — vycházet z existujících
   badge/list vzorů na téže stránce.
3. i18n (EN+DE, jeden commit) — `analysis.section_title`, `analysis.mentioned_yes`,
   `analysis.mentioned_no`, `analysis.mention_count_label`,
   `analysis.matched_terms_label`, `analysis.cited_yes`, `analysis.cited_no`,
   `analysis.cited_domains_label`.

Po dokončení:
1. `docker compose up -d --build`
2. Na klientovi s vyplněnou doménou a aspoň jedním aliasem (z P3-T2) spustit
   reálný run, kde je klient v odpovědi zmíněný a/nebo citovaný → run detail
   ukazuje sekci Analysis se správnými hodnotami.
3. Spustit run, kde klient zmíněný není → sekce ukazuje `mentioned=No`,
   `cited=No`, bez chyby.
4. Ověřit na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): compute and display mention/visibility analysis after each run
```

---

## P3-T5 — Zvýraznění zmínek v textu odpovědi

**Target:** `app/analysis/mention_visibility.py`, `app/routers/runs.py`,
`app/templates/runs/detail.html`, nová migrace `alembic/versions/0012_*.py`

Přidáno po P3-T4 na základě požadavku uživatele — viz design decision 12 pro
kompletní zdůvodnění (proč se opírá o uložené pozice, ne přepočet; proč HTML
sestavuje Python helper, ne Jinja).

1. `app/analysis/mention_visibility.py`:
   - Uprav interní `_match_positions`/ekvivalent tak, aby si pro každý start
     pamatoval i konec matche (`dict[int, int]`, `start -> end`; při shodném
     startu z různých kandidátů ponechej delší/pozdější `end`).
   - `MentionVisibilityRunner.run()` — z toho vytvoř `match_spans: list[list[int]]`
     (seřazené podle startu, přeskoč libovolný rozsah, jehož start je menší než
     `end` posledního zachovaného rozsahu — obrana proti překryvu, i když u
     word-boundary matchů prakticky nenastává). `mention_count =
     len(match_spans)`, `first_mention_position = match_spans[0][0]` nebo
     `None` — beze změny chování oproti dnešku, jen odvozené z nové struktury
     místo ze samostatné množiny pozic.
   - Nové pole ve výstupním dictu: `"match_spans": match_spans`.
2. Nová migrace 0012 — pouze datová (`op.execute(UPDATE ...)`), doplní
   `match_spans` do `analysis_skills.output_schema` JSON pro řádek
   `mention_visibility` (`"match_spans": "array of [start, end) pairs into
   rendered_text, used to highlight matches"`), ať dokumentační sloupec
   nezůstane pozadu za skutečným výstupem.
3. `app/routers/runs.py`:
   - Nová privátní funkce (např. `_highlight_matches(text: str, spans:
     list[list[int]]) -> Markup`, `from markupsafe import Markup, escape`) —
     `escape()` na text mimo rozsahy, `<mark class="...">` obalí `escape()`
     na text uvnitř rozsahu. Prázdné/`None` `spans` → vrátí jen `escape(text)`.
   - `run_detail` — najdi `match_spans` z `AnalysisResult.output` pro
     `mention_visibility` výsledek (pokud existuje), postav
     `rendered_text_html = _highlight_matches(raw_response.rendered_text,
     spans)` a přidej do kontextu šablony.
4. `app/templates/runs/detail.html` — sekce "Rendered answer" použije
   `rendered_text_html` (Markup, Jinja ho neescapuje podruhé) místo prostého
   `raw_response.rendered_text`, s fallbackem na `t('common.none')`, když
   `rendered_text` chybí.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. `docker compose up -d --build`
3. Na existujícím runu s `text_mentioned=true` (z P3-T4 ověření) znovu otevři
   run detail → v sekci "Rendered answer" jsou matchnuté výrazy vizuálně
   zvýrazněné (`<mark>`), zbytek textu beze změny.
4. Ověř, že text kolem zvýraznění není rozbitý/dvakrát escapovaný (žádné
   `&amp;` místo `&` apod.) a že HTML ze samotné AI odpovědi (pokud by
   nějaké znaky vypadaly jako značky) se nevykreslí jako živé HTML.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): highlight matched mention terms in the rendered answer
```

---

## P3-T6 — Testy

**Target:** nový `tests/test_client_aliases.py`, nový
`tests/test_analysis_mention_visibility.py`, úprava `tests/test_runs.py`

1. `tests/test_client_aliases.py` — přidání/smazání aliasu přes
   `/clients/{id}/aliases`; duplicitní alias vrací strukturovanou chybu, ne 500;
   `domain` pole se uloží přes create/edit formulář.
2. `tests/test_analysis_mention_visibility.py` — přímé jednotkové testy
   `MentionVisibilityRunner.run()` (bez HTTP/DB): žádná zmínka; zmínka jménem;
   zmínka jen aliasem; víc zmínek (počet/pozice sedí); word-boundary (řetězec
   uvnitř jiného slova nematchne); `rendered_text=None`; `client.domain=None` →
   `cited=False` bez chyby; citace přesně na doméně matchne; citace na subdoméně
   matchne; citace na jiné doméně nematchne; `match_spans` odpovídá skutečným
   pozicím/délkám matchů v testovacím textu.
3. `tests/test_runs.py` — rozšířit run-trigger test (přes `FakeAdapter`) o
   ověření, že po úspěšném běhu existuje odpovídající `AnalysisResult` řádek se
   `skill_version=1` a očekávaným `output` (vč. `match_spans`); a že selhání
   uvnitř analysis enginu (monkeypatch `get_runner` tak, aby vyhodil výjimku)
   run **nezhroutí** — `Run.status` zůstává `'success'`, `RawResponse` existuje.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover client aliases, mention/visibility matching, and analysis wiring
```

---

## Completion Checklist

- [ ] `clients.domain` + `client_aliases` existují a jdou editovat přes UI
- [ ] `analysis_skills`/`analysis_results` existují, `mention_visibility` seedovaný
      jako `rule_based`, `is_active=true`
- [ ] Analysis engine registry (`app/analysis/`) funguje stejným vzorem jako
      `app/adapters/` — nový skill = nový soubor + jeden řádek v registry
- [ ] Mention/visibility se počítá automaticky po každém úspěšném runu a je vidět
      na run detailu bez ruční DB inspekce
- [ ] Selhání analysis enginu nikdy nezpůsobí ztrátu/chybu evidence (`Run`,
      `RawResponse`, `Citation`)
- [ ] Matchnuté výrazy jsou vizuálně zvýrazněné v "Rendered answer" na run
      detailu, opřené o uložené `match_spans`, ne o přepočet za běhu
- [ ] `pytest` sada zelená, pokrývá alias CRUD, matching logiku (vč.
      `match_spans`) i wiring do runu
- [ ] `docs/TASKS.md` — poznámka, že fáze 3 větev existuje a co pokrývá (odkaz na
      tenhle soubor) — **hotovo v rámci přípravy tohoto dokumentu**, ověřit že
      zůstává aktuální po mergi
- [ ] `docs/REQUIREMENTS.md` §4 "Explicitly Out of Scope" — odstranit/upravit
      "Analysis skills and structured AI-generated analysis results" řádek, jakmile
      je větev smergnutá (stejný vzor jako amandment pro fázi 2)
