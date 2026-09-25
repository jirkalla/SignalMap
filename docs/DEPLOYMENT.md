# SignalMap — Deployment runbook

## v1.2 | Září 2026

Odškrtávací seznam pro nasazení nové verze na produkční server. U každého
kroku: **příkaz → co dělá a proč → čekaný výstup.** README popisuje nasazení
v próze pro toho, kdo projekt nezná; tenhle dokument je postup, podle kterého
se nasazuje.

**Produkce:** `https://expressyourself.ai` · `2.29.23.252` · `/opt/signalmap`
· Compose projekt `signalmap` · Postgres 18 ve svazku `signalmap_pgdata`

**Pravidlo, které platí u každého kroku:** když krok selže, **zastav se a
nepokračuj dalším**. Nasazení je postupné odemykání, ne seznam přání.

**Lokálně vs. na serveru:** příkazy s `ssh signalmap '...'` spouštěj ze svého
počítače. `git archive`/`scp` musí jít z počítače vždy — čtou lokální soubory.
Zbytek jde spustit i přímo v otevřené SSH session na serveru (bez `ssh`
obalu) — ale **vždy jako jeden řetězený příkaz přesně tak, jak je napsaný
níže**, nikdy rozepsaný na samostatné řádky (viz varování u kroku 3.3).

---

## Rychlý přehled

```
0. VERZE              bump podle MAJOR/MINOR/PATCH, tag, na masteru
1. PŘÍPRAVA          den předem, bez dopadu na provoz
2. TĚSNĚ PŘED         odstávka, stop workeru, záloha, výchozí čísla
3. NASAZENÍ           kód, skripty, migrace, restart (bez workeru)
4. OVĚŘENÍ            interní kontrola, start workeru, odstávka pryč, health, UI
5. UZAVŘENÍ           sledování, zápis, zpráva
6. ROLLBACK           jen když je potřeba
```

Odhad: příprava 15 minut den předem, samotné okno 15–20 minut, z toho
výpadek 2–5 minut.

---

## 0. Rozhodnout verzi

Release = merge do `master` + jeden deploy. Před bumpem rozhodni podle
tabulky, ne podle pocitu — SignalMap nemá veřejné API, takže „breaking"
se definuje proti tomu, kdo nasazuje, a proti uloženým datům, ne proti
klasickému SemVer významu:

| Díl | Kdy | Konkrétně |
|---|---|---|
| **MAJOR** | nasazení není „jen deploy", nebo se mění význam uložených dat | nová **povinná** env proměnná bez defaultu (případ `SECRET_KEY`), destruktivní/nevratná migrace (ta, kterou nejde vrátit downgradem, viz 1.3), multi-tenancy, zrušení nebo přejmenování existující URL |
| **MINOR** | nová funkce, nasazení je obyčejný deploy | nový provider, nový pohled na dashboardu, export, scheduler, bulk import — typicky commity `feat(...)` |
| **PATCH** | nic nového není vidět, jen se něco spravilo | `fix`, `refactor`, `chore`, `test`, úpravy textů, výkon |

Heuristika, v tomhle pořadí:
1. Musí ten, kdo nasazuje, udělat něco navíc než deploy? → **MAJOR**
2. Uvidí uživatel v appce něco nového? → **MINOR**
3. Jinak → **PATCH**

Za samotné `docs(...)` commity se verze nezvedá — dokumentace není release.

Postup (na `master`, po mergnutí PR, dělá ho člověk — ne AI agent, viz
`AI_INSTRUCTIONS.md` §4):
1. `app/__init__.py::__version__` na nové číslo.
2. V `CHANGELOG.md` přesunout obsah `## [Unreleased]` pod nový nadpis
   `## [vX.Y.Z] - YYYY-MM-DD`, `[Unreleased]` nechat prázdné nahoře.
3. `git tag -a vX.Y.Z -m "..."` a `git push origin vX.Y.Z`.
4. Pokračuj kapitolou 1 tohoto runbooku.

