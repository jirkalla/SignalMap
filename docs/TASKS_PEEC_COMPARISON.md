# SignalMap — Tasks: Peec Comparison

## v1.0 | Září 2026
## Branch: feature/signalmap-peec-comparison
## Task ID prefix: PC

Status: navrženo v konverzaci 2026-09-26. Lokální nástroj, jen čte
z dev DB a z exportů Peec — žádná změna aplikace ani schématu. Pět úkolů.

**Goal:** jedním příkazem porovnat odpovědi z Peec (export „chats") se
SignalMapem pro stejné prompty, stejné modely a stejné dny, a výsledek
uložit do jedné složky, kterou si Philip otevře v Excelu: všechny zdroje
(CSV + XLSX), výsledky a srozumitelný popis metody, mezer a omezení.

---

## Výchozí stav (ověřeno 2026-09-26)

- `docs/peec/` (v `.gitignore`, klientská data) obsahuje 25 exportů
  `peec_knauf_S{set}-p{prompt_id}_{štítek}_2026-09-23_2026-09-26.json`.
  Každý = 1 prompt, 150 odpovědí (6 modelů; 23. 9. po 3 opakováních,
  24.–26. 9. po jednom). Texty promptů se shodují s DB znak po znaku
  (25/25, klient Knauf, prompt sety 2/3/4).
- Pole odpovědi Peec: `id`, `promptId`, `model`, `user`, `assistant`,
  `mentions` (jen značky sledované v Peec), `sources` (domény),
  `citations` (počet), `position`, `created` (den, `22:00Z` = půlnoc
  Europe/Prague), `content_in_chat`.
- Modely: Peec `gpt-5-6-terra` ↔ SignalMap `gpt-5.6-terra`,
  Peec `claude-haiku-4-5` ↔ SignalMap `claude-haiku-4-5-20251001`.
  Jen v Peec: `chatgpt-ui`, `gemini-ui`, `google-ai-mode`,
  `google-ai-overview` (sběr z webového UI). Jen v SignalMapu:
  `gpt-5.6-luna`, `gemini-3.1-flash-lite`.
- SignalMap: 400 úspěšných runů Knaufu 23.–26. 9. (dev DB = kopie
  produkce přes `tools/local/refresh_dev_db.py`).

**Pilot (konverzace 2026-09-26, průměrný Jaccard, stejné pravítko na
obou stranách):**

| Model | Srovnání | Knauf ano/ne | Značky | Domény |
|---|---|---|---|---|
| terra | Peec × SignalMap, stejný den | 0,97 | 0,85 | 0,25 |
| terra | Peec × Peec, stejný den | 0,97 | 0,86 | 0,39 |
| terra | SignalMap × SignalMap, jiný den | 0,98 | 0,86 | 0,35 |
| haiku | Peec × SignalMap, stejný den | 0,97 | 0,93 | 0,18 |
| haiku | Peec × Peec, stejný den | 0,97 | 0,95 | 0,66 |
| haiku | SignalMap × SignalMap, jiný den | 0,96 | 0,92 | 0,32 |

Značky se shodují na úrovni šumu, zdroje se liší víc než šum. Pilot
zároveň odhalil, že sledovaná entita „Saint Gobain" (bez pomlčky, bez
aliasů) v SignalMapu nenajde „Saint-Gobain" — 19 přehlédnutých zmínek
u terra + haiku. Skript to má ukázat jako nález, ne potichu obejít.

---

## Výstup — struktura složky

```
docs/peec/comparison_knauf_2026-09-23_2026-09-26/
  README.md                  ← stejný text jako list README v report.xlsx
  report.xlsx                ← výsledky pro Philipa (listy viz T3)
  sources/
    sources.xlsx             ← všechny zdrojové tabulky níže, 1 list = 1 CSV
    prompts.csv              ← prompt_id, set, text, peec_prompt_id, soubor, v Peec, v SignalMapu
    model_mapping.csv        ← Peec model ↔ SignalMap model, porovnáno ano/ne, důvod
    tracked_brands.csv       ← značka, pravidlo pravítka, sledováno v Peec, v SignalMapu
    peec_answers.csv         ← 1 řádek = 1 odpověď Peec (vč. celého textu)
    peec_sources.csv         ← 1 řádek = 1 zdrojová doména odpovědi Peec
    signalmap_answers.csv    ← 1 řádek = 1 run (vč. celého textu, persona, trh)
    signalmap_citations.csv  ← 1 řádek = 1 citace
    pairs.csv                ← 1 řádek = 1 porovnaný pár (vstup pro report)
    peec_raw/                ← kopie původních JSON exportů
```

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Nový skript `tools/local/compare_peec.py`**, stejný vzor jako
   `refresh_dev_db.py`: běží na hostiteli (Windows), DB čte přes
   `docker compose exec postgres psql` (`_dbtools.psql`), výstup na disk.
   Aplikace se nemění; nic se neukládá do DB.
