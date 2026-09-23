# SignalMap — Tasks: Tři noví AI provideři (Perplexity, DeepSeek, Grok)

## Status: ✅ Done — PR #20, merged 2026-09-23, release pending

## v1.0 | Září 2026
## Branch: feature/signalmap-new-providers
## Task ID prefix: NP

> Co je hotové a smergnuté viz index feature branchí v `docs/TASKS.md`.
> Tenhle dokument pokrývá přidání **tří dalších providerů**:
> Perplexity, DeepSeek a xAI Grok. Motivace: srovnávací benchmark proti
> peec.ai (klient Knauf) — Peec nabízí všechny tři a dnes je nemáme čím
> pokrýt.
>
> Není to jedna z pěti roadmap fází ze skillu `signalmap-conventions`.
> Je to čtvrté až šesté opakování už zavedeného vzoru „nový provider =
> nový adapter + řádky v `providers`/`ai_models`", který se od fáze 2
> nezměnil.

---

## ⚠️ Flag podle AI_INSTRUCTIONS.md §4 — potvrdit před NP-T1

Tahle práce **nezavádí žádnou novou tabulku ani sloupec.** Provideři a
modely jsou data, ne kód (§3), takže vše se vejde do `INSERT`ů v migraci —
stejně jako `0009` (Anthropic) a `0023` (OpenAI). Schema flag tedy
neplatí.

Platí ale jiný bod §4: **„nevymýšlej provider list nad rámec toho, co
popisuje `docs/REQUIREMENTS.md` / aktivní fáze."** FR-8 dodnes říká
„pouze Google Gemini ve fázi 1" s amendmentem z 2026-09-09, který
doplnil Anthropic. OpenAI se do textu nikdy nedopsal a tihle tři tam také
nejsou. **NP-T5 to napraví jedním souhrnným amendmentem** — ale samotné
rozhodnutí přidat tři providery je uživatelovo, ne odvoditelné z
dokumentu. Nezačínej NP-T1 bez výslovného potvrzení.

Druhá prerekvizita je mimo kód: **placené účty a API klíče** u
Perplexity, DeepSeeku a xAI. Bez nich se nedá ověřit ani tvar odpovědi,
natož napsat mapping. Viz NP-T1.

---

## Ověřeno proti dokumentaci (2026-09-22)

Všechno níž pochází z oficiální dokumentace providerů stažené 2026-09-22,
ne z paměti modelu. Co z ní **neplyne** — přesné tvary JSON odpovědí — se
ověřuje reálným voláním v NP-T1, ne dalším čtením dokumentace. Precedent:
komentář u `TOKEN_USAGE_SHAPES` v `app/services/cost.py`, kde se tvary
zjišťovaly dotazem nad reálně uloženými `raw_responses`, protože
dokumentace se v jednom bodě mýlila.

| Provider | Web search | Citace v odpovědi | Tvar API |
|---|---|---|---|
| **Perplexity** | ano, `web_search` tool s `filters` | položka v `output` s `type: "search_results"` (url, title, date, snippet, source) | Responses API na ~~`/v1/agent`~~ `/v1/responses` (viz korekce níž) |
| **DeepSeek** | **žádný** | **žádné** | OpenAI i Anthropic kompatibilní, `https://api.deepseek.com` |
| **xAI Grok** | ano, `web_search` tool | ~~`response.citations`~~ | OpenAI Responses API, `base_url=https://api.x.ai/v1` |

> **Korekce (NP-T1, 2026-09-23):** řádek „Citace v odpovědi" pro xAI
> Grok byl špatně — platí pro Perplexity a DeepSeek, u Groku ne. Reálné
> volání ukázalo `output[].content[].annotations` s `type:
> "url_citation"`, stejný tvar jako u OpenAI, ne ploché pole
> `response.citations`. Viz sekce „Ověřené tvary odpovědí (NP-T1)" níž
> pro detail a dopad na design decision 4.

---

## Design decisions

**1. Perplexity se staví proti Agent API, ne proti Sonaru.**
Dokumentace Perplexity uvádí: „Sonar Chat Completions is now Agent API.
Sonar will be supported until September 27, 2026." Stavět adapter proti
API, které končí, nemá smysl. Agent API volá `responses.create()`,
prompt jde do `input`, system prompt do `instructions`, search
se zapíná `tools=[{"type": "web_search", "filters": {...}}]`, text se čte z
`output_text`. To je **tvarově totéž, co už umí `app/adapters/openai.py`** —
Perplexity adapter je varianta existujícího, ne stavba od nuly.

> **Korekce (NP-T1, 2026-09-23):** cesta `/v1/agent` z dokumentace se
> jako reálný request path nepotvrdila. Skutečný endpoint je
> `POST {base_url}/responses` s `base_url="https://api.perplexity.ai/v1"`
> — SDK sám skládá `/responses` za `base_url`, takže bez `/v1` v
> `base_url` dá Perplexity 404. `POST /v1/agent/responses` vrací 405.
> Detail v sekci „Ověřené tvary odpovědí (NP-T1)".

