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
4. OVĚŘENÍ           health, čísla, UI, logy
5. UZAVŘENÍ          odstávka pryč, sledování, zápis, zpráva
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
docker compose exec -T app python -m pytest -q
```

Na server nejde nic, co neprošlo tady. Projdi i ručně obrazovky, kterých se
změna týká.

### 1.2 Co běží na serveru teď

```bash
ssh signalmap 'cd /opt/signalmap && git rev-parse --short HEAD && git log -1 --format=%s'
```

```bash
ssh signalmap 'cd /opt/signalmap && docker compose exec -T postgres psql -U signalmap_user -d signalmap -tAc "SELECT version_num FROM alembic_version"'
```

**Ten commit si zapiš** — je to cíl případného rollbacku.

*(Pokud první příkaz řekne, že to není git checkout, provede se jednorázový
převod podle README, sekce „One-time: make the server a Git checkout".)*

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
ssh signalmap 'touch /opt/signalmap/deploy/maintenance.on'
```

> ⚠️ **Zatím neimplementováno.** Vyžaduje statickou stránku v Caddy
> (HTML soubor + mount + `@maintenance` matcher v Caddyfile), vrácenou jako
> HTTP 503 s hlavičkou `Retry-After`. Stránka musí patřit **Caddy, ne appce** —
> stránka servírovaná appkou nefunguje právě ve chvíli, kdy appka neběží.
> Do té doby tenhle krok přeskoč a spolehni se na oznámení z 1.4.

**Proč je odstávka před zálohou, ne za ní:** kdyby appka po záloze ještě
chvíli přijímala data, rollback by o ně přišel. Když nejdřív zavřeš vstup,
je záloha přesně tím stavem, do kterého se vracíš.

### 2.2 Zastavit worker

```bash
ssh signalmap 'cd /opt/signalmap && docker compose stop worker'
```

> ⚠️ **Zatím neexistuje** — přibude se schedulerem (`docs/TASKS_SCHEDULER.md`).
> Pak bude povinný: worker se zastavuje jako první, aby uprostřed nasazení
> neběžel placený run.

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

Jeden příkaz — stáhne commit, přeinstaluje hostitelské skripty, přestaví
a nastartuje:

```bash
ssh signalmap 'cd /opt/signalmap && git fetch origin && git reset --hard origin/master && ./tools/server/install.sh && docker compose up -d --build --wait && git log -1 --oneline'
```

Co se děje uvnitř a proč v tomhle pořadí:

| | |
|---|---|
| `git reset --hard` | netrackované soubory (`.env`!) nemaže — **`git clean` nikdy nespouštěj** |
| `./tools/server/install.sh` | bez něj se hostitelské skripty tiše rozejdou s repem |
| `docker compose up -d --build --wait` | staví **zatímco starý kontejner obsluhuje**, teprve pak prohodí — appku předem nezastavuj, jen by to prodloužilo výpadek o dobu buildu |
| migrace | běží automaticky ze `CMD` v `dockerfile`; když selžou, **appka nenaběhne** — nedostaneš polovičně zmigrovanou běžící databázi |

---

## 4. Ověření

### 4.1 Technická kontrola

```bash
curl --fail --show-error https://expressyourself.ai/health
```

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

### 4.2 Kontrola v prohlížeči

- přihlášení stávajícím účtem
- `/clients` — klienti jsou tam
- `/ops` — čísla a náklady se zobrazují
- detail libovolného runu — odpověď i citace se vykreslí
- obrazovka, které se nasazovaná změna týká

### 4.3 Logy

```bash
ssh signalmap 'cd /opt/signalmap && docker compose logs --tail=100 app | grep -iE "error|traceback|exception" || echo "zadne chyby"'
```

---

## 5. Uzavření

### 5.1 Odstávková stránka VYP

```bash
ssh signalmap 'rm -f /opt/signalmap/deploy/maintenance.on'
```

**Nejčastěji zapomenutý krok celého postupu.** Až bude stránka
implementovaná, dej si ho do seznamu jako první věc po ověření.

### 5.2 Pět minut sledovat

```bash
ssh signalmap 'cd /opt/signalmap && docker compose logs -f --tail=20 app caddy'
```

Zavřít terminál hned po `curl /health` je, jak se přehlédne chyba, která se
projeví až při prvním skutečném požadavku uživatele.

### 5.3 Zápis do deploy logu

```bash
ssh signalmap 'echo "$(date -Is) $(cd /opt/signalmap && git rev-parse --short HEAD) OK" >> /var/log/signalmap-deploys.log'
```

Za půl roku je tohle jediné místo, kde zjistíš, kdy se co nasadilo.

### 5.4 Úklid a zpráva

- `/opt/signalmap.old` smaž, až je vše ověřené (existuje jen po prvním převodu)
- e-mail „hotovo" s popisem změn

---

## 6. Rollback

### 6.1 Deploy neobsahoval destruktivní migraci

```bash
ssh signalmap 'cd /opt/signalmap && git reset --hard <commit-z-kroku-1.2> && ./tools/server/install.sh && docker compose up -d --build --wait'
```

### 6.2 Deploy obsahoval destruktivní migraci

Samotný návrat kódu **nestačí** — starší appka by sahala na sloupce, které
migrace zahodila. Nejdřív kód, pak databáze ze zálohy z kroku 2.3:

```bash
ssh signalmap 'cd /opt/signalmap && git reset --hard <commit-z-kroku-1.2> && ./tools/server/install.sh'
```

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

- **odstávková stránka** (2.1, 5.1) — návrh hotový, implementace čeká
- **zastavení workeru** (2.2) — přibude se schedulerem
- **automatické nasazení** (CI/CD) — vědomě ne; jeden maintainer, jeden server,
  ruční postup s kontrolami je při téhle velikosti spolehlivější než pipeline,
  kterou nikdo neudržuje
- **nasazení bez výpadku** (blue/green, rolling) — vyžadovalo by dvě instance
  a load balancer; při krátkém výpadku v ohlášeném okně se to nevyplatí
