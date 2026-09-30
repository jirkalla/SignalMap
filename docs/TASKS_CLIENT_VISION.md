# SignalMap — Tasks: Client Vision (vydání 1, první větev)

## v1.0 | Září 2026
## Branch: feature/signalmap-client-vision
## Task ID prefix: VI
## Cílová verze: v1.3.0 (MINOR) — nasazuje se společně s `feature/signalmap-citation-hardening`

## Status: ✅ Hotovo na větvi (T1–T4), merge do `master` a PR čekají — nasazení s v1.3.0 (CH-T7)

Navrženo 2026-09-30 (`docs/ROADMAP.md` #23, požadavek uživatelů).
Čtyři úkoly, **jedna migrace** (jen přidává).

**Goal:** klient má pole **Vision** — jak chce, aby ho AI asistenti
popisovali — zadávané ve formuláři nad Notes a zobrazené výrazně na
detailu klienta a na dashboardu.

**Pořadí v rámci vydání 1:** tahle větev první → merge do `master` **bez
nasazení** → `feature/signalmap-citation-hardening` → CH-T7 nasadí obě
jako v1.3.0. Vlastní nasazovací úkol tahle větev nemá; co ověřit po
nasazení, je v sekci „Ověření po nasazení" níže a CH-T7 na ni odkazuje.

**Flagováno předem (`AI_INSTRUCTIONS.md` §4):** nový sloupec
`clients.vision` + migrace. Odpovídá `docs/ROADMAP.md` #23 a sekci „Mimo
tuhle roadmapu" (plný Desired Perception Claims model nahrazený pro v1
„prostým volným textovým polem").

---

## Výchozí stav (kód k 2026-09-30, master 7807f7a)

- `app/models/client.py:37` `notes: Mapped[str | None] = mapped_column(Text)`.
- `app/routers/clients.py`:
  - `create_client` (POST `/clients`, :218) — `Form` `name`, `industry=""`,
    `notes=""`, `domain=""`; `notes.strip() or None`; 303 na detail.
  - `update_client` (POST `/clients/{id}/edit`, :294) — stejná pole +
    `priority`, `daily_run_limit`, `monthly_budget_usd`; přiřazení
    na :321-324, validace scheduler polí :331-362.
  - `new_client_form` (:207), `edit_client_form` (:276), `client_detail` (:249).
- `app/templates/clients/form.html:12-15` — `text_field` name, industry,
  domain, pak `textarea_field('notes', t('client.notes_label'), …, rows=4)`;
  :16-22 scheduler pole jen při editaci.
- `app/templates/clients/detail.html` — hlavička :5-22 (název, test
  badge, export, „View in dashboard", Edit/Delete); metadata jako prostý
  text :26-53 (notes na :52 `whitespace-pre-line`); pak karty
  `{% call card() %}` (retro-verify :55, aliasy :71, tracked entities :97,
  rozvrhy :159, prompt sety :161).
- `app/templates/partials/macros.html:13` `textarea_field(name, label,
  value='', rows=4, required=False)` — **bez `help`/`placeholder`**;
  nevyužitý i18n klíč `client.notes_placeholder` (en/de :250).
- Dashboard: `dashboard.py:264` posílá do Vue islandu
  `clients: [{id, name, domain, tracked_entities_count}]`;
  `dashboard/index.html` — `#dashboard-app` (Vue 3, `[[ ]]` delimitery),
  `selectedClient` computed (:369, export :574), klient jen v selectu
  filtru (:19-20).
- `app/templates/help.html:43-45` — nápověda k polím klienta.
- Klient nemá JSON CRUD API; export nese jen `client_name`.

---

## Design decisions

1. **Vlastní sloupec `clients.vision TEXT NULL`**, ne přejmenování ani
   znovupoužití `notes`. Vision = žádoucí vnímání klienta (vstup pro
   budoucí #8 gap score a #14 Executive Summary), Notes = interní
   poznámky agentury. Jiný účel, jiný čtenář — a jednou jiná viditelnost
   (Vision uvidí i externí klient v portálu #14, Notes nikdy).
2. **Prostý text, žádná struktura.** Žádné odrážky/claimy/váhy — přesně
   rozsah, který roadmapa pro v1 zvolila. Struktura (verzované claimy)
   až s #8, pokud bude potřeba.
3. **Limit 4 000 znaků**, validace v routeru s inline chybou (DE/EN),
   stejný vzor jako ostatní validace formuláře klienta. Limit v kódu jako
   konstanta, ne v DB (`TEXT`), ať jde změnit bez migrace.
4. **Formulář:** Vision **nad** Notes, `rows=5`, s nápovědou („Jak chce
   klient, aby ho AI asistenti popisovali — hodnoty, pozice, klíčová
   sdělení.") a placeholderem s příkladem. `textarea_field` se rozšíří
   o volitelné `help` a `placeholder` (zpětně kompatibilně) — rozšířit
   makro, ne psát markup (`AI_INSTRUCTIONS.md` §3). Notes pak může
   použít existující `client.notes_placeholder`.
5. **Detail klienta:** Vision jako **první karta** pod hlavičkou —
   zvýrazněná levou lištou, nadpis „Vision", `whitespace-pre-line`.
   Prázdná: editor/admin vidí nenápadný odkaz „Doplnit vision" (na edit
   formulář), viewer nic. Notes zůstávají v metadatech beze změny.
6. **Dashboard:** `vision` do `dashboard_init.clients`; pod filtry
   sbalitelný pruh „Vision klienta", jen když je vybraný klient a vision
   má. Výchozí **sbalený** (dashboard je hlavně o číslech); stav
   rozbalení v `localStorage` s `try/catch` (per-viewer pohodlí, ne data).
7. **Oprávnění** stejná jako ostatní pole klienta: editor/admin edituje
   přes formulář, viewer čte (detail i dashboard).
8. **Vision nejde do exportu ani do runů** — nijak neovlivňuje prompty
   ani analýzu. Export ji přidá do souhrnného listu ve vydání 4
   (`docs/TASKS_EXPORT_V2.md` design decision 8).
9. **Migrace** `ADD COLUMN` bez backfillu (existující klienti mají
   `NULL`). Číslo = další volné (dnes `0044`).

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Migrace + model | ✅ |
| T2 | Formulář (makro, router, validace) | ✅ |
| T3 | Detail klienta, dashboard, nápověda | ✅ |
| T4 | Dokumentace + CHANGELOG | ✅ |

---

## T1 — Migrace + model

**Target:** `alembic/versions/00NN_client_vision.py`, `app/models/client.py`

1. Migrace: `op.add_column("clients", sa.Column("vision", sa.Text(),
   nullable=True))`; downgrade `drop_column`. Docstring: proč (ROADMAP #23),
   rozdíl od `notes`, jen přidává (pro `docs/DEPLOYMENT.md` 1.3).
2. Model: `vision: Mapped[str | None] = mapped_column(Text)` + docstring
   atributu (design decisions 1, 2, 8).
3. Lokálně `alembic upgrade head` → `downgrade -1` → `upgrade head`.

**Done when:** migrace projde tam i zpět; `pytest` projde.

**Expected commit:** `feat(clients): add a vision column to clients`

---

## T2 — Formulář

**Target:** `app/templates/partials/macros.html`, `app/routers/clients.py`,
`app/templates/clients/form.html`, `app/i18n/en.json`, `app/i18n/de.json`,
`tests/test_clients.py`

1. `textarea_field(..., help=None, placeholder=None)` — `help` jako
   `<p class="text-xs text-stone-500 mt-1">`, `placeholder` atribut;
   ověřit, že existující volání vypadají stejně.
2. `create_client` / `update_client`: `vision: str = Form("",
   description="How the client wants AI assistants to describe it —
   free text, max 4000 characters.")`; `vision.strip() or None`;
   délka > `VISION_MAX_LENGTH` → inline chyba `errors.client_vision_too_long`
   (stejný mechanismus jako validace scheduler polí), vyplněný formulář
   se vrátí.
3. `form.html`: Vision nad Notes (design decision 4); Notes dostane
   `placeholder=t('client.notes_placeholder')`.
4. i18n DE/EN: `client.vision_label`, `client.vision_help`,
   `client.vision_placeholder`, `errors.client_vision_too_long`.
5. Testy: create s vision → uloženo; edit → změněno; prázdné → `NULL`;
   4 001 znaků → chyba a nic se neuloží; viewer POST → 403 (stávající
   vzor).

**Done when:** testy projdou.

**Expected commit:** `feat(clients): edit client vision in the client form`

---

## T3 — Detail klienta, dashboard, nápověda

**Target:** `app/templates/clients/detail.html`, `app/routers/dashboard.py`,
`app/templates/dashboard/index.html`, `app/templates/help.html`,
`app/i18n/*.json`, `tests/test_clients.py`, `tests/test_dashboard.py`

1. Detail: karta Vision (design decision 5) — `can_edit(current_user)`
   pro odkaz „Doplnit vision".
2. Dashboard: `vision` do dict na `dashboard.py:264`; v islandu
   sbalitelný pruh pod filtry navázaný na `selectedClient` (design
   decision 6), `localStorage` klíč např. `signalmap.dashboard.visionOpen`
   v `try/catch`.
3. `help.html`: řádek pro Vision k polím klienta (i18n DE/EN).
4. Testy: detail s vision vykreslí kartu a text; bez vision editor vidí
   odkaz, viewer ne; `dashboard_init` obsahuje `vision`.
5. Prohlížeč ~375 / ~768 px / desktop: formulář, detail s/bez vision,
   dlouhý víceřádkový text (zalomení), dashboard sbalený/rozbalený a po
   reloadu stav zachovaný; viewer účet. Screenshoty.

**Done when:** testy projdou, screenshoty ukázané uživateli.

**Expected commit:** `feat(clients): show client vision on the detail page and dashboard`

---

## T4 — Dokumentace + CHANGELOG

**Target:** `CHANGELOG.md`, `docs/REQUIREMENTS.md` (sekce o klientovi —
ukázat diff), `docs/ROADMAP.md` #23

1. `CHANGELOG.md` → `## [Unreleased]` → `### Added`:
   - Clients have a Vision field — how the client wants AI assistants to
     describe it — shown at the top of the client page and, collapsible,
     on the dashboard.
2. `docs/REQUIREMENTS.md`: doplnit pole Vision k entitě klienta.
3. `docs/ROADMAP.md` #23 → „implementováno, nasazuje se s vydáním 1".

**Done when:** diff ukázaný uživateli a odsouhlasený. Pak merge do
`master` (s potvrzením uživatele) **bez nasazení**.

**Expected commit:** `docs(docs): document the client vision field`

---

## Ověření po nasazení (provádí se v CH-T7)

1. `/clients/<Knauf>/edit` — pole Vision nad Notes s nápovědou; uložit
   krátký text → detail ukazuje kartu nahoře.
2. Dashboard s vybraným Knaufem — pruh „Vision klienta" sbalený, po
   rozbalení text.
3. Klient bez vision — detail bez karty (editor vidí „Doplnit vision").
4. `select count(*) from clients where vision is not null;` — odpovídá
   tomu, co se vyplnilo.

---

## Co tahle větev vědomě nedělá

- **Žádná struktura** (claimy, váhy, verze) — design decision 2.
- **Vision se nepoužívá v analýze ani v promptech** — napojení je věc
  #8 / #14.
- **Žádný export Vision** — vydání 4.
- **Nemění Notes** kromě placeholderu.