---

## 1. Příprava (den předem)

### 1.1 Lokální test na masteru

```bash
git checkout master && git pull --ff-only && docker compose up -d --build --wait
```

```bash
COPYFILE_DISABLE=1 tar -cf - tests requirements-dev.txt | docker compose run --rm --no-deps -T app sh -c 'tar -xf - && pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -p no:cacheprovider -q'
```
`pytest` je jen vývojová závislost, do image se nebalí — `docker compose exec
app pytest` proto vždy selže na „No module named pytest". Tenhle příkaz ho
nainstaluje do jednorázového kontejneru, spustí a zahodí.

**Čekaný výstup:** `N passed`, 0 failed. Na server nejde nic, co tady
neprošlo. Projdi i ručně obrazovky, kterých se změna týká.

### 1.2 Co běží na serveru teď

```bash
ssh signalmap 'cat /opt/signalmap/DEPLOYED_COMMIT 2>/dev/null || echo "soubor chybi"; cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```
**Čekaný výstup:** dva řádky — SHA posledního nasazeného commitu a aktuální
revize migrací (např. `0035`). **Zapiš si obojí** — SHA je cíl případného
rollbacku kódu, revize cíl případného downgradu databáze (kapitola 6); SHA je
navíc vstup pro krok 1.3.

`/opt/signalmap` **není git checkout** (je to rozbalený archiv, viz kapitola
3), takže `git rev-parse` na serveru nikdy nefunguje. Pokud `DEPLOYED_COMMIT`
chybí (nasazení starší než tahle evidence), máš aspoň revizi migrací.

### 1.3 Které migrace poběží a jdou vrátit downgradem

```bash
git --no-pager log --oneline <commit-ze-serveru>..master -- alembic/versions/
```
**Čekaný výstup:** seznam nových migrací. Je-li prázdný, databáze se nemění a
rollback je jen návrat kódu.

Jinak u každé přečti docstring a rozhodni **jednu otázku**: obsahuje
`DROP COLUMN`, `DROP TABLE`, nebo maže/přepisuje řádky?

| Odpověď | Co to znamená pro rollback |
|---|---|
| Ne — jen `CREATE`/`ADD`/nové číselníkové řádky | jde obnovit ze zálohy (6.1) **i** vrátit downgradem (6.2) |
| **Ano** | jen obnova ze zálohy (6.1) — `downgrade()` zahozená data nevrátí |

Precedent pro „ano": migrace `0026` zahodila cenové sloupce, `0028` smazala a
znovu vložila řádky v `citations`. **Rozhodni to teď, ne až se něco
pokazí** — rozhoduje to, jestli po zpřístupnění appky (4.4) ještě půjde
vrátit se bez ztráty nových dat.

**Proč nestačí jen nasadit starší kód:** kontejner `app` při startu spouští
`alembic upgrade head`. Databáze už je na revizi, kterou starší kód nezná
(soubor s ní v něm není), Alembic skončí chybou „Can't locate revision" a
appka nenaběhne — **i když migrace nic nemazala**. Každý rollback nasazení
s migrací proto musí vrátit i databázi: zálohou (6.1), nebo downgradem (6.2).

### 1.4 Oznámení uživatelům

E-mail s termínem okna. Když deploy mění čísla, která lidé sledují (např.
přepočet citací migrací 0028, nebo pokles unikátních domén po normalizaci),
napiš to **předem** — jinak to druhý den vypadá jako chyba.

### 1.5 Kritérium pro ústup

Napiš si předem: *„Když to do HH:MM nepojede, vracím se podle kapitoly 6."*

---

## 2. Těsně před

### 2.1 Odstávková stránka ZAP

