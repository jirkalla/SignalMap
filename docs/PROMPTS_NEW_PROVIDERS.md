# SignalMap — Claude Code Session Prompts: Tři noví AI provideři

## Status: ✅ Done — PR #20, merged 2026-09-23, release pending

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. NEZAKLÁDEJ větev, dokud není výslovně potvrzené rozhodnutí přidat
##    tyhle tři providery — viz "⚠️ Flag podle AI_INSTRUCTIONS.md §4" v
##    docs/TASKS_NEW_PROVIDERS.md. Není to schema flag (žádná nová tabulka),
##    ale provider list nad rámec docs/REQUIREMENTS.md FR-8.
## 2. Prerekvizita mimo kód: placené účty a API klíče u Perplexity, xAI a
##    DeepSeeku. Bez nich nelze dokončit ani NP-1.
## 3. git checkout -b feature/signalmap-new-providers (z aktuálního master)
## 4. Prompty NP-1, NP-2, NP-4, NP-5 jsou povinné, POŘADÍ VYNUCENÉ.
##    NP-3 (DeepSeek) je PODMÍNĚNÝ — potvrdit zvlášť (design decision 3)
##    — a NEBLOKUJE NP-4: když se potvrzení protáhne, přeskoč ho a
##    pokračuj Grokem.
## 5. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuto větev.
## 6. Každý prompt musí skončit "appka nastartuje bez chyby" (+ specifická
##    kontrola daného promptu) než jdeš na další.
## 7. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 8. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_NEW_PROVIDERS.md přepni řádek daného task ID v
##       tabulce "Task Index" z ⏳ na ✅.
## 9. Nikdy nekombinuj dva prompty do jedné session.
## 10. Kompletní zdůvodnění vč. design decisions 1-8:
##     docs/TASKS_NEW_PROVIDERS.md — přečti si konkrétní task ID před psaním
##     kódu, ideálně celý soubor před NP-1.
## 11. KLÍČOVÉ: tvary JSON odpovědí se NEBEROU z dokumentace. NP-1 je
##     zaznamená z reálných volání a NP-2 až NP-4 z nich vycházejí. Stejná
##     disciplína jako u TOKEN_USAGE_SHAPES v app/services/cost.py, kde se
##     dokumentace v jednom bodě mýlila.
## 12. Až je větev hotová a smergnutá: NP-5 dopíše amendment do
##     docs/REQUIREMENTS.md a sekci do docs/TASKS.md.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-new-providers.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/REQUIREMENTS.md
3. docs/TASKS_NEW_PROVIDERS.md — CELÉ, hlavně sekci "⚠️ Flag podle
   AI_INSTRUCTIONS.md §4" a design decisions 1-8
4. app/adapters/openai.py — referenční implementace, ze které tahle
   práce vychází

KONTEXT: Fáze 1, fáze 2, OpenAI provider, Export, Search queries, Cost
components, Ops dashboard a Scheduler jsou hotové a smergnuté do master.
Tahle větev přidává tři další providery: Perplexity, DeepSeek a xAI Grok.
Motivace je srovnávací benchmark proti peec.ai pro klienta Knauf — Peec
všechny tři nabízí a my je nemáme čím pokrýt. Není to jedna z pěti
roadmap fází; je to čtvrté až šesté opakování zavedeného vzoru "nový
provider = nový adapter + řádky v providers/ai_models".

KRITICKÉ:
- Žádná nová tabulka ani sloupec. Provideři a modely jsou DATA, ne kód
  (AI_INSTRUCTIONS.md §3). Všechno jde přes INSERTy v migraci, stejně
  jako 0009 (Anthropic) a 0023 (OpenAI). Pokud se ti zdá, že potřebuješ
  nový sloupec, ZASTAV SE a zeptej se.
- Router NIKDY nevolá SDK providera přímo — vždy přes adapter (§4).
- Žádný hardcoded seznam providerů v kódu. Jediné místo, kde se kód
  providera objeví, je registry ADAPTERS v app/adapters/__init__.py.
  Nepřidávej podmínky typu `if provider_code == "grok"` do routerů ani
  šablon.
- Tvary JSON odpovědí neber z dokumentace. NP-1 je zaznamenal z reálných
  volání do docs/TASKS_NEW_PROVIDERS.md — vycházej z nich.
