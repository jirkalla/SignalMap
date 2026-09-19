# SignalMap — Deployment runbook

## v1.0 | Září 2026

Odškrtávací seznam pro nasazení nové verze na produkční server. README popisuje
nasazení v próze pro toho, kdo projekt nezná; tenhle dokument je postup, podle
kterého se nasazuje.

**Produkce:** `https://expressyourself.ai` · `2.29.23.252` · `/opt/signalmap`
· Compose projekt `signalmap` · Postgres 18 ve svazku `signalmap_pgdata`

**Pravidlo, které platí u každého kroku:** když krok selže, **zastav se a
nepokračuj dalším**. Nasazení je postupné odemykání, ne seznam přání.

---

## Rychlý přehled

```
1. PŘÍPRAVA          den předem, bez dopadu na provoz
2. TĚSNĚ PŘED        odstávka, záloha, výchozí čísla
3. NASAZENÍ          kód, skripty, migrace, restart
4. OVĚŘENÍ           interní kontrola, odstávka pryč, health, UI
5. UZAVŘENÍ          sledování, zápis, zpráva
6. ROLLBACK          jen když je potřeba
```

Odhad: příprava 15 minut den předem, samotné okno 15–20 minut, z toho
výpadek 2–5 minut.

---

## 1. Příprava (den předem)

### 1.1 Lokální test na masteru

```bash
git checkout master && git pull --ff-only && docker compose up -d --build --wait
```

```bash
COPYFILE_DISABLE=1 tar -cf - tests requirements-dev.txt | docker compose run --rm --no-deps -T app sh -c 'tar -xf - && pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -p no:cacheprovider -q'
```

`docker compose exec -T app python -m pytest` **nefunguje** — `pytest` je jen
vývojová závislost (`requirements-dev.txt`), do image se nebalí, takže v běžícím
kontejneru modul vždycky chybí (`No module named pytest`, ověřeno 2026-09-19).
Skutečný postup je přesně ten z `README.md` „Running tests": jednorázový
kontejner, do kterého se `requirements-dev.txt` nainstaluje za běhu.

Na server nejde nic, co neprošlo tady. Projdi i ručně obrazovky, kterých se
změna týká.

### 1.2 Co běží na serveru teď

