# SignalMap — Deployment runbook

## v1.1 | Září 2026

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
2. TĚSNĚ PŘED         odstávka, záloha, výchozí čísla
3. NASAZENÍ           kód, skripty, migrace, restart
4. OVĚŘENÍ            interní kontrola, odstávka pryč, health, UI
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
| **MAJOR** | nasazení není „jen deploy", nebo se mění význam uložených dat | nová **povinná** env proměnná bez defaultu (případ `SECRET_KEY`), destruktivní/nevratná migrace (ta, pro kterou má tenhle dokument §6.2 vlastní rollback větev), multi-tenancy, zrušení nebo přejmenování existující URL |
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
ssh signalmap 'cat /opt/signalmap/DEPLOYED_COMMIT 2>/dev/null || echo "soubor chybi"'
```
**Čekaný výstup:** SHA posledního nasazeného commitu. **Zapiš si ho** — je to
cíl případného rollbacku (kapitola 6) a vstup pro krok 1.3.

`/opt/signalmap` **není git checkout** (je to rozbalený archiv, viz kapitola
3), takže `git rev-parse` na serveru nikdy nefunguje. Pokud soubor chybí
(nasazení starší než tahle evidence), zjisti aspoň aplikovanou migraci:

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```

### 1.3 Které migrace poběží a je deploy vratný

```bash
git --no-pager log --oneline <commit-ze-serveru>..master -- alembic/versions/
```
**Čekaný výstup:** seznam nových migrací. U každé přečti docstring a rozhodni
**jednu otázku**: obsahuje `DROP COLUMN`, `DROP TABLE`, nebo maže/přepisuje
řádky?

| Odpověď | Co to znamená pro rollback |
|---|---|
| Ne — jen `CREATE`/`ADD` | stačí vrátit kód (6.1) |
| **Ano** | návrat kódu nestačí, musí se obnovit i databáze (6.2) |

Precedent pro „ano": migrace `0026` zahodila cenové sloupce, `0028` smazala a
znovu vložila řádky v `citations` — po takovém nasazení je starší appka proti
nové databázi nepoužitelná. **Rozhodni to teď, ne až se něco pokazí.**

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

### 2.2 Ověřit, že neběží žádný run

```bash
ssh signalmap 'sh -s' <<'EOF'
cd /opt/signalmap
docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT count(*) FROM runs WHERE status = 'pending'"
EOF
```
Run je synchronní požadavek (5–28 s) — rebuild kontejneru uprostřed by
nechal řádek navždy v `pending` (nic ho dnes needítuje).

**Čekaný výstup:** `0`. Nenulové číslo → počkej a zopakuj, nebo zkontroluj
`started_at` (starší než pár minut = uvízlý řádek z minula, ne běžící run).

> Se schedulerem sem přibude `docker compose stop worker` jako první krok
> (`docs/TASKS_SCHEDULER.md`, SCH-T4).

### 2.3 Záloha

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

### 2.4 Výchozí čísla

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT '\''clients '\''||count(*) FROM clients UNION ALL SELECT '\''prompts '\''||count(*) FROM prompts UNION ALL SELECT '\''runs '\''||count(*) FROM runs UNION ALL SELECT '\''raw_responses '\''||count(*) FROM raw_responses UNION ALL SELECT '\''citations '\''||count(*) FROM citations UNION ALL SELECT '\''users '\''||count(*) FROM users"'
```
**Čekaný výstup:** šest čísel. Opiš si je — po nasazení (4.1) se porovnají.

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
ssh signalmap "mkdir -p /tmp/signalmap-$STAMP && tar -xzf /tmp/signalmap-$STAMP.tar.gz -C /tmp/signalmap-$STAMP && rsync -a --delete --exclude='.env' --exclude='*.dump' --exclude='DEPLOYED_COMMIT' /tmp/signalmap-$STAMP/ /opt/signalmap/ && cd /opt/signalmap && ./tools/server/install.sh && GIT_SHA=$SHA BUILD_TIME=$BUILD_TIME docker compose up -d --build --wait"
```

| | |
|---|---|
| `rsync --delete` | smaže na serveru soubory, které v nové verzi nejsou |
| `--exclude='.env'` | produkční tajemství zůstávají nedotčená |
| `./tools/server/install.sh` | bez něj se hostitelské skripty rozejdou s repem |
| `GIT_SHA=$SHA BUILD_TIME=$BUILD_TIME` | zapeče se do image jako `ENV` (dockerfile) — bez toho appka po nasazení neví, jaký commit/build je |
| `docker compose up -d --build --wait` | staví, zatímco starý kontejner obsluhuje — appku předem nezastavuj |
| migrace | běží automaticky ze `CMD`; když selžou, appka nenaběhne |

