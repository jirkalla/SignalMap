# SignalMap — Tasks: Maintenance Page

## v1.0 | Září 2026
## Branch: feature/signalmap-maintenance-page
## Task ID prefix: MP

Status: navrženo v konverzaci 2026-09-18, po prvním ostrém nasazení na
produkci. Malá, uzavřená práce — tři úkoly.

Vzniklo z `docs/DEPLOYMENT.md`, kde kroky 2.1 a 5.1 od začátku počítají
s odstávkovou stránkou, ale nesou varování „zatím neimplementováno" a
**nesprávnou cestu** k příznakovému souboru. Dnešní nasazení proběhlo bez
ní: kdo web během výpadku otevřel, dostal chybu prohlížeče.

**Goal:** během plánované odstávky ukázat návštěvníkovi branded stránku
s HTTP 503 místo chyby spojení, a umět ji zapnout i vypnout jedním
příkazem, bez restartu čehokoliv.

---

## Design decisions (rozhodnuto před psaním kódu)

1. **Stránku servíruje Caddy, ne aplikace.** Stránka servírovaná appkou
   nefunguje právě ve chvíli, kdy appka neběží — což je jediná chvíle, kdy
   je potřeba. Proto statický soubor v proxy.
2. **HTTP 503 + `Retry-After`, ne 200.** Odstávková stránka vrácená jako 200
   je pro prohlížeč i vyhledávač platný obsah webu a může se zakešovat.
   `503` je „dočasně nedostupné", `Retry-After` říká kdy to zkusit znovu.
3. **Příznakový soubor leží mimo `/opt/signalmap`** — v
   `/var/lib/signalmap/maintenance/`. Nasazení pouští v adresáři aplikace
   `rsync --delete`; příznak uvnitř by zmizel uprostřed nasazení, tedy
   přesně tehdy, když běží migrace a appka je dole. `/var/lib/<aplikace>` je
   podle FHS místo pro stav, který má přežít aktualizace — stejná úvaha jako
   u `/var/backups/signalmap`.
4. **Přepínání nic nerestartuje.** Caddy se na existenci souboru dívá při
   každém požadavku, takže `touch`/`rm` platí okamžitě. Alternativy
   (zakomentovaný blok v Caddyfile, proměnná v `.env`) by vyžadovaly
   `docker compose up` — přesně to, co během výpadku nechceš dělat.
5. **Mount jen pro čtení** (`:ro`). Caddy stránku jen čte; z hostitele se
   zapisuje normálně. Proxy si tím pádem nemůže vlastní odstávkovou stránku
   přepsat.
6. **Žádné externí zdroje.** Systémové fonty, žádné CDN, žádná knihovna
   ikon, všechno inline v jednom souboru. Stránka se ukazuje ve chvíli, kdy
   už je něco nedostupné — nesmí záviset na dalších službách. Logo je proto
   text, ne obrázek.
7. **Oba jazyky na jedné stránce, bez přepínače.** Statický soubor nemá jak
   poznat uživatele a JS přepínač na chybové stránce je zbytečné riziko.
   Němčina nahoře kvůli Knaufu, angličtina pod ní.
8. **Text zdůrazňuje, že se neztrácejí data** — to je první otázka, kterou
   si uživatel položí, když appka neběží.