- Backend kód anglicky, UI texty přes t() v DE i EN (§3).
- git commit ani push NESPOUŠTĚJ. Zprávu navrhni a ukaž.
```

---
---

## NP-1 — Prerekvizity: klíče, config, compose + ověření tvaru odpovědí

```
Úkol NP-T1 z docs/TASKS_NEW_PROVIDERS.md.

Přidej konfiguraci pro tři nové providery a zaznamenej reálné tvary
jejich odpovědí. V tomhle promptu se NEPÍŠE žádný adapter.

1. app/config.py — xai_api_key, perplexity_api_key, deepseek_api_key
   (stejný vzor jako stávající openai_api_key).
2. .env.example — zdokumentuj tři nové klíče. Skutečné hodnoty patří
   jen do gitignorovaného .env.
3. docker-compose.yaml — XAI_API_KEY, PERPLEXITY_API_KEY,
   DEEPSEEK_API_KEY do služby `app` I do služby `worker`. Obě, ne jednu:
   když klíč chybí u workeru, scheduler tiše padá, zatímco manuální
   trigger funguje — chyba, která se projeví až za den (design decision 7).
4. Ověř jedním probe voláním na providera, že se dá vůbec dovolat:
   - xAI: openai SDK, base_url https://api.x.ai/v1, responses.create()
   - Perplexity: Agent API na /v1/agent, responses.create()
   - DeepSeek: openai SDK, base_url https://api.deepseek.com,
     chat.completions.create()
   Použij tools/local/probe_model.py, pokud existuje; jinak jednorázově
   přes `docker compose exec -T app python -`.
5. TOHLE JE HLAVNÍ VÝSTUP PROMPTU: pro každého providera ulož jeden
   skutečný response.model_dump(mode="json") a do
   docs/TASKS_NEW_PROVIDERS.md dopiš sekci "Ověřené tvary odpovědí
   (NP-T1)" s tím, jak se reálně jmenují pole pro:
   - citace / zdroje
   - texty vyhledávacích dotazů (pokud nějaké jsou)
   - token usage (přesné klíče, a jestli input už obsahuje cache tokeny)
   U Groku a Perplexity volej SE ZAPNUTÝM web_search toolem — bez něj
   žádné citace nepřijdou a záznam bude k ničemu.

Pokud nemáš API klíč k některému z providerů, NEPOKRAČUJ u něj a řekni
to — tvar odpovědi se nedá vymyslet.

Kontrola po dokončení:
1. docker compose up -d --build app worker — obě služby nastartují
2. docker compose exec app python -c "from app.config import get_settings; s=get_settings(); print(bool(s.xai_api_key), bool(s.perplexity_api_key), bool(s.deepseek_api_key))"
   → True True True
3. Sekce s ověřenými tvary je dopsaná v docs/TASKS_NEW_PROVIDERS.md

Na konci: implementation summary + navrhni commit message. Necommituj.
```

---

## NP-2 — Perplexity (Agent API)

```
Úkol NP-T2 z docs/TASKS_NEW_PROVIDERS.md.

Přidej providera Perplexity. Stavíš proti AGENT API, ne proti Sonaru —
Sonar Chat Completions končí 27. 9. 2026 (design decision 1). Do
docstringu napiš proč, ať to někdo "nezjednoduší" zpátky na chat
completions.

Tohle je referenční task série: rozepisuje celý sdílený checklist, na
který se NP-3 a NP-4 pak odkazují. Zároveň je to nejméně prošlapaný
provider z trojice (Agent API je čerstvě zmigrované) — ber vážně kroky
"ověř na reálné odpovědi" a nepiš mapping podle dokumentace.

1. app/adapters/perplexity.py — PerplexityAdapter proti /v1/agent,
   responses.create(). Formát docstringu podle app/adapters/openai.py:
   co bylo ověřené, kdy a proti čemu.
2. Request: input místo messages, instructions pro system prompt, preset
   místo model. Ověř podle NP-1, jak se preset mapuje na naše
   ai_models.model_name. Jestli Perplexity pracuje s presety a ne s
   modely, ZAZNAMENEJ to jako nové design decision v
   docs/TASKS_NEW_PROVIDERS.md a zvol, co půjde do model_name (návrh:
   preset, protože to je to, co se reálně posílá).
