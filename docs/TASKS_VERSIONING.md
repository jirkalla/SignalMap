# SignalMap — Tasks: Verzování, changelog a patička

## Status: 🔜 Merged (PR #19, 2026-09-23) — release pending, bundled with new AI providers into v1.1.0

## v1.0 | Září 2026
## Branch: feature/signalmap-versioning-footer
## Task ID prefix: VER

> Fáze 1–6, hardening, export, search queries, cost components, ops
> dashboard, bulk import, maintenance page, pre-scheduler hygiena a
> scheduler jsou hotové a smergnuté (18 PR, poslední 2026-09-22). Tenhle
> dokument zavádí **verzování aplikace, changelog a patičku**, která
> nasazenou verzi ukáže v prohlížeči.
>
> Není to roadmap fáze ze skillu `signalmap-conventions`. Je to
> infrastruktura kolem releasů, která měla vzniknout někdy kolem prvního
> ostrého nasazení (2026-09-17) a nevznikla.

---

## Motivace — tři konkrétní vady dneška

1. **Z prohlížeče nejde zjistit, co běží.** Jediný dnešní způsob je
   `ssh signalmap "cat /opt/signalmap/DEPLOYED_COMMIT"`
   (`docs/DEPLOYMENT.md` §3.4). Po každém deployi i při „proč se mi to
   nezměnilo" je to první otázka a odpověď je za SSH.
2. **Verze v kódu už teď lže.** `app/main.py` deklaruje
   `FastAPI(version="0.1.0")` — hodnota z fáze 1, nikdy neaktualizovaná,
   viditelná v `/openapi.json` a ve Swagger UI.
3. **Neexistuje pojem „release".** Merge do masteru a deploy nemají číslo,
   takže nejde říct „na produkci je starší verze než na dev PC" jinak než
   porovnáním SHA.

---

## Rozhodnutí potvrzená uživatelem (2026-09-22)

Tahle větev začíná s hotovými odpověďmi na to, co je jinak otázka na
uživatele. Neotevírej je znovu:

| Otázka | Rozhodnutí |
|---|---|
| Startovní číslo verze | **`1.0.0`** |
| Umístění changelogu | **`CHANGELOG.md` v rootu repozitáře** |
| Jazyk changelogu | **angličtina** |
| Text v patičce | **`built by jirkalla`**, bez odkazu na GitHub |
| Hotové TASKS/PROMPTS dokumenty | **nearchivovat** — status hlavička + index v `docs/TASKS.md` |
| Vztah k novým AI providerům | řeší se **jinou větví**; tahle sahá na `app/config.py` a `docker-compose.yaml` první |

---

## ⚠️ Flag podle AI_INSTRUCTIONS.md §4 — vyřízený

- **Nový top-level dokument.** §4 zakazuje zakládat nové top-level
  dokumentační soubory bez výslovného pokynu. `CHANGELOG.md` v rootu je
  výslovně odsouhlasený v konverzaci 2026-09-22. VER-T5 to doplní do §4
  jako trvalou výjimku, ať to příště někdo neodstraní jako porušení
  pravidla.
- **Změny v `AI_INSTRUCTIONS.md` samotném.** Dělá je VER-T5, a to až po
  potvrzení, že mechanismus z VER-T1 až VER-T4 reálně funguje (§7 bod 3).
- **Databáze.** Tahle větev nezavádí žádnou tabulku, sloupec ani migraci.
  Schema flag neplatí.

---

## Design decisions

