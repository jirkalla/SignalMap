# SignalMap — Tasks: Citation Verification

## v1.0 | Září 2026
## Branch: feature/signalmap-citation-verification
## Task ID prefix: CV
## Roadmap: `docs/ROADMAP.md` #12 (LLM quote-verification skill), rozšířeno

Status: navrženo v konverzaci 2026-09-28. Návrh, prototypy i měření
vznikly nad dev DB (kopie produkce, runy 13.–26. 9. 2026, 495 odpovědí,
6 676 citací) a nad reálně staženými zdroji. Kód v repu se při návrhu
neměnil.

Podklady (artifacty, privátní):
- Návrh, přehled providerů, kritický feedback: „Citace pod lupou“
  https://claude.ai/artifact/HBQvTaEL4QnYBGm8RZMNQU
- Prototyp detailu runu #108 s reálným ověřením:
  https://claude.ai/artifact/VzBL3yWkQkV9FAPRTZ2Hau
- Prototyp pro všechny providery vč. LLM posouzení (runy 108, 311, 422,
  290, 291, 288): https://claude.ai/artifact/39VLVcB4u27cvpv87PRcvs

**Goal:** každé tvrzení v „Rendered answer“ je propojené se zdrojem,
ze kterého pochází, a u každé citace je vidět, jestli zdroj tvrzení
skutečně obsahuje nebo podporuje. Důkaz (snímek stránky, nalezená
pasáž, umístění na stránce) je uložený u nás, takže platí i poté, co se
stránka změní nebo zmizí.

**Jedna větev, šest etap.** Etapy jsou samostatně užitečné a mají
vlastní commity:

- **Etapa A** (T1–T2) — správné tvrzení u OpenAI a Anthropicu, bez
  změny schématu. Opraví dnešní zavádějící data.
- **Etapa B** (T3–T6) — zajištění zdrojů: snímky stránek, rozbalení
  Gemini odkazů, fronta úloh, backfill historie. **Časově kritické**
  (stránky mizí, Gemini přesměrování nemají dokumentovanou životnost).
- **Etapa C** (T7–T9) — deterministické ověření citátů (Anthropic,
  Perplexity) a UI.
- **Etapa D** (T10) — záloha přes archive.org.
- **Etapa E** (T11–T15) — LLM posouzení parafrází (OpenAI, Gemini),
  přepínač u klienta, ruční hodnocení a brána před zapnutím.
- **Etapa F** (T16) — agregace na dashboardu a v `/ops`.
- **Dokumentace a nasazení** (T17–T18).

**Checkpoint po T6:** etapy A+B je legitimní zmergovat a nasadit
samostatně (hlavně kvůli backfillu snímků historie), C–F dodělat na
navazující větvi. Rozhoduje uživatel po T6.

---

## Výchozí stav (naměřeno 2026-09-28, dev DB = kopie produkce)

### Co je dnes v tabulce `citations`

| Provider | Citací | Tvrzení z odpovědi | Text ze zdroje | Poznámka |
|---|---|---|---|---|
| Gemini | 3 472 | ✅ segment | ❌ | všech 3 472 URL je `vertexaisearch.cloud.google.com/grounding-api-redirect/…` |
| OpenAI | 1 930 | ⚠️ **jen značka odkazu** | ❌ | 1 930/1 930 `cited_answer_span` je `([doména](url?utm_source=openai))`, ne tvrzení |
| Anthropic | 1 081 | ❌ (je v `raw_payload`, blok s citací) | ✅ `cited_text` | 715/1 081 zkrácených „…“, 59 složených přes „·“, 13 s HTML entitou |
| xAI | 163 | ❌ indexy 0/0 | ❌ | jen seznam URL, na které agent narazil |
| Perplexity | 30 | ❌ | ✅ `search_results[].snippet` v `raw_payload` | úryvek je stránka převedená do Markdownu |
| DeepSeek | 0 | — | — | bez web search |

UI detailu runu i export dnes u OpenAI zobrazují jako „Cited claim“
značku odkazu.

### Pokus: citát proti živé stránce (60 náhodných citací Anthropicu)

- 48 stránek staženo; **37 citátů doslova**, 7 po normalizaci (oddělovač
  „·“, `&amp;`, začátek uprostřed slova), 1 stránka přepsaná (doloženo
  snímkem archive.org z 2026-03-03, obsahuje původní větu doslova),
  2 články zmizely nebo přesměrovaly jinam, 1 nejasný.
- 12 stránek nestaženo: 5× 403, 3× 404, 2× PDF (pokus je nečetl), 1× 504,
  1× prázdný JS obsah.
- Vymyšlený citát: **žádný**.

### Prototyp na celých runech (přístup 2026-09-28)

| Run | Provider | Výsledek |
|---|---|---|
| 108 | Anthropic (Haiku 4.5) | 14 citací / 8 URL: 11 doslova, 2 po normalizaci (PDF Bundestagu, str. 17 a 10), 1 nelze (404, archive.org 504/429) |
| 311 | OpenAI (gpt-5.6-terra) | 10 citací: LLM 3 podloženo, 3 částečně; 4 nelze (3× Radware, 1× 404) |
| 422 | Gemini (3.1-flash-lite) | 37 vazeb / 18 URL: LLM 7 podloženo, 14 částečně, 5 nepodloženo; 11 nelze (Radware) |
| 290 | Perplexity (sonar) | 15 zdrojů: 9 doslova, 1 po normalizaci (po odstranění Markdownu); 5 nelze (403, 503, úryvek jen z tabulky) |
| 291 | xAI (grok-4.7) | 93 URL: 84 dostupných, 9 ne (Trustpilot 403, Cloudflare, 404, 500) |
| 288 | DeepSeek | žádné zdroje |

