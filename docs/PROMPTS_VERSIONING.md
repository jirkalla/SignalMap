# SignalMap — Claude Code Session Prompts: Verzování, changelog a patička

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-versioning-footer (z aktuálního master)
## 2. Pět promptů (VER-1 až VER-5). Pořadí je vynucené u VER-1 → VER-2
##    (patička musí existovat, než do ní přibude SHA) a u VER-3 → VER-5
##    (changelog musí existovat, než se na něj odkážou pravidla).
##    VER-4 je nezávislý, ale VER-5 se na něj odkazuje.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 5. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_VERSIONING.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 6. Nikdy nekombinuj dva prompty do jedné session.
## 7. Kompletní zdůvodnění vč. design decisions 1-19:
##    docs/TASKS_VERSIONING.md — přečti si ho celý před VER-1.
## 8. ROZHODNUTÍ JSOU HOTOVÁ. Startovní verze 1.0.0, CHANGELOG.md v rootu,
##    anglicky, patička "built by jirkalla" bez odkazu na GitHub, hotové
##    dokumenty se nearchivují. Potvrzeno uživatelem 2026-09-22 — neotevírej
##    to znovu a nenabízej alternativy.
## 9. AGENT NETAGUJE A NEBUMPUJE (decision 18). `git tag` ani změna
##    `__version__` na jiné číslo než 1.0.0 v téhle větvi nepatří do žádného
##    promptu. Tag vytváří uživatel po mergnutí PR.
## 10. ŽÁDNÁ MIGRACE. Tahle větev nezavádí tabulku ani sloupec. Kdyby se
##     během implementace zdálo, že je potřeba něco uložit do databáze —
##     ZASTAV a zeptej se. Verze je konstanta v kódu, ne data.
## 11. Až je větev hotová a smergnutá: Completion checklist na konci
##     docs/TASKS_VERSIONING.md (tag, deploy, kontrola patičky na produkci).

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-versioning-footer.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_VERSIONING.md — CELÉ, hlavně design decisions 1-19
3. docs/DEPLOYMENT.md — sekce 3.1, 3.4 a 5.2, na které to navazuje

KONTEXT: Appka běží na produkci (Hetzner) od 2026-09-17. Dnes nejde z
prohlížeče zjistit, jaká verze je nasazená — jediný způsob je
`ssh signalmap "cat /opt/signalmap/DEPLOYED_COMMIT"`. Zároveň
app/main.py deklaruje FastAPI(version="0.1.0"), což je hodnota z fáze 1 a
dávno neplatí. Tahle větev zavádí verzi jako jediný zdroj pravdy,
CHANGELOG.md, patičku a pravidla releasu.

KRITICKÉ:
- Verze žije v app/__init__.py::__version__. Žádné git describe ani
  setuptools-scm: deploy je `git archive` a /opt/signalmap NENÍ git
  checkout (docs/DEPLOYMENT.md §5.2).
- Startovní číslo je 1.0.0. Je potvrzené, nediskutuje se.
- Patička: vlevo název + verze, vpravo "built by jirkalla". ŽÁDNÝ odkaz
  na GitHub, žádné © a žádný rok.
- Nepřihlášený uživatel NESMÍ vidět commit SHA ani prostředí — stejný
  důvod, proč app/main.py vypíná neautentizované /docs a /openapi.json.
- /health se NEMĚNÍ. Je neautentizovaný kvůli Docker healthchecku.
- Texty patičky jdou přes t() do en.json i de.json (parita klíčů se
  kontroluje při importu, chybějící klíč shodí start). Číslo verze se
  nepřekládá.