**1. Číslo verze žije v kódu, ne v gitu.**
`setuptools-scm`, `git describe` ani `git rev-parse` uvnitř appky
nefungují: deploy podle `docs/DEPLOYMENT.md` §3.1 vyrábí `git archive` z
commitu a rozbaluje ho na server, takže `/opt/signalmap` **není git
checkout** — §5.2 toho dokumentu na to výslovně upozorňuje („vždy spadne
na »not a git repository«"). Jediný zdroj pravdy je
`app/__init__.py::__version__`.

**2. `FastAPI(version=...)` čte tutéž konstantu.**
Dnešní natvrdo napsané `"0.1.0"` je důkaz, že druhá kopie čísla se
rozejde. Po VER-T1 existuje jedno místo a všechno ostatní ho importuje.

**3. Start na `1.0.0`, ne na `0.x`.**
`1.0.0` v SemVeru neznamená „funkčně hotové", znamená „je to v produkčním
použití a nebude se to měnit bez varování". Appka běží od 2026-09-17 na
Hetzneru a pracuje se v ní na reálném klientovi; `0.x` by explicitně
říkalo opak. Celá dosavadní historie (18 PR) se zpětně stává obsahem
`1.0.0` — viz decision 13.

**4. Verze se mění per release, ne per commit.**
Jeden release = merge do `master` + jeden deploy. Bump `__version__` a
`git tag -a vX.Y.Z` se dělá **na masteru po mergnutí PR**, ne průběžně na
větvi. Tři `feat` commity v jedné větvi = jeden MINOR bump.

**5. Pravidla MAJOR/MINOR/PATCH jsou ušitá na aplikaci, ne na knihovnu.**
Klasický SemVer definuje „breaking" proti veřejnému API, které SignalMap
nemá. Definuje se proto proti **tomu, kdo nasazuje, a proti uloženým
datům**:

| Díl | Kdy | Konkrétně |
|---|---|---|
| **MAJOR** | nasazení není „jen deploy", nebo se mění význam uložených dat | nová **povinná** env proměnná bez defaultu (případ `SECRET_KEY`), destruktivní/nevratná migrace (ta, pro kterou má `DEPLOYMENT.md` §6.2 vlastní rollback větev), multi-tenancy, zrušení nebo přejmenování existující URL |
| **MINOR** | nová funkce, nasazení je obyčejný deploy | nový provider, nový pohled na dashboardu, export, scheduler, bulk import — typicky commity `feat(...)` |
| **PATCH** | nic nového není vidět, jen se něco spravilo | `fix`, `refactor`, `chore`, `test`, úpravy textů, výkon |

Heuristika, v tomhle pořadí:
1. Musí ten, kdo nasazuje, udělat něco navíc než deploy? → **MAJOR**
2. Uvidí uživatel v appce něco nového? → **MINOR**
3. Jinak → **PATCH**

Za samotné `docs(...)` commity se verze nezvedá — dokumentace není release.

**6. Commit SHA se injektuje při buildu z hodnoty, kterou deploy už má.**
`docs/DEPLOYMENT.md` §3.1 si `$SHA` počítá tak jako tak, aby vyrobilo
archiv, a §3.4 ho zapisuje do `DEPLOYED_COMMIT`. Stejná proměnná se předá
jako `--build-arg`. Žádný nový zdroj pravdy, jen se ten existující dostane
dovnitř appky.

**7. `ARG`/`ENV` patří na konec dockerfile.**
`ARG` uvedený nahoře invaliduje cache všech následujících vrstev, takže by
se při každém buildu znovu instalovaly requirements. Řádky pro `GIT_SHA` a
`BUILD_TIME` jdou až za `COPY`, těsně před `CMD`.

**8. Worker dostane verzi zadarmo.**
`docker-compose.yaml` staví image jen ve službě `app`; `worker` používá
`image: signalmap-app` bez vlastního buildu (komentář u té služby to
zdůvodňuje). Protože je hodnota zapečená do image jako `ENV`, nemusí se —
na rozdíl od API klíčů — vypisovat v `environment` dvakrát. To je rozdíl
proti design decision 7 v `docs/TASKS_NEW_PROVIDERS.md`; nesnaž se ty dva
případy sjednotit.

**9. Patička neobsahuje žádný odkaz.**
Navigace je nahoře, patička je informační. Vpravo `built by jirkalla` jako
prostý text — bez odkazu na GitHub, bez `©` a bez roku. Rok by se musel
buď počítat serverově, nebo ho každý leden opravovat, a žádnou informaci
navíc nenese.

**10. Nepřihlášený uživatel nevidí SHA ani prostředí.**
Přesná verze plus commit na veřejně dostupné přihlašovací stránce umožní
spárovat nasazený build se známou zranitelností. Není to teorie — v tomhle
repu stejné rozhodnutí už jednou padlo: `app/main.py` vypíná
neautentizované `/docs`, `/redoc` a `/openapi.json`, protože schovat odkaz
v navigaci nestačilo (`docs/TASKS_PHASE6.md` follow-up). Přihlášený vidí
`vX.Y.Z · sha · prostředí`, nepřihlášený jen `vX.Y.Z`.

**11. `/health` zůstává beze změny.**
Je neautentizovaný kvůli Docker healthchecku, takže přidat tam verzi
znamená obejít decision 10 zadními vrátky. `tests/test_health.py` se
nemění. Strojově čitelná verze patří na `/ops` (autentizované,
engineering-facing) — mimo rozsah téhle větve.

**12. Changelog: Keep a Changelog, anglicky, granularita = uživatelsky
viditelná změna.**
Jedna odrážka za věc, kterou uživatel pozná („Added Perplexity provider"),
ne tři řádky o adapteru, migraci a testech. Angličtina proto, že položky
jsou v podstatě učesané commit subjekty, a ty jsou podle §8 anglicky.

**13. Zpětné naplnění jedním blokem `1.0.0`, ne rekonstrukcí 18 releasů.**
Ty releasy nikdy neexistovaly; vymýšlet jim data a čísla by byla fikce.
Jeden blok `## [1.0.0] - 2026-09-22` s odrážkami po funkčních celcích a
odkazem na `git log` a `docs/TASKS_*.md` pro detail.

**14. Guard test proti driftu `__version__` × `CHANGELOG.md`.**
Dva textové testy, které znemožní vydat verzi bez záznamu v changelogu a
naopak. Bez nich se ty dvě věci do měsíce rozejdou přesně tak, jako se už
rozešlo `FastAPI(version="0.1.0")` s realitou.

**15. Hotové dokumenty se nearchivují.**
Přesun do `docs/archive/` je první nápad, který každého napadne, a je
špatný: v repu je **601 křížových odkazů** na `docs/TASKS_*.md` /
`docs/PROMPTS_*.md` (napříč `app/`, `docs/`, `tests/`, `README.md`),
včetně docstringů, kde se jimi zdůvodňují design decisions. Přesun je
rozbije všechny najednou a hotový dokument nikomu nepřekáží — jen musí být
poznat, že je hotový. Řešením je jednotná status hlavička plus jeden index
v `docs/TASKS.md`.

**16. Nové dokumenty přestanou v hlavičce vypisovat historii.**
Dnes každý nový `TASKS_*` ručně vyjmenovává, co už je hotové („Fáze 1,
fáze 2, třetí provider, Export, Search queries, Cost components, Ops
dashboard a Scheduler jsou hotové a smergnuté"). Ten seznam roste s každou
větví a zpětně ho nikdo neopravuje. Nahradí ho jedna věta s odkazem na
index.

**17. Pravidla verzování patří do `docs/DEPLOYMENT.md`, ne do
`docs/REQUIREMENTS.md`.**
Verzování není funkční ani nefunkční požadavek na produkt. Je to součást
releasu, a release je deploy — runbook už má §3.4 „Zaznamenat verzi" i
§5.2 deploy log, na které to navazuje. `REQUIREMENTS.md` se v téhle větvi
nemění vůbec.

**18. Agent nikdy nebumpuje verzi ani netaguje.**
Stejná třída akce jako `git commit` / `git push` podle §4 a §8: navrhne,
ukáže, čeká. Tag `v1.0.0` vytváří uživatel po mergnutí PR. VER-T5 to
zapisuje do §4 natrvalo.

**19. `app/templates/auth/change_password.html` patičku nedostane.**
Je to jediná šablona, která nedědí z `base.html` (samostatný HTML
dokument) — vynucená stavová stránka, kde jediná správná akce je změnit
heslo. Patička tam nechybí omylem. `tools/server/maintenance.html` je
statická stránka servírovaná Caddym ve chvíli, kdy appka neběží; ta o
verzi vědět nemůže a nesahá se na ni.

**20. Index feature branchí žije v `docs/00_INDEX.md`, ne jako sekce v
`docs/TASKS.md`.** Revize původního plánu bodu VER-T4.3 (níže), rozhodnuto
při implementaci. `docs/TASKS.md` se jmenuje „Tasks (Phase 1)" a jeho
hlavní obsah je návod ke stavbě fáze 1 v pořadí od nejstaršího — přesný
opak toho, jak se čte index (nejnovější nahoře, lookup, ne návod). Přidat
index jako sekci na konec 440řádkového dokumentu by ho udělalo prakticky
nedohledatelným. Přejmenovat/rozdělit `TASKS.md` samotné nejde (decision
15 — 601 křížových odkazů), ale `docs/00_INDEX.md` je nový soubor bez
jediného existujícího odkazu, takže ho lze pojmenovat i řadit správně od
začátku. `00_` prefix ho řadí v abecedním výpisu `docs/` jako první.
`docs/TASKS.md` dostává jen jednořádkový pointer na něj hned pod status
řádek nahoře.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| VER-T1 | Verze jako jediný zdroj pravdy + patička | ✅ Done |
| VER-T2 | Commit SHA a čas buildu z build argumentů | ✅ Done |
| VER-T3 | `CHANGELOG.md` + guard test proti driftu | ✅ Done |
| VER-T4 | Úklid hotových TASKS/PROMPTS dokumentů + index | ✅ Done |
| VER-T5 | Pravidla do AI_INSTRUCTIONS, DEPLOYMENT a README | ✅ Done |

Pořadí vynucené u T1 → T2 (patička musí existovat, než do ní přibude SHA)
a u T3 → T5 (changelog musí existovat, než se na něj odkážou pravidla).
VER-T4 je nezávislý, ale VER-T5 se na něj odkazuje, takže patří před něj.
VER-T5 se dělá poslední, až je zbytek ověřený uživatelem (§7 bod 3).

---

## VER-T1 — Verze jako jediný zdroj pravdy + patička

**Target:** `app/__init__.py`, `app/main.py`, `app/templating.py`,
`app/templates/partials/footer.html` (nový), `app/templates/base.html`,
`app/i18n/en.json`, `app/i18n/de.json`

1. `app/__init__.py` (dnes prázdný) — `__version__ = "1.0.0"` s krátkým
   docstringem: jediný zdroj pravdy, pravidla bumpování v
   `docs/DEPLOYMENT.md`, historie v `CHANGELOG.md`.
2. `app/main.py` — `version=__version__` v konstruktoru `FastAPI(...)`
   místo natvrdo napsaného `"0.1.0"` (decision 2).
3. `app/templating.py` — `templates.env.globals["app_version"] =
   __version__` vedle stávajících `can_edit`/`can_schedule`. **Do
   `render()` to nepatří** — je to konstanta na celý běh procesu, ne
   per-request hodnota; `render()` dnes do kontextu dává jen věci, které
   se request od requestu liší.
4. `app/templates/partials/footer.html` — nový partial. Vlevo název appky
   a `v{{ app_version }}` (mono font, tlumená barva), vpravo
   `{{ t('footer.built_by') }}`. Tailwind ve stylu zbytku appky: horní
   `border-t border-stone-200`, `bg-white`, stejný kontejner
   `max-w-6xl mx-auto px-4 sm:px-6` jako `<header>` a `<main>`, aby
   patička lícovala s obsahem. Na úzkém displeji se oba bloky zalomí pod
   sebe (`flex-wrap`), nezmenšují se pod čitelnou velikost.
5. `app/templates/base.html` — `<body>` dostane `flex flex-col`, `<main>`
   `flex-1`, `{% include 'partials/footer.html' %}` za `</main>`. Bez toho
   se patička na krátkých stránkách (prázdný seznam, chybová stránka)
   vyveze doprostřed obrazovky.
6. `app/i18n/en.json` + `de.json` — `footer.built_by` (`built by jirkalla`
   / `erstellt von jirkalla`). Klíč musí být v **obou** souborech, jinak
   `app/i18n/__init__.py` shodí aplikaci při importu na kontrole parity.
   Číslo verze se nepřekládá — je to identifikátor, ne prosa.

Po dokončení:
1. `docker compose up -d --build app` — nastartuje bez chyby.
2. Patička je vidět na `/clients`, `/dashboard`, `/login` i na chybové
   stránce a drží se dole i na stránce s minimem obsahu.
3. Prohlížeč na ~640 px a ~1024 px (§3, §7 bod 2) — ne jen desktop.
4. Přepnutí EN/DE mění text patičky.
5. `/openapi.json` (admin) ukazuje `"version": "1.0.0"`.
6. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
feat(infra): show app version and author in the page footer
```

---

## VER-T2 — Commit SHA a čas buildu

**Target:** `app/config.py`, `dockerfile`, `docker-compose.yaml`,
`app/templating.py`, `app/templates/partials/footer.html`

Prerekvizita: VER-T1 hotový.

1. `app/config.py` — `git_sha: str = ""` a `build_time: str = ""`. Prázdný
   default je záměrný: na dev PC se buildí bez build argumentů a patička
   ty dva údaje prostě nezobrazí. **Chybějící hodnota nikdy nesmí shodit
   start** (rozdíl proti `secret_key`, kde je to naopak žádoucí).
2. `dockerfile` — `ARG GIT_SHA=""` / `ARG BUILD_TIME=""` a odpovídající
   `ENV`, **až za `COPY` vrstvami, těsně před `CMD`** (decision 7).
3. `docker-compose.yaml` — `build.args` u služby `app`, hodnoty z
   prostředí (`GIT_SHA: ${GIT_SHA:-}`, `BUILD_TIME: ${BUILD_TIME:-}`).
   Do `environment` u `worker` se **nic nepřidává** (decision 8) — image
   je společný a hodnota je v něm zapečená. Ověř to:
   `docker compose exec worker python -c "from app.config import
   get_settings; print(get_settings().git_sha)"`.
4. `app/templating.py` — globals `git_sha`, `build_time` a
   `app_environment` (z `get_settings()`, jednou při importu).
5. `footer.html` — SHA (prvních 7 znaků, mono, tlumeně) a badge prostředí
   se zobrazí **jen když je `current_user` a hodnota není prázdná**
   (decision 10); badge prostředí navíc jen když `app_environment !=
   "production"`. Verze dostane `title` s plným SHA a časem buildu —
   tooltip, ne odkaz.
6. **Nic v `/health`** (decision 11). `tests/test_health.py` se nemění.

Po dokončení:
1. `GIT_SHA=$(git rev-parse HEAD) BUILD_TIME=$(date -u +%FT%TZ) docker
   compose up -d --build app worker` — patička přihlášenému ukáže SHA
   shodné s `git rev-parse --short HEAD`.
2. Build **bez** těch proměnných proběhne a appka funguje — patička jen
   SHA neukáže (kontrola prázdného defaultu).
3. Odhlášený pohled na `/login` SHA ani prostředí neukazuje.
4. `docker compose exec worker …` vrací totéž SHA jako `app`.
5. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
chore(infra): bake git sha and build time into the image
```

---

## VER-T3 — CHANGELOG a guard test

**Target:** `CHANGELOG.md` (nový, root repozitáře), `tests/test_version.py`
(nový)

1. `CHANGELOG.md` podle Keep a Changelog (https://keepachangelog.com):
   hlavička s větou, že projekt dodržuje SemVer podle pravidel v
   `docs/DEPLOYMENT.md`, pak `## [Unreleased]` a pod ním
   `## [1.0.0] - 2026-09-22`.
2. Obsah bloku `1.0.0` — sestav z `git log --merges --date=short
   --pretty="%ad %s"` (18 PR) a z `docs/TASKS.md`. **Po funkčních celcích,
   ne po PR:** fáze 1–6, hardening, export, search queries,
   ChatGPT/persona/ceny, bulk import a multi-model run, local time, ops
   dashboard, cost components, oprava Gemini citací, maintenance page,
   pre-scheduler hygiena, scheduler. U každého číslo PR v závorce.
   Zakonči větou, že detailní historie před `1.0.0` je v git logu a v
   `docs/TASKS_*.md` (decision 13).
3. Používej jen sekce z Keep a Changelog (`Added`, `Changed`, `Fixed`,
   `Removed`, `Security`) — žádné vlastní.
4. `tests/test_version.py` — přečte `CHANGELOG.md`, vytáhne regexem první
   nadpis tvaru `## [X.Y.Z] - YYYY-MM-DD` (blok `[Unreleased]` přeskočí) a
   porovná s `app.__version__`. Druhý test: nadpisy verzí jdou odshora
   sestupně a mají platné datum. Bez databáze a bez HTTP klienta — jsou to
   dva textové testy.

Po dokončení:
1. `docker compose exec app pytest tests/test_version.py` — zelené.
2. Zkušebně změň `__version__` na `9.9.9` → test spadne → vrať zpět.
   Test, který nikdy nespadl, není pojistka.
3. Uživatel projde blok `1.0.0` a potvrdí, že odpovídá realitě — je to
   jediná část, kterou nelze ověřit strojově.
4. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
chore(infra): add CHANGELOG with 1.0.0 baseline and a version drift test
```

---

## VER-T4 — Úklid hotových TASKS/PROMPTS dokumentů

**Target:** `docs/00_INDEX.md` (nový, viz decision 20), `docs/TASKS.md`,
dvojice `docs/TASKS_*.md` / `docs/PROMPTS_*.md`

Žádné přesuny, žádné mazání (decision 15).

1. **Jednotná status hlavička** pod nadpisem každého dokumentu:
   ```
   ## Status: ✅ Done — PR #18, merged 2026-09-22, released in v1.0.0
   ```
   Data ber z `git log --merges --date=short --pretty="%ad %s"`, ne z
   paměti. U dokumentů bez PR (hardening, server tooling — mergnuté přímo)
   `merged YYYY-MM-DD (direct merge)` bez čísla.
2. `docs/TASKS_NEW_PROVIDERS.md` dostane
   `## Status: ⏳ Not started — needs API keys and the §4 confirmation`.
   Je to jediný nehotový dokument.
3. `docs/00_INDEX.md` (nový soubor, viz decision 20) — tabulka: dokument ·
   task prefix · branch · PR · merged · released in · status, seřazená od
   nejnovější po nejstarší (stejná konvence jako `CHANGELOG.md`), s
   odděleným blokem pro nehotové/rozpracované nahoře. Jeden řádek na
   dvojici TASKS/PROMPTS. `docs/TASKS.md` dostane jen jednořádkový pointer
   na něj hned pod status řádek nahoře.
4. Existující text v `TASKS.md` („Beyond phase 1 scope") **nemaž** — je to
   historický zápis fáze 1. `docs/00_INDEX.md` ho doplňuje, nenahrazuje.
5. `docs/TASKS_NEW_PROVIDERS.md` — nahraď úvodní odstavec s ručním výčtem
   hotových fází jednou větou s odkazem na `docs/00_INDEX.md`
   (decision 16). Starých 20 dokumentů zpětně **nepřepisuj**; jejich
   hlavičky jsou historický zápis toho, co platilo v době vzniku.

Po dokončení:
1. `grep -c "^## Status:" docs/TASKS_*.md docs/PROMPTS_*.md` — každý
   soubor má právě jeden.
2. Tabulka v `docs/00_INDEX.md` má tolik řádků, kolik je dvojic, čísla PR
   sedí s `git log --merges`, a je řazená od nejnovější po nejstarší.
3. V kódu se nic nezměnilo — appka se nerestartuje, testy se nespouštějí.
4. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
docs(docs): mark finished task documents done and index them in TASKS.md
```

---

## VER-T5 — Pravidla do dokumentace

**Target:** `AI_INSTRUCTIONS.md`, `docs/DEPLOYMENT.md`, `README.md`

Až jsou VER-T1 až VER-T4 hotové a **ověřené uživatelem** (§7 bod 3).

1. `AI_INSTRUCTIONS.md`:
   - §3 DOCUMENTATION — nová odrážka: uživatelsky viditelná změna = jedna
     odrážka pod `## [Unreleased]` v `CHANGELOG.md`, jako součást docs
     kroku úkolu.
   - §3 DOCUMENTATION — druhá odrážka: na konci větve, před mergnutím,
     dostane `docs/TASKS_*.md` i `docs/PROMPTS_*.md` status hlavičku a
     řádek v indexu v `docs/TASKS.md`. To je to, co VER-T4 dělá zpětně —
     tady se z toho stává trvalý krok.
   - §4 NEVER — „Nikdy nezvyšuj `__version__` a nikdy nevytvářej git tag
     bez výslovného pokynu" (decision 18).
   - §4 NEVER — do zákazu nových top-level dokumentů doplnit, že
     `CHANGELOG.md` je schválená výjimka.
   - §7 TASK COMPLETION PROTOCOL — bod o changelogu a o status hlavičce.
   - §8 COMMIT CONVENTIONS — tvar release commitu
     `chore(release): v1.1.0`.
2. `docs/DEPLOYMENT.md`:
   - Nová sekce **„0. Rozhodnout verzi"** před §1: tabulka
     MAJOR/MINOR/PATCH a heuristika z decision 5, bump `__version__`,
     přesun `Unreleased` bloku pod nové číslo s datem, `git tag -a vX.Y.Z`.
   - §3.1 — předat `GIT_SHA=$SHA` a `BUILD_TIME` do buildu.
   - §3.4 — poznámka, že `DEPLOYED_COMMIT` zůstává, ale nasazenou verzi
     je nově vidět i v patičce po přihlášení.
   - §4.1 interní kontrola — nový bod: patička ukazuje očekávanou verzi a
     SHA. Je to nejrychlejší důkaz, že se nasadil ten správný build.
3. `README.md` — v sekci „Ship a release" dva řádky: bump, tag, changelog,
   s odkazem na `docs/DEPLOYMENT.md` §0.
4. `docs/REQUIREMENTS.md` se **nemění** (decision 17).

Po dokončení:
1. Uživatel si přečte §0 v runbooku a potvrdí, že podle něj umí vydat
   verzi, aniž by se musel ptát.
2. Implementation summary + navrhni commit message (nespouštěj git).

**Expected commit:**
```
docs(infra): document versioning rules, release flow and changelog duties
```

---

## Completion checklist

Po mergnutí PR do masteru, v tomhle pořadí:

1. **Uživatel** vytvoří tag: `git tag -a v1.0.0 -m "First tagged release"`
   a pushne ho (`git push origin v1.0.0`). Agent to nedělá (decision 18).
2. Deploy podle `docs/DEPLOYMENT.md` — nově včetně sekce 0 a s
   `GIT_SHA`/`BUILD_TIME` v buildu.
3. Na produkci po přihlášení zkontrolovat, že patička ukazuje `v1.0.0` a
   SHA shodné s `cat /opt/signalmap/DEPLOYED_COMMIT`. Tím je celá větev
   ověřená end-to-end.
4. Přesunout tuhle větev z bloku „In progress" do bloku „Released" v
   `docs/00_INDEX.md` (VER-T4 bod 3) a status hlavičku sem.

Verze téhle větve samotné: je to `feat` (patička je viditelná novinka), ale
zároveň zakládá baseline, takže release po mergnutí je **`v1.0.0`**, ne
`v1.1.0`.

---

## Co tenhle dokument záměrně neřeší

- **CI/CD a automatický release.** Deploy je dnes ruční runbook; přidávat
  k němu pipeline je samostatné rozhodnutí.
- **„What's new" v aplikaci.** Changelog je pro vývojáře, ne pro uživatele
  appky. Kdyby ho měl vidět klient, je to vlastní práce s vlastním textem.
- **Verzování API.** Appka veřejné API nemá (`/api/*` obsluhuje vlastní
  dashboard). Až vznikne, bude se verzovat zvlášť a jinak.
- **Strojově čitelná verze pro monitoring.** Decision 11 ji odsouvá na
  `/ops`; dokud nikdo nemá monitoring, který by ji četl, nemá smysl.
- **Noví AI provideři.** Vlastní větev, `docs/TASKS_NEW_PROVIDERS.md`.
  Jediný dotyk je `app/config.py` a `docker-compose.yaml` — tahle větev
  jde první, aby se konflikty nemusely řešit.