LLM posouzení (runy 311 a 422): Claude Haiku 4.5, teplota 0, bez
nástrojů, 32 volání, 204 tis. vstupních + 4 tis. výstupních tokenů,
**0,23 USD**. Věta, o kterou se LLM opřelo, se na stránce dohledala
u 26 z 28 posudků.

### Zjištění, která mění návrh

1. **HTTP 200 nestačí.** `bundeswirtschaftsministerium.de` vrací 200
   se stránkou „Radware Page – Verifying your browser…“ místo obsahu.
   Bez detekce by ověřovač hledal citát v textu ochrany a hlásil
   „nepodloženo“.
2. **knauf.com je za Cloudflare challenge** (`cf-mitigated: challenge`,
   403). 45,5 % citací v odpovědích o Knaufu vede na domény Knaufu.
3. **Citát bývá ve sbalené sekci** (akordeon s atributem `hidden`,
   leichtbau-bw.eu „Bauwesen“). Ctrl+F ho nenajde, ověřovač ano. UI
   musí ukázat, **kde** na stránce text je.
4. **Gemini segmenty bývají utržené** („Carbonbeton), um den
   Materialeinsatz zu optimieren“) → LLM bez kontextu vrací
   „nepodloženo“. Segment je potřeba rozšířit na celou větu.
5. **Perplexity úryvek je Markdown** (`**`, `- `, `|tabulka|`). Po
   odstranění značek sedí 10/10 stažených.
6. **Ořezání stránky na 24 000 znaků** pro LLM ořízlo řadu stránek.
   Posílat vybrané pasáže, ne začátek stránky.
7. **Model občas vrátí neplatný JSON** (neescapované „ “ v citované
   větě). Parsování musí být odolné, nebo strukturovaný výstup.
8. **archive.org je pomalý a rate-limitovaný** (429, 504, „Temporarily
   Offline“ během jednoho odpoledne). Jen záloha s opakováním.
9. **Gemini přesměrování si blokuje robots.txt sám Google.** Ověřeno
   2026-09-28 na reálném runu (CV-5, prompt #7 „Leichtbau“, Knauf,
   gemini-3.1-flash-lite): všech 19 unikátních
   `vertexaisearch.cloud.google.com/grounding-api-redirect/…` odkazů
   skončilo s `error_reason='robots'`. `curl
   https://vertexaisearch.cloud.google.com/robots.txt`:
   ```
   User-agent: *
   Disallow: /grounding-api-redirect
   Disallow: /grounding-redirect
   ```
   Design decision 6 počítala s „rozbalit přesměrování v rámci
   stažení“, ale přesně tuhle cestu Google sám zakazuje crawlerům —
   **nejde o ochranu proti botům** (design decision 8), je to
   deklarovaná politika stránky, kterou design decision 7 říká
   respektovat. Důsledek: bez dalšího kroku se **žádná Gemini citace
   přes tento mechanismus nikdy neověří** (`unverifiable/robots`
   natrvalo, ne dočasně). **Otevřená otázka pro T7/T9** (rozhoduje
   uživatel): buď to tak necháme (Gemini citace budou vždy
   „unverifiable“, UI to musí odlišit od ostatních důvodů), nebo se
   najde jiný, robots.txt respektující způsob rozbalení přesměrování
   (např. Google k tomu má sankcionované API/SDK) — nezkoumáno.

### Velikost snímků (80 náhodných zdrojů)

| | Průměr | Medián | Max |
|---|---|---|---|
| Surové HTML | 204 KB | 113 KB | 1,3 MB |
| **Extrahovaný text** | **10,6 KB** | 7,5 KB | 75 KB |
| PDF soubor | 5,1 MB | — | 17,9 MB |

~3 000 unikátních URL za 14 dní → ~6 500 snímků/měsíc bez deduplikace
→ **~30 MB/měsíc** jen textu v Postgresu (dnes má celá DB 33 MB).
128 citací vede přímo na `.pdf`.

---

## Co vrací provideři (oficiální dokumentace, přístup 2026-09-28)

| Provider | Text ze zdroje | Důkaz | Dokumentace |
|---|---|---|---|
| Anthropic | ano, zkrácený | „cited_text: Up to 150 characters of the cited content“ | https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool |
| OpenAI | ne | `url_citation` = `url`, `title`, `start_index`, `end_index` | https://developers.openai.com/api/docs/guides/tools-web-search |
| Gemini | ne | `groundingChunks` = „web sources (uri and title)“, offsety v bajtech | https://ai.google.dev/gemini-api/docs/google-search · https://ai.google.dev/api/generate-content |
| Perplexity | úryvek, bez záruky doslovnosti | `search_results[].snippet` | https://docs.perplexity.ai/docs/agent-api/quickstart |
| xAI | ne | „a comprehensive list of URLs for all sources the agent encountered“ | https://docs.x.ai/developers/tools/citations |
| DeepSeek | nic | `citations` „Ignored“ | https://api-docs.deepseek.com/guides/anthropic_api |