```bash
ssh signalmap 'mkdir -p /var/lib/signalmap/maintenance && touch /var/lib/signalmap/maintenance/maintenance.on'
```
`mkdir -p` je kvůli úplně prvnímu nasazení téhle mechaniky (adresář jinak
vzniká až v 3.3). Odstávka jde **před** zálohou, ne za ní — kdyby appka po
záloze ještě chvíli přijímala data, rollback by o ně přišel.

**Čekaný výstup:** žádný (příkaz nic netiskne). Ověř v prohlížeči/curlem.

### 2.2 Zastavit worker

```bash
ssh signalmap 'cd /opt/signalmap && docker compose stop worker'
```
Odstávková stránka blokuje jen HTTP — worker by dál spouštěl naplánované runy.
Ty by se zapsaly **po** záloze (rollback ze zálohy by je smazal, přestože je
provider už naúčtoval) a běžely by starým kódem proti databázi, kterou krok
3.3 migruje. Worker na `SIGTERM` dokončí rozpracovanou položku a skončí
(`stop_grace_period: 120s`).

Zmeškané plánované runy se nevytratí: po startu workeru (4.2) je ticker
dožene, dokud nejsou starší než `SCHEDULER_GRACE_PERIOD_MINUTES` (výchozí
360 min).

**Čekaný výstup:** `✔ Container signalmap-worker-1 Stopped` — může trvat až
dvě minuty, pokud worker zrovna čeká na providera.

### 2.3 Ověřit, že neběží žádný run

```bash
ssh signalmap 'sh -s' <<'EOF'
cd /opt/signalmap
docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT 'pending_runs '||count(*) FROM runs WHERE status = 'pending' UNION ALL SELECT 'leased_queue '||count(*) FROM run_queue WHERE status = 'leased'"
EOF
```
Ruční run je synchronní požadavek (5–28 s); pro odstávku v 2.1 už nový
nezačne, ale ten rozběhnutý by rebuild kontejneru přerušil. Worker v 2.2 už
svou položku dokončil.

