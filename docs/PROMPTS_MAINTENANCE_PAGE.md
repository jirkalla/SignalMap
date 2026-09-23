# SignalMap — Claude Code Session Prompts: Maintenance Page

## Status: ✅ Done — PR #16, merged 2026-09-18, released in v1.0.0

## v1.0 | Září 2026
##
## JAK POUŽÍVAT:
## 1. git checkout -b feature/signalmap-maintenance-page (z aktuálního master)
## 2. Tři prompty (MP-1 až MP-3), pořadí vynucené — MP-2 popisuje v runbooku
##    to, co MP-1 postaví, a MP-3 to nasadí.
## 3. SESSION HEADER vlož jen JEDNOU na začátku nové konverzace pro tuhle větev.
## 4. Po každém promptu: git commit (message navržená na konci promptu,
##    commit provádíš ty, ne agent — agent NIKDY nespouští git commit/push
##    sám bez výslovného potvrzení, a to i přesto, že zprávu sám navrhl).
## 5. PROGRESS TRACKING — po každém dokončeném a commitnutém promptu:
##    a) V TOMTO souboru dopiš pod nadpis promptu řádek `### DONE — commit {hash}`.
##    b) V docs/TASKS_MAINTENANCE_PAGE.md přepni řádek daného task ID v tabulce
##       "Task Index" z ⏳ na ✅.
## 6. Kompletní zdůvodnění vč. design decisions 1-11:
##    docs/TASKS_MAINTENANCE_PAGE.md — přečti si ho celý před MP-1.
## 7. Tahle větev sahá na PRODUKČNÍ konfiguraci proxy. Nic se nenasazuje,
##    dokud to neprojde lokálním curl testem podle MP-1.

---
---

## SESSION HEADER (zkopíruj na začátek KAŽDÉ session v této větvi)

```
Pracuji na projektu SignalMap, branch feature/signalmap-maintenance-page.
Před začátkem si přečti v tomto pořadí:

1. AI_INSTRUCTIONS.md
2. docs/TASKS_MAINTENANCE_PAGE.md — CELÉ, hlavně design decisions 1-11
3. docs/DEPLOYMENT.md — kroky 2.1 a 5.1, kterých se to týká

KONTEXT: Appka běží na https://expressyourself.ai za Caddy proxy
(docker-compose.yaml, služba `caddy`, konfigurace je inline v bloku
`configs.caddyfile`). První ostré nasazení proběhlo 2026-09-18 bez
odstávkové stránky — kdo web během výpadku otevřel, dostal chybu
prohlížeče. Tahle větev to napravuje.

Stránka `tools/server/maintenance.html` UŽ EXISTUJE — dvojjazyčná DE/EN,
ověřená v prohlížeči na ~375 px i desktopu. Nepřepisuj ji, chybí jen
mechanismus, který ji zobrazí.

KRITICKÉ:
- Stránku servíruje Caddy, NE aplikace. Stránka z appky nefunguje právě
  tehdy, když appka neběží.
- Vrací HTTP 503 + Retry-After, NIKDY 200 — jinak si ji prohlížeče a
  vyhledávače zakešují jako platný obsah webu.
- Příznakový soubor leží v /var/lib/signalmap/maintenance/, tedy MIMO
  /opt/signalmap. Nasazení tam pouští rsync --delete a příznak uvnitř by
  zmizel uprostřed odstávky.
- Zapnutí a vypnutí nesmí nic restartovat. Caddy kontroluje existenci
  souboru při každém požadavku.
- install.sh příznak nikdy nevytváří ani nemaže.
- Žádné externí zdroje ve stránce (fonty, CDN, ikony) — ukazuje se ve
  chvíli, kdy už je něco nedostupné.
```

---
---

## MP-1 — Caddy mechanismus