**2. Pořadí: Perplexity → DeepSeek → Grok.**
Rozhodnuto uživatelem 2026-09-23. Perplexity jde první, protože má pro
benchmark proti peec.ai největší hodnotu — Peec ji nabízí na obou
úrovních (API i UI) a je to vyhledávací produkt, tedy přesně ten typ
zdroje, který Knaufa zajímá.

Zapsáno i to, co se tím vědomě přijímá, ať to za dva měsíce nevypadá jako
nedopatření: původní návrh stavěl Grok první, protože je nejlevnější
(stejná Responses API plocha, jen jiné mapování citací) a ověřil by
ohnutí vzoru na třetí variantu téhož API **dřív**, než se sáhne na
Perplexity, kde je API čerstvě zmigrované a překvapení pravděpodobnější.
Tímhle pořadím se ten levný test ztrácí: vzor se ohýbá rovnou na tom
nejnovějším a nejméně prošlapaném API. Praktický dopad — u NP-T2 počítej
s větší rezervou a ber vážně krok „ověř na reálné odpovědi" (design
decisions 4 a 5); co u Groku projde na první dobrou, tady projít nemusí.

**3. DeepSeek nemá web search — a to je rozhodnutí, ne detail.**
Jeho API nenabízí žádný search tool ani parametr; grounding má jen
konzumentská aplikace. Každý run tedy skončí s `has_citations = False`
natrvalo. To není dočasná mezera jako u chybějících `source_passage` —
je to strukturální vlastnost, která vyřazuje DeepSeek ze všeho, co v
SignalMapu stojí na citacích (FR-12, FR-13, signal map, analytické
skilly). DeepSeek měří **jinou věc**: co model o klientovi řekne z
vlastních vah, bez vyhledávání. To je legitimní kontrolní vzorek, ale
patří do jiné kategorie dat než zbylých pět providerů. **NP-T3 je proto
podmíněný a potvrzuje se zvlášť** (stejný režim jako volitelné SQ-T5).

Důsledek pořadí z design decision 2: tenhle podmíněný task teď sedí
uprostřed řady, ne na konci. **Nepotvrzený NP-T3 proto nesmí blokovat
NP-T4** — Grok na DeepSeeku nijak nezávisí. Když se rozhodnutí o
DeepSeeku protáhne, přeskoč ho a pokračuj Grokem; vrátit se k němu jde
kdykoli později.

**4. `cited_answer_span` u Groku a Perplexity zůstane prázdný.**
Oba vracejí ploché seznamy zdrojů bez offsetů do textu odpovědi
(`response.citations` u Groku, `search_results` u Perplexity) — na rozdíl
od OpenAI, jehož `url_citation` anotace nesou `start_index`/`end_index`.
`AdapterCitation` to už dnes unese (pole jsou nullable), je to stejná
situace jako `source_passage` u Gemini a OpenAI: vlastnost API, ne defekt.
Zapiš to do docstringu adapteru, ať to nikdo nezkouší „opravit".

Výjimka k ověření v NP-T2: Perplexity podle dokumentace vkládá do
`output_text` číslované odkazy `[1]`, takže span by se teoreticky odvodit
dal. **Neimplementuj to na první dobrou** — nejdřív se podívej na reálnou
odpověď, jestli je číslování spolehlivě spárované s pořadím v
`search_results`. Pokud ne, nech prázdné.