Nikdo negarantuje, že zdroj tvrzení podporuje. Podmínky zobrazení:
OpenAI — citace „clearly visible and clickable“; Anthropic — citace
zobrazit koncovým uživatelům; Google (https://ai.google.dev/gemini-api/terms)
— Search Suggestions, nemodifikovat odkazy, grounded text držet nejvýš
2 roky.

---

## ⚠️ Schema flagy (AI_INSTRUCTIONS.md §4) — schválit před T3/T7/T11/T14

Nic z toho dnes `docs/REQUIREMENTS.md` nepopisuje. Každá migrace se
ukáže uživateli před napsáním.

| Migrace | Tabulka / sloupec | Účel | Task |
|---|---|---|---|
| 0036 | `source_texts` (`sha256` PK, `text`, `chars`) | obsahově adresovaný text snímku; stejný text = jeden řádek | T3 |
| 0036 | `source_documents` | jedno stažení jedné URL (live/archive), neměnné | T3 |
| 0036 | `verification_jobs` | fronta úloh capture/judge (SKIP LOCKED jako `run_queue`) | T3 |
| 0037 | `citation_verifications` | jedno ověření jedné citace, neměnné | T7 |
| 0038 | `clients.auto_verify_citations` (bool, default `false`) | přepínač LLM posouzení | T13 |
| 0039 | `verification_labels` | ruční verdikty (slepá sada + souhlas/nesouhlas) | T14 |

Nové závislosti v `requirements.txt` (pinned, HD-T3): `httpx` (dnes jen
tranzitivně přes SDK a v `requirements-dev.txt`), `pypdf`.

---

## Design decisions (rozhodnuto v konverzaci 2026-09-28)

### Obecné

1. **Dva kroky s různou povahou.**
   - **Zajištění (capture):** rozbalit odkaz, stáhnout stránku, uložit
     snímek. Skoro zdarma, **nesnese odklad** → běží vždy, pro všechny
     klienty, po každém úspěšném runu.
   - **Posouzení (judge):** porovnat citát / tvrzení se snímkem. Doslovná
     kontrola (Anthropic, Perplexity) je zdarma a běží vždy. LLM
     posouzení stojí peníze → řídí ho přepínač u klienta (T13).
2. **Evidence se nepřepisuje** (`AI_INSTRUCTIONS.md` §3, NFR-6).
   `citations` se nemění. Každé stažení = nový `source_documents` řádek,
   každé ověření = nový `citation_verifications` řádek s
   `verifier_version`. Lepší ověřovač přidá nové ověření vedle starého;
   UI ukazuje nejnovější.
   **Odchylka od ROADMAP #12:** ta počítala s `AnalysisSkill`
   (`execution_type='llm_prompt'`) a výsledkem v `analysis_results.output`.
   Verdikt je ale per citace, ne per odpověď, a dashboard ho potřebuje
   agregovat podle providera a domény — proto vlastní tabulka
   `citation_verifications` (rozhodnuto 2026-09-28). Hák na
   `AnalysisSkill` zůstává volný pro sentiment/atributy.
3. **Nic z toho neběží v requestu.** Stahování a LLM jsou ve workeru
   (T5). Ruční „Ověřit“ jen zařadí úlohu.

### Tvrzení (etapa A)

4. **Odvození tvrzení podle providera** (čistá funkce, testovaná na
   reálných případech):
   - **Anthropic:** text bloku z `raw_payload.content[]`, na kterém
     citace visí (pořadí citací v DB = pořadí v blocích, ověřeno na
     runu 108).
   - **OpenAI:** text před značkou `([…](…))` od nejbližší hranice:
     konec věty, nový řádek, `|` buňky tabulky, předchozí značka.
     Offsety OpenAI jsou ve znacích (71/71, `app/adapters/base.py`).
   - **Gemini:** segment rozšířený na celou větu / položku seznamu
     v `rendered_text` (hledat jako text, offsety jsou v bajtech).
     Původní segment zůstává v `citations`.
   - **Perplexity, xAI:** tvrzení neexistuje — API vazbu na věty nevrací.
5. **V etapě A se tvrzení počítá při zobrazení a exportu**, neukládá se.
   Uloží se až v `citation_verifications.claim_text` (T7), spolu s
   metodou odvození (`claim_method`).

### Zajištění zdrojů (etapa B)

6. **Gemini přesměrování rozbalit hned** (redirect follow v rámci
   stažení). **Zobrazovat původní odkaz**, rozbalenou adresu jen jako
   informaci — podmínky Google zakazují odkazy modifikovat.
   `utm_source=openai` se v zobrazení odstraní, stahuje se URL tak, jak ji
   provider vrátil.
7. **Poctivý User-Agent:** `SignalMapVerifier/1.0 (+kontaktní URL)`,
   respektovat `robots.txt` (stdlib `urllib.robotparser`, cache na
   doménu). Browser UA z prototypu se nepoužije — umožní to Knaufu (a
   komukoli dalšímu) ověřovač povolit podle UA. T4 změří rozdíl úspěšnosti
   proti browser UA na vzorku; když bude propad velký, rozhodne uživatel.
8. **Ochranu proti botům nikdy neobcházet.** Žádný headless prohlížeč na
   obcházení challenge, žádné CAPTCHA. Detekce:
   - 403/503 s `cf-mitigated`, `server: cloudflare`;
   - **200 s interstitial** — krátký text (< 1 500 znaků) odpovídající
     vzoru (`verifying your browser|just a moment|checking your browser|
     captcha|access denied|incapsula|radware|…`).
   Výsledek: `unverifiable` s důvodem `bot_challenge` a názvem vendoru.
9. **Limity:** timeout 20 s, max 20 MB, max 10 přesměrování, souběžně
   nejvýš 1 požadavek na doménu a 1 s mezi požadavky na stejnou doménu.
10. **Extrakce HTML vlastním parserem z prototypu**, ne `trafilatura`.
    Důvod: `trafilatura` zahazuje „boilerplate“ a sbalený obsah, ze
    kterého provideři prokazatelně citují (akordeon na leichtbau-bw.eu).
    Parser bere viditelný i skrytý text, vynechá `script/style/noscript/
    svg/template` a ke každému textovému uzlu si pamatuje řetězec
    nadpisů a sbalenou sekci (`hidden`, zavřený `<details>`, titulek přes
    `aria-labelledby`/`<summary>`).
11. **PDF přes `pypdf`**: text po stranách, spojit rozdělená slova
    (`(\w)-\n(\w)`), uložit začátky stran pro číslo strany. Ukládá se
    jen text, ne soubor. Bez OCR — PDF bez textové vrstvy =
    `unverifiable/pdf_no_text`.
12. **Úložiště = varianta A:** text v `source_texts` (klíč sha256 textu,
    deduplikace i mezi URL), metadata v `source_documents`. Surové HTML
    se neukládá (když bude potřeba, varianta B do souborového volume, ne
    do DB).
13. **Cache 24 h:** stejná URL stažená před < 24 h se nestahuje znovu,
    ověření použije existující snímek.
14. **Backfill historie capture hned po nasazení B** (CLI, T6), protože
    každý den čekání znamená další zmizelé stránky.

### Ověření citátu (etapa C)

15. **Normalizace** na obou stranách, s mapou zpět na původní offsety:
    NFKC, malá písmena, sjednocené uvozovky/apostrofy/pomlčky, odstraněné
    měkké dělení, ß→ss, sloučené bílé znaky, HTML entity.
    **Slova ani čísla se nemění.**
16. **Rozdělení na kusy:** `...`, `…`, ` · ` (Anthropic); u Perplexity
    navíc nové řádky, buňky `|` a odstranění Markdownu (`**`, `__`,
    odrážky, `#`). Kusy < 20 znaků se nekontrolují. Musí sedět všechny.
17. **Podobnost jako pojistka:** kus bez přesné shody se hledá oknem
    s podobností ≥ 0,9 (`SequenceMatcher`). **Pravidlo čísel:** všechny
    číselné sekvence kusu musí být v nalezeném okně beze změny, jinak
    shoda neplatí („150 bytů“ ≠ „50 bytů“).
18. **Umístění** se ukládá k nálezu: nadpisy, sbalená sekce + titulek,
    číslo strany PDF. UI k tomu dá odkaz „otevřít na tomto místě“
    (`#:~:text=` pro HTML, `#page=N` pro PDF) s upozorněním, že sbalenou
    sekci prohlížeč sám neotevře.

### archive.org (etapa D)

19. **Jen záloha:** při 404/410, přesměrování na jinou stránku nebo
    nenalezeném citátu. Wayback CDX `closest` k datu runu, jen
    `statuscode:200`, stažení přes `id_` URL. 1 požadavek / 3 s,
    timeout 60 s. 429/5xx → úloha se odloží a zopakuje (backoff), ne
    `unverifiable` natrvalo. Uloží se jako `source_documents` s
    `method='archive'` a `archive_timestamp`.

### LLM posouzení (etapa E)

20. **Přes adaptér, ne SDK.** `ProviderAdapter` dostane metodu
    `judge(system, user, model_name) -> JudgePayload` bez search nástrojů
    (AI_INSTRUCTIONS §3 — žádné volání SDK mimo `app/adapters/`). Nejdřív
    Anthropic. Model je řádek v `ai_models` (výchozí
    `claude-haiku-4-5-20251001`), cena přes existující
    `ai_model_price_components` a `estimate_run_cost`.
21. **Vstup = tvrzení + top-k pasáží**, ne ořezaná stránka. BM25 nad
    větami/dvojicemi vět stránky, k = 5, celkem ≤ ~6 000 znaků. Teplota 0.
22. **Výstup:** verdikt (`supported / partially_supported /
    not_supported / contradicted`), jednovětové zdůvodnění, věta ze
    stránky, o kterou se opírá. Strukturovaný výstup API, kde to adaptér
    umí, jinak odolné parsování (prototyp: fallback přes regex).
23. **Věta, o kterou se LLM opírá, se deterministicky dohledá na
    snímku** (stejný matcher jako T8). Nenalezena → verdikt se uloží,
    ale s `needs_review = true` a UI ho tak označí.
24. **Verdikt tvrzení = nejlepší verdikt jeho zdrojů** (tvrzení je
    podložené, když ho podpoří aspoň jeden). UI u „částečně“ vysvětlí
    typický případ: věta jmenuje víc subjektů a každý zdroj pokrývá svůj.
25. **Přepínač u klienta** `auto_verify_citations` (výchozí vypnuto)
    řídí jen LLM posouzení nových runů. Vedle toho: tlačítko „Ověřit
    citace“ na detailu runu, hromadné ověření klienta za období
    s počtem citací a odhadem ceny před spuštěním.
26. **Cena ověřování se počítá do měsíčního rozpočtu klienta**
    (`client_month_to_date_spend`, `budget.threshold_exceeded`) a
    ukazuje se v `/ops` jako samostatná řádka.
27. **Brána před zapnutím:** ruční slepá sada ~100 citací (verdikt LLM
    hodnotitel nevidí), shoda LLM s člověkem a hlavně počet falešných
    „supported“. Navržený práh: shoda ≥ 90 %, falešné „supported“ ≤ 3 %.
    Práh potvrdí uživatel v T15. Sada se uchová; při změně promptu,
    modelu nebo vstupu se přeměří na stejné sadě. Průběžně tlačítko
    „Souhlasím / Nesouhlasím“ u verdiktu na detailu runu.

### Verdikty

28. Jeden číselník verdiktů pro UI i agregace:

| Verdikt | Kdy |
|---|---|
| `verified_exact` | všechny kusy citátu doslova po normalizaci |
| `verified_normalized` | všechny kusy, některé přes podobnost ≥ 0,9 + pravidlo čísel |
| `page_changed` | živá stránka citát nemá, archivní snímek z doby runu ano |
| `archive_only` | živá stránka zmizela (404/410/jinam), archiv citát má |
| `partially_found` | jen některé kusy citátu |
| `not_found` | stránka stažená, citát není ani v archivu |
| `llm_supported` / `llm_partial` / `llm_not_supported` / `llm_contradicted` | LLM posouzení parafráze |
| `unverifiable` + `reason` | `http_403`, `http_404`, `http_5xx`, `timeout`, `bot_challenge`, `robots`, `too_large`, `pdf_no_text`, `no_checkable_text`, `archive_unavailable` |
| `source_reachable` | xAI: zdroj dostupný (bez tvrzení) |

29. **xAI = „prohlédnuté zdroje“, ne citace.** V agregacích zvlášť.
    DeepSeek se do metrik ověřování nepočítá.
30. **Verze:** MINOR (nové funkce + migrace), `docs/DEPLOYMENT.md`
    kapitola 0. Bump a tag dělá uživatel.

---

## Task Index

| ID | Name | Etapa | Status |
|----|------|-------|--------|
| T1 | Odvození tvrzení podle providera (služba + testy) | A | ⏳ |
| T2 | Tvrzení v detailu runu a v exportu | A | ⏳ |
| T3 | Migrace 0036: `source_texts`, `source_documents`, `verification_jobs` | B | ⏳ |
| T4 | Služba pro stažení a extrakci zdroje | B | ⏳ |
| T5 | Fronta úloh a zpracování ve workeru | B | ⏳ |
| T6 | Backfill snímků historie (CLI) | B | ⏳ |
| T7 | Migrace 0037: `citation_verifications` | C | ⏳ |
| T8 | Ověření citátu: normalizace, kusy, umístění | C | ⏳ |
| T9 | UI: souhrn, zvýraznění tvrzení, panel s důkazem | C | ⏳ |
| T10 | Záloha přes archive.org | D | ⏳ |
| T11 | Adaptér: `judge()` bez nástrojů + cena | E | ⏳ |
| T12 | LLM posouzení: pasáže, verdikt, dohledání věty | E | ⏳ |
| T13 | Přepínač u klienta, ruční a hromadné ověření, rozpočet | E | ⏳ |
| T14 | Ruční verdikty: slepá sada, souhlas/nesouhlas | E | ⏳ |
| T15 | Měření shody a rozhodnutí o zapnutí (bez kódu) | E | ⏳ |
| T16 | Agregace: dashboard a `/ops` | F | ⏳ |
| T17 | Dokumentace + CHANGELOG | — | ⏳ |
| T18 | Nasazení, backfill na produkci, end-of-branch docs | — | ⏳ |

---

## T1 — Odvození tvrzení podle providera

**Target:** `app/services/claims.py` (nový), `tests/test_claims.py` (nový)

1. `derive_claim(provider_code, citation, rendered_text, raw_payload)
   -> DerivedClaim | None` (`text`, `start`, `end` v `rendered_text`,
   `method`: `anthropic_block` / `openai_before_marker` /
   `gemini_sentence` / `none`). Pravidla v design decision 4.
2. Anthropic: projít `raw_payload["content"]`, bloky `type == "text"`
   s `citations`, spárovat s řádky `citations` podle pořadí; ověřit shodu
   `url`. Když pořadí nesedí, vrátit `None` a zalogovat (nehádat).
3. OpenAI: od `answer_span_start` zpět k hranici; odstranit `**`,
   úvodní `- `, `#`, `|`. Když výsledek < 15 znaků, vzít i předchozí větu.
4. Gemini: najít `cited_answer_span` v `rendered_text` jako text,
   rozšířit na hranice věty / řádku.
5. Testy na reálných případech z dev DB (zkopírovat jako fixtures, ne
   číst DB v testech): run 108 (Anthropic, 3 citace ve stejném bloku),
   run 311 (OpenAI, věta v tabulce, dvě značky za sebou), run 422
   (Gemini, utržený segment „Carbonbeton), um den Materialeinsatz zu
   optimieren“ → celá odrážka).

