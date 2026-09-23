# SignalMap — Tasks: Tři noví AI provideři (Perplexity, DeepSeek, Grok)

## Status: ⏳ Not started — needs API keys and the §4 confirmation

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
| **Perplexity** | ano, `web_search` tool s `filters` | položka v `output` s `type: "search_results"` (url, title, date, snippet, source) | Responses API na `/v1/agent` |
| **DeepSeek** | **žádný** | **žádné** | OpenAI i Anthropic kompatibilní, `https://api.deepseek.com` |
| **xAI Grok** | ano, `web_search` tool | `response.citations` | OpenAI Responses API, `base_url=https://api.x.ai/v1` |

---

## Design decisions

**1. Perplexity se staví proti Agent API, ne proti Sonaru.**
Dokumentace Perplexity uvádí: „Sonar Chat Completions is now Agent API.
Sonar will be supported until September 27, 2026." Stavět adapter proti
API, které končí, nemá smysl. Agent API volá `responses.create()` na
`/v1/agent`, prompt jde do `input`, system prompt do `instructions`, search
se zapíná `tools=[{"type": "web_search", "filters": {...}}]`, text se čte z
`output_text`. To je **tvarově totéž, co už umí `app/adapters/openai.py`** —
Perplexity adapter je varianta existujícího, ne stavba od nuly.

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
| NP-T1 | Prerekvizity: klíče, config, compose + ověření tvaru odpovědí | ⏳ |
| NP-T2 | Perplexity (Agent API): adapter, migrace, ceny, testy | ⏳ |
| NP-T3 (podmíněný) | DeepSeek: adapter + zacházení s runem bez citací | ⏳ |
| NP-T4 | Grok (xAI): adapter, migrace, ceny, testy | ⏳ |
| NP-T5 | Docs: REQUIREMENTS amendment, TASKS.md, oprava SQ design decision 9 | ⏳ |

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
   (`/v1/agent`, `responses.create()`), ne proti Sonaru (design decision 1;
   Sonar končí 27. 9. 2026). Do docstringu napiš proč, ať to někdo
   „nezjednoduší" zpátky na chat completions. Formát docstringu podle
   `app/adapters/openai.py`: co bylo ověřené, kdy a proti čemu.
2. Request: `input` místo `messages`, `instructions` pro system prompt,
   `preset` místo `model` — ověř podle NP-T1, jak se preset mapuje na naše
   `ai_models.model_name`. Pokud Perplexity pracuje s presety a ne s
   modely, **zaznamenej to jako nové design decision** a zvol, co půjde do
   `model_name` (návrh: preset, protože to je to, co se reálně posílá).
3. `_map_citations()` — z položky `output` s `type: "search_results"`:
   `url` → `source_url`, `title` → `source_title`, doména přes
   `extract_domain`. `citation_position` = pořadí v seznamu.
   `has_citations` explicitně `False` u prázdné odpovědi, ne jen prázdný
   list (FR-13). Pole `snippet` je kandidát na `source_passage` — ověř na
   reálných datech, jestli je to citovaná pasáž ze stránky, nebo jen
   náhled výsledku; při pochybnosti nech `None` a zdůvodni v docstringu.
4. Span z číslovaných `[1]` odkazů v `output_text` implementuj **jen
   tehdy**, když se na reálné odpovědi ukáže spolehlivé párování s pořadím
   v `search_results`. Jinak nech `cited_answer_span`, `answer_span_start`
   a `answer_span_end` prázdné (design decision 4).
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
2. `_map_citations()` — z `response.citations`. Pozor, je to ploché pole,
   ne anotace s offsety: `cited_answer_span`, `answer_span_start`,
   `answer_span_end` a `source_passage` zůstávají `None` (design decision
   4, napiš to do docstringu). `citation_position` = pořadí v poli.
   `has_citations` explicitně `False` při prázdném seznamu (FR-13).
3. `_map_search_queries()` — podle toho, co ukázalo NP-T1. Pokud xAI texty
   dotazů nevrací, vrať prázdný seznam a **zdůvodni to v docstringu**.
4. `market_country` → doménové/geo filtry `web_search` toolu, pokud je
   xAI podporuje (ověř v NP-T1). Pokud ne, zdokumentuj to jako rozdíl
   proti Anthropicu a OpenAI, kde je geo targeting reálný.
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