2. **Výjimka ze „standard library only" (TASKS_DEV_DB_REFRESH decision 2):
   `openpyxl`.** XLSX bez knihovny psát nejde rozumně. `openpyxl==3.1.5`
   už je v `requirements.txt`; skript ho importuje až při zápisu XLSX a
   bez něj skončí srozumitelnou chybou (`pip install openpyxl==3.1.5`).
   CSV se píše vždy jen standardní knihovnou. Žádný pandas/scipy —
   statistika (Jaccard, kappa, Spearman, bootstrap) je pár řádků.
3. **Jen čtení z DB.** Jediný dotazovací vstup je `COPY (SELECT …) TO
   STDOUT` přes psql. Žádný zápis, žádná transakce se zápisem.
4. **Porovnávají se jen modely v obou nástrojích** (terra, haiku).
   Ostatní modely jsou v `model_mapping.csv` a v listu Gaps s důvodem
   („jen v Peec — webové UI, jiný kanál než API" / „jen v SignalMapu").
   Mapování modelů je explicitní tabulka v kódu, žádné hádání podle jména.
5. **Párování: prompt × model × lokální datum (Europe/Prague).** Peec
   `created` je jen den (`22:00Z`), přesný čas dne neznámý → srovnává se
   den, ne hodina. Peec 23. 9. má 3 opakování: pár se počítá pro každé
   opakování a **skupina (prompt × model × den) se nejdřív zprůměruje**,
   aby den se 3 opakováními neměl 3× větší váhu.