**Done when:** testy projdou; celá sada `pytest` projde.

**Expected commit:** `feat(runs): derive the cited claim per provider`

---

## T2 — Tvrzení v detailu runu a v exportu

**Target:** `app/routers/runs.py` (`run_detail`), `app/templates/runs/detail.html`,
`app/services/export.py`, `app/i18n/en.json`, `app/i18n/de.json`,
`tests/test_runs.py`, `tests/test_export.py`

1. Detail runu: „Cited claim“ bere z `derive_claim`; u OpenAI se značka
   odkazu už jako tvrzení neukazuje. U Anthropicu se tvrzení nově
   zobrazí (dnes chybí).
2. Export: nový sloupec `claim_text` (+ `claim_method`) vedle
   ponechaného `cited_answer_span` — odběratelé exportu se nerozbijí.
3. i18n klíče DE/EN pro novou nápovědu („Derived from the answer text“).
4. Testy: OpenAI citace v detailu neukazuje značku jako tvrzení; export
   má nové sloupce ve všech třech formátech.

**Done when:** testy projdou; ruční kontrola runů 108, 311, 422 v
prohlížeči na ~640 / ~1024 / desktop.

**Expected commit:** `fix(runs): show the real cited claim instead of the link marker`

---

## T3 — Migrace 0036: zdroje a fronta úloh