Viz `docs/TASKS_MAINTENANCE_PAGE.md` T1. Shrnutí: mount
`/var/lib/signalmap/maintenance:/srv:ro` do služby `caddy`, `@maintenance`
větev v Caddyfile s `file_server { status 503 }` a `Retry-After`, rozšíření
`tools/server/install.sh` o instalaci stránky.

Nejdřív navrhni CO uděláš + PROČ (AI_INSTRUCTIONS.md §2: přesné cesty
souborů + zdůvodnění proti T1) a počkej na potvrzení.

**Kritické:** Caddyfile z TASKS dokumentu **opiš jen jako výchozí bod a
ověř ho proti běžící Caddy.** Syntaxe `file` matcheru i `file_server {
status }` se mezi verzemi Caddy měnila; kdyby tenhle tvar nefungoval, druhá
cesta je `error 503` + `handle_errors`. **Netvrď, že to funguje, dokud to
nevrátí 503 v `curl -i`.**

Adresář `/var/lib/signalmap/maintenance` **nesmí** mít práva `700` jako
adresář se zálohami — Caddy v kontejneru běží pod jiným uživatelem a musí
ho umět přečíst.

**Po dokončení:**
1. Připravit testovací adresář mimo repo a přesměrovat na něj mount
   v `docker-compose.override.yaml` (dočasně, override je gitignorovaný).
2. `docker compose up -d --wait`
3. Se zapnutým příznakem: `curl -i http://127.0.0.1:58000/health | head -5`
   → musí být **503**, `Retry-After` a obsah stránky.
4. `rm` příznaku, `curl` znovu → **200** a `{"status":"ok"}`, **bez
   restartu kontejneru**.
5. V prohlížeči zkontrolovat vzhled na ~375 px a desktopu.
6. Uklidit dočasnou úpravu overridu.
7. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(infra): serve a branded maintenance page from Caddy
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MP-2 — Runbook

Viz `docs/TASKS_MAINTENANCE_PAGE.md` T2. Shrnutí: v `docs/DEPLOYMENT.md`
opravit cestu k příznaku v krocích 2.1 a 5.1, smazat varování „zatím
neimplementováno", vyškrtnout položku ze sekce „Co runbook zatím
nepokrývá", a do kroku 4 přidat kontrolu, že odstávka je po nasazení
**vypnutá**.

Nejdřív navrhni CO uděláš + PROČ a počkej na potvrzení.

**Kritické:** ta kontrola v kroku 4 není formalita. Zapomenutá zapnutá
odstávková stránka je nejpravděpodobnější chyba celého postupu — appka
běží, všechno je zelené, a uživatelé pořád vidí „vracíme se za chvíli".

**Po dokončení:**
1. Projít runbook od začátku do konce a ověřit, že žádný krok neodkazuje
   na neexistující cestu ani na neimplementovanou věc.
2. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs: fold the maintenance page into the deploy runbook
```

### (sem dopiš DONE — commit {hash} až bude hotovo)

---

## MP-3 — Nasazení a ověření na produkci

Viz `docs/TASKS_MAINTENANCE_PAGE.md` T3. **Tenhle prompt nepíše kód** —
provádí nasazení podle `docs/DEPLOYMENT.md` a ověřuje výsledek.

**Kritické:** změna `docker-compose.yaml` znamená znovuvytvoření kontejneru
`caddy`, tedy krátké přerušení. **Spoj to s oknem pro aktualizaci OS**
(design decision 11) — dvě změny, jedno okno, jedno oznámení uživatelům.

**Postup:**
1. Celý runbook `docs/DEPLOYMENT.md`, kapitoly 1–5.
2. Po nasazení ověřit v tomhle pořadí:
   - bez příznaku: `curl -i https://expressyourself.ai/health` → **200**
   - zapnout příznak → **503** + stránka, zkontrolovat i v mobilu
   - vypnout příznak → **200**
3. Doplnit do `docs/ROADMAP.md`, že položka je hotová.
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
docs(roadmap): record the maintenance page as done
```

### (sem dopiš DONE — commit {hash} až bude hotovo)