6. **Stejné pravítko na obou stranách.** Hlavní srovnání značek jde přes
   jeden detektor nad textem obou nástrojů (tabulka pravidel v kódu →
   `tracked_brands.csv`: regex, aliasy — „Saint-Gobain"/„Saint Gobain",
   „ROCKWOOL"/„Rockwool"; krátká jména jako „Sto" case-sensitive s
   hranicí slova). Vlastní detektory obou nástrojů (Peec `mentions`,
   SignalMap skill „Competitive Visibility Detection") se **zvlášť**
   porovnají s pravítkem → list „Detector check". Tam patří nález
   Saint-Gobain.
7. **Šum jako měřítko (test–retest).** Každá metrika páru se ukáže
   vedle tří referencí: Peec × Peec stejný den (opakování), Peec × Peec
   jiný den, SignalMap × SignalMap jiný den. Verdikt v Summary:
   rozdíl = cross − nižší z vnitřních hodnot, 95% bootstrap interval
   (pevný seed → reprodukovatelné); horní mez < 0 → „liší se víc než
   šum", jinak „v rámci šumu".
8. **Úrovně metrik:** L1 pokrytí/párování, L2 značky (viditelnost
   Knaufu, viditelnost konkurentů, pozice první zmínky, share of voice,
   Cohenovo kappa pro Knauf ano/ne), L3 zdroje (Jaccard domén, podíl
   top domén, Spearman pořadí domén, typ domény z `domain_classifications`),
   L5 profil odpovědi (délka, počet domén/citací). **L4 (sémantická
   podobnost textu, shoda tvrzení) v téhle větvi není** — v Gaps jako
   „neměřeno".
9. **Normalizace domén:** malá písmena, bez schématu, bez `www.`, bez
   cesty; `utm_*` se zahodí. Gemini redirecty (`vertexaisearch…`) se
   u terra/haiku nevyskytují — kdyby ano, řádek se označí, ne tiše
   přeskočí.
10. **Jazyk výstupu: angličtina** (Philipův XLSX je anglicky). README a
    popisky listů anglicky; kód a komentáře anglicky (AI_INSTRUCTIONS §3).
11. **CSV: UTF-8 s BOM, čárka (RFC 4180).** BOM, aby Excel správně
    zobrazil přehlásky; pro pohodlné otevření v Excelu je tu `sources.xlsx`
    (CSV v DE/CZ locale Excelu čeká středník). Excel má limit 32 767 znaků
    na buňku — nejdelší odpověď má 14 565, přesto kontrola: delší text se
    v XLSX zkrátí s příznakem `text_truncated=true`, v CSV zůstane celý.
12. **Výstupní složka se nepřepisuje.** Existuje-li, skript skončí chybou;
    `--overwrite` ji nahradí. V README je `generated_at`, rozsah dat,
    počet runů/odpovědí a nejnovější `runs.started_at` v DB (z jakého
    stavu dev DB se počítalo).
13. **CLI:**
    ```
    python tools/local/compare_peec.py                      # Knauf, docs/peec, rozsah z exportů
    python tools/local/compare_peec.py --plan               # jen vypsat, co by se načetlo a kam
    python tools/local/compare_peec.py --client Knauf --peec-dir docs/peec --overwrite
    ```
14. **Testy: `tools/local/test_compare_peec.py`, stdlib `unittest`**,
    spouštěné na hostiteli (`python -m unittest tools/local/test_compare_peec.py`).
    Pytest sada běží v kontejneru, kam `tools/` nejde (dockerfile ho
    nekopíruje). Testují se čisté funkce: normalizace domén, pravítko
    značek, převod `22:00Z` → lokální datum, párování, Jaccard, kappa,
    Spearman, bootstrap s pevným seedem.
15. **Data se necommitují.** Výstup je v `docs/peec/` (gitignored).
    Philipovi se posílá složka (zip) mimo git.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Načtení, párování a export zdrojů (L1) | ⏳ |
| T2 | Metriky L2/L3/L5 + šum a verdikt | ⏳ |
| T3 | `report.xlsx` + README (metoda, mezery) | ⏳ |
| T4 | Testy čistých funkcí + ověření na reálných datech | ⏳ |
| T5 | Dokumentace, CHANGELOG, uzavření větve | ⏳ |

---

## T1 — Načtení, párování a export zdrojů

**Target:** `tools/local/compare_peec.py`

1. Načíst `docs/peec/peec_knauf_*.json`; ID promptu **z textu promptu**
   proti DB (ne z názvu souboru) — neshoda textu = chyba se jménem
   souboru, ne tichý přeskok. Soubor s víc prompty = chyba.
2. Z DB (jen `SELECT`): prompty klienta (id, set, text, verze, aktivní),
   úspěšné runy v rozsahu dat exportu (run, prompt, model, provider,
   `started_at` v Europe/Prague, persona, trh, latence, `rendered_text`),
   citace (url, doména, titulek, pozice), výstup skillu Competitive
   Visibility Detection, `tracked_entities` + aliasy, `domain_classifications`.
3. Mapování modelů (design decision 4), párování (design decision 5).
4. `--plan`: vypsat počty (soubory, odpovědi Peec podle modelu a dne,
   runy SignalMapu podle modelu a dne, páry), cílovou složku, a skončit.
5. Zapsat `sources/*.csv`, `sources/sources.xlsx`, zkopírovat JSON do
   `sources/peec_raw/`.

**Done when:** `--plan` ukáže 25 promptů, 2 porovnávané modely,
150 odpovědí Peec a 100 runů SignalMapu na model; `sources/` se zapíše
a otevře v Excelu s čitelnými přehláskami.

**Expected commit:** `feat(tools): export Peec and SignalMap answers for comparison`

---

## T2 — Metriky a šum

**Target:** `tools/local/compare_peec.py`

1. Pravítko značek (design decision 6), normalizace domén (9).
2. Pro každý pár: Knauf ano/ne na obou stranách, Jaccard značek, pozice
   první zmínky Knaufu mezi značkami, share of voice, Jaccard domén,
   délka textu, počet domén → `sources/pairs.csv`.
3. Agregáty za model a nástroj: viditelnost každé značky, průměrná pozice,
   share of voice, top 20 domén s podílem a typem, Cohenovo kappa (Knauf),
   Spearman pořadí značek a top domén.
4. Reference šumu a verdikt (design decision 7), bootstrap s pevným seedem.
5. Detector check: vlastní detektor každého nástroje vs pravítko, po
   značkách (kolikrát se liší a ukázkový run/odpověď).

**Done when:** čísla za terra a haiku odpovídají pilotu v „Výchozí stav"
(± zaokrouhlení; rozdíl vysvětlit — pilot neprůměroval skupiny podle
decision 5); Saint-Gobain se objeví v Detector check.

**Expected commit:** `feat(tools): compute Peec vs SignalMap agreement metrics`

---

## T3 — `report.xlsx` + README

**Target:** `tools/local/compare_peec.py`

Listy `report.xlsx` (anglicky):

| List | Obsah |
|---|---|
| README | Co se porovnávalo (klient, prompty, období, **jen modely v obou nástrojích** a proč), odkud data, jak se párovalo, co znamená každá metrika a verdikt, jak číst šum, omezení, `generated_at`, stav DB |
| Summary | Za každý model: metrika × (cross, 3 reference šumu, rozdíl, 95% CI, verdikt) + 3–5 vět hlavních zjištění generovaných z čísel |
| Coverage | Matice prompt × model × den: počet odpovědí v Peec / SignalMapu, chybějící páry |
| Brands | Značka × model × nástroj: viditelnost, pozice, share of voice |
| Domains | Doména × model: podíl v Peec / SignalMapu, typ domény, pořadí |
| Detector check | Rozdíly vlastních detektorů proti pravítku (Saint-Gobain) |
| Pairs | Kopie `pairs.csv` s filtrem |
| Gaps | Seznam mezer: neporovnané modely + důvod; chybějící páry; metriky „víc než šum"; problémy detektorů; neznámá konfigurace Peec (systémový prompt, lokalita, web search pro terra/haiku — vyplní uživatel); co se neměří (L4, sentiment) |

README.md = stejný text jako list README. Hlavička listů zamražená,
autofiltr, šířky sloupců, čísla na 2 desetinná místa.

**Done when:** Philip (nebo uživatel) otevře `report.xlsx` a bez dalšího
vysvětlení pochopí, co se porovnávalo, s čím a kde jsou mezery.

**Expected commit:** `feat(tools): write the Peec comparison report workbook`

---

## T4 — Testy + ověření na reálných datech

**Target:** `tools/local/test_compare_peec.py`

1. Unit testy (design decision 14), mj.: `2026-09-22T22:00:00Z` →
   `2026-09-23`; „Saint-Gobain" i „Saint Gobain" → stejná značka; „Stoff"
   není „Sto"; `https://www.knauf.com/de-DE?utm_source=openai` →
   `knauf.com`; skupina se 3 opakováními má váhu 1; bootstrap se stejným
   seedem = stejný výsledek.
2. Celý běh nad reálnými daty; výstup otevřít v Excelu a projít listy.
3. Ruční kontrola 3 náhodných párů v `pairs.csv` proti textům odpovědí.

**Done when:** `python -m unittest tools/local/test_compare_peec.py`
projde; ruční kontrola sedí.

**Expected commit:** `test(tools): cover the Peec comparison helpers`

---

## T5 — Dokumentace a uzavření

**Target:** `README.md` (sekce lokálních nástrojů), `CHANGELOG.md`,
end-of-branch docs

1. `README.md`: krátký odstavec o `compare_peec.py` vedle
   `refresh_dev_db.py` (co dělá, že výstup je v gitignored `docs/peec/`).
2. `CHANGELOG.md` pod `## [Unreleased]` → `### Added`, jedna odrážka.
3. End-of-branch: `## Status: ...` v obou souborech, řádek v
   `docs/00_INDEX.md`.

**Expected commit:** `docs(docs): document the Peec comparison tool`

---

## Co tahle větev vědomě nedělá

- **Neporovnává modely, které nejsou v obou nástrojích** (design decision 4).
- **Neměří sémantickou podobnost textu ani shodu tvrzení (L4)** — další
  krok, až bude jasné, že metriky L2/L3 stačí nebo ne.
- **Neopravuje alias Saint-Gobain** — to je data v UI (uživatel), skript
  to jen ukáže.
- **Nic neimportuje do DB.** Trvalé ukládání dat z Peec by znamenalo
  novou tabulku → vlastní položka v `docs/ROADMAP.md` se schválením.
- **Nestahuje data z Peec automaticky** — vstupem jsou ručně stažené
  exporty.