**Target:** `alembic/versions/0036_source_documents.py`,
`app/models/verification.py` (nový), `app/models/__init__.py`

**Nejdřív ukázat návrh schématu uživateli (schema flag).**

1. `source_texts`: `sha256` (char 64, PK), `text` (text), `chars` (int),
   `created_at`.
2. `source_documents`: `id`, `requested_url`, `final_url`, `method`
   (`live`/`archive`, CHECK), `archive_timestamp`, `http_status`,
   `content_type`, `error_reason` (číselník z design decision 28),
   `challenge_vendor`, `text_sha256` (FK `source_texts`, NULL při
   selhání), `page_starts` (jsonb, PDF), `locations` (jsonb — mapa
   textových uzlů na nadpisy / sbalené sekce, formát z prototypu),
   `bytes`, `duration_ms`, `fetched_at`, `verifier_version`.
   Index `(requested_url, fetched_at desc)`.
3. `verification_jobs`: `id`, `raw_response_id` (FK), `kind`
   (`capture`/`judge`, CHECK), `status` (`queued/leased/done/error/
   deferred`, CHECK), `priority`, `scheduled_for`, `attempts`,
   `leased_by`, `lease_expires_at`, `requested_by_user_id`, `error`,
   `created_at`, `finished_at`. Index pro claim jako `run_queue`
   (`priority desc, scheduled_for` where `status='queued'`).