```bash
ssh signalmap 'cat /opt/signalmap/DEPLOYED_COMMIT 2>/dev/null || echo "soubor chybi - nasazeno pred zavedenim evidence"'
```

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```

**Ten commit si zapiš** — je to cíl případného rollbacku.

`/opt/signalmap` **není git checkout**, je to rozbalená kopie (viz kapitola 3),
takže `git rev-parse` tam nefunguje. Verzi drží soubor `DEPLOYED_COMMIT`, který
zapisuje krok 3.4. U nasazení staršího než zavedení téhle evidence se verze
zpětně nezjistí — pak se orientuj podle `alembic_version` a data v
`/var/log/signalmap-deploys.log`.

### 1.3 Které migrace poběží a je deploy vratný

```bash
git log --oneline <commit-ze-serveru>..master -- alembic/versions/ | cat
```

Projdi docstring každé z nich a rozhodni **jednu otázku**:

> Obsahuje některá `DROP COLUMN`, `DROP TABLE`, nebo maže/přepisuje řádky?

| Odpověď | Co to znamená pro rollback |
|---|---|
| Ne — jen `CREATE`/`ADD` | stačí vrátit kód (6.1) |
| **Ano** | návrat kódu nestačí, musí se obnovit i databáze (6.2) |

Precedent: migrace `0026` zahodila cenové sloupce, `0028` smazala a znovu
vložila řádky v `citations`. Po takovém nasazení je starší verze appky
nepoužitelná proti nové databázi.

**Rozhodni to teď, ne až se něco pokazí.**

### 1.4 Oznámení uživatelům

E-mail s termínem okna a informací, že se ozveš, až bude hotovo. Když deploy
mění čísla, která lidé sledují (např. přepočet citací migrací 0028), napiš to
předem — jinak to druhý den vypadá jako chyba.

### 1.5 Kritérium pro ústup

Napiš si předem: *„Když to do HH:MM nepojede, vracím se podle kapitoly 6."*
Rozhodnutí přijaté v klidu je lepší než rozhodnutí přijaté ve dvacáté minutě
výpadku.

---

## 2. Těsně před

### 2.1 Odstávková stránka ZAP

```bash
ssh signalmap 'mkdir -p /var/lib/signalmap/maintenance && touch /var/lib/signalmap/maintenance/maintenance.on'
```

`mkdir -p` je tu kvůli úplně prvnímu nasazení týhle mechaniky: adresář
jinak vzniká až v kroku 3.3 (`install.sh`), a ten běží až po tomhle kroku.
Na každém dalším nasazení už adresář existuje a `mkdir -p` jen potvrdí, že
tam je.

**Proč je odstávka před zálohou, ne za ní:** kdyby appka po záloze ještě
chvíli přijímala data, rollback by o ně přišel. Když nejdřív zavřeš vstup,
je záloha přesně tím stavem, do kterého se vracíš.

### 2.2 Ověřit, že neběží žádný run

```bash
ssh signalmap 'sh -s' <<'EOF'
cd /opt/signalmap
docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT count(*) FROM runs WHERE status = 'pending'"
EOF
```

Musí vyjít **`0`**.

Run je dnes synchronní HTTP požadavek — `Run` se zakládá se stavem `pending`
těsně před voláním providera a na `success`/`error` se přepne až po návratu
odpovědi (5–28 s). Když kontejner zrecykluješ mezitím, výsledek se nemá kam
zapsat a řádek **zůstane `pending` navždy**: nic ho dnes neuklidí a v
`/ops` pak trvale straší jako nedokončený run.

Když vyjde nenulové číslo, buď někdo právě spustil run — počkej a zopakuj —
nebo tam takový uvízlý řádek z minula už je. Zjistíš to takhle:

```bash
ssh signalmap 'sh -s' <<'EOF'
cd /opt/signalmap
docker compose exec -T postgres psql -U signalmap_user -d signalmap -c "SELECT id, prompt_id, model_id, started_at FROM runs WHERE status = 'pending' ORDER BY started_at"
EOF
```

Řádek starší než pár minut je uvízlý, ne běžící.

> ⚠️ **Se schedulerem sem přibude zastavení workeru** jako první krok —
> `docker compose stop worker` (`docs/TASKS_SCHEDULER.md`, SCH-T4). Zároveň
> tenhle problém zmizí: rekonciliace (design decision 13) uvízlé `pending`
> runy sama označí jako chybu.

### 2.3 Záloha

```bash
ssh signalmap '/usr/local/bin/signalmap-backup.sh'
```

```bash
python tools/local/pull_backup.py
```

Druhý příkaz musí skončit `OK stazeno=1 ...`. **Bez čerstvé zálohy staršího
data než pár minut nepokračuj** — noční záloha ze 03:15 není totéž co stav
těsně před zásahem.

### 2.4 Výchozí čísla

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT '\''clients '\''||count(*) FROM clients UNION ALL SELECT '\''prompts '\''||count(*) FROM prompts UNION ALL SELECT '\''runs '\''||count(*) FROM runs UNION ALL SELECT '\''raw_responses '\''||count(*) FROM raw_responses UNION ALL SELECT '\''citations '\''||count(*) FROM citations UNION ALL SELECT '\''users '\''||count(*) FROM users"'
```

Opiš si výsledek. Po nasazení poběží ten samý příkaz a čísla se porovnají.

---

## 3. Nasazení

Postup podle Khalidova předání (2026-09-17), doplněný o instalaci hostitelských
skriptů a evidenci nasazené verze. Server **není git checkout** a nemusí mít
přístup na GitHub — kód se tam dopravuje jako archiv z tvého počítače.

### 3.1 Vyrobit archiv z commitu

```bash
STAMP=$(date +%Y%m%d-%H%M%S) && SHA=$(git rev-parse HEAD) && ARCHIVE="/tmp/signalmap-$STAMP.tar.gz" && git archive --format=tar.gz -o "$ARCHIVE" HEAD && ls -lh "$ARCHIVE" && echo "commit: $SHA"
```

`git archive` exportuje **z gitu, ne z pracovního adresáře**. Necommitnuté
změny, netrackované soubory (`docker-compose.override.yaml`, `.claude/`) ani
CRLF konce řádků se tím pádem na server nedostanou. To je důvod, proč se
nepoužívá `rsync` přímo z Windows.

### 3.2 Nahrát na server

```bash
scp "$ARCHIVE" signalmap:/tmp/
```

### 3.3 Rozbalit, promítnout, přestavět