- Agent nespouští git commit, git tag ani git push.
```

---
---

## VER-1 — Verze jako jediný zdroj pravdy + patička

Viz `docs/TASKS_VERSIONING.md` VER-T1. Shrnutí: `__version__ = "1.0.0"` v
`app/__init__.py`, `FastAPI(version=__version__)` v `app/main.py`, global
`app_version` v `app/templating.py`, nový partial
`app/templates/partials/footer.html`, jeho `include` v `base.html` plus
sticky-footer layout (`body` → `flex flex-col`, `main` → `flex-1`), a klíč
`footer.built_by` do obou i18n souborů.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti VER-T1) a počkej na potvrzení.

**Kritické:**
- `app_version` patří do `templates.env.globals`, **ne** do `render()`.
  Je to konstanta na celý běh procesu; `render()` dává do kontextu jen to,
  co se liší request od requestu (viz jeho docstring a `locale_next`).
- Patička musí lícovat s obsahem — stejný kontejner
  `max-w-6xl mx-auto px-4 sm:px-6` jako `<header>` a `<main>`.
- Sticky footer ověř na stránce s minimem obsahu (např. prázdný seznam
  nebo chybová stránka), ne na dlouhém výpisu runů. Na dlouhé stránce
  vypadá správně i špatně udělaná patička.
- `app/templates/auth/change_password.html` patičku **nedostane** — je to
  jediná šablona mimo `base.html` a je to záměr (decision 19). Nepřepisuj
  ji na `extends`.

**Ověř v prohlížeči na ~640 px i ~1024 px** (§3, §7 bod 2) a přepni EN/DE.
Netvrď, že to funguje, dokud jsi to neviděl na obou šířkách.

**Navrhovaná commit message:**
```
feat(infra): show app version and author in the page footer
```

---

## VER-2 — Commit SHA a čas buildu

Viz `docs/TASKS_VERSIONING.md` VER-T2. Shrnutí: `git_sha` a `build_time`
do `app/config.py` (prázdný default), `ARG`/`ENV` na konec `dockerfile`,
`build.args` do služby `app` v `docker-compose.yaml`, globals v
`templating.py` a podmíněné zobrazení v patičce.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- `ARG`/`ENV` **až za `COPY` vrstvami**, těsně před `CMD`. Nahoře by
  invalidovaly cache a každý build by znovu instaloval requirements
  (decision 7).
- Do `worker` v compose se **nepřidává nic**. Sdílí `image: signalmap-app`,
  takže hodnotu zapečenou v `ENV` dostane zadarmo — na rozdíl od API
  klíčů, které se vypisují dvakrát. Ověř to příkazem, netipuj (decision 8).
- Prázdná hodnota **nesmí shodit start**. Build bez `--build-arg` je
  normální dev scénář; patička v tom případě SHA prostě nezobrazí.
- SHA a badge prostředí jen pro přihlášeného (decision 10). Ověř to
  odhlášeným pohledem na `/login`, ne jen úvahou nad šablonou.
- `/health` ani `tests/test_health.py` se nedotýkej (decision 11).

**Navrhovaná commit message:**
```
chore(infra): bake git sha and build time into the image
```

---

## VER-3 — CHANGELOG a guard test

Viz `docs/TASKS_VERSIONING.md` VER-T3. Shrnutí: nový `CHANGELOG.md` v
rootu podle Keep a Changelog, blok `## [1.0.0] - 2026-09-22` sestavený z
reálné historie, prázdný `## [Unreleased]` nahoře, a `tests/test_version.py`
hlídající shodu s `app.__version__`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- Obsah bloku `1.0.0` **sestav z `git log --merges` a z
  `docs/TASKS.md`**, ne z paměti a ne z toho, co si myslíš, že appka umí.
  Osmnáct PR, po funkčních celcích, u každého číslo PR.
- Nevymýšlej historii releasů před 1.0.0 (decision 13). Jeden blok, pod
  ním věta odkazující na git log a `docs/TASKS_*.md`.
- Anglicky. Granularita = uživatelsky viditelná změna, ne commit.
- Guard test musí **reálně spadnout** při rozejití. Změň dočasně
  `__version__` na `9.9.9`, ukaž, že test padá, a vrať to. Test, který
  nikdy nespadl, není pojistka.
