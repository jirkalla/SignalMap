# SignalMap — Project Requirements (Phase 1: Thin Vertical Slice)

## 1. Purpose

Prove the core loop of the AI corporate-perception system end to end, with
the smallest scope that's still real: configure a client, ask a real
question about them, run it against an AI provider, and see exactly what
came back — including sources cited. Everything else in the full MVP brief
(strategy config, multi-provider comparison, analysis skills, dashboard,
scheduling, auth) is deliberately deferred until this loop is proven.

## 2. Functional Requirements

### 2.1 Client management
- FR-1: User can create a client with name, industry, and free-text notes.
- FR-2: User can view a list of existing clients.
- FR-3: User can edit an existing client's name, industry, and notes.
- Strategy/reputation fields (guiding principles, priority topics, desired
  wording) are explicitly **out of scope for phase 1** — deferred to the
  analysis-layer phase.

### 2.2 Prompt management
- FR-4: User can create a prompt set under a client.
- FR-5: User can add one or more prompts to a prompt set. Each prompt has:
  free-text question, a market (language + country), an optional topic
  label, and an active/inactive flag.
- FR-6: User can view the list of prompts for a client.
- Prompt versioning field exists in the data model from phase 1 (cheap to
  include now), but a UI for editing/versioning a prompt is not required
  yet — only creating new prompts.

### 2.3 Running prompts against an AI provider
- FR-7: User can select a prompt and a model, and manually trigger a run.
- FR-8: Only Google Gemini is supported as a provider in phase 1, with
  Google Search grounding enabled. The system must not hardcode a single
  model — model selection is a user choice among the seeded models.
- FR-9: Scheduled/automatic runs are out of scope for phase 1 — manual
  trigger only.

### 2.4 Storing and viewing results
- FR-10: Every run stores the complete, unmodified raw provider response.
- FR-11: Every run stores a human-readable rendered answer text, separate
  from the raw payload.
- FR-12: Every claim-source link the provider returns is stored
  individually: URL, title, domain, position, and — where the provider
  exposes it — the text on each side of the link. Those are two different
  texts and they live in two separate fields: the **answer span** the source
  supports (`cited_answer_span`, plus `answer_span_start`/`answer_span_end`
  locating it, both stored exactly as the provider returned them) and the
  **passage quoted from the source page** (`source_passage`). No provider
  fills both halves — Gemini and OpenAI expose the answer span, Anthropic
  the source passage — so each field is optional and which one is populated
  is a property of the provider's API, not a gap.
  A stored row is one *(answer segment, source)* pair, not one source: an
  answer may cite the same URL for several claims and several URLs for one
  claim, and every such pair is its own row rather than being flattened.
  Duplicate URLs within one response are therefore expected and are not
  deduplicated — collapsing them would discard links the provider actually
  returned.
- FR-13: If a response has no citations, the system records that
  explicitly (has_citations = false) rather than leaving it ambiguous or
  silently omitting the field.
- FR-14: User can view, per run: which exact prompt was used, which model
  and provider, when it ran, how long it took, its status (success/error),
  the rendered answer, the raw JSON response, and the list of citations.
- FR-15: User can view a list of all runs for a given prompt, showing
  model, provider, timestamp, and status at a glance.

> **What a citation count means.** Because a row is one claim-source link
> (FR-12), `count(citations)` answers *"how many times did a source back a
> claim"*, not *"how many sources were used"*. The two numbers are
> different and both are legitimate — for sources, count distinct
> `source_domain` (or `source_url`) instead. Dashboard KPIs, the domain
> league table and exports all use the link count, uniformly across
> providers; a domain backing five claims is counted five times there by
> design, since that is what its influence on the answer looks like.
> Historical figures moved upward when this was corrected for Gemini
> (`docs/TASKS_GEMINI_CITATIONS.md`), consistently across the whole
> archive rather than as a step at the deploy date.

### 2.5 Error handling
- FR-16: If a provider call fails (timeout, API error, rate limit), the
  run is recorded with status "error" and a stored error message — it must
  be visible in the UI, not just silently missing from the run list.

## 3. Non-Functional Requirements

- NFR-1 (Language): All backend code — variables, functions, comments,
  docstrings, log/exception messages — is written in English, regardless
  of the fact that the product and its builder operate in a German/Czech
  context. See signalmap-conventions skill.
- NFR-2 (Localization architecture): UI text must be built for DE/EN
  bilingual support from the first screen — a shared translation-key
  mechanism (not hardcoded strings), even though German content itself
  isn't required to be filled in during phase 1.
- NFR-3 (DRY / reusability): UI components (form fields, tables) are built
  to be reused across the whole project, not per-screen. A component built
  for one CRUD screen must be reusable by the next one.