```bash
ssh signalmap "mkdir -p /tmp/signalmap-$STAMP && tar -xzf /tmp/signalmap-$STAMP.tar.gz -C /tmp/signalmap-$STAMP && rsync -a --delete --exclude='.env' --exclude='*.dump' --exclude='DEPLOYED_COMMIT' /tmp/signalmap-$STAMP/ /opt/signalmap/ && cd /opt/signalmap && ./tools/server/install.sh && docker compose up -d --build --wait"
```

| | |
|---|---|
| `rsync --delete` | běží **na serveru**, ne z Windows; smaže soubory, které v nové verzi nejsou |
| `--exclude='.env'` | produkční tajemství zůstávají nedotčená |
| `./tools/server/install.sh` | bez něj se hostitelské skripty tiše rozejdou s repem |
| `docker compose up -d --build --wait` | staví **zatímco starý kontejner obsluhuje**, teprve pak prohodí — appku předem nezastavuj, jen by to prodloužilo výpadek o dobu buildu |
| migrace | běží automaticky ze `CMD` v `dockerfile`; když selžou, **appka nenaběhne** — nedostaneš polovičně zmigrovanou běžící databázi |

**`&&` mezi kroky není stylistika, je to bezpečnostní pojistka.** Incident
2026-09-19: adaptace tohohle příkazu na spuštění přímo v SSH session (rozepsáno
na samostatné řádky kvůli čitelnosti) tu vazbu zrušila. `tar` selhal (archiv se
kvůli chybě v `scp` kroku nikdy nedostal do `/tmp/`), ale `rsync --delete` na
dalším „řádku" se přesto spustil — s prázdným zdrojovým adresářem, čímž smazal
celý `/opt/signalmap` kromě vyloučených souborů. Rozepsání na řádky nebo
vkládání příkaz po příkazu do interaktivní session tuhle záruku ruší — **vždy
spouštěj jako jeden řetězený příkaz** (přesně jak je napsaný výše), ať už přes
`ssh signalmap "..."` z Windows, nebo zkopírovaný beze změny do session na
serveru.

### 3.4 Zaznamenat verzi a uklidit

```bash
ssh signalmap "echo $SHA > /opt/signalmap/DEPLOYED_COMMIT && echo \"\$(date -Is) $SHA\" >> /var/log/signalmap-deploys.log && rm -rf /tmp/signalmap-$STAMP /tmp/signalmap-$STAMP.tar.gz && cat /opt/signalmap/DEPLOYED_COMMIT"
```

Bez tohohle kroku se **nedá zjistit, co je nasazené** — archiv žádnou stopu po
commitu nenese. Je to jediné, co Khalidovu postupu chybělo.

---

## 4. Ověření

Rozdělené na dvě půlky schválně: appka jde zpátky ven (4.2) až poté, co
interní kontrola (4.1) potvrdí, že migrace doběhly a appka nepadá — nemá
smysl ji ukazovat světu dřív, než tohle víš.

### 4.1 Interní kontrola (appka je pořád v odstávce)

Všechno tady jde přes SSH a `docker exec`, ne přes veřejnou doménu — na
odstávkové stránce nezáleží.

```bash
ssh signalmap 'cd /opt/signalmap && docker compose ps && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```

Pak stejný dotaz na počty jako v 2.4 a porovnání:

| Tabulka | Očekávání |
|---|---|
| `clients`, `users` | **beze změny** |
| `prompts`, `runs`, `raw_responses` | **beze změny**, pokud deploy neobsahoval datovou migraci |
| `citations` | beze změny — **kromě** nasazení s přepočtem (např. 0028), kde naroste |

Jakýkoliv nečekaný pokles je důvod k zastavení a rollbacku.

```bash
ssh signalmap 'cd /opt/signalmap && docker compose logs --tail=100 app | grep -iE "error|traceback|exception" || echo "zadne chyby"'
```

### 4.2 Odstávková stránka VYP

```bash
ssh signalmap 'rm -f /var/lib/signalmap/maintenance/maintenance.on'
```

**Nejčastěji zapomenutý krok celého postupu** — ale teprve teď, po interní
kontrole výše, dává smysl appku pustit ven. Pokud ho přeskočíš, další
podkrok (4.3) na to okamžitě upozorní.

### 4.3 Externí kontrola (appka je zpátky venku)

```bash
curl --fail --show-error https://expressyourself.ai/health
```

