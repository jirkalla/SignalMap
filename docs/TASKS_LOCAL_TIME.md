# SignalMap — Tasks: Local Timezone Display for Timestamps

## v1.0 | Září 2026
## Branch: feature/signalmap-local-time-display
## Task ID prefix: LT

> Appka ukládá časy do databáze v UTC (`TIMESTAMPTZ`, správně), ale všech 14 míst napříč
> 7 šablonami, co čas zobrazují, dělá `.strftime(...)` přímo na uloženou hodnotu — bez
> převodu na jakoukoliv místní časovou zónu. Nalezeno 2026-09-14 při manuálním ověřování
> appky po deploy hardening práci (run zobrazoval čas o 2 hodiny posunutý oproti reálnému —
> rozdíl UTC vs. CEST). Tenhle dokument řeší opravu jako samostatnou práci, mimo fázový
> seznam `docs/TASKS.md` — stejný precedent jako `docs/TASKS_EXPORT.md`/`docs/TASKS_HARDENING.md`.

**Goal:** každý zobrazený časový údaj v appce se převede na časovou zónu prohlížeče
každého konkrétního uživatele, ne appka nebo server. Žádná nová databázová/backendová
logika — appka dál ukládá a posílá čas v UTC, převod je čistě zobrazovací (klientský).

---

## Design decisions (rozhodnuto v diskuzi před psaním kódu)

1. **Převod na klientovi (browser timezone), ne pevná zóna na serveru.** Zvažováno i
   jednodušší řešení (pevně nastavená zóna, např. `Europe/Prague`, na serveru) — zamítnuto
   na výslovný požadavek v konverzaci: appka může mít uživatele mimo střední Evropu, takže
   správně je zobrazit čas podle zóny prohlížeče každého uživatele, ne podle jedné pevné
   firemní zóny.
