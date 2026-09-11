# SignalMap — Tasks: Phase 5 (Dashboard extensions + competitive visibility)

## v1.0 | Září 2026
## Branch: feature/signalmap-phase5-competitive-visibility
## Task ID prefix: P5

> Fáze 1 (`docs/TASKS.md`), fáze 2 (`docs/TASKS_PHASE2.md`), production-readiness
> hardening (`docs/TASKS_HARDENING.md`), runs export (`docs/TASKS_EXPORT.md`), fáze 3
> (`docs/TASKS_PHASE3.md`), search query capture (`docs/TASKS_SEARCH_QUERIES.md`) a fáze 4
> (`docs/TASKS_PHASE4.md`) jsou hotové a smergnuté. Tenhle dokument pokrývá tři kusy práce
> bundlované do jedné branch — sólo vývoj, žádný druhý recenzent, takže granularita "jedna
> branch na jeden úkol" z předchozích fází tu není potřeba (viz konverzace, kde bylo
> odsouhlaseno bundlovat, dokud práce nezávisí na tom, že něco předchozího už běží živě v
> provozu — to je jediné tvrdé omezení, co branch nespojí):
>
> 1. **Dashboard: trendové delty** na KPI dlaždicích (P5-T1) — nezávislé.
> 2. **Dashboard: doménová typologie** citovaných domén (P5-T2) — nezávislé, menší schema flag.
> 3. **Konkurenční viditelnost** — sledované entity, share-of-voice, position (P5-T3 až P5-T7)
>    — druhá generace analysis skills, část "2b/2c" z diskuze o generaci 2. Sentiment (2d) a
>    gap/opportunity score zůstávají mimo tuhle branch — sentiment čeká, až tahle práce běží
>    ověřená v provozu (stejný důvod, proč fáze 3 sama odložila sentiment), gap score čeká na
>    reálná konkurenční data, která bez P5-T3–T7 neexistují.
> 4. **Dashboard UX + dokumentace** — odkaz z dashboardu na detail vybraného klienta (P5-T9)
>    a nová sekce "Dashboard" v `/help` vysvětlující metriky a jejich výpočet (P5-T10) —
>    obojí nezávislé, žádná schema změna. P5-T10 dokumentuje jen to, co dnes běží (fáze 4
>    metriky), ne P5-T1/T2/T7 — ty ještě neexistují, psát o nich v živé nápovědě by matlo
>    skutečné uživatele. Čísla T9/T10 jen odrážejí, kdy byly do plánu přidané — obě jdou
>    stavět úplně první, klidně před P5-T1.
>
> **Korekce oproti dřívějšímu plánu:** původně navrhovaný krok "2a — query-level
> transparentnost" (zobrazit skutečné vyhledávací dotazy, co provider vygeneroval) se při
> kontrole kódu ukázal jako **už hotový** — `search_queries` tabulka, mapping v obou
> adaptérech, zobrazení na run detailu i export, všech pět úkolů v
> `docs/TASKS_SEARCH_QUERIES.md` odškrtnutých, pytest 51/51, ověřeno v prohlížeči. Jediné, co
> zbývá, je nesouvisející housekeeping položka ("docs/TASKS.md — nová sekce po smergnutí") —
> triviální jednořádková oprava, ne součást téhle branch. Generace-2 doporučení z konverzace
> tím ztrácí jeden bod, ne restrukturalizaci zbytku.

---

## ⚠️ Schema flagy (AI_INSTRUCTIONS.md §4 — potvrdit zvlášť před P5-T2 a P5-T3)

Tahle branch zavádí **dvě** nové tabulky nad rámec `schema_phase1.sql`. P5-T1 na žádnou z
nich nečeká a jde stavět hned.

### Návrh A: `domain_classifications` (pro P5-T2)