**Čekaný výstup:** `pending_runs 0` a `leased_queue 0`. Nenulové číslo →
počkej a zopakuj, nebo zkontroluj `started_at` (starší než pár minut = uvízlý
řádek z minula, ne běžící run — worker ho po 30 minutách sám převede na
`error` „worker interrupted", viz `reconcile_interrupted_runs`).

### 2.4 Záloha

```bash
ssh signalmap '/usr/local/bin/signalmap-backup.sh'
```
```bash
python tools/local/pull_backup.py
```
**Čekaný výstup:** druhý příkaz skončí `OK stazeno=1 ...`. Bez čerstvé zálohy
(ne noční z 03:15) nepokračuj.

Pokud `/usr/local/bin/signalmap-backup.sh` ještě neexistuje (úplně první
nasazení — instaluje ho až krok 3.3), zálohuj ručně:
```bash
ssh signalmap 'mkdir -p /var/backups/signalmap && chmod 700 /var/backups/signalmap && cd /opt/signalmap && docker compose exec -T postgres pg_dump -U signalmap_user -d signalmap -Fc > /var/backups/signalmap/signalmap-$(date +%Y%m%d-%H%M%S).dump'
```

### 2.5 Výchozí čísla

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT '\''clients '\''||count(*) FROM clients UNION ALL SELECT '\''prompts '\''||count(*) FROM prompts UNION ALL SELECT '\''runs '\''||count(*) FROM runs UNION ALL SELECT '\''raw_responses '\''||count(*) FROM raw_responses UNION ALL SELECT '\''citations '\''||count(*) FROM citations UNION ALL SELECT '\''users '\''||count(*) FROM users UNION ALL SELECT '\''run_schedules '\''||count(*) FROM run_schedules UNION ALL SELECT '\''run_queue '\''||count(*) FROM run_queue"'
```
**Čekaný výstup:** osm čísel. Opiš si je — po nasazení (4.1) se porovnají.

---

## 3. Nasazení

Server **není git checkout** a nemusí mít přístup na GitHub — kód se tam
dopravuje jako archiv z tvého počítače.

### 3.1 Vyrobit archiv z commitu

```bash
STAMP=$(date +%Y%m%d-%H%M%S) && SHA=$(git rev-parse HEAD) && BUILD_TIME=$(date -u +%FT%TZ) && ARCHIVE="/tmp/signalmap-$STAMP.tar.gz" && git archive --format=tar.gz -o "$ARCHIVE" HEAD && ls -lh "$ARCHIVE" && echo "commit: $SHA"
```
`git archive` exportuje z gitu, ne z pracovního adresáře — necommitnuté
změny, netrackované soubory a CRLF konce řádků se na server nedostanou.

**Čekaný výstup:** velikost archivu a `commit: <SHA>`. **Zapiš si `$SHA`** —
použiješ ho v 3.4 a 5.2.

### 3.2 Nahrát na server

```bash
scp "$ARCHIVE" signalmap:/tmp/
```
**Čekaný výstup:** progress `100% ... KB/s ...`. Ověř na serveru, že soubor
fakt dorazil (`ls -lh /tmp/signalmap-$STAMP.tar.gz`) — než půjdeš dál.

### 3.3 Rozbalit, promítnout, přestavět

```bash
ssh signalmap "mkdir -p /tmp/signalmap-$STAMP && tar -xzf /tmp/signalmap-$STAMP.tar.gz -C /tmp/signalmap-$STAMP && rsync -a --delete --exclude='.env' --exclude='*.dump' --exclude='DEPLOYED_COMMIT' /tmp/signalmap-$STAMP/ /opt/signalmap/ && cd /opt/signalmap && ./tools/server/install.sh && GIT_SHA=$SHA BUILD_TIME=$BUILD_TIME docker compose up -d --build --wait app caddy"
```

| | |
|---|---|
| `rsync --delete` | smaže na serveru soubory, které v nové verzi nejsou |
| `--exclude='.env'` | produkční tajemství zůstávají nedotčená |
| `./tools/server/install.sh` | bez něj se hostitelské skripty rozejdou s repem |
| `GIT_SHA=$SHA BUILD_TIME=$BUILD_TIME` | zapeče se do image jako `ENV` (dockerfile) — bez toho appka po nasazení neví, jaký commit/build je |
| `docker compose up -d --build --wait` | staví, zatímco starý kontejner obsluhuje — appku předem nezastavuj |
| `app caddy` na konci | **bez workeru** — ten sdílí image `signalmap-app`, takže nový kód dostane sám, ale pouští se až po interní kontrole (4.2); bez výčtu služeb by ho `up` spustil hned |
| migrace | běží automaticky ze `CMD`; když selžou, appka nenaběhne |

**⚠️ `&&` mezi kroky je bezpečnostní pojistka, ne styl.** Incident
2026-09-19: rozepsání tohohle příkazu na samostatné řádky (kvůli spuštění
v interaktivní SSH session) tu vazbu zrušilo — `tar` selhal (chybějící
archiv), ale `rsync --delete` na dalším „řádku" se přesto spustil s prázdným
zdrojem a smazal `/opt/signalmap`. **Vždy spouštěj jako jeden řetězený
příkaz přesně jak je napsaný výše** — zkopírovaný beze změny, nikdy
retypovaný na řádky.

**Čekaný výstup:** `docker compose` na konci vypíše `✓ Container ...
Healthy` pro `postgres`, `app` a `caddy`. Worker zůstává zastavený.

### 3.4 Zaznamenat verzi a uklidit

```bash
ssh signalmap "echo $SHA > /opt/signalmap/DEPLOYED_COMMIT && echo \"\$(date -Is) $SHA\" >> /var/log/signalmap-deploys.log && rm -rf /tmp/signalmap-$STAMP /tmp/signalmap-$STAMP.tar.gz && cat /opt/signalmap/DEPLOYED_COMMIT"
```
Bez tohohle kroku se nedá zjistit, co je nasazené — archiv žádnou stopu po
commitu nenese.

**Čekaný výstup:** `$SHA` vypsaný zpátky z `DEPLOYED_COMMIT`.

`DEPLOYED_COMMIT` zůstává zdrojem pravdy pro rollback (kapitola 6) —
nasazenou verzi ale nově vidí i přihlášený uživatel přímo v patičce appky.

---

## 4. Ověření

Worker se pouští (4.2) a appka jde ven (4.3) až po interní kontrole (4.1) —
nemá smysl spouštět naplánované runy ani ukazovat appku světu dřív, než víš,
že migrace doběhly a appka nepadá. Dokud worker neběží, je rollback ze zálohy
(6.1) bez ztráty dat.

### 4.1 Interní kontrola (appka je pořád v odstávce, worker stojí)

```bash
ssh signalmap 'cd /opt/signalmap && docker compose ps && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```
**Čekaný výstup:** `postgres`, `app`, `caddy` `Up`/`healthy` (`worker` ve
výpisu chybí — `ps` ukazuje jen běžící služby); `version_num` odpovídá
nejnovější migraci z 1.3.

Pak stejný dotaz na počty jako v 2.5:

| Tabulka | Očekávání |
|---|---|
| `clients`, `users`, `run_schedules`, `run_queue` | beze změny |
| `prompts`, `runs`, `raw_responses` | beze změny, pokud deploy neobsahoval datovou migraci |
| `citations` | beze změny — **kromě** nasazení s přepočtem (např. 0028) |

Jakýkoliv nečekaný pokles = zastavit a jít na rollback (kapitola 6).

```bash
ssh signalmap 'cd /opt/signalmap && docker compose logs --tail=100 app | grep -iE "error|traceback|exception" || echo "zadne chyby"'
```
**Čekaný výstup:** `zadne chyby`.

Patička appky (po dočasném přihlášení admin účtem) ukazuje očekávanou
`vX.Y.Z · <SHA>` shodnou s `$SHA` z 3.1 — nejrychlejší důkaz, že se
nasadil ten správný build.

**Odstávková stránka** — otestuj přímo na serveru, dokud appka ještě není
venku:
```bash
ssh signalmap 'curl -sk -o /dev/null -w "%{http_code}\n" https://expressyourself.ai/ --resolve expressyourself.ai:443:127.0.0.1'
```
**Čekaný výstup:** `503`. Použij `--resolve`, ne `-H "Host: ..."` s
`https://localhost/` — TLS handshake (SNI) jede podle URL, ne podle Host
hlavičky, takže `localhost` narazí na chybějící certifikát a **visí**
(incident 2026-09-19, potvrzeno `Ctrl+C`).

### 4.2 Spustit worker

```bash
ssh signalmap 'cd /opt/signalmap && docker compose up -d --wait worker && docker compose logs --tail=20 worker'
```
`up` bez `--build` — image už postavil krok 3.3, worker ho jen převezme.
Od tohoto kroku může worker zapisovat nové runy; rollback ze zálohy (6.1) by
je smazal.

**Čekaný výstup:** `✓ Container signalmap-worker-1 Healthy` a v logu řádek
`Worker ... starting (dry_run=..., enabled=...)`. **Zkontroluj obě hodnoty** —
produkce má běžet s `dry_run=False` a `enabled=True`; `SCHEDULER_DRY_RUN` má
v compose výchozí hodnotu `true`, takže chybějící řádek v `.env` znamená, že
scheduler tiše nic nespouští.

### 4.3 Odstávková stránka VYP

```bash
ssh signalmap 'rm -f /var/lib/signalmap/maintenance/maintenance.on'
```
**Nejčastěji zapomenutý krok** — appku má smysl pustit ven až po 4.1.

### 4.4 Externí kontrola (appka je zpátky venku)

```bash
curl --fail --show-error https://expressyourself.ai/health
```
**Čekaný výstup:** `{"status":"ok"}`. `503` tady nejčastěji znamená
zapomenutý krok 4.3 — zkontroluj `ssh signalmap 'ls /var/lib/signalmap/maintenance/'`.

Pak v prohlížeči: přihlášení, `/clients`, `/ops`, `/schedules` (stav workeru
bez varování „Scheduler not responding"), detail libovolného runu, a obrazovka, které se
nasazovaná změna týká.

---

## 5. Uzavření

### 5.1 Pět minut sledovat

```bash
ssh signalmap 'cd /opt/signalmap && docker compose logs -f --tail=20 app worker caddy'
```
Zavřít terminál hned po `curl /health` je, jak se přehlédne chyba, která se
projeví až při prvním skutečném požadavku uživatele.

### 5.2 Zápis do deploy logu

```bash
ssh signalmap "echo \"\$(date -Is) $SHA OK\" >> /var/log/signalmap-deploys.log"
```
`$SHA` je ten z kroku 3.1 — **ne** `git rev-parse` na serveru (`/opt/signalmap`
není git checkout, vždy spadne na „not a git repository").

**Čekaný výstup:** žádný. Za půl roku je tohle jediné místo, kde zjistíš, kdy
se co nasadilo.

### 5.3 Úklid a zpráva

- `/opt/signalmap.old` smaž, až je vše ověřené (existuje jen po prvním převodu)
- e-mail „hotovo" s popisem změn

---

## 6. Rollback

Rollback vrací **kód i databázi** — samotný starší kód nad novou databází
nenaběhne (viz konec kroku 1.3). Jsou dvě cesty:

| Kdy selhání přišlo | Cesta |
|---|---|
| ve 4.1 až 4.3 (appka ještě v odstávce) | **6.1 obnova ze zálohy** — výchozí, vždy funguje; runy, které worker stihl od 4.2 spustit, zmizí (scheduler je po startu znovu zařadí, dokud nevyprší grace period) |
| až ve 4.4 nebo později (appka byla venku) | **6.2 downgrade**, pokud 1.3 řekl „jen `CREATE`/`ADD`" a nechceš přijít o data zapsaná od nasazení; jinak 6.1 a uživatelům napsat, co se ztratilo |
| deploy neobsahoval migraci (1.3 prázdný) | kroky a) a c) z 6.1, bez obnovy databáze |

### 6.0 Vždy nejdřív

```bash
ssh signalmap 'touch /var/lib/signalmap/maintenance/maintenance.on && cd /opt/signalmap && docker compose stop worker'
```
Odstávka zpátky a worker stát — stejný důvod jako v 2.1/2.2. Ve 4.1 je obojí
už v tomhle stavu a příkaz nic nezmění; od 4.2 dál je nutný.

Pak archiv **staršího commitu** stejnou cestou jako kapitola 3, `HEAD`
nahrazeno commitem z kroku 1.2:

```bash
OLD=<commit-z-kroku-1.2> && STAMP=$(date +%Y%m%d-%H%M%S)-rollback && BUILD_TIME=$(date -u +%FT%TZ) && ARCHIVE="/tmp/signalmap-$STAMP.tar.gz" && git archive --format=tar.gz -o "$ARCHIVE" "$OLD" && scp "$ARCHIVE" signalmap:/tmp/
```

### 6.1 Obnova ze zálohy (výchozí)

Záloha z 2.4 vznikla, když byla appka v odstávce a worker stál — nic
novějšího v databázi není, obnova tedy nic neztratí (dokud se appka nepustila
ven). Funguje i po destruktivní migraci.

**a) Zastavit appku a vrátit kód — bez spuštění:**
```bash
ssh signalmap "cd /opt/signalmap && docker compose stop app worker && mkdir -p /tmp/signalmap-$STAMP && tar -xzf /tmp/signalmap-$STAMP.tar.gz -C /tmp/signalmap-$STAMP && rsync -a --delete --exclude='.env' --exclude='*.dump' --exclude='DEPLOYED_COMMIT' /tmp/signalmap-$STAMP/ /opt/signalmap/ && cd /opt/signalmap && ./tools/server/install.sh"
```
(stejné varování o `&&` jako v 3.3 platí i tady.) `app` musí stát, než
začne obnova — `pg_restore --clean` maže a znovu zakládá tabulky pod ním.