3. _map_citations() z položky output s type "search_results": url →
   source_url, title → source_title, doména přes extract_domain.
   citation_position = pořadí v seznamu. has_citations explicitně False
   u prázdné odpovědi, ne jen prázdný list (FR-13). Pole snippet je
   kandidát na source_passage — ověř na reálných datech, jestli je to
   citovaná pasáž ze stránky, nebo jen náhled výsledku. Při pochybnosti
   nech None a zdůvodni v docstringu.
4. Span z číslovaných [1] odkazů v output_text implementuj JEN tehdy,
   když se na reálné odpovědi ukáže spolehlivé párování s pořadím v
   search_results. Jinak nech prázdné (design decision 4).
5. _map_search_queries() z kroků v poli output. Tohle je místo, kde je
   design decision 9 v docs/TASKS_SEARCH_QUERIES.md překonané — to
   zjištění platilo pro Sonar. Opravu toho dokumentu ale NEDĚLEJ tady,
   patří do NP-5.
6. app/adapters/__init__.py — jeden řádek do ADAPTERS, kód "perplexity".
   Žádné další místo v kódu kód providera znát nesmí.
7. app/services/cost.py — nový TokenUsageShape podle reálného payloadu
   z NP-1. Rozhodni a OKOMENTUJ, jestli input_key už obsahuje cache
   tokeny; když to spleteš, cachovaný provoz se naúčtuje dvakrát
   (design decision 13 v tom souboru).
8. Nová alembic migrace — INSERT do providers (code='perplexity') a
   ai_models podle toho, co prošlo probem v NP-1. Ceny do
   ai_model_price_components s poznámkou, proti čemu a kdy byly ověřené.
   Plus výchozí system_instruction šablona pro nového providera.
   Nejdřív `alembic heads`, ať down_revision sedí.
9. tests/test_adapters.py — mapping testy nad uloženým payloadem z NP-1.

Kontrola po dokončení:
1. docker compose exec app alembic upgrade head — bez chyby
2. Reálný run přes /prompts → detail runu ukáže odpověď i citace
3. /ops ukáže nenulovou cenu, A porovnej ji s hodnotou usage.cost z
   raw_payload — rozdíl nad pár procent znamená špatný token shape
   (design decision 6)
4. docker compose exec app pytest tests/test_adapters.py — zelené
5. Appka nastartuje bez chyby

Na konci: implementation summary + navrhni commit message. Necommituj.
```

---

## NP-3 (PODMÍNĚNÝ) — DeepSeek

```
⚠️ NESPOUŠTĚJ tenhle prompt bez výslovného potvrzení. DeepSeek nemá web
search, takže jeho runy nikdy neponesou citace — to je rozhodnutí o tom,
jaká data vůbec sbíráme, ne technický detail (design decision 3 v
docs/TASKS_NEW_PROVIDERS.md).

A pokud se potvrzení protáhne: PŘESKOČ tenhle prompt a pokračuj NP-4.
Grok na DeepSeeku nijak nezávisí.

Úkol NP-T3.

Kód je z celé trojice nejjednodušší: DeepSeek je OpenAI-kompatibilní,
takže adapter je openai.OpenAI(base_url="https://api.deepseek.com") a
chat.completions.create() bez jakýchkoli toolů. Modely deepseek-v4-pro a
deepseek-flash (ověř probem; starší deepseek-v4-flash je deprecated).

Skutečná práce je jinde a řeší se PŘED psaním adapteru:

1. Co uvidí uživatel na detailu runu, kde nejsou žádné citace? Dnešní
   šablona počítá s tím, že jejich absence je výjimka. U DeepSeeku je to
   pravidlo a potřebuje vlastní vysvětlující text přes t() v DE i EN, ne
   prázdnou sekci. Navrhni řešení a nech si ho potvrdit, než budeš psát.
2. Co s ním udělá dashboard a analytické skilly, které počítají citace a
   domény? Provider bez citací zkreslí každou agregaci, do které spadne.
   Návrh k potvrzení: vyloučit ho ze všeho, co agreguje zdroje, a nechat
   jen tam, kde se pracuje s textem odpovědi.
3. Teprve pak adapter a sdílený checklist — body 6 až 9 z NP-2 (registry
   "deepseek", cost shape, migrace s cenami a system_instruction
   šablonou, testy).

_map_citations() vrací vždy prázdný seznam a has_citations=False.
_map_search_queries() vždy prázdný seznam. Obojí s vysvětlením v
docstringu, že jde o strukturální vlastnost API, ne o nedodělek — stejný
styl jako poznámka o source_passage v app/adapters/base.py.

