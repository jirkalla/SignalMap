# SignalMap — Tasks: OpenAI SDK Import Race

## Status: 🔜 Merged (PR #21, 2026-09-25) — production deploy and verification (T3) pending

## v1.0 | Září 2026
## Branch: feature/signalmap-openai-import-race
## Task ID prefix: OIR

Status: navrženo v konverzaci 2026-09-25, při ověřování nasazení v1.1.0.
Malá, uzavřená oprava — tři úkoly.

Vzniklo při kroku 4.4 runbooku (`docs/DEPLOYMENT.md`): první testovací run
na Grok po nasazení v1.1.0 skončil chybou

```
deadlock detected by _ModuleLock('openai.resources.chat') at 131511161147984
```

Opakovaný run prošel. Data se neztratila — run je uložený jako `error`,
což je pravdivý záznam o tom, co se stalo.

**Goal:** první souběžné runy po startu kontejneru nesmí spadnout na
Python import deadlock — u žádného providera, jehož SDK načítá resource
moduly líně.

**Kterých adaptérů se to týká** (ověřeno ve zdrojácích verzí
z `requirements.txt`, 2026-09-25):

| Adaptér | SDK | Volání | Líný import | Týká se |
|---|---|---|---|---|
| `app/adapters/openai.py` | `openai==3.13.0` | `client.responses` | ano | **ano** |
| `app/adapters/perplexity.py` | `openai==3.13.0` | `client.responses` | ano | **ano** |
| `app/adapters/grok.py` | `openai==3.13.0` | `client.responses` | ano | **ano** (incident) |
| `app/adapters/deepseek.py` | `openai==3.13.0` | `client.chat` | ano | **ano** |
| `app/adapters/anthropic.py` | `anthropic==1.4.0` | `client.messages` | ano — stejný vzor (`@cached_property` + `from .resources.messages import Messages`) | **ano** |
| `app/adapters/google.py` | `google-genai==2.22.0` | `client.models` | ne — `Models` se importuje na úrovni modulu a instancuje v `Client.__init__` | ne |

Anthropic je samostatné SDK, takže samo o sobě se s `openai` na zámku
nesrazí; dva souběžné první runy na Anthropic ale ano.

---

## Příčina (ověřeno ve zdrojovém kódu `openai==3.13.0`)

`openai/_client.py` načítá resource moduly **líně**, až při prvním přístupu:

```python
@cached_property
def chat(self) -> Chat:
    from .resources.chat import Chat
    return Chat(self)

@cached_property
def responses(self) -> Responses:
    from .resources.responses import Responses
    return Responses(self)
```

`import openai` v adaptérech tedy tyhle moduly **nenačte**. Načtou se až
v prvním `self._client.chat…` / `self._client.responses…` — v `app` to je
uvnitř requestu, tedy ve vlákně Starlette threadpoolu. Dva souběžné
requesty = dvě vlákna importující navzájem provázané moduly zároveň →
Python detekuje deadlock na per-module zámku a jednomu z nich import
přeruší (`_DeadlockError`), místo aby se zasekl.

Chybu dostal Grok (`responses`), ale zámek byl na `openai.resources.chat` —
moduly jsou tedy navzájem provázané; nestačí předem načíst jen jeden.

Po prvním úspěšném načtení jsou moduly v `sys.modules` a problém se
**do dalšího restartu procesu neopakuje**. Proto se ukazuje právě po
nasazení: čerstvý kontejner + testovací runy rychle po sobě.

**Worker se to netýká** — běží v jedné smyčce, runy zpracovává
sekvenčně. Týká se jen `app` (ruční triggery v threadpoolu).

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Načíst moduly předem při startu procesu, ne zamykat první volání.**
   Import na úrovni modulu proběhne v hlavním vlákně při startu, dřív než
   uvicorn přijme první request — souběh tím přestává existovat. Zámek
   kolem prvního volání v každém adaptéru by řešil totéž víc kódem a na
   čtyřech místech.