**b) Obnovit databázi ze zálohy z 2.4** (dump leží na serveru):
```bash
ssh signalmap 'ls -1t /var/backups/signalmap/ | head -3'
```
```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres pg_restore --clean --if-exists --no-owner --no-privileges -U signalmap_user -d signalmap < /var/backups/signalmap/<zaloha-z-2.4>.dump'
```
Pokud dump na serveru chybí, použij lokální kopii z `pull_backup.py` — přesměrování
je pak **za** uvozovkami, takže soubor čte tvůj počítač:
```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres pg_restore --clean --if-exists --no-owner --no-privileges -U signalmap_user -d signalmap' < "C:/Backups/SignalMap/dumps/<zaloha-z-2.4>.dump"
```

**Čekaný výstup:** žádná chyba. Revize migrací musí být zpátky ta z 1.2:
```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```

**c) Spustit starší kód a zapsat verzi:**
```bash
ssh signalmap "cd /opt/signalmap && GIT_SHA=$OLD BUILD_TIME=$BUILD_TIME docker compose up -d --build --wait app caddy && echo $OLD > /opt/signalmap/DEPLOYED_COMMIT && echo \"\$(date -Is) $OLD ROLLBACK\" >> /var/log/signalmap-deploys.log"
```
`GIT_SHA=$OLD` kvůli patičce — bez něj by ve 4.1 chyběl důkaz, že běží
správný build.