```python
class DomainClassification(Base):
    """Manual editorial classification of a cited domain (Institutional/Editorial/...).

    Keyed by normalized domain, not by citation row — one classification serves every
    citation of that domain across every client, since "iea.org is Institutional" doesn't
    vary per client. Set manually in the UI, never inferred/guessed automatically (same
    caution as sentiment — a wrong auto-classification is worse than an honest "unclassified").
    """

    __tablename__ = "domain_classifications"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)  # normalize_domain() output
    domain_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CHECK constraint, see below
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

`domain_type` hodnoty: `institutional`, `editorial`, `corporate`, `reference`, `ugc`, `other`
— **záměrně bez `competitor`** jako vlastní kategorie. Peec.ai export tuhle kategorii měl, ale
SignalMap po P5-T3 bude mít `tracked_entities.domain` jako nezávislý zdroj pravdy o tom, co je
čí konkurent — druhá, ručně udržovaná kopie stejné informace v `domain_classifications` by byla
přesně ta duplicita, které se `is_own_domain`/`normalize_domain` sdílený helper snaží vyhnout
(fáze 4 design decision 6). "Je tahle doména konkurenční" se odvozuje z `tracked_entities`, ne
z týhle tabulky.

CHECK constraint na `domain_type` (ne Python enum — konzistentní s tím, jak `execution_type` na
`analysis_skills` je taky prostý `VARCHAR` bez DB enum typu).

### Návrh B: `tracked_entities` + `tracked_entity_aliases` (pro P5-T3)

```python
class TrackedEntity(Base):
    """A competitor tracked alongside a client, for share-of-voice/position analysis.

    Deliberately does NOT include a row for the client itself — Client.name/Client.aliases
    (phase 3) already own that identity. Duplicating it here would create two independent
    sources of truth for "what is this client called" (see design decision 1 below).
    """

    __tablename__ = "tracked_entities"
    __table_args__ = (
        Index("uq_tracked_entities_client_id_lower_name", "client_id", text("lower(name)"), unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(200))  # for citation-match, same as Client.domain
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="tracked_entities")
    aliases: Mapped[list["TrackedEntityAlias"]] = relationship(back_populates="entity", cascade="all, delete-orphan")