- NFR-4 (API documentation): FastAPI's automatic OpenAPI/Swagger docs
  (`/docs`) must be usable as real documentation — every endpoint needs a
  meaningful docstring, every non-obvious Pydantic field needs a
  description.
- NFR-5 (Error response consistency): All API errors follow one shared
  structured shape (error_code, message, detail) rather than ad-hoc
  strings.
- NFR-6 (Data integrity): Historical rows (prompt versions, raw responses)
  are never overwritten — new data is always a new row.
- NFR-7 (Responsiveness): Screens should be usable on mobile, tablet, and
  desktop — not blocked in phase 1 by missing visual polish, but the
  underlying HTML/layout choices shouldn't actively break on a narrow
  screen either.
- NFR-8 (Portability): The application must run identically via Docker
  Compose on the developer's PC and on a future VPS deployment — no
  environment-specific setup steps outside `.env` values.
- NFR-9 (Measurement methodology limitation — API vs. deployed interface):
  Every provider adapter calls the provider's API directly (with grounding/
  `web_search` enabled where available), not the provider's deployed
  consumer chat product (chatgpt.com, gemini.google.com, claude.ai).
  External research (Wang, Baumann, Ho & Koyejo, "API Benchmark Scores Do
  Not Reliably Transfer to Chatbot Interfaces," arXiv:2609.08861, Sept
  2026) found that API-based evaluations of ChatGPT/Claude/Gemini diverge
  from their deployed chat interfaces by ~3.4 percentage points in
  accuracy and ~2.1 points in test-retest agreement — a bigger gap than
  between consecutive model generations — and that exposed API controls
  (system prompt, sampling, reasoning settings) could not reliably close
  it. SignalMap's runs should therefore be read as "how the provider's API
  answers with search grounding on," which is a close proxy for, but not
  proven identical to, what an end user sees in the provider's consumer
  chat product. This is a known limitation of the measurement approach
  itself, not a defect — flag it if a future request assumes API results
  are interchangeable with chat-interface behavior.