4. Žádná data se nemigrují.

**Done when:** `alembic upgrade head` a `downgrade -1` projdou lokálně;
`pytest` projde.

**Expected commit:** `feat(runs): add source snapshot and verification job tables`

---

## T4 — Služba pro stažení a extrakci zdroje

**Target:** `app/services/source_capture.py` (nový),
`app/services/source_extract.py` (nový), `requirements.txt`,
`tests/test_source_capture.py`, `tests/fixtures/sources/` (nahrané HTML/PDF)

1. `capture_url(db, url, *, now) -> SourceDocument` podle design
   decisions 6–13: cache 24 h, `robots.txt`, UA, limity, redirecty,
   detekce challenge (vč. 200 interstitial), PDF.
2. Extraktor HTML z prototypu (`Extract` + `locate`), přepsaný čistě,
   s docstringy; PDF extrakce se začátky stran.
3. Ukládání: `source_texts` insert-if-missing podle sha256,
   `source_documents` vždy nový řádek.
4. `requirements.txt`: pin `httpx`, `pypdf`.
5. Testy **bez sítě** (`httpx.MockTransport`): 200 s obsahem, 200
   s Radware stránkou → `bot_challenge`, 403 s `cf-mitigated`, 404,
   PDF (malý fixture), `robots.txt` zakazuje, redirect Gemini → finální
   URL, sbalený akordeon → `locations` s titulkem „Bauwesen“, cache
   24 h, deduplikace textu.
6. **Měření UA** (ne test, jednorázově lokálně): 100 URL z dev DB
   s poctivým UA a s browser UA, výsledek do tohoto dokumentu. Když je
   rozdíl > 10 p. b., zastavit se a nechat rozhodnout uživatele.

**Done when:** testy projdou; měření UA zapsané.

**Expected commit:** `feat(runs): capture and extract cited source pages`

---

## T5 — Fronta úloh a zpracování ve workeru

**Target:** `app/services/verification_queue.py` (nový), `app/worker.py`,
`app/services/run_execution.py`, `tests/test_verification_queue.py`

1. `enqueue_capture(db, raw_response_id)` po úspěšném runu (v
   `execute_run`, stejně „best effort“ jako analýzy — chyba nesmí
   shodit run).
2. `claim_next_job` se `FOR UPDATE SKIP LOCKED`, lease 15 min,
   `release_expired_job_leases`.
3. Worker: **runy mají přednost** — úlohu ověření bere jen když
   `claim_next` z `run_queue` nic nevrátí. Zpracování capture: unikátní
   URL citací odpovědi → `capture_url`. Selhání archivu/5xx → `deferred`
   s backoffem.
4. Nemá zdržet runy: jedna úloha = jedna odpověď; limit času na úlohu.
5. Testy: po runu vznikne úloha; worker s prázdnou `run_queue` úlohu
   zpracuje; s položkou v `run_queue` vezme nejdřív run; expirovaný lease
   se uvolní.

**Done when:** testy projdou; lokálně jeden run fixture (Skoda, prompt
54, gemini-3.1-flash-lite) → během minuty snímky jeho zdrojů v DB.

**Expected commit:** `feat(runs): queue source capture after each run`

---

## T6 — Backfill snímků historie

**Target:** `app/cli/backfill_sources.py` (nový), `tests/test_backfill_sources.py`

1. `python -m app.cli.backfill_sources [--client ID] [--since DATE]
   [--dry-run]` — zařadí `capture` úlohy pro odpovědi bez snímků, od
   nejstarších (Gemini přesměrování jako první).
2. `--dry-run` vypíše počty odpovědí, citací a unikátních URL.
3. Idempotentní: odpověď s už existující capture úlohou se přeskočí.

**Done when:** test idempotence projde; lokálně nad dev DB `--dry-run`
ukáže ~3 000 URL; ostrý běh na vzorku jednoho klienta.

**Expected commit:** `feat(runs): add a backfill command for source snapshots`

**➜ Checkpoint:** tady se rozhodne, jestli A+B zmergovat a nasadit hned.

---

## T7 — Migrace 0037: `citation_verifications`

**Target:** `alembic/versions/0037_citation_verifications.py`,
`app/models/verification.py`

**Nejdřív ukázat návrh schématu uživateli (schema flag).**

`id`, `citation_id` (FK), `source_document_id` (FK, NULL), `claim_text`,
`claim_method`, `check_type` (`quote`/`llm`/`reachability`), `verdict`
(CHECK, design decision 28), `reason`, `similarity`, `matched_text`,
`match_start`, `match_end`, `page_number`, `location` (jsonb),
`fragments` (jsonb — výsledek po kusech), `llm_model_id` (FK `ai_models`,
NULL), `llm_reason`, `llm_quote`, `llm_quote_found`, `needs_review`,
`tokens_in`, `tokens_out`, `cost_usd`, `verifier_version`, `created_at`.
Index `(citation_id, created_at desc)`.

**Done when:** upgrade/downgrade projde; `pytest` projde.

**Expected commit:** `feat(runs): add the citation verification table`

---

## T8 — Ověření citátu

**Target:** `app/services/quote_match.py` (nový),
`app/services/citation_verification.py` (nový),
`tests/test_quote_match.py`

1. Normalizace s mapou offsetů, kusy, podobnost, **pravidlo čísel**,
   umístění (design decisions 15–18).
2. Anthropic: `cited_text`; Perplexity: `snippet` z `raw_payload`
   `search_results` spárovaný s citací podle URL.
3. Po capture úloze se pro odpověď spustí doslovná kontrola (zdarma)
   a zapíše `citation_verifications`.