**⚠️ `&&` mezi kroky je bezpečnostní pojistka, ne styl.** Incident
2026-09-19: rozepsání tohohle příkazu na samostatné řádky (kvůli spuštění
v interaktivní SSH session) tu vazbu zrušilo — `tar` selhal (chybějící
archiv), ale `rsync --delete` na dalším „řádku" se přesto spustil s prázdným
zdrojem a smazal `/opt/signalmap`. **Vždy spouštěj jako jeden řetězený
příkaz přesně jak je napsaný výše** — zkopírovaný beze změny, nikdy
retypovaný na řádky.

**Čekaný výstup:** `docker compose` na konci vypíše `✓ Container ...
Healthy` pro všechny tři služby.

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

Appka jde ven (4.2) až po interní kontrole (4.1) — nemá smysl ji ukazovat
světu dřív, než víš, že migrace doběhly a appka nepadá.

### 4.1 Interní kontrola (appka je pořád v odstávce)

```bash
ssh signalmap 'cd /opt/signalmap && docker compose ps && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```
**Čekaný výstup:** všechny služby `Up`/`healthy`; `version_num` odpovídá
nejnovější migraci z 1.3.

Pak stejný dotaz na počty jako v 2.4:

| Tabulka | Očekávání |
|---|---|
| `clients`, `users` | beze změny |
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

### 4.2 Odstávková stránka VYP

```bash
ssh signalmap 'rm -f /var/lib/signalmap/maintenance/maintenance.on'
```
**Nejčastěji zapomenutý krok** — appku má smysl pustit ven až po 4.1.

### 4.3 Externí kontrola (appka je zpátky venku)

```bash
curl --fail --show-error https://expressyourself.ai/health
```
**Čekaný výstup:** `{"status":"ok"}`. `503` tady nejčastěji znamená
zapomenutý krok 4.2 — zkontroluj `ssh signalmap 'ls /var/lib/signalmap/maintenance/'`.

Pak v prohlížeči: přihlášení, `/clients`, `/ops`, detail libovolného runu,
a obrazovka, které se nasazovaná změna týká.

---

## 5. Uzavření

### 5.1 Pět minut sledovat

```bash
ssh signalmap 'cd /opt/signalmap && docker compose logs -f --tail=20 app caddy'
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

> **Pokud selhání přišlo až ve 4.3** (appka byla venku), **než začneš
> rollback, znovu zapni odstávku:**
> `ssh signalmap 'touch /var/lib/signalmap/maintenance/maintenance.on'`.
> Pokud selhalo dřív (ve 4.1), appka je pořád v odstávce.

Rollback = nasadit **starší commit** stejnou cestou jako kapitola 3, `HEAD`
nahrazeno commitem z kroku 1.2:

```bash
OLD=<commit-z-kroku-1.2> && STAMP=$(date +%Y%m%d-%H%M%S)-rollback && ARCHIVE="/tmp/signalmap-$STAMP.tar.gz" && git archive --format=tar.gz -o "$ARCHIVE" "$OLD" && scp "$ARCHIVE" signalmap:/tmp/
```

### 6.1 Deploy neobsahoval destruktivní migraci

Stačí vrátit kód:
```bash
ssh signalmap "mkdir -p /tmp/signalmap-$STAMP && tar -xzf /tmp/signalmap-$STAMP.tar.gz -C /tmp/signalmap-$STAMP && rsync -a --delete --exclude='.env' --exclude='*.dump' --exclude='DEPLOYED_COMMIT' /tmp/signalmap-$STAMP/ /opt/signalmap/ && cd /opt/signalmap && ./tools/server/install.sh && docker compose up -d --build --wait && echo $OLD > /opt/signalmap/DEPLOYED_COMMIT"
```
(stejné varování o `&&` jako v 3.3 platí i tady)

### 6.2 Deploy obsahoval destruktivní migraci

Návrat kódu **nestačí** — starší appka by sahala na sloupce, které migrace
zahodila. Nejdřív kód (příkaz z 6.1, ale **bez** `docker compose up`), pak
databáze ze zálohy z kroku 2.3:

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres pg_restore --clean --if-exists --no-owner --no-privileges -U signalmap_user -d signalmap' < "C:/Backups/SignalMap/dumps/<zaloha-z-2.3>.dump"
```
```bash
ssh signalmap 'cd /opt/signalmap && docker compose up -d --build --wait'
```
Pak celá kapitola 4 znovu.

### Co nikdy

| | |
|---|---|
| `docker compose down -v` | smaže databázi i certifikáty |
| `git clean -fd` na serveru | smaže `.env` |
| rollback bez ověření | nevíš, jestli tě vrátil tam, kam jsi chtěl |

---

## Co runbook zatím nepokrývá

- **zastavení workeru** (2.2) — přibude se schedulerem
- **automatické nasazení** (CI/CD) — vědomě ne; jeden maintainer, jeden
  server, ruční postup s kontrolami je při téhle velikosti spolehlivější
  než pipeline, kterou nikdo neudržuje
- **nasazení bez výpadku** (blue/green, rolling) — vyžadovalo by dvě
  instance a load balancer; při krátkém výpadku v ohlášeném okně se
  nevyplatí