2. **Místo: `app/adapters/__init__.py`** (registr adaptérů). Importuje ho
   startovní cesta `app` i workeru (přes `app/services/run_execution.py`),
   takže jedno místo pokryje všech pět dotčených adaptérů. Patří
   tam věcně — registr je jediný modul, který o všech adaptérech ví.
3. **Oba moduly: `openai.resources.chat` i `openai.resources.responses`.**
   Viz „Příčina" — incident ukázal, že jsou provázané.
4. **Cesty modulů jsou převzaté z vlastních lazy importů SDK** v
   `_client.py` (v3.13.0), ne odhadnuté. Při upgradu `openai` se musí
   ověřit znovu; kdyby modul zmizel, import selže **hlasitě při startu**
   (appka nenaběhne, migrace neproběhne jako jediný krok) — to je správné
   chování, lepší než tiché vrácení rizika.
5. **Anthropic ano, Google ne — podle zdrojáku, ne podle odhadu.**
   `anthropic==1.4.0` má v `_client.py` stejný vzor jako `openai`
   (`@cached_property def messages` s lokálním
   `from .resources.messages import Messages`) → přidat
   `anthropic.resources.messages`. `google-genai==2.22.0` importuje
   `Models` na úrovni modulu `google/genai/client.py` a vytváří ho už
   v `Client.__init__` → nic nepřidávat. OIR-T1 to znovu ověří proti
   verzím nainstalovaným v kontejneru.
8. **Upgrade SDK problém neřeší a do téhle větve nepatří.** Nejnovější
   `openai` (3.19.2, PyPI 2026-09-25) má `chat` i `responses` pořád líně
   (`@cached_property` + lokální import) — ověřeno ve zdrojáku tagu
   v3.19.2. Upgrade je samostatné rozhodnutí s vlastním rizikem (změny
   response shape, na které stojí mappery citací) a nemíchá se s opravou.
6. **Žádný retry v adaptéru.** Opakovat volání po chybě by u selhání až po
   odeslání požadavku znamenalo riziko dvojího účtování providerem
   (`docs/TASKS_SCHEDULER.md` design decision 13 — nejistý výsledek se
   zaznamená, neopakuje se naslepo). Tady chyba vzniká ještě před voláním
   API, ale obecný retry by tu hranici nerozlišil.
7. **PATCH release — v1.1.1.** Nic nového není vidět, jen se spravilo
   (`docs/DEPLOYMENT.md` kapitola 0). Bump verze a tag dělá člověk.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Eager import v registru adaptérů + regresní test | ✅ |
| T2 | CHANGELOG | ✅ |
| T3 | Nasazení v1.1.1 a ověření na produkci | ⏳ |

---

## T1 — Eager import + test

**Target:** `app/adapters/__init__.py`, `tests/test_adapters.py`

1. **Ověřit diagnózu v produkčních logech** (čtení, provádí uživatel na
   serveru) — dva runy přes `openai`-based providery spuštěné v rozmezí
   pár sekund těsně před chybou:

   ```bash
   cd /opt/signalmap && docker compose logs --since 2026-09-25T06:00:00 app | grep -E "Triggering run|deadlock" | head -30
   ```

   Když se souběh nepotvrdí, **zastavit a vrátit se k diagnóze** — oprava
   by pak mířila vedle.

2. **Ověřit cesty modulů proti nainstalované verzi** (v kontejneru, ne
   z paměti):

   ```bash
   docker compose exec -T app sh -c 'P=$(python -c "import openai,os;print(os.path.dirname(openai.__file__))"); grep -n "from .resources" $P/_client.py'
   ```

   Totéž pro `anthropic` (`from .resources.messages`) a `google.genai`
   (design decision 5 — ověřeno proti GitHub tagům, tady se potvrzuje
   proti tomu, co je opravdu nainstalované).