- Test nesmí potřebovat databázi ani HTTP klienta — je to čtení dvou
  souborů.

**Navrhovaná commit message:**
```
chore(infra): add CHANGELOG with 1.0.0 baseline and a version drift test
```

---

## VER-4 — Úklid hotových TASKS/PROMPTS dokumentů

Viz `docs/TASKS_VERSIONING.md` VER-T4. Shrnutí: jednotná status hlavička
do každého `docs/TASKS_*.md` a `docs/PROMPTS_*.md`, nová sekce „Index of
feature branches" s tabulkou v `docs/TASKS.md`, a náhrada ručního výčtu
hotových fází v hlavičce `docs/TASKS_NEW_PROVIDERS.md`.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- **Nic nepřesouvej a nic nemaž.** Žádné `docs/archive/`. V repu je 601
  křížových odkazů na tyhle soubory, včetně docstringů v `app/`
  (decision 15).
- Čísla PR a data ber z `git log --merges --date=short --pretty="%ad %s"`.
  Nevymýšlej je. U dvou větví mergnutých bez PR uveď `(direct merge)`.
- Hlavičky starých dokumentů jinak **nepřepisuj** — je to historický
  zápis toho, co platilo v době vzniku. Mění se jen
  `TASKS_NEW_PROVIDERS.md`, protože je jediný nehotový.
- „Beyond phase 1 scope" v `docs/TASKS.md` zůstává. Tabulka ho doplňuje.
- Tenhle prompt nesahá na kód. Nic se nebuildí, nic nerestartuje.

**Navrhovaná commit message:**
```
docs(docs): mark finished task documents done and index them in TASKS.md
```

---

## VER-5 — Pravidla do dokumentace

Viz `docs/TASKS_VERSIONING.md` VER-T5. Shrnutí: `AI_INSTRUCTIONS.md` §3,
§4, §7 a §8; nová sekce 0 v `docs/DEPLOYMENT.md` plus úpravy §3.1, §3.4 a
§4.1; dva řádky v `README.md`.

**Tenhle prompt spusť až po tom, co uživatel potvrdí, že VER-1 až VER-4
reálně fungují** (§7 bod 3 — dokumentace se aktualizuje až po potvrzení).

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:**
- `AI_INSTRUCTIONS.md` je pravidlový dokument, kterým se sám řídíš.
  **Ukaž přesné diffy** navrhovaných odstavců, než je zapíšeš — ne
  parafrázi.
- Do §4 patří obojí: zákaz bumpu a tagu bez pokynu **i** výjimka pro
  `CHANGELOG.md` ze zákazu nových top-level dokumentů. Bez té druhé bude
  příští session považovat `CHANGELOG.md` za porušení pravidla.
- Tabulku MAJOR/MINOR/PATCH opiš z design decision 5, včetně heuristiky
  ve třech krocích. Nepřeformulovávej ji — je to rozhodovací pravidlo, ne
  prosa.
- `docs/REQUIREMENTS.md` se **nemění** (decision 17). Verzování není
  požadavek na produkt.
- Sekce 0 v runbooku musí být použitelná bez čtení tohohle dokumentu:
  někdo ji čte ve chvíli, kdy nasazuje, ne když plánuje.

**Navrhovaná commit message:**
```
docs(infra): document versioning rules, release flow and changelog duties
```

---
---

## Po mergnutí

Completion checklist je na konci `docs/TASKS_VERSIONING.md`. Stručně:
uživatel vytvoří tag `v1.0.0`, nasadí podle nové sekce 0 v runbooku a po
přihlášení ověří, že patička na produkci ukazuje `v1.0.0` a SHA shodné s
`/opt/signalmap/DEPLOYED_COMMIT`. Teprve tím je větev ověřená end-to-end —
lokální kontrola dokazuje jen to, že mechanismus funguje, ne že se nasadil
správný build.
