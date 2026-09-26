# SignalMap — Tasks: Dev DB Refresh

## Status: ✅ Done — PR #22, merged 2026-09-26, release pending

## v1.0 | Září 2026
## Branch: feature/signalmap-dev-db-refresh
## Task ID prefix: DR

Status: navrženo v konverzaci 2026-09-25. Malá, uzavřená práce: čtyři úkoly,
jen lokální nástroj, žádná změna aplikace ani schématu.

Vychází z existujícího řetězce záloh (`README.md`, sekce „Automated backups“).
`pull_backup.py` stahuje produkční dumpy na tenhle PC. `restore_local.py`
je umí obnovit, ale jen do kontrolní databáze `signalmap_restore_check`, protože
ověřuje obnovitelnost zálohy. Pracovat lokálně s produkčními daty zatím
znamená ruční `pg_restore` do `signalmap`. Při tom se dev data ztratí bez zálohy
a lokální worker převezme produkční rozvrhy.

**Goal:** jedním příkazem nahrát do lokální dev databáze poslední (nebo
zvolený) produkční dump. Před tím se dev databáze zazálohuje a obnovená data
nesmí nic spustit, tedy žádné volání providera a žádné útraty. Stejně
snadno jde vrátit libovolnou dev zálohu.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Nový skript `tools/local/refresh_dev_db.py`, ne rozšíření
   `restore_local.py`.** `restore_local.py` ověřuje, že záloha je obnovitelná,
   a úmyslně nesahá na pracovní databázi. Refresh dev databáze dělá opak,
   tedy přepisuje pracovní databázi. Dvě různé odpovědnosti patří do dvou
   skriptů. Společné pomocné funkce (`compose()`, `psql()`, `DUMP_RE`, výběr
   dumpu) se vytáhnou do sdíleného modulu (T1), aby se nekopírovaly.
2. **Jen standardní knihovna, jako zbytek `tools/local/`.** Skript běží na
   hostiteli (Windows), ne v kontejneru. Nepotřebuje venv ani requirements.
   Postgres klienta volá přes `docker compose exec postgres`.
3. **CLI přes podpříkazy (styl `git` / `docker`):**

   ```
   refresh_dev_db.py                           # = refresh s nejnovějším dumpem
   refresh_dev_db.py refresh [DUMP] [--no-pull]
   refresh_dev_db.py restore-dev [SNAPSHOT]
   refresh_dev_db.py snapshot
   refresh_dev_db.py list
   ```
   Společné přepínače: `--yes` (bez potvrzení) a `--plan` (jen vypsat, co by
   se stalo). Každá operace má vlastní jméno. Nevzniká kombinace přepínačů,
   kde některé kombinace nedávají smysl.
4. **`--plan`, ne `--dry-run`.** `--dry-run` je u CLI běžný název, ale tady by
   se pletl se `SCHEDULER_DRY_RUN`, kolem kterého se celý skript točí.
5. **Nejdřív plán, pak potvrzení `[y/N]`.** Každý příkaz, který přepisuje
   databázi, vypíše zdroj, cíl, cestu k záloze a výsledky kontrol a čeká na
   odpověď. Výchozí odpověď je „ne“. Spuštění bez parametrů je pohodlné, ale
   bez potvrzení nikdy nic nepřepíše. `--yes` potvrzení přeskočí. Když stdin
   není terminál a `--yes` chybí, skript skončí chybou a nečeká.
6. **Každý přepis nejdřív zálohuje, bez výjimky.** Platí pro `refresh` i
   `restore-dev`. Když se přepisuje prod kopie, na které jsi něco zkoušel,
   zazálohuje se taky. Není pak potřeba přemýšlet, kdy se data ztratí.