4. Testy na reálných případech z prototypu: „Techno- logien“ (PDF),
   „·“, `&amp;`, Markdown Perplexity (ADAC), sbalený akordeon, 404,
   challenge; **negativní test** „150 bis 400 Wohnungen“ vs. „50
   Wohnungen“ nesmí projít podobností.

**Done when:** testy projdou; nad runy 108 a 290 (lokálně) stejný
výsledek jako prototyp (108: 11 + 2 + 1; 290: 9 + 1 + 5).

**Expected commit:** `feat(runs): verify cited quotes against captured sources`

---

## T9 — UI: souhrn, zvýraznění tvrzení, panel s důkazem

**Target:** `app/templates/runs/detail.html`,
`app/templates/partials/verification.html` (nový),
`app/routers/runs.py`, `app/i18n/*.json`, `tests/test_runs.py`

1. Podle prototypu (artifacty výše): karta „Citation verification“ se
   souhrnem a filtrem podle verdiktu; v „Rendered answer“ podtržená
   tvrzení (barva = verdikt) s čísly citací; panel „Evidence“ (citát,
   nalezená pasáž se zvýrazněním, umístění, odkaz na místo, stav
   snímku); seznam citací s verdiktem.
2. Bez frameworku: Jinja + malý vanilla JS (klik → panel), žádný Vue
   ostrůvek. Na mobilu panel pod textem.
3. Gemini: odkaz původní, rozbalená adresa jako text (design decision 6).
4. Verdikty a důvody přes `t()`, DE/EN.
5. Stav „čeká na zajištění“ u runu, jehož úloha ještě neproběhla.

**Done when:** ověřeno v prohlížeči na ~640 / ~1024 / desktop u runů
108, 290, 291, 288 (každý mód jednou).

**Expected commit:** `feat(runs): show citation verification on the run detail`

---

## T10 — Záloha přes archive.org

**Target:** `app/services/archive_lookup.py` (nový),
`app/services/citation_verification.py`, `tests/test_archive_lookup.py`

1. Design decision 19. Výsledky `page_changed` / `archive_only`.
2. Rate limit a odložení při 429/5xx přes `verification_jobs`
   (`deferred`, backoff).
3. Testy s `MockTransport`: CDX vrátí snímek → verdikt `page_changed`;
   CDX prázdný → `not_found`; 429 → úloha odložená.

**Done when:** testy projdou; ruční ověření na karriere-familienunternehmen.de
(snímek 2026-03-03 obsahuje původní větu).

**Expected commit:** `feat(runs): fall back to archived snapshots for changed pages`

---

## T11 — Adaptér: `judge()` bez nástrojů

**Target:** `app/adapters/base.py`, `app/adapters/anthropic.py`,
`tests/test_adapters.py`, `tests/fake_adapter.py`

1. `judge(system, user, model_name) -> JudgePayload` (`text`,
   `token_usage`), **bez** web search nástrojů, teplota 0.
2. Nejdřív jen Anthropic; ostatní adaptéry `NotImplementedError`
   s jasnou zprávou. Model vybraný v nastavení (řádek `ai_models`).
3. FakeAdapter: deterministický `judge` pro testy.

**Done when:** testy projdou.

**Expected commit:** `feat(adapters): add a tool-free judge call`

---

## T12 — LLM posouzení

**Target:** `app/services/claim_judge.py` (nový),
`app/services/passages.py` (nový), `tests/test_claim_judge.py`

1. BM25 top-k pasáží (design decision 21), prompt ze sekce níže,
   verdikt + zdůvodnění + věta.
2. Odolné parsování; věta se dohledá na snímku (`quote_match`) →
   `needs_review` když ne.
3. Cena přes `estimate_run_cost` s cenou modelu soudce, uložit
   `tokens_in/out`, `cost_usd`.
4. Zapisuje `citation_verifications` s `check_type='llm'`.
5. Testy s FakeAdapterem: platný JSON, rozbitý JSON (neescapované
   uvozovky), věta nenalezena → `needs_review`.

**Prompt (výchozí, z prototypu):**
```
You check whether a web page supports a claim that an AI assistant made while citing that page.
Judge only against the page text you are given. Do not use outside knowledge.
Verdicts: supported | partially_supported | not_supported | contradicted
Reply with JSON only: {"verdict": "...", "reason": "<one sentence>", "quote": "<the single most relevant sentence from the page, copied character for character, or empty string>"}
```

**Done when:** testy projdou; lokálně nad runy 311 a 422 výsledek
srovnatelný s prototypem (311: 3/3; 422: 7/14/5), cena < 0,30 USD.

**Expected commit:** `feat(runs): judge paraphrased claims against source passages`

---

## T13 — Přepínač u klienta, ruční a hromadné ověření, rozpočet

**Target:** migrace `0038_client_auto_verify.py`, `app/models/client.py`,
`app/routers/clients.py`, `app/routers/runs.py`,
`app/templates/clients/*.html`, `app/templates/runs/detail.html`,
`app/services/notifications.py`, `app/services/ops_dashboard.py`,
`app/i18n/*.json`, testy

1. `clients.auto_verify_citations` (schema flag), checkbox ve formuláři
   klienta s vysvětlením ceny.
2. Detail runu: „Ověřit citace“ (HTMX POST → úloha `judge`, `HX-Redirect`
   podle vzoru `trigger_run`).
3. Klient: „Ověřit zpětně“ s obdobím → náhled (počet citací, odhad ceny
   z průměru tokenů) → potvrzení → úlohy.
4. `client_month_to_date_spend` započítá `citation_verifications.cost_usd`;
   `/ops` samostatná řádka „Citation verification“.
5. Testy: přepínač vypnutý → po runu jen capture + doslovná kontrola;
   zapnutý → i `judge`; rozpočet zahrnuje cenu ověření.

**Done when:** testy projdou; ruční průchod v prohlížeči.