Kontrola po dokončení:
1. docker compose exec app alembic upgrade head — bez chyby
2. Reálný run → detail runu ukáže odpověď a srozumitelné vysvětlení,
   proč nejsou zdroje
3. Dashboard s DeepSeek runy nezkresluje doménové agregace
4. docker compose exec app pytest — zelené
5. Appka nastartuje bez chyby

Na konci: implementation summary + navrhni commit message. Necommituj.
```

---

## NP-4 — Grok (xAI)

```
Úkol NP-T4 z docs/TASKS_NEW_PROVIDERS.md.

Přidej providera xAI Grok. Vycházej z ověřeného tvaru odpovědi
zaznamenaného v NP-1, ne z dokumentace. NP-3 (DeepSeek) NENÍ
prerekvizita — když je nepotvrzený, jdeš rovnou sem.

Z celé trojice nejlevnější task: stejná Responses API plocha jako NP-2,
liší se hlavně mapování citací.

1. app/adapters/grok.py — GrokAdapter implementující ProviderAdapter.
   Jde přes openai SDK s base_url="https://api.x.ai/v1" a
   responses.create(), protože xAI tuhle plochu podporuje. NEPIŠ vlastního
   HTTP klienta. Docstring podle vzoru app/adapters/openai.py.
2. _map_citations() z response.citations. POZOR: je to ploché pole, ne
   anotace s offsety — cited_answer_span, answer_span_start,
   answer_span_end a source_passage zůstávají None. Napiš to do
   docstringu i s důvodem, ať to nikdo nezkouší "opravit" (design
   decision 4). citation_position = pořadí v poli. has_citations
   explicitně False u prázdné odpovědi (FR-13).
3. _map_search_queries() podle toho, co ukázalo NP-1. Když xAI texty
   dotazů nevrací, vrať prázdný seznam a zdůvodni v docstringu.
4. market_country → geo/doménové filtry web_search toolu, pokud je xAI
   podporuje. Když ne, zdokumentuj to jako rozdíl proti Anthropicu a
   OpenAI, kde je geo targeting reálný.
5. Sdílený checklist — body 6 až 9 z NP-2 (registry "xai", cost shape,
   migrace, testy). Model grok-4.7; Philipův seznam uvádí grok-4.6,
   dokumentace 4.7 — použij to, co prošlo probem v NP-1.

Kontrola po dokončení:
1. docker compose exec app alembic upgrade head — bez chyby
2. Reálný run přes /prompts na Grok model → detail runu ukáže odpověď
   i citace
3. /ops ukáže u toho runu nenulovou cenu (ověří token shape)
4. docker compose exec app pytest tests/test_adapters.py — zelené
5. Appka nastartuje bez chyby

Na konci: implementation summary + navrhni commit message. Necommituj.
```

---
## NP-5 — Docs

```
Úkol NP-T5 z docs/TASKS_NEW_PROVIDERS.md.

Spouštěj až potom, co uživatel potvrdil, že předchozí prompty reálně
fungují (AI_INSTRUCTIONS.md §7 — dokumentace se aktualizuje až po
potvrzení, ne po dopsání kódu).

1. docs/REQUIREMENTS.md — amendment k FR-8 ve stejném stylu jako ten z
   2026-09-09. Jeden souhrnný zápis, který doplní VŠECHNY providery
   přibyvší od fáze 1: OpenAI (do textu se nikdy nedopsal), Grok,
   Perplexity a podle výsledku i DeepSeek. U DeepSeeku výslovně uveď, že
   nemá grounding, takže jeho runy nikdy nenesou citace — ať to za půl
   roku nikdo nehlásí jako bug.
2. docs/TASKS.md — nová sekce odkazující na tuhle větev, stejně jako
   mají ostatní dokončené práce.
3. docs/TASKS_SEARCH_QUERIES.md — oprav design decision 9. NEŠKRTEJ ho;
   dopiš datovanou korekci, že zjištění platilo pro Sonar Chat
   Completions a Agent API se chová jinak. Stejný styl jako "v1.1
   korekce" na začátku toho dokumentu.

Ukaž diff každého souboru, než ho zapíšeš.

Na konci: implementation summary + navrhni commit message. Necommituj.
```