> Vrátí-li se tu `503` (`curl: (22) The requested URL returned error: 503`),
> nejpravděpodobnější příčina je přeskočený krok 4.2 — zkontroluj
> `ssh signalmap 'ls /var/lib/signalmap/maintenance/'`, jestli tam
> `maintenance.on` pořád leží.

- přihlášení stávajícím účtem
- `/clients` — klienti jsou tam
- `/ops` — čísla a náklady se zobrazují
- detail libovolného runu — odpověď i citace se vykreslí
- obrazovka, které se nasazovaná změna týká

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

`$SHA` je ten samý, co sis zapsal v kroku 3.1 — **ne** `git rev-parse` spuštěný
na serveru: `/opt/signalmap` není git checkout (§3), takže tam `git rev-parse`
vždy spadne na „not a git repository" (ověřeno 2026-09-19). Krok 3.4 už jeden
záznam s plným SHA zapsal — tenhle je jen krátký `OK` potvrzující, že celé
ověření (kapitola 4) proběhlo bez problému.

Za půl roku je tohle jediné místo, kde zjistíš, kdy se co nasadilo.

### 5.3 Úklid a zpráva

- `/opt/signalmap.old` smaž, až je vše ověřené (existuje jen po prvním převodu)
- e-mail „hotovo" s popisem změn

---

## 6. Rollback

> **Pokud selhání přišlo až ve 4.3** (appka byla mezitím puštěná ven,
> veřejně dostupná), **než začneš rollback, znovu zapni odstávku:**
> `ssh signalmap 'touch /var/lib/signalmap/maintenance/maintenance.on'` —
> ať uživatelé nevidí rozbitou appku, zatímco vracíš kód (a případně
> databázi) zpět. Pokud selhání přišlo dřív, ve 4.1, appka je pořád v
> odstávce a nic zapínat nemusíš.

Rollback = nasadit **starší commit** stejnou cestou jako kapitola 3, jen
`HEAD` nahradíš tím commitem z kroku 1.2:

```bash
OLD=<commit-z-kroku-1.2> && STAMP=$(date +%Y%m%d-%H%M%S)-rollback && ARCHIVE="/tmp/signalmap-$STAMP.tar.gz" && git archive --format=tar.gz -o "$ARCHIVE" "$OLD" && scp "$ARCHIVE" signalmap:/tmp/
```

### 6.1 Deploy neobsahoval destruktivní migraci

Stačí vrátit kód:

```bash
ssh signalmap "mkdir -p /tmp/signalmap-$STAMP && tar -xzf /tmp/signalmap-$STAMP.tar.gz -C /tmp/signalmap-$STAMP && rsync -a --delete --exclude='.env' --exclude='*.dump' --exclude='DEPLOYED_COMMIT' /tmp/signalmap-$STAMP/ /opt/signalmap/ && cd /opt/signalmap && ./tools/server/install.sh && docker compose up -d --build --wait && echo $OLD > /opt/signalmap/DEPLOYED_COMMIT"
```

### 6.2 Deploy obsahoval destruktivní migraci

Samotný návrat kódu **nestačí** — starší appka by sahala na sloupce, které
migrace zahodila. Nejdřív kód (příkaz z 6.1, ale **bez** `docker compose up`),
pak databáze ze zálohy z kroku 2.3:

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres pg_restore --clean --if-exists --no-owner --no-privileges -U signalmap_user -d signalmap' < "C:/Backups/SignalMap/dumps/<zaloha-z-2.3>.dump"
```

```bash
ssh signalmap 'cd /opt/signalmap && docker compose up -d --build --wait'
```

Pak celá kapitola 4 znovu — rollback se ověřuje stejně jako nasazení.

### Co nikdy

| | |
|---|---|
| `docker compose down -v` | smaže databázi i certifikáty |
| `git clean -fd` na serveru | smaže `.env` |
| rollback bez ověření | nevíš, jestli tě vrátil tam, kam jsi chtěl |

---

## Co runbook zatím nepokrývá

- **zastavení workeru** (2.2) — přibude se schedulerem
- **automatické nasazení** (CI/CD) — vědomě ne; jeden maintainer, jeden server,
  ruční postup s kontrolami je při téhle velikosti spolehlivější než pipeline,
  kterou nikdo neudržuje
- **nasazení bez výpadku** (blue/green, rolling) — vyžadovalo by dvě instance
  a load balancer; při krátkém výpadku v ohlášeném okně se to nevyplatí
