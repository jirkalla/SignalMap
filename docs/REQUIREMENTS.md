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
- FR-12: Every citation/source the provider returns is stored individually
  (URL, title, domain, position, and the answer span it supports, where the
  provider exposes that link).
- FR-13: If a response has no citations, the system records that
  explicitly (has_citations = false) rather than leaving it ambiguous or
  silently omitting the field.
- FR-14: User can view, per run: which exact prompt was used, which model
  and provider, when it ran, how long it took, its status (success/error),
  the rendered answer, the raw JSON response, and the list of citations.
- FR-15: User can view a list of all runs for a given prompt, showing
  model, provider, timestamp, and status at a glance.

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