9. **Vizuál podle screenshotu k budoucímu UI** (`docs/ROADMAP.md` #9) —
   žlutý brand pruh, magenta/cyan podtržení, bílá karta. Stránka tím
   neztvrzuje design systém (ten podle #9 neexistuje), jen se drží toho
   jediného vizuálu, co dnes je.
10. **`prefers-reduced-motion`** vypne pulzující tečku.
11. **Nasazení se spojí s oknem pro aktualizaci OS.** Změna
    `docker-compose.yaml` znamená znovuvytvoření kontejneru `caddy`, tedy
    další krátké přerušení — a jedno přerušení navíc už bylo ohlášené kvůli
    OS. Dvě změny, jedno okno, jedno oznámení.

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| T1 | Caddy mechanismus: mount, Caddyfile, `install.sh` | ⏳ |
| T2 | Runbook: opravit cesty, odstranit varování | ⏳ |
| T3 | Nasazení a ověření na produkci | ⏳ |

`tools/server/maintenance.html` už existuje (napsaná a ověřená v prohlížeči
na ~375 px i desktopu, 2026-09-18) — commitne se spolu s T1.

---

## T1 — Caddy mechanismus

**Target:** `docker-compose.yaml` (služba `caddy` + blok `configs.caddyfile`),
`tools/server/install.sh`

1. Mount do služby `caddy`:

   ```yaml
         - /var/lib/signalmap/maintenance:/srv:ro
   ```

2. Caddyfile — přidat před `reverse_proxy` větev, která se uplatní jen když
   příznak existuje. Zhruba tenhle tvar:

   ```
   ${SITE_ADDRESS:-:80} {
     encode gzip

     @maintenance file /srv/maintenance.on
     handle @maintenance {
       root * /srv
       rewrite * /index.html
       header Retry-After 300
       file_server {
         status 503
       }
     }

     handle {
       reverse_proxy app:8000
     }
   }
   ```

   **Přesnou syntaxi `file` matcheru a `file_server { status }` je nutné
   ověřit proti běžící Caddy, ne převzít odtud** — mezi verzemi se tyhle
   direktivy měnily. Kdyby `status` v téhle podobě neexistoval, druhá cesta
   je `error 503` + `handle_errors`.

3. `install.sh` — založit `/var/lib/signalmap/maintenance` a nakopírovat do
   něj stránku jako `index.html`:

   ```sh
   mkdir -p "$MAINT_DIR"
   install -o root -g root -m 644 "$SRC/maintenance.html" "$MAINT_DIR/index.html"
   ```

   Adresář **nesmí** mít `700` jako adresář se zálohami — Caddy v kontejneru
   běží pod jiným uživatelem a musí ho umět přečíst.

4. `install.sh` příznak **nikdy nemaže ani nevytváří** — kdyby ho mazal,
   nasazení by odstávku uprostřed vlastní práce vyplo.

**Ověření lokálně** (mimo repo, ať se testovací příznak neplete do gitu):

```bash
mkdir -p /c/Users/jiriv/signalmap-maintenance && cp tools/server/maintenance.html /c/Users/jiriv/signalmap-maintenance/index.html && touch /c/Users/jiriv/signalmap-maintenance/maintenance.on
```

Dočasně přesměrovat mount v `docker-compose.override.yaml` na tenhle
adresář, pak:

```bash
docker compose up -d --wait && curl -i http://127.0.0.1:58000/health | head -5
```

**Done when:** se zapnutým příznakem vrací `curl` **`HTTP/1.1 503`**,
hlavičku `Retry-After` a obsah stránky; po `rm` příznaku vrací
`{"status":"ok"}` — obojí **bez restartu kontejneru**. Ověřit i v prohlížeči
na ~375 px a desktopu.

**Expected commit:** `feat(infra): serve a branded maintenance page from Caddy`

---

## T2 — Runbook

**Target:** `docs/DEPLOYMENT.md`

1. Krok 2.1 — opravit cestu na
   `/var/lib/signalmap/maintenance/maintenance.on` a **smazat varovný blok**
   „zatím neimplementováno".
2. Krok 5.1 — totéž pro vypnutí.
3. Sekce „Co runbook zatím nepokrývá" — vyškrtnout odstávkovou stránku.
4. Doplnit do kroku 4 (Ověření) kontrolu, že stránka je **vypnutá**:
   po nasazení musí `curl` vracet `200`, ne `503`. Zapomenutá zapnutá
   odstávka je nejčastější chyba celého postupu.

**Done when:** runbook jde projít od začátku do konce, aniž by kterýkoliv
krok odkazoval na něco neexistujícího.

**Expected commit:** `docs: fold the maintenance page into the deploy runbook`

---

## T3 — Nasazení a ověření

**Target:** produkce, žádné soubory

Spojit s oknem pro aktualizaci OS (design decision 11).

1. Nasadit podle `docs/DEPLOYMENT.md` — `install.sh` v kroku 3 stránku
   nainstaluje sám.
2. Ověřit, že **bez** příznaku appka běží normálně:
   `curl -i https://expressyourself.ai/health` → `200`.
3. Zapnout příznak, ověřit `503` a stránku, vypnout, ověřit `200`.
4. Zapsat do `docs/ROADMAP.md`, že položka je hotová.

**Done when:** odstávka se dá zapnout a vypnout na produkci jedním
příkazem a ověřeně funguje.

---

## Co tahle větev vědomě nedělá

- **Neplánuje odstávku sama.** Zapnutí a vypnutí je ruční krok v runbooku;
  automatizace dává smysl až spolu se skriptem pro celé nasazení
  (`docs/DEPLOYMENT.md`, sekce „Co runbook nepokrývá").
- **Neukazuje stránku při neplánovaném výpadku.** Caddy by ji uměl
  servírovat i místo 502, když appka nereaguje — to je ale jiná věc
  (rozlišit „aktualizujeme" od „něco je rozbité") a patří do vlastního
  rozhodnutí, ne sem.
- **Neřeší odhad času návratu.** Stránka neříká „zpátky v 9:15" — takový
  údaj by musel někdo udržovat a zastaralý je horší než žádný.