- NFR-10 (Ops visibility is admin/editor only, never client-facing): The
  `/ops` dashboard (docs/TASKS_OPS_DASHBOARD.md) surfaces cost, latency,
  and error data — internal engineering/operations visibility, not a
  client-facing report. It must never be reachable by the `viewer` role or
  by any future external/client-scoped account (docs/ROADMAP.md #10), and
  this must be enforced on every route and endpoint itself (`require_role`),
  not just hidden in the navigation. This is a separate access-control
  boundary from the client-facing `/dashboard` (phase 4), which viewers can
  already reach — the two must never be merged into one screen or one
  permission check.
- NFR-11 (Ops aggregates exclude test clients by default): `/ops` (NFR-10)
  runs, success rate, and cost figures never include a client flagged
  `clients.is_test` unless the analyst explicitly asks for them
  (`?include_test=1`, docs/TASKS_PRE_SCHEDULER.md PRE-1) — a client used to
  validate the app against real providers is not real client work, and
  counting it inflates run volume and the cost estimate the service is
  priced from. The flag is evaluated per query, not stored on the run, so
  flagging or unflagging a client changes what its ENTIRE run history
  contributes to these totals, not just runs from that point on. It never
  hides the client anywhere else — `/clients`, its own detail page, every
  client selector, and the client-facing `/dashboard` (NFR-10's own
  boundary) show it exactly like any other client, badged as a test
  client. Only an admin may set the flag.
- NFR-12 (A run's cost is priced at its own time, and "unknown" is never
  "zero"): `ai_model_price_components` is append-only — a price change is
  always a new row, never an edit to an existing one (NFR-6's evidence
  discipline extended to pricing data) — and a run's cost is computed from
  whichever price row was effective at that run's own `started_at`, never
  from today's price (docs/TASKS_COST_COMPONENTS.md). A run made before any
  price was ever recorded for its model has no computable cost and must
  show as such (`null`/a dash), never as `$0` — a missing price is a gap in
  what we know, not a claim that the run was free. `/ai-models`'s price
  history displays this per-component timeline grouped and merged for
  readability (docs/TASKS_PRE_SCHEDULER.md PRE-2), but every underlying
  record stays exactly as entered, including two records that carry the
  same price with different validity — expected wherever a price was later
  backfilled for a period before it was entered.
- NFR-13 (Domains are normalized for comparison and grouping, never in
  evidence): Everywhere a domain is aggregated, grouped, or compared — the
  cited-domains league table and its unique-domain count (`/dashboard`),
  `domain_classifications` lookups, and `is_own_domain` matching — it is
  lowercased with a leading `www.` stripped first, so `meag.com` and
  `www.meag.com` are treated as one source
  (docs/TASKS_PRE_SCHEDULER.md PRE-4). `citations.source_domain` itself is
  never rewritten: it is evidence of exactly what the provider returned
  (NFR-6), and `app/services/export.py` exports it to the client raw, in
  its original form. Subdomains are a deliberate exception — `blog.acme.com`
  is never unified with `acme.com`, since it is a different source, not a
  formatting variant of the same one.

## 3a. Phase 1 amendments (2026-09-08)

Deliberate deviations from the scope above, decided when building the
phase-1 prototype, recorded here rather than left as conversation history:

- **Tailwind CSS is in scope now, not deferred.** §4 below originally
  listed "Tailwind visual styling" as out of scope for phase 1. The
  prototype is being used to present the project, so a plain-HTML screen
  would undersell it — Tailwind is loaded via CDN (no build step), so the
  cost of doing this now is negligible.
- **i18n (NFR-2) ships as mechanism only, not path-prefix routing.** The
  signalmap-conventions skill recommends URL path-prefix locale routing
  (`/de/...`, `/en/...`) as the long-term direction. Phase 1 implements the
  `t()` translation mechanism with real EN/DE content and a cookie-based
  locale switch instead — full path-prefix routing touches every route and
  is a bigger architectural change than "exactly Tasks 1–6" calls for. The
  skill's recommended direction is still the target for a later phase.
- **Market is not purely descriptive metadata as originally written.**
  Confirmed against the current API docs: Gemini's Google Search grounding
  has no location/language parameter at all, while Anthropic's `web_search`
  tool does (`user_location`). So for Gemini, a prompt's market is now also
  turned into a `system_instruction` locale-framing hint sent with every
  run (nudges answer language/framing; does not change what the underlying
  search retrieves) — see app/routers/runs.py. FR-7 is extended
  accordingly: a run is triggered with a prompt, a model, **and a market**,
  which defaults to the prompt's own market but can be overridden per run
  (`runs.market_id`, migration 0002) to compare how the same prompt text
  is framed for a different one. The market actually used is recorded on
  the run, not inferred from the prompt, so history stays accurate even
  when they diverge.

## 4. Explicitly Out of Scope for Phase 1

- Authentication and user accounts (single shared local access for now).
- Multi-tenancy / row-level access control (client sees only their data).
- StrategyConfig (guiding principles, reputation attributes).
- Scheduled/recurring runs.
- Source/signal map, intervention hypotheses.
- Tailwind visual styling / "evidence dossier" design polish.
- Filled-in German translation content (mechanism only, not content).

**Amendment (2026-09-09):** "Any provider other than Google Gemini" removed
from this list — a second provider (Anthropic Claude) shipped in phase 2,
see `docs/TASKS.md` "Phase 2: Anthropic provider + admin UI" and
`docs/TASKS_PHASE2.md`.

**Amendment (2026-09-10):** "Analysis skills and structured AI-generated
analysis results" removed from this list — the first analysis skill
(deterministic mention/visibility detection) shipped in phase 3, see
`docs/TASKS.md` "Phase 3: First analysis skill" and `docs/TASKS_PHASE3.md`.

**Amendment (2026-09-11):** "Dashboard" and "Vue3 interactive components"
removed from this list — dashboard v0 (domain league table + time series,
the first Vue3 island in the app) shipped in phase 4, see `docs/TASKS.md`
"Phase 4: Dashboard v0" and `docs/TASKS_PHASE4.md`. Source/signal map and
intervention hypotheses remain out of scope — later phases.

**Amendment (2026-09-11):** Competitor tracking and share-of-voice/position
figures shipped in phase 5, see `docs/TASKS.md` "Phase 5: Competitive
visibility" and `docs/TASKS_PHASE5.md`. Not previously named anywhere in
this list, in either direction — phase 1 predates the concept, so this
doesn't remove anything from §4 above; recorded here for the same
discoverability reason phases 2–4 each got an entry.

## 5. Data Model Reference

See `schema_phase1.sql` for the authoritative phase 1 schema: `clients`,
`markets`, `prompt_sets`, `prompts`, `providers`, `ai_models`, `runs`,
`raw_responses`, `citations`.

## 6. Acceptance Criteria (Phase 1 "Done")

- A user can create a client, add a prompt set with at least one prompt
  (with a market attached), and see both listed correctly.
- A user can trigger a run of that prompt against a selected Gemini model
  and see the run appear with status "success" (or "error" with a message,
  on failure).
- Opening the run detail page shows the rendered answer, the full raw JSON,
  and any citations the provider returned — with no manual database
  inspection required to see any of this.
- The application starts from a clean checkout with `docker compose up -d
  --build` and no manual steps beyond running the Alembic migration.