> **Korekce (NP-T1, 2026-09-23):** mechanismus popsaný výš pro Grok
> („ploché seznamy bez offsetů, `response.citations`") je špatně —
> Grok vrací `url_citation` anotace stejně jako OpenAI, ale s
> `start_index`/`end_index` vždy `0`/`0` a `title` vždy rovným `url`.
> **Závěr zůstává stejný** (span prázdný), ale z jiného důvodu — nulové,
> ne chybějící offsety. Adapter by měl dokumentovat tohle, ne kopírovat
> zdůvodnění z tohohle odstavce. Perplexity naopak dopadla přesně podle
> plánu: `output_text.annotations` bylo prázdné pole, žádné `[1]`
> odkazy se v textu neobjevily, span zůstává `None`. Detail viz sekce
> „Ověřené tvary odpovědí (NP-T1)".

**5. Design decision 9 v `docs/TASKS_SEARCH_QUERIES.md` je překonané.**
Ten dokument zaznamenal (2026-09-10), že Perplexity texty vyhledávacích
dotazů vůbec nevrací a `search_queries` u něj budou „trvale prázdné, ne
dočasně". **To platilo pro Sonar.** Agent API v poli `output` vrací podle
dokumentace „every step the model took — the searches it ran and their
results". NP-T5 tu poznámku opraví. Do té doby ji ber jako neplatnou.

**6. Cenu počítáme z tokenů, i když ji Perplexity reportuje sama.**
Perplexity vrací v `usage` objekt `cost` s rozpadem na input/output/
reasoning/request. Nepoužívej ho jako zdroj pravdy — spočítej cenu stejně
jako u všech ostatních, přes `ai_model_price_components` a
`app/services/cost.py`, ať je napříč providery jedna metodika. Reportovaná
hodnota zůstane v `raw_payload` (FR-10) a je z ní použitelná kontrola:
když se naše čísla a jejich rozcházejí o víc než pár procent, máme někde
špatně namapovaný token shape.

**7. Klíče patří do `app/config.py` i do obou compose služeb.**
`docker-compose.yaml` definuje env proměnné zvlášť pro `app` a zvlášť pro
`worker`. Přidat klíč jen do jedné znamená, že manuální trigger funguje a
scheduler tiše padá (nebo naopak) — chyba, která se projeví až za den.

**8. Žádný hardcoded seznam providerů v kódu.**
Registry `ADAPTERS` v `app/adapters/__init__.py` je jediné místo, kde se
provider kód objeví v kódu. Vše ostatní (dropdowny, `/providers`,
`/ai-models`) čte z databáze. Nepřidávej podmínky typu
`if provider_code == "grok"` do routerů ani šablon.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| NP-T1 | Prerekvizity: klíče, config, compose + ověření tvaru odpovědí | ✅ config/compose hotové; všichni tři provideři ověřeni reálným voláním |
| NP-T2 | Perplexity (Agent API): adapter, migrace, ceny, testy | ✅ ověřeno reálným runem, uživatel potvrdil |
| NP-T3 (podmíněný) | DeepSeek: adapter + zacházení s runem bez citací | ✅ potvrzeno uživatelem před startem i po dokončení |
| NP-T4 | Grok (xAI): adapter, migrace, ceny, testy | ✅ ověřeno reálným runem, uživatel potvrdil |
| NP-T5 | Docs: REQUIREMENTS amendment, TASKS.md, oprava SQ design decision 9 | ✅ |

Pořadí podle design decision 2. NP-T2, NP-T3 i NP-T4 potřebují klíče a
ověřené tvary z NP-T1 — to je jediná tvrdá závislost.

**NP-T2 je referenční implementace** téhle série: rozepisuje celý sdílený
checklist (registry, cost shape, migrace, ceny, system_instruction
šablona, testy), na který se NP-T3 a NP-T4 už jen odkazují. Když se bude
měnit pořadí znovu, přesuň tenhle rozpis s prvním providerem v řadě.

NP-T3 je **podmíněný a potvrzuje se zvlášť** (design decision 3) a
**neblokuje NP-T4** — když se rozhodnutí o DeepSeeku protáhne, přeskoč ho.
NP-T5 se dělá až nakonec, aby amendment popisoval, co reálně vzniklo.

---

## NP-T1 — Prerekvizity a ověření tvaru odpovědí

**Target:** `app/config.py`, `.env.example`, `docker-compose.yaml`

Prerekvizita mimo kód: placené účty a API klíče u xAI, Perplexity a
DeepSeeku. Bez nich tenhle task nelze dokončit — nepokračuj a řekni to.

**Stav klíčů (2026-09-23):** Všichni tři provideři mají klíč v `.env`
a byli probnuti níž (xAI klíč přibyl a byl doprobnut dodatečně týž
den) — NP-T2, NP-T3 i NP-T4 mají tedy ověřený tvar odpovědi k dispozici.

### Ověřené tvary odpovědí (NP-T1)

Ověřeno reálným voláním 2026-09-23 (probe skript spuštěný jednorázově
přes `docker compose exec app python .probe_*.py`, žádný `tools/local/
probe_model.py` v repu neexistoval). Jeden `response.model_dump(mode=
"json")`/raw JSON na providera, výsledky nejsou uložené v repu (obsahují
skutečný text odpovědi) — kdokoli chce reprodukovat, spustí probe znovu.

#### DeepSeek

- **API tvar:** OpenAI Chat Completions (`chat.completions.create()`),
  `base_url="https://api.deepseek.com"`, **ne** Responses API.
- **Modely (z `client.models.list()`, ne z dokumentace):** `deepseek-flash`,
  `deepseek-v4-pro`. Starší `deepseek-v4-flash` z dokumentace se
  v `models.list()` vůbec neobjevil — potvrzuje se deprecated.
- **Web search:** žádný. `choices[0].message` nemá `tool_calls` a
  `annotations` je `null`. Potvrzuje design decision 3 — strukturální
  vlastnost, ne dočasná mezera.
- **Citace:** žádné pole v odpovědi k mapování neexistuje.
  `_map_citations()` bude vždy vracet `([], False)`.
- **Search queries:** žádné pole. `_map_search_queries()` bude vždy `[]`.
- **Token usage** (`usage` klíč):
  ```
  usage.prompt_tokens              int  — CELKEM, cache tokeny už zahrnuty
  usage.completion_tokens          int
  usage.total_tokens                int  — prompt_tokens + completion_tokens
  usage.prompt_tokens_details.cached_tokens        int (byl 0 v testu)
  usage.prompt_cache_hit_tokens     int — DUPLICITNÍ s cached_tokens výše (stejná hodnota, 0)
  usage.prompt_cache_miss_tokens    int — prompt_tokens - prompt_cache_hit_tokens
  usage.completion_tokens_details.reasoning_tokens int (nenulové i bez
    explicitního reasoning modelu — DeepSeek reasonuje interně a účtuje to
    do completion_tokens; `message.reasoning_content` nese ten text)
  ```
  **Pro NP-T3 (pokud se potvrdí):** `prompt_tokens` už obsahuje cache
  tokeny (je to součet hit+miss, ne jen miss) — `input_key` v
  `TokenUsageShape` proto musí mapovat na `prompt_tokens` přímo, ne na
  `prompt_cache_miss_tokens`, jinak se cachovaný provoz naúčtuje 0×
  místo 1× (opačná chyba, než na jakou varuje design decision 6, ale
  stejná rodina bugu).

#### Perplexity (Agent API)

- **API tvar:** Responses API tvar (`responses.create()`), ale **base_url
  musí být `https://api.perplexity.ai/v1`**, ne `https://api.perplexity.ai`
  — SDK skládá `{base_url}/responses`, takže bez `/v1` v base_url dá
  Perplexity 404. Cesta z dokumentace „`/v1/agent`" se jako reálný
  request path nepotvrdila; `POST /v1/agent/responses` vrací 405,
  `POST /v1/responses` je ten správný endpoint. Zaznamenat do docstringu
  adapteru, ať se `/v1/agent` nikdo nesnaží použít doslova.
- **Model/preset:** `model` (ne `preset`, jak spekulovala NP-T2 §2) —
  string ve tvaru `{vendor}/{model}`, např. `perplexity/sonar`,
  `xai/grok-4.7`, `anthropic/claude-opus-5-5`. `client.models.list()`
  vrací celý katalog přes vendory — Perplexity Agent API je router přes
  víc modelů, ne jen přes vlastní Sonar rodinu. **Design decision pro
  NP-T2:** `ai_models.model_name` = ten plný `vendor/model` string
  (`perplexity/sonar` pro výchozí), protože přesně to jde do `model`
  pole requestu.
- **Web search:** `tools=[{"type": "web_search"}]` funguje beze změny
  oproti tomu, co dělá `app/adapters/openai.py`.
- **Citace:** **žádná položka `type: "message"` s `annotations`
  obsahujícím `url_citation`** — jiný tvar než OpenAI/Grok. Citace jsou
  samostatná položka v `output` s `type: "search_results"`:
  ```
  output[i].type == "search_results"
  output[i].queries   list[str]  — vyhledávací dotazy pro tenhle krok
  output[i].results   list[{
    id: int, url: str, title: str, source: "web",
    snippet: str, date: str|None, last_updated: str,
  }]
  ```
  `snippet` je v testu delší, strukturovaný text s `...` odděleným
  více fragmenty stránky (ne jedna citovaná pasáž) — **potvrzuje
  podezření z NP-T2 bodu 3**: nejde o `source_passage` v tom smyslu,
  jak ho má Anthropic; nechat `source_passage = None`.
  `citation_position` = pořadí v `results`.
- **Search queries:** `output[i].queries` (stejná položka jako citace
  výše, ne samostatný typ kroku) — potvrzuje design decision 5, design
  decision 9 v `TASKS_SEARCH_QUERIES.md` je pro Agent API skutečně
  překonané.
- **Span z `[1]` odkazů (design decision 4, výjimka k ověření):**
  **neimplementovat.** Textová zpráva (`output[j].type == "message"`,
  `content[0].type == "output_text"`) měla `annotations: []` — prázdné,
  žádné číslované odkazy v `text` ani offsety. Span zůstává `None` podle
  základního pravidla design decision 4, výjimka se nepotvrdila.
- **Token usage** (`usage` klíč, celý jiný tvar než OpenAI/DeepSeek):
  ```
  usage.input_tokens                              int
  usage.output_tokens                             int
  usage.total_tokens                               int
  usage.input_tokens_details.cache_creation_input_tokens  int
  usage.input_tokens_details.cache_read_input_tokens      int
  usage.input_tokens_details.cached_tokens                int
  usage.output_tokens_details.reasoning_tokens             int
  usage.cost.input_cost / output_cost / cache_creation_cost /
       tool_calls_cost / tool_calls_cost_details.search_web / total_cost
       — Perplexity vlastní přepočet v USD (design decision 6: jen pro
         křížovou kontrolu, ne zdroj pravdy)
  ```
  V testu byl `input_tokens` (5272) výrazně vyšší než `output_tokens`
  (97) kvůli `cache_creation_input_tokens` (4725) — potvrzuje, že
  `input_tokens` **zahrnuje** cache tokeny stejně jako DeepSeekův
  `prompt_tokens`; `TokenUsageShape.input_key` pro Perplexity mapuje na
  `usage.input_tokens` přímo.

#### xAI Grok

Ověřeno reálným voláním 2026-09-23 (klíč doplněn do `.env` uživatelem
mezitím). **Zjištění se v jednom bodě rozchází s design decision 1 a 4
tohohle dokumentu** — obě vycházely z dokumentace, ne z probu, a
dokumentace se mýlila. Zapsáno tady jako oprava, ne mlčky přepsáno.

- **API tvar:** Responses API (`responses.create()`),
  `base_url="https://api.x.ai/v1"` — tohle se potvrdilo.
- **Modely:** `client.models.list()` vrátil `grok-4.7` jako existující
  (spolu s `grok-4.3`/`4.5`/`4.6` a několika `grok-4.20-*` a
  `grok-imagine-*` variantami mimo scope) — **potvrzuje `grok-4.7`** nad
  `grok-4.6` z design decision 8 v NP-T4, teď i přímo proti `api.x.ai`,
  ne jen zprostředkovaně přes Perplexity.
- **Web search:** `tools=[{"type": "web_search"}]` funguje, `output`
  obsahuje `web_search_call` položky s `action.query` a
  `action.sources` (holé URL, bez title/snippet) — pro
  `_map_search_queries()` použitelné (`action.query`), pro citace ne
  (viz níž, skutečné citace jsou jinde).
- **⚠️ OPRAVA design decision 1 a 4 — citace NEJSOU v `response.citations`.**
  Žádné pole `citations` na top úrovni odpovědi neexistuje
  (`"citations" in data` → `False`). Grok používá **stejný tvar jako
  OpenAI** (`app/adapters/openai.py`): `output` obsahuje položku
  `type: "message"`, jejíž `content[0].annotations` nese
  `type: "url_citation"` záznamy s `url`, `title`, `start_index`,
  `end_index`. `_map_citations()` pro Grok tedy může být **skoro
  identická s OpenAI adapterem**, ne vlastní mapování nad plochým
  polem, jak NP-T4 bod 2 předpokládal.
- **Ale span opravdu nejde použít — z jiného důvodu, než tvrdila
  dokumentace:** všech 11 anotací v testu mělo `start_index == 0` a
  `end_index == 0` (ne chybějící pole, ale nulové/neplatné offsety) a
  `title` byl vždy identický s `url` (žádný skutečný název stránky).
  Výsledek pro NP-T4 je stejný, jaký předpokládala design decision 4
  (`cited_answer_span`, `answer_span_start`, `answer_span_end`,
  `source_passage` zůstávají `None`) — ale **z jiného mechanismu**:
  není to "plochý seznam bez offsetů", je to anotační tvar s offsety,
  které jsou vždy 0/0 a tedy nepoužitelné. Docstring adapteru by měl
  psát pravdu (nulové offsety), ne kopírovat zdůvodnění z OpenAI/Anthropic
  rozdílu, které sem nesedí.
- **`source_domain`** lze odvodit z `url` přes `extract_domain` stejně
  jako u OpenAI. `citation_position` = pořadí v `content[0].annotations`.
- **Token usage** (`usage` klíč, vlastní tvar, ani OpenAI ani
  Perplexity):
  ```
  usage.input_tokens                           int
  usage.output_tokens                           int
  usage.total_tokens                             int
  usage.input_tokens_details.cached_tokens       int
  usage.output_tokens_details.reasoning_tokens    int — nenulové (Grok
    reasonuje interně přes samostatné `type: "reasoning"` output
    položky, podobně jako DeepSeek)
  usage.num_sources_used                          int — byl 0 v testu
    i přes 11 citací v odpovědi; nepoužívat jako proxy za has_citations
  usage.num_server_side_tools_used                int
  usage.server_side_tool_usage_details.web_search_calls  int
  usage.cost_in_usd_ticks                         int — xAI vlastní
    jednotka (desetinné "tickety", ne USD přímo); nepoužívat jako zdroj
    pravdy (design decision 6), navíc by potřebovalo zjistit přepočet
  ```
  `input_tokens` (12001) zahrnoval `cached_tokens` (2304) v součtu —
  stejný vzorec jako u DeepSeek/Perplexity výš; `TokenUsageShape.input_key`
  mapuje na `usage.input_tokens` přímo.
- **`market_country` → geo filtry `web_search` toolu:** v tomhle testu
  nebyl posílán žádný filtr, takže se to neověřilo. Nechat jako otevřený
  bod pro NP-T4 bod 4 — zkusit `tools=[{"type": "web_search",
  "user_location": {...}}]` po vzoru OpenAI/Anthropic a ověřit, jestli
  xAI parametr přijme, než se do adapteru napíše cokoliv o geo
  targetingu.

1. `app/config.py` — přidej `xai_api_key: str = ""`,
   `perplexity_api_key: str = ""`, `deepseek_api_key: str = ""` (stejný
   vzor jako stávající `openai_api_key`).
2. `.env.example` — zdokumentuj tři nové klíče. Skutečné hodnoty **jen**
   do gitignorovaného `.env` (§4).
3. `docker-compose.yaml` — `XAI_API_KEY`, `PERPLEXITY_API_KEY`,
   `DEEPSEEK_API_KEY` do služby `app` **i** `worker` (design decision 7).
4. Ověř, že jsou modely vůbec volatelné, jedním probe voláním na
   providera. Použij `tools/local/probe_model.py`, pokud už existuje;
   jinak jednorázově přes `docker compose exec -T app python -`.
5. **Zaznamenej reálný tvar odpovědi.** Pro každého providera ulož jeden
   skutečný `response.model_dump(mode="json")` (u Groku a Perplexity
   **se zapnutým** `web_search` toolem, jinak nebudou citace) a do tohoto
   dokumentu dopiš sekci „Ověřené tvary odpovědí (NP-T1)" s tím, jak se
   jmenují pole pro citace, vyhledávací dotazy a token usage. Tohle je
   vstup pro NP-T2 až NP-T4 — mapping se nepíše z dokumentace.

Po dokončení:
1. `docker compose up -d --build app worker` — obě služby nastartují.
2. `docker compose exec app python -c "from app.config import get_settings; s=get_settings(); print(bool(s.xai_api_key), bool(s.perplexity_api_key), bool(s.deepseek_api_key))"` → `True True True`.
3. Sekce s ověřenými tvary dopsaná v tomhle dokumentu.
4. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
chore(infra): add xAI, Perplexity and DeepSeek API keys to config and compose
```

---

## NP-T2 — Perplexity (Agent API)

**Target:** `app/adapters/perplexity.py`, `app/adapters/__init__.py`,
`app/services/cost.py`, nová `alembic/versions/00XX_perplexity_provider_and_models.py`,
`tests/test_adapters.py`

Prerekvizita: NP-T1 hotový, tvar odpovědi ověřený.

**Tohle je referenční task série** — rozepisuje celý sdílený checklist,
na který se NP-T3 a NP-T4 pak odkazují. Zároveň je to ten nejméně
prošlapaný provider z trojice (Agent API je čerstvě zmigrované), takže
počítej s větší rezervou než u zbylých dvou (design decision 2).

1. `app/adapters/perplexity.py` — `PerplexityAdapter` proti **Agent API**
   (`base_url="https://api.perplexity.ai/v1"`, `responses.create()` →
   reálně `POST /v1/responses`, **ne** `/v1/agent` — viz korekce u design
   decision 1 a sekce „Ověřené tvary odpovědí (NP-T1)"), ne proti Sonaru
   (Sonar končí 27. 9. 2026). Do docstringu napiš proč, ať to někdo
   „nezjednoduší" zpátky na chat completions. Formát docstringu podle
   `app/adapters/openai.py`: co bylo ověřené, kdy a proti čemu.
2. Request: `input` místo `messages`, `instructions` pro system prompt,
   `model` (**ne `preset`** — NP-T1 to ověřilo a vyvrátilo). Hodnota je
   string `{vendor}/{model}`, např. `perplexity/sonar`, `xai/grok-4.7` —
   Agent API je router přes víc modelů/vendorů, ne jen přes vlastní
   Sonar rodinu. `ai_models.model_name` = ten plný `vendor/model` string
   (`perplexity/sonar` pro výchozí), protože přesně to jde do `model`
   pole requestu (design decision zapsaná v NP-T1 sekci).
3. `_map_citations()` — z položky `output` s `type: "search_results"`:
   `url` → `source_url`, `title` → `source_title`, doména přes
   `extract_domain`. `citation_position` = pořadí v seznamu.
   `has_citations` explicitně `False` u prázdné odpovědi, ne jen prázdný
   list (FR-13). Pole `snippet` je kandidát na `source_passage` — ověř na
   reálných datech, jestli je to citovaná pasáž ze stránky, nebo jen
   náhled výsledku; při pochybnosti nech `None` a zdůvodni v docstringu.
4. Span z číslovaných `[1]` odkazů **neimplementuj** — NP-T1 to už
   ověřilo na reálné odpovědi: `output_text.annotations` bylo prázdné
   pole, žádné číslované odkazy se v textu neobjevily. `cited_answer_span`,
   `answer_span_start` a `answer_span_end` zůstávají prázdné (design
   decision 4).
5. `_map_search_queries()` — z kroků v poli `output`. Tohle je místo, kde
   je design decision 9 v `docs/TASKS_SEARCH_QUERIES.md` překonané (design
   decision 5 tady); opravu toho dokumentu ale **nedělej tady**, patří do
   NP-T5.
6. `app/adapters/__init__.py` — jeden řádek do `ADAPTERS`
   (`"perplexity"`). Žádné další místo v kódu kód providera znát nesmí
   (design decision 8).
7. `app/services/cost.py` — nový `TokenUsageShape` podle reálného
   payloadu z NP-T1, ne podle dokumentace. Rozhodni a **okomentuj**,
   jestli `input_key` už obsahuje cache tokeny; když to spleteš,
   cachovaný provoz se naúčtuje dvakrát (design decision 13 v tom souboru).
8. Migrace — `INSERT` do `providers` (`code='perplexity'`) a `ai_models`
   podle toho, co prošlo probem v NP-T1. Ceny do
   `ai_model_price_components` s poznámkou, proti čemu a kdy byly ověřené.
   Plus výchozí `system_instruction` šablona pro nového providera.
   Nejdřív `alembic heads`, ať `down_revision` sedí.
9. `tests/test_adapters.py` — mapping testy nad uloženým payloadem z
   NP-T1: citace se namapují, prázdná odpověď dá `has_citations=False`.

Po dokončení:
1. `docker compose exec app alembic upgrade head` — bez chyby.
2. Reálný run přes `/prompts` na Perplexity model → detail runu ukáže
   odpověď i citace.
3. `/ops` ukáže nenulovou cenu, **a** porovnej ji s hodnotou `usage.cost`
   z `raw_payload` — rozdíl nad pár procent znamená špatný token shape
   (design decision 6).
4. `docker compose exec app pytest tests/test_adapters.py` — zelené.
5. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
feat(adapters): add Perplexity provider via Agent API with search results
```

---

## NP-T3 (podmíněný) — DeepSeek

**⚠️ Nezačínej bez výslovného potvrzení** (design decision 3). A pokud se
potvrzení protáhne, **přeskoč tenhle task a pokračuj NP-T4** — Grok na
DeepSeeku nijak nezávisí.

**Target:** `app/adapters/deepseek.py`, `app/adapters/__init__.py`,
`app/services/cost.py`, nová migrace, `tests/test_adapters.py`, plus
rozhodnutí o zobrazení

Kód sám je z celé trojice nejjednodušší: DeepSeek je OpenAI-kompatibilní,
takže adapter je `openai.OpenAI(base_url="https://api.deepseek.com")` a
`chat.completions.create()` bez jakýchkoli toolů. Modely: `deepseek-v4-pro`,
`deepseek-flash` (ověř probem; starší `deepseek-v4-flash` je deprecated).

Skutečná práce je jinde a musí se rozhodnout **před** psaním kódu:

1. **Co uvidí uživatel na detailu runu**, kde nejsou žádné citace? Dnešní
   šablona počítá s tím, že jejich absence je výjimka. U DeepSeeku je to
   pravidlo a potřebuje to vlastní vysvětlující text (DE/EN přes `t()`),
   ne prázdnou sekci.
2. **Co s ním udělá dashboard a analytické skilly**, které počítají
   citace a domény? Provider bez citací zkreslí každou agregaci, do které
   spadne. Návrh: vyloučit ho ze všeho, co agreguje zdroje, a nechat jen
   v tom, co pracuje s textem odpovědi.
3. **Teprve pak** adapter a sdílený checklist — body 6 až 9 z NP-T2
   (registry `"deepseek"`, cost shape, migrace s cenami a
   `system_instruction` šablonou, testy).

`_map_citations()` vrací vždy prázdný seznam a `has_citations=False`.
`_map_search_queries()` vždy prázdný seznam. Obojí **s vysvětlením v
docstringu**, že to je strukturální vlastnost API, ne nedodělek.

Po dokončení:
1. `docker compose exec app alembic upgrade head` — bez chyby.
2. Reálný run → detail runu ukáže odpověď a srozumitelné vysvětlení,
   proč nejsou zdroje.
3. Dashboard s DeepSeek runy nezkresluje doménové agregace.
4. `docker compose exec app pytest` — zelené.
5. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
feat(adapters): add DeepSeek provider for ungrounded baseline answers
```

---

## NP-T4 — Grok (xAI)

**Target:** `app/adapters/grok.py`, `app/adapters/__init__.py`,
`app/services/cost.py`, nová `alembic/versions/00XX_grok_provider_and_models.py`,
`tests/test_adapters.py`

Prerekvizita: NP-T1 hotový, tvar odpovědi ověřený. NP-T3 **není**
prerekvizita — když je DeepSeek nepotvrzený, jde se rovnou sem.

Z celé trojice nejlevnější task: stejná Responses API plocha jako NP-T2,
liší se hlavně mapování citací.

1. `app/adapters/grok.py` — `GrokAdapter` implementující
   `ProviderAdapter`. Jde přes OpenAI SDK s
   `base_url="https://api.x.ai/v1"` a `responses.create()`, protože xAI
   tuhle plochu podporuje; **nepiš vlastního HTTP klienta.**
2. `_map_citations()` — **⚠️ NE z `response.citations`, to pole
   neexistuje.** NP-T1 to ověřilo reálným voláním a opravilo (viz
   „Ověřené tvary odpovědí (NP-T1)" → xAI Grok): zdroj je
   `output[].content[].annotations` s `type: "url_citation"`, stejný
   tvar jako `app/adapters/openai.py`. `cited_answer_span`,
   `answer_span_start`, `answer_span_end` a `source_passage` přesto
   zůstávají `None` — ne protože pole chybí, ale protože `start_index`/
   `end_index` byly v testu vždy `0`/`0` a `title` vždy rovné `url`.
   `citation_position` = pořadí v `annotations`.
   `has_citations` explicitně `False` při prázdném seznamu (FR-13).
3. `_map_search_queries()` — podle toho, co ukázalo NP-T1. Pokud xAI texty
   dotazů nevrací, vrať prázdný seznam a **zdůvodni to v docstringu**.
4. `market_country` → doménové/geo filtry `web_search` toolu, pokud je
   xAI podporuje — **NP-T1 tohle neověřilo** (probe žádný geo filtr
   neposílal), zůstává otevřené pro tenhle task. Zkus
   `tools=[{"type": "web_search", "user_location": {...}}]` po vzoru
   OpenAI/Anthropic. Pokud xAI parametr nepřijme, zdokumentuj to jako
   rozdíl proti Anthropicu a OpenAI, kde je geo targeting reálný.
5. Sdílený checklist — body 6 až 9 z NP-T2 (registry `"xai"`, cost shape,
   migrace, testy). Model `grok-4.7`; pozor, Philipův seznam uvádí
   `grok-4.6`, dokumentace `grok-4.7` — použij to, co prošlo probem.

Po dokončení:
1. `docker compose exec app alembic upgrade head` — bez chyby.
2. Reálný run přes `/prompts` na Grok model → detail runu ukáže odpověď
   i citace.
3. `/ops` ukáže u toho runu nenulovou cenu (ověří token shape).
4. `docker compose exec app pytest tests/test_adapters.py` — zelené.
5. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
feat(adapters): add xAI Grok provider with web_search citations
```

---
## NP-T5 — Docs

**Target:** `docs/REQUIREMENTS.md`, `docs/TASKS.md`,
`docs/TASKS_SEARCH_QUERIES.md`

Až jsou předchozí tasky hotové a ověřené uživatelem (§7 — dokumentace se
aktualizuje až po potvrzení, že věc funguje):

1. `docs/REQUIREMENTS.md` — amendment k FR-8 v tom stylu, jaký tam už je
   z 2026-09-09. Jeden souhrnný zápis, který doplní **všechny** providery
   přibyvší od fáze 1: OpenAI (nikdy se nedopsal), Grok, Perplexity a
   podle výsledku i DeepSeek. U DeepSeeku výslovně zmínit, že nemá
   grounding, takže jeho runy nikdy nenesou citace.
2. `docs/TASKS.md` — nová sekce odkazující na tuhle větev, stejně jako
   mají ostatní dokončené práce.
3. `docs/TASKS_SEARCH_QUERIES.md` — oprav design decision 9. Neškrtej ho;
   dopiš datovanou korekci, že zjištění platilo pro Sonar Chat Completions
   a Agent API se chová jinak (design decision 5 tady). Stejný styl jako
   v1.1 korekce na začátku toho dokumentu.

**Expected commit:**
```
docs(requirements): record Grok, Perplexity and DeepSeek providers
```

---

## Co tenhle dokument záměrně neřeší

- **Výběr modelů pro benchmark proti peec.ai.** To je obchodní
  rozhodnutí řešené s klientem, ne součást téhle práce.
- **Perplexity UI vs API.** Peec nabízí u Perplexity oboje; my stavíme
  jen API cestu. Rozdíl mezi API a nasazeným rozhraním popisuje NFR-9 a
  platí i tady.
- **Analytická vrstva nad novými providery.** Adaptery jen dodají data;
  co s nimi udělají analytické skilly, je samostatná práce.