3. Do `app/adapters/__init__.py`, nad importy adaptérů:

   ```python
   # The openai and anthropic SDKs load these lazily on first `client.chat` /
   # `client.responses` / `client.messages` access (cached_property + local import in their
   # _client.py). Left lazy, that first import happens inside a request thread, and two
   # concurrent runs can deadlock on Python's per-module import lock (_DeadlockError, seen on
   # the first Grok run after the v1.1.0 deploy). Importing them here moves it to process
   # startup, in the main thread. google-genai doesn't need this: it imports Models eagerly.
   import anthropic.resources.messages  # noqa: F401
   import openai.resources.chat  # noqa: F401
   import openai.resources.responses  # noqa: F401
   ```

4. Regresní test v `tests/test_adapters.py` — **v samostatném procesu**
   (`subprocess.run([sys.executable, "-c", ...])`): `import app.adapters` a
   ověřit, že všechny předem načítané moduly jsou v `sys.modules`. Ve
   stejném procesu by test nic nedokazoval — moduly už mohl načíst jiný
   test.

   Samotný souběh se **netestuje** — závisí na načasování vláken, test by
   byl nedeterministický (viz „Co tahle větev vědomě nedělá").

**Done when:** test projde; celá sada `pytest` projde (příkaz z
`docs/DEPLOYMENT.md` 1.1); `docker compose up -d --build --wait` lokálně
naběhne a jeden run přes DeepSeek i Grok (nebo FakeAdapter, pokud chybí
klíče) projde.

**Expected commit:** `fix(adapters): preload openai SDK resource modules at startup`

---

## T2 — CHANGELOG

**Target:** `CHANGELOG.md`

Pod `## [Unreleased]` založit `### Fixed` s jednou odrážkou, zhruba:

```
- The first runs on OpenAI, Perplexity, DeepSeek, Grok, or Anthropic
  after a restart could fail with "deadlock detected by _ModuleLock"
  when triggered concurrently.
```

**Done when:** odrážka je v `[Unreleased]`, nic jiného se v CHANGELOGu
nemění.

**Expected commit:** `docs(changelog): record the openai import race fix`

---

## T3 — Nasazení v1.1.1 a ověření

**Target:** produkce, žádné soubory (kromě end-of-branch docs)

1. Merge PR, bump `app/__init__.py::__version__` na `1.1.1`, přesun
   `[Unreleased]` → `[1.1.1]`, tag — **dělá uživatel**
   (`docs/DEPLOYMENT.md` kapitola 0, `AI_INSTRUCTIONS.md` §4).
2. Nasadit podle `docs/DEPLOYMENT.md`, kapitoly 1–5. Migrace žádné
   (1.3 prázdný) → rollback je jen návrat kódu.
3. **Ověření opravy** hned po 4.4, dokud je kontejner čerstvý: spustit
   **současně** (dva taby, klik rychle po sobě) run na DeepSeek a na Grok.
   Oba musí projít. Stojí to dva levné runy. Anthropic má vlastní SDK —
   pokrýt ho zvlášť dvěma současnými runy na dvou různých Anthropic
   modelech (unikátní index `pending` runu je per prompt + model).
4. End-of-branch docs: `## Status: ...` v tomhle souboru a v
   `docs/PROMPTS_OPENAI_IMPORT_RACE.md`, řádek v `docs/00_INDEX.md`.

**Done when:** v1.1.1 běží na produkci a souběžné runy po restartu
procházejí.

---

## Co tahle větev vědomě nedělá

- **Netestuje samotný souběh.** Reprodukce závisí na načasování vláken a
  verzi Pythonu; takový test by byl buď flaky, nebo by nic nedokazoval.
  Regresní test hlídá příčinu (moduly načtené předem), ne symptom.
- **Nemění threadpool ani počet workerů uvicornu.** Souběh ručních runů je
  žádoucí vlastnost, problém byl jen v líném importu.
- **Nepřidává retry** (design decision 6).
- **Neupgraduje `openai` ani `anthropic`** (design decision 8).
- **Neopravuje run z incidentu.** Uložený `error` je pravdivá historie —
  nepřepisuje se (NFR-6), nový run už proběhl úspěšně.