7. **Dev zálohy jsou v samostatné složce se samostatným tvarem jména.**
   Výchozí složka je `C:\Backups\SignalMap\dev-snapshots\` (proměnná
   `SIGNALMAP_DEV_SNAPSHOT_DIR`), jméno
   `signalmap-local-YYYYMMDD-HHMMSS-<původ>.dump`. `DUMP_RE` z
   `pull_backup.py` na tenhle tvar nesedí, takže se nepletou s retencí ani s
   výběrem „nejnovějšího“ prod dumpu. Retence: posledních 10
   (`SIGNALMAP_DEV_SNAPSHOT_KEEP`). Maže jen soubory, které odpovídají vlastnímu
   regexu. Cizí soubory ve složce nechává být, stejně jako `pull_backup.py`.
8. **Původ dat se zapisuje do databáze jako komentář.** Po refreshi
   `COMMENT ON DATABASE signalmap IS 'prod:signalmap-20260925-031500'`, po
   restore-dev původ zálohy. Z komentáře se skládá `<původ>` ve jménu zálohy
   (`dev`, nebo `prod-20260925`). Bez komentáře je původ `dev`. `pg_dump -Fc`
   bez `--create` komentář databáze nenese, proto ho skript zapisuje sám.
   Nepotřebuje žádnou tabulku navíc.
9. **Obnova jde do dočasné databáze, pak se přejmenuje.** `pg_restore` míří do
   `signalmap_incoming` (případný pozůstatek z minula se nejdřív smaže). Pak
   se zkontrolují počty řádků a teprve potom proběhne `DROP DATABASE signalmap`
   + `ALTER DATABASE signalmap_incoming RENAME TO signalmap`. Když obnova
   selže, dev databáze zůstane nedotčená. To je výhoda oproti
   `pg_restore --clean` přímo do cíle.
10. **Pojistky proti spuštění rozvrhů jsou tři, každá by stačila sama.**
    a) **Kontrola před startem:** hodnota `SCHEDULER_DRY_RUN`, kterou worker
       skutečně dostane, se čte z `docker compose config --format json`
       (`services.worker.environment`), ne z textu `.env`. Tak se počítá i
       proměnná ze shellu i výchozí hodnota v Compose. Když není `true`, skript
       skončí **dřív, než cokoli zastaví nebo smaže**.
    b) **Úprava obnovených dat** (jedna transakce, před startem aplikace):
       všechny `run_schedules` dostanou `is_active=false`, `inactive_reason='user'`.
       Nevyřízené položky `run_queue` (`queued`, `leased`, `deferred`) se
       nastaví na `cancelled`. Ostatní úpravy viz rozhodnutí 12.
    c) **Kontrola po startu:** skript počká na první heartbeat lokálního
       workeru a ověří `worker_heartbeats.dry_run = true`, tedy hodnotu, kterou
       si běžící proces opravdu načetl. Když nesedí, worker hned zastaví a skončí
       chybou.
11. **Skript `.env` nikdy nepřepisuje.** Jen odmítne pokračovat a napíše, co
    změnit. `.env` je soubor s tajnými údaji, který patří uživateli. Změna, o
    které by nevěděl, by ho později překvapila.
12. **`inactive_reason='user'`, ne nová hodnota.** UI (`schedules/index.html`,
    `routers/schedules.py`) zná jen `completed`, `user` a `owner_deactivated`.
    `user` znamená „ručně vypnuto“, UI ho umí zobrazit a rozvrh jde v UI zase
    zapnout. Vlastní hodnota `dev_refresh` by potřebovala i18n klíče a úpravu
    šablony kvůli čistě lokální věci. Dále se smažou produkční řádky ve
    `worker_heartbeats` a nevyřízené řádky v `notification_outbox` se převedou
    do koncového stavu (povolené hodnoty agent ověří v migraci). Až bude
    existovat SMTP kanál, lokální kopie nesmí rozesílat e-maily skutečným
    uživatelům.
    **Běhy `runs` se stavem `pending`** (v produkci přerušené v okamžiku dumpu)
    se nemění, protože jde o evidenci (AI_INSTRUCTIONS §3). Skript jen vypíše
    jejich počet.
13. **Kontrola, že skript běží lokálně.** Skript skončí, pokud výsledná
    konfigurace Compose nemá `ENVIRONMENT=development` a porty svázané na
    `127.0.0.1`. Nikdy nesmí běžet proti serveru.
14. **Před přejmenováním se zastaví `app` a `worker`.** Přejmenovat ani smazat
    databázi nejde, dokud na ni někdo je připojený. Zbylá spojení (např. DBeaver)
    skript vypíše v plánu a pak je ukončí přes `pg_terminate_backend`.
15. **Migrace po obnově se nespouštějí zvlášť.** `app` při startu sám spustí
    `alembic upgrade head` a doplní migrace, které má větev navíc proti
    produkci. Když je naopak produkce *novější* než lokální kód (revize v
    `alembic_version` není v `alembic/versions/`), skript to zjistí **před**
    přepsáním a skončí s vysvětlením, že je potřeba přejít na aktuální master.
16. **Po obnově se znovu založí dev admin.** Skript spustí
    `docker compose exec app python -m scripts.create_admin --from-env`, který
    je idempotentní. Produkční dump obsahuje produkční uživatele, takže bez
    tohoto kroku by se nešlo přihlásit dev účtem.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Sdílené pomocné funkce pro `tools/local/` | ✅ |
| T2 | `refresh_dev_db.py`: CLI, `list`, `snapshot`, kontroly, plán | ✅ |
| T3 | `refresh` a `restore-dev`: výměna databáze, úprava dat, restart | ✅ |
| T4 | Dokumentace: README, CHANGELOG | ✅ |

---

## T1 — Sdílené pomocné funkce

**Target:** nový `tools/local/_dbtools.py`, úprava `tools/local/restore_local.py`
(a `tools/local/pull_backup.py`, pokud z něj něco sdílí)

1. Přesunout z `restore_local.py` do `_dbtools.py`: `PROJECT_DIR`, `DEST`,
   `DB_USER`, `DUMP_RE`, `compose()`, `psql()`, `newest_dump()`, `COUNT_TABLES`.
   Skripty se spouštějí jako `python tools/local/x.py`, takže jejich složka je
   na `sys.path` a stačí obyčejný `import _dbtools`.
2. `DUMP_RE` je dnes definovaný dvakrát (v `pull_backup.py` s capture groups, v
   `restore_local.py` bez nich). Sjednotit na jednu definici **jen pokud to
   nezmění chování** `pull_backup.py`, který běží každý den v Task Scheduleru.
   Jinak ho nechat být a zapsat proč.
   **Výsledek:** `pull_backup.py` zůstal beze změny. `_dbtools.DUMP_RE` má
   capture groups a sedí na přesně stejná jména (ověřeno na všech souborech v
   `dumps\`), takže by sjednocení chování neměnilo. Import ze `_dbtools` by ale
   byl nový způsob, jak skript bez dozoru selže (chybějící nebo rozbitý modul,
   jiný `sys.path`), a projevil by se jen chybějícími zálohami. Způsob spouštění
   v Task Scheduleru se navíc z uživatelského účtu nepodařilo ověřit. `DEST`,
   `DUMP_RE` a `PGDMP_MAGIC`/`MIN_SIZE` jsou proto úmyslně zdvojené, s
   poznámkou v hlavičce `_dbtools.py`.
3. Refaktor, **žádná změna chování.**

**Done when:** `python tools/local/restore_local.py` doběhne stejně jako před
změnou (obnoví nejnovější dump do kontrolní databáze, vypíše počty, smaže ji)
a `python tools/local/pull_backup.py` doběhne s kódem 0 nebo 2 jako dřív.

**Expected commit:** `refactor(tools): share local db helpers between scripts`

---

## T2 — CLI, `list`, `snapshot`, kontroly, plán

**Target:** nový `tools/local/refresh_dev_db.py`

1. `argparse` s podpříkazy podle rozhodnutí 3. Bez podpříkazu se chová jako
   `refresh`.
2. **`list`:** dvě tabulky, prod dumpy (`DEST`) a dev zálohy (rozhodnutí 7),
   s datem, velikostí a u záloh s původem. Označí nejnovější. Nic nemění a
   nespouští kontroly.
3. **`snapshot`:** `pg_dump -Fc` databáze `signalmap` do složky dev záloh
   (zápis přes `.tmp` + přejmenování po kontrole hlavičky `PGDMP`, stejně jako
   `pull_backup.py`), pak retence. Nic nepřepisuje, takže nepotřebuje kontrolu
   dry-run ani potvrzení.
4. **Výběr souboru (`DUMP` / `SNAPSHOT`):** prázdné = nejnovější. Jinak shoda
   se začátkem časového razítka (`20260920`, `20260925-1030`). Víc shod →
   vypsat je a skončit, nehádat. Existující cesta k souboru jinde na disku →
   použít ji, pokud má hlavičku `PGDMP`.
5. **Kontroly** (rozhodnutí 10a, 13, 15) jako samostatné funkce vracející
   seznam problémů, aby je `--plan` uměl vypsat všechny najednou, ne jen první.
6. **Plán + potvrzení** (rozhodnutí 5): výpis zdroje, cíle, cesty k záloze,
   výsledků kontrol a otevřených spojení k databázi.

**Done when:**
- `list` vypíše obě složky.
- `snapshot` vytvoří obnovitelnou zálohu (ověřit
  `restore_local.py --dump` s plnou cestou nebo ručním `pg_restore --list`).
- `refresh --plan` se `SCHEDULER_DRY_RUN=false` v `.env` vypíše chybu kontroly
  a skončí nenulovým kódem. Se `true` vypíše plán a skončí s 0, **bez jakékoli
  změny** (dev databáze i kontejnery stejné jako předtím).

**Expected commit:** `feat(tools): add dev db refresh CLI with safety preflight`

---

## T3 — `refresh` a `restore-dev`

**Target:** `tools/local/refresh_dev_db.py`

Pořadí kroků je závazné, protože na něm stojí bezpečnost:

1. Kontroly (T2). Při jakémkoli problému konec, **nic se nezměnilo**.
2. `refresh` bez `--no-pull` a bez `DUMP`: spustit `pull_backup.py` (jako
   podproces, jeho exit kód 2, tedy zastaralá záloha na serveru, je jen varování).
3. Plán + potvrzení.
4. Záloha aktuální dev databáze (logika z `snapshot`). Když selže, konec.
5. `docker compose stop app worker`, ukončit zbylá spojení (rozhodnutí 14).
6. `DROP DATABASE IF EXISTS signalmap_incoming`, `CREATE DATABASE`,
   `pg_restore --exit-on-error --no-owner --no-privileges` do ní.
7. Kontrola obsahu (`COUNT_TABLES`, nesmí být prázdné) a úprava dat
   (rozhodnutí 10b + 12) v jedné transakci **v `signalmap_incoming`**, tedy před
   výměnou, takže `signalmap` nikdy neobsahuje neupravená prod data.
   U `restore-dev` se úprava dělá taky, protože dev záloha může pocházet z prod
   kopie.
8. Výměna (rozhodnutí 9), `COMMENT ON DATABASE` (rozhodnutí 8).
9. Když cokoli v krocích 5–8 selže: `signalmap` zůstává původní (výměna se
   nestihla), `signalmap_incoming` se uklidí a skript vypíše cestu k záloze z
   kroku 4. Kontejnery se **nespouštějí** automaticky, uživatel má vidět, že
   něco selhalo.
10. `docker compose up -d --wait app worker` s `SCHEDULER_DRY_RUN=true`
    v prostředí podprocesu (druhá pojistka, rozhodnutí 10).
11. `create_admin --from-env` (rozhodnutí 16).
12. Kontrola heartbeatu (rozhodnutí 10c), timeout ~60 s.
13. Závěrečný souhrn: odkud, počty řádků, cesta k záloze, kolik rozvrhů se
    vypnulo, kolik položek fronty se zrušilo, kolik `pending` běhů zůstalo.

**Ověření** (fixní testovací data: Skoda Auto, prompt 54,
gemini-3.1-flash-lite, pro kontrolu, že data dorazila):
- `refresh` s nejnovějším dumpem: v UI jsou produkční klienti, všechny
  rozvrhy vypnuté, stránka rozvrhů ukazuje worker v dry-run, přihlášení dev
  adminem funguje.
- `restore-dev` vrátí dev databázi do stavu před refreshem (porovnat počty
  řádků ze souhrnu).
- Simulovaná chyba (např. poškozený dump, `--dump` na zkrácený soubor): dev
  databáze zůstane beze změny a `signalmap_incoming` neexistuje.

**Done when:** všechna tři ověření projdou a lokální worker po refreshi
nevytvořil žádný záznam v `runs`.

**Expected commit:** `feat(tools): refresh the local dev db from a prod dump`

---

## T4 — Dokumentace

**Target:** `README.md` (sekce „Backup, restore, or transfer local data“),
`CHANGELOG.md`

1. README: krátký odstavec „Refresh the local dev database from production“
   s příklady podpříkazů, tři pojistky jednou větou a upozornění, že lokální
   `.env` musí mít `SCHEDULER_DRY_RUN=true`, jinak skript odmítne běžet.
2. Odstavec o `restore_local.py` doplnit o větu, čím se liší od
   `refresh_dev_db.py` (ověření zálohy × práce s daty).
3. `CHANGELOG.md`: jeden bod pod `## [Unreleased]`.
4. Nový dokumentační soubor se nezakládá (AI_INSTRUCTIONS §4).

**Done when:** README popisuje oba skripty a čtenář pozná, který použít.

**Expected commit:** `docs(docs): document the dev db refresh script`

---

## Co tahle větev vědomě nedělá

- **Neanonymizuje osobní údaje.** Produkční dump obsahuje e-maily a hashe
  hesel uživatelů. U interního nástroje s pár uživateli to řeší to, že
  dumpy zůstávají jen na tomhle PC (`C:\Backups\SignalMap`). Kdyby se lokální
  kopie měla dostat k dalším lidem, patří přepis e-mailů do rozhodnutí 12.
- **Nesahá na `.env`** (rozhodnutí 11). Přepnutí lokálního
  `SCHEDULER_DRY_RUN` na `true` je ruční krok uživatele.
- **Neřeší oddělené API klíče pro dev.** Doporučení (samostatné dev klíče s
  nízkým limitem útrat u providera) je mimo repo. Pojistky výše fungují i bez
  toho.
- **Nemění `pull_backup.py` ani `restore_local.py` funkčně.** T1 je čistý
  refaktor.
- **Neplánuje refresh automaticky.** Spouští se ručně, když jsou produkční
  data potřeba.