class TrackedEntityAlias(Base):
    """One alternate name/spelling for a tracked entity — same role as ClientAlias, one level down."""

    __tablename__ = "tracked_entity_aliases"
    __table_args__ = (
        Index("uq_tracked_entity_aliases_entity_id_lower_alias", "tracked_entity_id", text("lower(alias)"), unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_entity_id: Mapped[int] = mapped_column(ForeignKey("tracked_entities.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entity: Mapped["TrackedEntity"] = relationship(back_populates="aliases")
```

Přesná kopie `Client`/`ClientAlias` tvaru z fáze 3, včetně case-insensitive unique indexu
zavedeného až dodatečně u `client_aliases` (migrace `0013`) — tady jde rovnou do prvního
návrhu, ne jako pozdější oprava.

**Alternativa zvážená a zamítnutá:** `tracked_entities.name` jako jediné pole bez samostatné
alias tabulky (v1 minimalismus). Zamítnuto — konzistence vzoru s `client_aliases` je
důležitější než ušetření jedné tabulky, a multi-jazyčné varianty jmen (viz konverzace o
UAE/VAE) bez aliasů by skill dělaly zbytečně slabým hned od začátku.

**Nezačínej P5-T2 ani P5-T3, dokud oba návrhy výslovně nepotvrdíš.**

---

## Design decisions (navrženo před psaním kódu)

1. **Klient sám nemá řádek v `tracked_entities`.** Matching engine za běhu sloučí
   `client.name + client.aliases` (fáze 3) s `tracked_entities` (+ jejich aliasy) do jednoho
   seznamu kandidátů — žádná duplicitní kopie klientovy identity, viz schema flag výše.
2. **Sdílený matching helper, ne druhá kopie regexu.** `_match_spans()`
   (`app/analysis/mention_visibility.py`) dnes už bere obecný `candidates: list[str]`, není
   vázaný na klienta — přesune se do `app/analysis/matching.py` a `mention_visibility.py` i
   nový `competitive_visibility.py` ho budou importovat. Žádná změna chování, jen přesun
   (stejný vzor jako `normalize_domain` přesun ve fázi 4).
3. **Nový skill `competitive_visibility`, `execution_type='rule_based'`, běží nezávisle na
   `mention_visibility`, ne místo něj.** Pro každou entitu (own klient + každý
   `tracked_entities` řádek) se spočítá `mention_count`/`first_position`/`cited` samostatně
   — žádná cross-entity deduplikace překryvů (dvě různé entity mohou legitimně sdílet
   podřetězec zmíněný na různých místech textu, na rozdíl od aliasů JEDNÉ entity, kde
   deduplikace dává smysl).
4. **`share_of_voice` je `null`, ne `0`, když je jmenovatel nulový** (žádná sledovaná entita
   nebyla v odpovědi zmíněná vůbec) — `0/0` je "nedá se zjistit", ne "nulový podíl", stejný
   instinkt jako `own_domain_rate` v dashboardu fáze 4.
5. **`position` je `null`, když vlastní klient zmíněný není** — nedá se řadit něco, co
   v odpovědi chybí. Tiebreak při shodném `first_position` (prakticky téměř nikdy u
   word-boundary matche): abecedně podle jména entity — stejný instinkt jako "deterministic
   league-table ranking" nález z code review fáze 4.
6. **Žádný re-run/backfill mechanismus v týhle fázi** — stejné zdůvodnění jako fáze 3 design
   decision 9 (řeší problém, co dnes ještě nenastal). Existující runy z před touhle branch
   analýzu nedostanou zpětně.
7. **Wiring nemění signaturu `_run_active_analysis_skills`** (`app/routers/runs.py`) — nový
   runner čte `client.tracked_entities` přímo z `client` parametru, co už dostává. Ověřit v
   P5-T6, že lazy-load týhle relationship funguje i po `db.expire_on_commit = False` (stejný
   pattern, co dnešní `analysis_client = prompt.prompt_set.client` řádek už používá).
8. **Krátká jména entit (< 4 znaky) dostanou varování v UI, ne blokaci** (P5-T4) — konkrétně
   kvůli případům jako "UAE"/"VAE" z konverzace: word-boundary regex snižuje riziko kolize,
   ale nemizí, a krátká zkratka je nejčastější zdroj false positive. Levný UI hint, ne fuzzy
   matching.
9. **Doménová typologie (P5-T2) je čistě ruční**, žádná automatická heuristika/klasifikace
   podle TLD nebo obsahu — stejná opatrnost jako u sentimentu, špatná automatická
   klasifikace je horší než upřímné "nezařazeno".
10. **Trendové delty (P5-T1) srovnávají aktuální rozsah s bezprostředně předcházejícím
    stejně dlouhým obdobím**, ne s libovolným pevným referenčním bodem — `30d` se srovná s
    předchozími 30 dny, `quarter` s předchozím kvartálem atd. `None`, když předchozí období
    nemá žádná data (dělení nulou by dalo zavádějící "+∞ %" nebo pád).
11. **Entity league table (P5-T7) čte `analysis_results.output->'entities'` JSONB pole přes
    `jsonb_array_elements`**, ne přes GROUP BY sloupce jako doménová league table (fáze 4) —
    jiný tvar dotazu, protože entity nejsou vlastní sloupec, ale pole uvnitř JSON výstupu.
    Vyžaduje ověření přesné SQLAlchemy syntaxe během implementace (v codebase dosud
    nepoužitý vzor) — flag v P5-T7, ne předpoklad, že funguje na první pokus.

---

## Task Index

| ID | Name | Závislost | Status |
|----|------|-----------|--------|
| P5-T1 | Dashboard: trendové delty na KPI dlaždicích | žádná | ⏳ |
| P5-T2 | Dashboard: doménová typologie | schema potvrzení (Návrh A) | ⏳ |
| P5-T3 | Schema: `tracked_entities`/`tracked_entity_aliases` + seed `competitive_visibility` skillu | schema potvrzení (Návrh B) | ⏳ |
| P5-T4 | UI: správa sledovaných entit na detailu klienta | P5-T3 | ⏳ |
| P5-T5 | Engine: sdílený matching helper + `competitive_visibility` runner | P5-T3 | ⏳ |
| P5-T6 | Wiring: zapojení do `trigger_run` + zobrazení na run detailu | P5-T5 | ⏳ |
| P5-T7 | Dashboard: share-of-voice/position (KPI, league table, time series) | P5-T6 | ⏳ |
| P5-T8 | Testy | P5-T1 až P5-T7 | ⏳ |
| P5-T9 | Dashboard: odkaz na detail klienta | žádná | ⏳ |
| P5-T10 | Help: dashboard metriky a jejich definice | žádná (dokumentuje jen fázi 4) | ⏳ |

P5-T1 a P5-T2 jsou nezávislé na zbytku a na sobě navzájem — jdou v libovolném pořadí, klidně
souběžně. P5-T4 je zařazený před P5-T5 stejně jako ve fázi 3 (P3-T2 před P3-T3) — ruční
ověření enginu v P5-T6 potřebuje mít aspoň jednu entitu zadanou přes UI. P5-T9 a P5-T10 jsou
taky nezávislé, bez jakékoliv schema závislosti — čísla jen odrážejí, kdy byly do plánu
přidané, ne že musí jít poslední; klidně jako první dva prompty v celé branch.

---

## P5-T1 — Dashboard: trendové delty

**Target:** `app/services/dashboard.py`, `app/routers/dashboard.py`, `app/templates/dashboard/index.html`, i18n

1. `app/services/dashboard.py` — nová `previous_range_bounds(date_from, date_to) -> tuple[datetime, datetime] | None`
   — stejně dlouhé období bezprostředně předcházející `date_from`; `None` pro `range="all"`
   (není co srovnávat).
2. `app/routers/dashboard.py` — `DashboardSummary` dostane `runs_count_delta_pct: float | None`,
   `citations_count_delta_pct: float | None`, `own_domain_rate_delta_pct: float | None` (rozdíl
   v procentních bodech u `own_domain_rate`, ne relativní %, protože je to samo procento).
   `dashboard_summary` route zavolá existující `count_runs`/`citation_totals`/`own_domain_rate`
   podruhé nad `run_ids_query` postaveným z `previous_range_bounds()`. `None`, když předchozí
   období nemá žádné runy (dělení nulou).
3. `app/templates/dashboard/index.html` — malý %-change badge vedle každé KPI dlaždice
   (šipka nahoru/dolů + hodnota), skrytý, když je delta `None`.
4. i18n (EN+DE) — `dashboard.trend_no_data` (tooltip/label, když delta chybí).

Po dokončení:
1. `docker compose up -d --build`
2. `/dashboard` na klientovi s runy v aktuálním i předchozím období — delty se zobrazí a
   sedí na ruční přepočet z `/dashboard/api/summary` volaného zvlášť pro obě období.
3. Klient s daty jen v aktuálním období → delta `None`/skrytá, ne zavádějící "+100 %" nebo pád.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): add period-over-period trend deltas to KPI tiles
```

---

## P5-T2 — Dashboard: doménová typologie

**Target:** nová migrace, `app/models/domain_classification.py`, `app/routers/dashboard.py`,
`app/templates/dashboard/index.html`, i18n

Prerekvizita: Návrh A ze schema flagu výslovně potvrzený.

1. Nová migrace — `CREATE TABLE domain_classifications` dle Návrhu A, CHECK constraint na
   povolené `domain_type` hodnoty.
2. `app/models/domain_classification.py` — `DomainClassification` model.
3. `app/routers/dashboard.py` — `DomainRow` dostane `domain_type: str | None`; `dashboard_domains`
   LEFT JOIN na `domain_classifications` (normalizovaná doména).
4. Nový `POST /dashboard/api/domains/{domain}/classify` (Form `domain_type`) — upsert řádku,
   AppError na neplatnou hodnotu.
5. `app/templates/dashboard/index.html` — badge s typem u každé domény v league table;
   nezařazená doména dostane inline select k rychlému zařazení (uloží se přes fetch, bez
   reloadu, stejný Vue vzor jako filtry).
6. i18n — `dashboard.domain_type_{institutional,editorial,corporate,reference,ugc,other}`,
   `dashboard.domain_type_unclassified`.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. `docker compose up -d --build`
3. V league table zařaď doménu přes inline select → badge se zobrazí bez reloadu, hodnota
   přežije refresh stránky (uloženo v DB).
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): add manual domain type classification to the league table
```

---

## P5-T3 — Schema: `tracked_entities`/`tracked_entity_aliases` + seed skillu

**Target:** nová migrace, `app/models/tracked_entity.py`, `app/models/client.py`, `app/models/__init__.py`

Prerekvizita: Návrh B ze schema flagu výslovně potvrzený.

1. Nová migrace — `CREATE TABLE tracked_entities`, `CREATE TABLE tracked_entity_aliases` dle
   Návrhu B (case-insensitive unique indexy od začátku). Seed nového řádku do
   `analysis_skills`: `key='competitive_visibility'`, `execution_type='rule_based'`,
   `output_schema` popisující `entities`/`share_of_voice`/`position` pole (stejný vzor jako
   P3-T1 seed pro `mention_visibility`), `is_active=true`.
2. `app/models/tracked_entity.py` — `TrackedEntity`, `TrackedEntityAlias` modely dle návrhu.
3. `app/models/client.py` — `Client` dostane `tracked_entities: Mapped[list["TrackedEntity"]]`
   relationship (`cascade="all, delete-orphan"`, stejný vzor jako `aliases`).
4. `app/models/__init__.py` — registrace nových modelů.

Po dokončení:
1. `docker compose exec app alembic upgrade head`
2. V DB ověř: `tracked_entities`/`tracked_entity_aliases` existují prázdné;
   `analysis_skills` má nový řádek `competitive_visibility`, `execution_type='rule_based'`,
   `is_active=true`.
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(schema): add tracked_entities/tracked_entity_aliases and seed competitive_visibility skill
```

---

## P5-T4 — UI: správa sledovaných entit

**Target:** `app/routers/clients.py`, `app/templates/clients/detail.html`, i18n

Prerekvizita: P5-T3 hotový.

1. `app/routers/clients.py`:
   - `POST /clients/{client_id}/tracked-entities` (Form `name`, volitelně `domain`) — vytvoří
     `TrackedEntity`. Duplicitní jméno → strukturovaná `AppError`, ne 500.
   - `POST /clients/{client_id}/tracked-entities/{entity_id}/delete`.
   - `POST /clients/{client_id}/tracked-entities/{entity_id}/aliases` (Form `alias`) — mirror
     existující alias endpointy klienta, jen o úroveň níž.
   - `POST /clients/{client_id}/tracked-entities/{entity_id}/aliases/{alias_id}/delete`.
2. `app/templates/clients/detail.html` — nová sekce "Tracked entities", mirror vizuálního
   vzoru existující sekce "Aliases": řádek na entitu (jméno, doména, delete), rozbalitelný
   mini-seznam aliasů pod každou entitou s vlastním add/delete. Jméno kratší než 4 znaky →
   inline varování (design decision 8), neblokuje uložení.
3. i18n (EN+DE) — `client.tracked_entities_title`, `client.tracked_entity_add_placeholder`,
   `client.tracked_entity_domain_placeholder`, `client.tracked_entity_delete_confirm`,
   `client.tracked_entity_short_name_warning`, `errors.tracked_entity_duplicate`,
   `errors.tracked_entity_not_found`.

Po dokončení:
1. `docker compose up -d --build`
2. Na existujícím klientovi přidej 2-3 konkurenty s doménou, přidej/smaž pár aliasů u
   jednoho z nich → seznam se aktualizuje bez chyby. Zkus duplicitní jméno → strukturovaná
   chyba. Zkus krátké jméno (< 4 znaky) → varování se zobrazí, uložení projde.
3. Ověř na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(clients): add tracked entity management for competitive visibility matching
```

---

## P5-T5 — Engine: sdílený matching helper + `competitive_visibility` runner

**Target:** nový `app/analysis/matching.py`, `app/analysis/mention_visibility.py`, nový
`app/analysis/competitive_visibility.py`, `app/analysis/__init__.py`

Prerekvizita: P5-T3 hotový (P5-T4 doporučeno pro ruční ověření, ne blokující).

1. Nový `app/analysis/matching.py` — přesuň `_match_spans()` z `mention_visibility.py` sem
   jako veřejnou `match_spans(rendered_text, candidates)`, beze změny chování. Docstring
   vysvětlující sdílení mezi `mention_visibility` a `competitive_visibility`.
2. `app/analysis/mention_visibility.py` — importuje `match_spans` z nového modulu místo
   lokální definice.
3. Nový `app/analysis/competitive_visibility.py` — `CompetitiveVisibilityRunner`:
   - Sestav seznam entit: `[(is_own=True, "<client name>", [client.name] + aliasy klienta)]
     + [(is_own=False, entity.name, [entity.name] + jeho aliasy) for entity in
     client.tracked_entities]`.
   - Pro každou entitu zavolej `match_spans()` nad jejím vlastním seznamem kandidátů —
     nezávisle na ostatních entitách (design decision 3).
   - `cited`/`cited_domains` per entitu stejnou logikou jako `mention_visibility`
     (`is_own_domain` z `app/utils.py`), nad `entity.domain` místo `client.domain`.
   - `share_of_voice` a `position` se počítají jen pro `is_own` entitu, dle design decision
     4-5 (null když nemá smysl, tiebreak abecedně).
   - Vrať dict přesně podle `output_schema` seedovaného v P5-T3.
4. `app/analysis/__init__.py` — přidej `"competitive_visibility": CompetitiveVisibilityRunner`
   do `ANALYSIS_SKILLS`.

Po dokončení (jednotkově, bez UI):
1. Krátký ad-hoc skript v kontejneru: zavolej `CompetitiveVisibilityRunner().run(...)` s
   ručně sestaveným textem a `Client`/`TrackedEntity` instancemi v paměti — ověř
   `share_of_voice`/`position` na pár ručně vymyšlených případech (klient zmíněný první,
   klient zmíněný poslední, klient nezmíněný vůbec, žádná entita zmíněná).
2. `pytest tests/test_analysis_mention_visibility.py` — musí zůstat zelené beze změny
   (přesun `match_spans` nesmí změnit chování).
3. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(analysis): add competitive_visibility skill with shared matching helper
```

---

## P5-T6 — Wiring: zapojení do `trigger_run` + zobrazení na run detailu

**Target:** `app/routers/runs.py`, `app/templates/runs/detail.html`, i18n

Prerekvizita: P5-T5 hotový.

1. `app/routers/runs.py` — `_run_active_analysis_skills` nepotřebuje změnu signatury (design
   decision 7) — registr už `competitive_visibility` najde a spustí automaticky. Ověř, že
   `client.tracked_entities` je čitelné v místě volání i po `db.expire_on_commit = False`
   (spike/manuální ověření, ne předpoklad).
2. `app/templates/runs/detail.html` — nová podsekce v "Analysis" (vedle existujícího
   `mention_visibility` výstupu): tabulka entit tohoto runu (jméno, own/competitor badge,
   zmíněn, počet, pozice) + dvě čísla "Share of voice"/"Position". Prázdný stav, když klient
   nemá žádné `tracked_entities` (skill vrátí jen vlastní klienta, `share_of_voice=null`) —
   zobraz vysvětlující text, ne 0 %.
3. i18n (EN+DE) — `analysis.competitive_section_title`, `analysis.share_of_voice_label`,
   `analysis.position_label`, `analysis.no_tracked_entities`.

Po dokončení:
1. `docker compose up -d --build`
2. Na klientovi s aspoň 2 sledovanými entitami spusť run, kde je v odpovědi zmíněný klient i
   konkurent → run detail ukazuje správnou tabulku, share of voice a position.
3. Klient bez `tracked_entities` → sekce ukazuje vysvětlující prázdný stav, ne chybu/nulu.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(runs): compute and display competitive visibility after each run
```

---

## P5-T7 — Dashboard: share-of-voice/position

**Target:** `app/services/dashboard.py`, `app/routers/dashboard.py`, `app/templates/dashboard/index.html`, i18n

Prerekvizita: P5-T6 hotový a ověřený na reálných datech (potřeba aspoň pár
`competitive_visibility` výsledků v DB, na kterých se dá dotaz vyzkoušet).

1. `app/services/dashboard.py`:
   - Nová `entity_league_rows(db, client, run_ids_query)` — čte `analysis_results.output`
     pro `competitive_visibility` skill, `jsonb_array_elements` na `entities` pole (design
     decision 11 — ověř přesnou SQLAlchemy syntaxi, v codebase zatím nepoužitý vzor).
     Agreguje per entitu: průměrný `mention_count`, průměrná `position` (jen z runů, kde
     entita byla zmíněná), `run_coverage_pct`.
   - `DashboardMetric` Literal rozšířen o `"share_of_voice"`, `"position"`; `weekly_values`
     dostane větev pro oba, čte `output->>'share_of_voice'`/`output->>'position'` z
     `competitive_visibility` výsledků (mirror `_mention_visibility_base_query`, nová
     `_competitive_visibility_base_query`).
2. `app/routers/dashboard.py` — nový `GET /dashboard/api/entities` (mirror `/api/domains`),
   nová `EntityRow` Pydantic model. `dashboard_summary` dostane `share_of_voice: float | None`
   KPI pole.
3. `app/templates/dashboard/index.html` — nová KPI dlaždice "Share of voice", nová
   "Competitive league table" sekce (mirror domain league table), metric toggle na grafu
   rozšířený o obě nové hodnoty.
4. i18n — `dashboard.kpi_share_of_voice`, `dashboard.competitive_table_title`,
   `dashboard.metric_share_of_voice`, `dashboard.metric_position`.

Po dokončení:
1. `docker compose up -d --build`
2. `/dashboard` na klientovi s několika runy analyzovanými `competitive_visibility` — KPI,
   league table i graf ukazují reálná čísla, sedí na ruční přepočet z jednotlivých run
   detailů (P5-T6).
3. Klient bez `tracked_entities` → sekce ukazuje prázdný stav, ne 0 %/pád dotazu.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): add share-of-voice and position KPIs, league table, and chart metric
```

---

## P5-T8 — Testy

**Target:** `tests/test_tracked_entities.py`, `tests/test_analysis_competitive_visibility.py`,
`tests/test_dashboard.py` (rozšíření), `tests/test_runs.py` (rozšíření)

Prerekvizita: P5-T1 až P5-T7 hotové.

1. `tests/test_tracked_entities.py` — CRUD entit/aliasů, duplicitní jméno → strukturovaná
   chyba, krátké jméno neblokuje uložení.
2. `tests/test_analysis_competitive_visibility.py` — přímé jednotkové testy runneru: žádná
   entita zmíněná (`share_of_voice=None`), klient zmíněný první/poslední, klient nezmíněný
   (`position=None`), tiebreak při shodném `first_position`, `cited`/`cited_domains` per
   entitu.
3. `tests/test_dashboard.py` — trendové delty sedí na ruční přepočet fixture dat, `None` bez
   předchozích dat; doménová typologie se vrací v `/api/domains`; entity league table a
   share-of-voice/position timeseries sedí na fixture data.
4. `tests/test_runs.py` — po úspěšném runu existuje `AnalysisResult` i pro
   `competitive_visibility`, ne jen `mention_visibility`.

Po dokončení:
1. `pytest` — všechny testy zelené (staré i nové).
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
test: cover tracked entities, competitive visibility skill, and dashboard extensions
```

---

## P5-T9 — Dashboard: odkaz na detail klienta

**Target:** `app/templates/dashboard/index.html`, i18n

Žádná závislost — jde jako jeden z prvních promptů v branch.

1. `app/templates/dashboard/index.html` — malý odkaz/tlačítko vedle KPI dlaždic (nebo u
   filtru klienta), vedoucí na `/clients/{{ filters.client_id }}` vybraného klienta — čistě
   klientská Vue vazba (`:href` postavený z `filters.client_id`, co Vue island už drží),
   žádná změna backendu (`dashboard_init` už dnes posílá `id`/`name`/`domain` pro každého
   klienta).
2. i18n (EN+DE, jeden commit) — `dashboard.view_client_link`.

Po dokončení:
1. `docker compose up -d --build`
2. `/dashboard` na libovolném klientovi → klik na nový odkaz → přesměruje na
   `/clients/{id}` se správným klientem.
3. Přepnutí klienta ve filtru → odkaz se aktualizuje na nově vybraného klienta bez plného
   reloadu stránky.
4. Ověř na ~640px/~1024px/desktop šířce.
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(dashboard): add link to the selected client's detail page
```

---

## P5-T10 — Help: dashboard metriky a jejich definice

**Target:** `app/templates/help.html`, i18n

Žádná závislost — dokumentuje jen to, co dnes běží (fáze 4 dashboard), ne nic z P5-T1 až
P5-T9. Jde jako jeden z prvních promptů v branch.

1. `app/templates/help.html` — nová `<section>` "Dashboard" mezi existující sekcí "Results"
   a "Analysis" (mirror struktury sekcí "Clients"/"Prompts" — úvodní odstavec + tabulka
   pojem/popis, reuse existujícího vizuálního vzoru, žádná nová komponenta):
   - Tabulka KPI dlaždic: Runs, Citations, Distinct domains, Own-domain citation rate (vč.
     vysvětlení, že "—" znamená chybějící `domain` u klienta, ne 0 %).
   - Tabulka sloupců league table: rank, domain, citations + share bar, "cited in %",
     first/last seen, own-domain badge.
   - Callout box (stejný vzor jako sekce "Prompt sets") vysvětlující chování týdenního
     grafu — tři přepínatelné metriky, týdny bez dat mají hodnotu 0, ne vynechaný bod.
   - Odkaz na `/dashboard` (stejný vzor jako odkazy v sekcích "Providers"/"Settings").
2. i18n (EN+DE, jeden commit) — `help.dashboard.title`, `help.dashboard.body`,
   `help.dashboard.kpi_runs`/`kpi_citations`/`kpi_domains`/`kpi_own_rate`,
   `help.dashboard.table_rank`/`domain`/`citations`/`cited_in`/`first_seen`/`last_seen`/
   `own_domain_badge`, `help.dashboard.chart_note`.
3. **Rozsah záměrně jen fáze 4.** Nepiš sem trendové delty (P5-T1), doménovou typologii
   (P5-T2) ani share-of-voice/position (P5-T7) — ty ještě neběží, psát o nich v živé in-app
   nápovědě by matlo skutečné uživatele appky. Až tyhle úkoly proběhnou, každý z nich by měl
   dostat svůj vlastní krok "rozšiř /help sekci Dashboard o [metrika]" — netřeba to teď
   předepisovat do detailu, stačí na to při psaní daného tasku nezapomenout.

Po dokončení:
1. `docker compose up -d --build`
2. `/help` — nová sekce "Dashboard" se zobrazuje na správném místě, obsah sedí na to, co
   `/dashboard` skutečně zobrazuje (žádný popis metriky, co ještě neexistuje).
3. Ověř na ~640px/~1024px/desktop šířce.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(help): document dashboard metrics and how they're calculated
```

---

## Completion Checklist

- [x] Schema návrhy (domain_classifications, tracked_entities/tracked_entity_aliases)
      výslovně potvrzené před P5-T2/P5-T3
- [x] Trendové delty fungují na KPI dlaždicích, `None` místo zavádějících hodnot bez dat
- [x] Doménová typologie — ruční klasifikace v league table, žádná automatická heuristika
- [x] `tracked_entities`/`tracked_entity_aliases` existují a jdou spravovat přes UI
- [x] `competitive_visibility` skill počítá share-of-voice/position automaticky po každém
      úspěšném runu, viditelné na run detailu bez ruční DB inspekce
- [x] Sdílený `match_spans` helper — žádná duplicitní regex logika mezi `mention_visibility`
      a `competitive_visibility`
- [x] Dashboard rozšířený o share-of-voice/position KPI, competitive league table, chart metriku
- [x] `pytest` sada zelená (106/106), pokrývá entity CRUD, skill logiku i dashboard rozšíření
- [x] Ověřeno v prohlížeči na ~375px/~768px/desktop, všechny nové obrazovky/sekce
- [x] Dashboard má odkaz na detail vybraného klienta (P5-T9)
- [x] `/help` má sekci "Dashboard" — **rozšířeno oproti původnímu zadání**: P5-10 šel v branch
      jako poslední, ne první, takže appka v době psaní sekce už reálně měla i P5-1/P5-2/P5-7
      (trendové delty, doménovou typologii, share-of-voice/position) — na výslovnou žádost
      uživatele sekce popisuje plný aktuální stav, ne jen fázi 4, jak zněl původní plán
- [x] `docs/TASKS.md` — poznámka, že fáze 5 větev existuje a co pokrývá (odkaz na tenhle soubor)
- [x] `docs/REQUIREMENTS.md` — amendment přidán (2026-09-11); nic ze stávajícího seznamu
      "Explicitly Out of Scope" se neruší, jen se zaznamenává nová schopnost pro konzistenci
      s tím, jak to dostaly fáze 2-4