**Expected commit:** `feat(clients): add per-client automatic citation verification`

---

## T14 — Ruční verdikty

**Target:** migrace `0039_verification_labels.py`,
`app/routers/verification.py` (nový), `app/templates/verification/label.html`,
`app/templates/runs/detail.html`, `app/i18n/*.json`, testy

1. `verification_labels`: `id`, `citation_id`, `user_id`, `verdict`,
   `mode` (`blind`/`review`), `agrees_with_verification_id` (NULL u
   blind), `note`, `created_at` (schema flag).
2. `/verification/label`: slepé hodnocení — tvrzení, zdroj (snímek
   s nejbližšími pasážemi + odkaz), tlačítka verdiktu; verdikt LLM
   **nevidět**. Výběr vzorku stratifikovaně podle providera a verdiktu
   LLM.
3. Detail runu: u LLM verdiktu „Souhlasím / Nesouhlasím“ (+ volitelně
   správný verdikt).
4. Jen role editor/admin.

**Done when:** testy projdou; ověřeno na mobilu (hodnocení z telefonu).

**Expected commit:** `feat(runs): collect human verdicts for citation checks`

---

## T15 — Měření shody a rozhodnutí o zapnutí

**Target:** žádný kód; výsledky do tohoto dokumentu

1. Uživatel ohodnotí slepou sadu (~100 citací, 2–3 h); ~20 z nich
   nezávisle druhý člověk, pokud je k dispozici.
2. Report: shoda celkem, matice záměn, falešné „supported“, rozpad podle
   providera; shoda mezi lidmi.
3. Uživatel potvrdí práh (design decision 27) a rozhodne o zapnutí
   `auto_verify_citations` pro Knauf.
4. Když práh nesplněno: úprava promptu / pasáží / modelu a přeměření na
   stejné sadě.

**Done when:** výsledky a rozhodnutí zapsané.

**Expected commit:** `docs(citation-verification): record judge evaluation results`

---

## T16 — Agregace

**Target:** `app/services/dashboard.py`, `app/services/ops_dashboard.py`,
šablony dashboardu a `/ops`, `app/i18n/*.json`, testy

1. Dashboard: podíl ověřených / nepodložených / neověřitelných citací
   podle providera a domény; typ zdroje z `domain_classifications`;
   vlastní domény klienta zvlášť.
2. xAI jako „prohlédnuté zdroje“ zvlášť, DeepSeek mimo.
3. `/ops`: úspěšnost capture podle důvodu (`bot_challenge` podle
   vendoru, 404…), fronta úloh, cena ověřování.

**Done when:** testy projdou; ověřeno v prohlížeči na třech šířkách.

**Expected commit:** `feat(runs): add citation verification rates to dashboards`

---

## T17 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/REQUIREMENTS.md`, `docs/ROADMAP.md`,
`docs/DEPLOYMENT.md`, `docs/TASKS.md` (jen odkaz)

1. `CHANGELOG.md` `[Unreleased]`: `### Added` (ověřování citací, snímky
   zdrojů, přepínač), `### Fixed` (tvrzení u OpenAI).
2. `docs/REQUIREMENTS.md`: nové tabulky, verdikty, zásada „evidence se
   nepřepisuje“ i pro snímky.
3. `docs/ROADMAP.md` #12: odkaz sem, co je hotové.
4. `docs/DEPLOYMENT.md`: backfill krok, `robots`/UA pro allowlist
   klientů, co dělat při `bot_challenge` u klienta.

**Done when:** diff ukázaný uživateli a odsouhlasený.

**Expected commit:** `docs(docs): document citation verification`

---

## T18 — Nasazení, backfill, end-of-branch

**Target:** produkce; end-of-branch docs

1. **Před nasazením (uživatel):** požádat Knauf o povolení ověřovače
   ve WAF (UA `SignalMapVerifier/…` + IP serveru).
2. Merge, bump, tag — uživatel. Migrace 0036–0039.
3. Backfill na produkci (`--dry-run`, pak ostře) — sledovat `/ops`.
4. Druhý den: úspěšnost capture podle důvodu, počet `bot_challenge`
   u knauf.com před/po allowlistu.
5. End-of-branch: `## Status:` v obou souborech, řádek v
   `docs/00_INDEX.md`.

**Done when:** backfill doběhl, měření zapsané.

**Expected commit:** `docs(citation-verification): record deploy and close the branch`

---

## Otevřené otázky (rozhoduje uživatel)

1. **Allowlist u Knaufu** — kdo a kdy požádá (T18 bod 1).
2. **Jedna nebo dvě větve** — checkpoint po T6.
3. **Práh brány** (design decision 27) — potvrdit v T15.
4. **Retence snímků** — Google omezuje grounded text na 2 roky; snímky
   jsou naše data, ale pravidlo mazání chybí. Návrh: 24 měsíců,
   samostatná úloha mimo tuhle větev.
5. **Model soudce** — výchozí Haiku 4.5; jiný provider než posuzovaná
   odpověď je volitelný, ne povinný.

---

## Co tahle větev vědomě nedělá

- **Neobchází ochranu proti botům** — žádný headless prohlížeč na
  challenge, žádné CAPTCHA (design decision 8).
- **Žádné OCR** skenovaných PDF.
- **Neukládá surové HTML ani PDF soubory** (design decision 12).
- **Nedohledává původní zdroj faktu** (agregátor → tisková zpráva).
- **Nezobrazuje Search Suggestions** Google — samostatné téma podmínek
  Google, patří k ROADMAP #14 (klientský portál).
- **Nehledá zdroje za DeepSeek** a nepřiřazuje věty Perplexity ke
  zdrojům přes LLM (možné rozšíření, ne v1).
- **Neřeší retenci a mazání snímků** (otevřená otázka 4).
- **Nepřepisuje `citations` ani historické runy** (NFR-6).