Pak celá kapitola 4 znovu (včetně 4.2 — worker se spouští až tam). Počty ve
4.1 musí přesně odpovídat 2.5.

### 6.2 Downgrade migrací (jen když appka už byla venku)

Jen pokud 1.3 řekl „jen `CREATE`/`ADD`" — `downgrade()` destruktivní migrace
zahozená data nevrátí. Zachová data, která uživatelé zapsali od nasazení.

**a) Vrátit databázi — ještě s novým kódem** (starší kód novější revize
nezná, takže downgrade umí jen nová verze):
```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T app alembic downgrade <revize-z-1.2> && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```
**Čekaný výstup:** poslední řádek je revize z 1.2.

Downgrade migrace, která zakládala číselníkové řádky (např. `0033`–`0035`
mazou svého providera a jeho modely), **selže na cizím klíči**, jakmile
existuje run nad těmi modely. To je správně — mazal by historii. V tom
případě zbývá jen 6.1 se ztrátou dat od nasazení.

**b) Vrátit kód a spustit** — kroky a) a c) z 6.1 (bez obnovy databáze).

Pak celá kapitola 4 znovu.

### Co nikdy

| | |
|---|---|
| `docker compose down -v` | smaže databázi i certifikáty |
| `git clean -fd` na serveru | smaže `.env` |
| nasadit starší kód bez obnovy/downgradu databáze | nenaběhne — `alembic upgrade head` nezná novější revizi |
| obnova ze zálohy s běžícím `app`/`worker` | `pg_restore --clean` jim maže tabulky pod rukama; worker navíc může zapisovat |
| rollback bez ověření | nevíš, jestli tě vrátil tam, kam jsi chtěl |

---

## Co runbook zatím nepokrývá

- **automatické nasazení** (CI/CD) — vědomě ne; jeden maintainer, jeden
  server, ruční postup s kontrolami je při téhle velikosti spolehlivější
  než pipeline, kterou nikdo neudržuje
- **nasazení bez výpadku** (blue/green, rolling) — vyžadovalo by dvě
  instance a load balancer; při krátkém výpadku v ohlášeném okně se
  nevyplatí