2. **Žádný nový `/static` adresář ani FastAPI `StaticFiles` mount.** Appka dnes má veškerý
   JS inline v jednom `<script>` bloku v `app/templates/base.html` — žádný build krok, žádná
   externí knihovna. Rozšíření tohohle stávajícího bloku o jednu další delegovanou funkci je
   konzistentní se stávajícím vzorem; zavádět samostatnou static-file infrastrukturu jen kvůli
   jedné funkci by bylo předčasné (`AI_INSTRUCTIONS.md` §5 — "don't design for hypothetical
   future requirements"). Až budoucí frontend rebrand (`docs/ROADMAP.md` #9) přinese větší
   množství vlastního JS, bude to přirozený moment static strukturu zavést — ne teď dopředu.
3. **Nativní `Intl.DateTimeFormat`, žádná JS knihovna** (ne `day.js`, `moment`, `timeago.js`)
   — appka nemá build krok a `Intl` je dostupné ve všech moderních prohlížečích bez jakékoliv
   závislosti.
4. **`<time datetime="...">` element se serverem vykresleným UTC textem jako fallback.**
   Pokud JS selže/je vypnutý, appka pořád ukáže čitelný (byť nepřevedený) UTC čas — žádná
   ztráta funkčnosti, jen ztráta pohodlí.
5. **Musí fungovat i po HTMX swapu**, ne jen při prvním načtení stránky — appka používá HTMX
   pro dynamické výměny obsahu. Řešeno zavěšením na `htmx:afterSettle` event (HTMX ho
   vyvolává na `document` po každé výměně), ne jen na `DOMContentLoaded`.
6. **Sdílené makro v `partials/macros.html`, ne 14 kopií stejné `<time>` značky** — stejný
   princip jako existující `is_own_domain()`/`can_edit()` sdílené helpery, nález z dřívějších
   code review kol (`docs/TASKS_PHASE4.md`, `docs/TASKS_PHASE6.md`).

---

## Task Index

| ID | Name | Status |
|----|------|--------|
| LT-T1 | Sdílené makro + klientský JS převod časové zóny | ⏳ |
| LT-T2 | Nahradit všech 14 `.strftime(...)` výskytů makrem | ⏳ |

---

## LT-T1 — Sdílené makro + klientský JS převod časové zóny

**Target:** `app/templates/partials/macros.html`, `app/templates/base.html`

1. `partials/macros.html` — nové makro `local_time(dt, seconds=True)`:
   - Vrátí prázdný řetězec, pokud `dt` je `None` (volající šablona řeší "None"/"—" hlášku
     sama, stejně jako dnes u podmíněného `if run.finished_at else t('common.none')` volání
     — makro jen vykresluje časovou hodnotu, pokud existuje).
   - Jinak vykreslí:
     ```html
     <time datetime="{{ dt.isoformat() }}" data-local-time>{{ dt.strftime('%Y-%m-%d %H:%M:%S' if seconds else '%Y-%m-%d %H:%M') }} UTC</time>
     ```
2. `base.html` — rozšířit stávající `<script>` blok (za stávající logiku, stejný styl
   komentářů — jen "proč", ne "co") o:
   ```js
   function localizeTimes(root) {
     root.querySelectorAll('[data-local-time]').forEach(function (el) {
       var d = new Date(el.getAttribute('datetime'));
       if (isNaN(d)) return;
       el.textContent = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' }).format(d);
     });
   }
   localizeTimes(document);
   document.addEventListener('htmx:afterSettle', function (event) { localizeTimes(event.target); });
   ```

Po dokončení:
1. `docker compose up -d --build` — appka nastartuje bez chyby
2. V prohlížeči dočasně přidat testovací `{{ local_time(...) }}` výskyt (nebo počkat na
   LT-T2) a v DevTools ověřit, že `textContent` po načtení odpovídá místní zóně, ne UTC
3. Vypnout JS (DevTools → disable JavaScript), obnovit stránku → ověřit, že se pořád
   zobrazuje čitelný UTC čas (fallback funguje)
4. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
feat(ui): add shared local_time() macro with client-side timezone conversion
```

---

## LT-T2 — Nahradit všech 14 `.strftime(...)` výskytů makrem

**Target:** `app/templates/runs/detail.html`, `app/templates/prompts/detail.html`,
`app/templates/clients/detail.html`, `app/templates/clients/list.html`,
`app/templates/providers/list.html`, `app/templates/ai_models/list.html`,
`app/templates/ai_models/form.html`

Prerekvizita: LT-T1 hotový (makro musí existovat, než ho tahle šablona začne používat).

1. V každé z 7 šablon rozšířit `{% from "partials/macros.html" import ... %}` o `local_time`.
2. Nahradit `X.strftime('%Y-%m-%d %H:%M:%S')` → `{{ local_time(X) }}` a
   `X.strftime('%Y-%m-%d %H:%M')` → `{{ local_time(X, seconds=False) }}` (podle toho, jaký
   formát dané místo dnes používá — neměnit úroveň detailu, jen přidat převod zóny).
3. Přesný seznam nahrazovaných výskytů (řádky podle stavu k 2026-09-14, mohou se posunout):
   - `runs/detail.html:41` (`run.started_at`, se sekundami)
   - `runs/detail.html:45` (`run.finished_at`, se sekundami, podmíněné)
   - `clients/detail.html:133` (`ps.created_at`, bez sekund)
   - `prompts/detail.html:184` (`run.started_at`, se sekundami)
   - `prompts/detail.html:201` (`run.started_at`, se sekundami)
   - `providers/list.html:31` a `:50` (`provider.updated_at`, bez sekund)
   - `ai_models/list.html:45` a `:71` (`model.updated_at`, bez sekund)
   - `clients/list.html:28` a `:45` (`client.created_at`, bez sekund)
   - `ai_models/form.html:36` (`model.created_at`, bez sekund)
   - `ai_models/form.html:54` (`row.effective_from`, bez sekund)
   - `ai_models/form.html:55` (`valid_until`, bez sekund, podmíněné s fallback textem)

Po dokončení:
1. `docker compose up -d --build`
2. Projít v prohlížeči **všechny** dotčené obrazovky (run detail, prompt detail, client
   detail/list, providers list, ai-models list/form) a ověřit, že čas sedí s reálným místním
   časem, ne UTC
3. Ověřit HTMX swap scénář — na stránce, kde HTMX mění obsah bez plného reloadu, že se nově
   vložený čas taky správně převede (ne že zůstane v UTC)
4. `pytest` — žádný test by se neměl rozbít
5. Implementation summary + navrhni commit message (nespouštěj git)

**Expected commit:**
```
refactor(ui): use local_time() macro for all displayed timestamps
```

---

## Completion Checklist

- [x] `local_time()` makro existuje a funguje (LT-T1)
- [x] Všech 14 výskytů `.strftime(...)` nahrazeno makrem (LT-T2)
- [ ] Ověřeno v prohlížeči na všech dotčených obrazovkách — čas odpovídá místní zóně
- [ ] Fallback bez JS ukazuje čitelný UTC čas
- [ ] HTMX swap scénář ověřen
- [x] `docs/TASKS.md` — poznámka, že tahle oprava existuje (odkaz na tenhle soubor)
