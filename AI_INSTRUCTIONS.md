# AI Instructions — SignalMap
## v1.0 | 2026-09-08

This file governs how an AI coding agent (Claude Code, or any equivalent)
must behave when working on the SignalMap repository.

Read `docs/REQUIREMENTS.md` and the `signalmap-conventions` skill before
starting any task. Together they are the single source of truth for what
to build and how — this file does not repeat their content. It covers *how
the agent should operate*: workflow discipline, git hygiene, and the things
that must never happen silently.

======================================================================
1. PROJECT CONTEXT
======================================================================

SignalMap is an internal AI corporate-perception intelligence tool: it
runs configured prompts against AI providers, stores raw answers and
citations, and (in later phases) runs structured analysis over them.

Stack:
  Backend    FastAPI + SQLAlchemy + PostgreSQL
  Frontend   Jinja2 + HTMX (CRUD screens) + Vue3 islands (later, dashboard)
  Styling    Tailwind CSS (CDN, no build step)
  Migrations Alembic, driven by `schema_phase1.sql` as the authoritative schema
  Deploy     Docker Compose, identical on dev PC and VPS

Build sequencing (see the skill for full detail — do not reorder this):
  1. One client, one prompt, one provider (Google), end-to-end, no auth.
  2. Second provider (Anthropic).
  3. First analysis skill.
  4. Dashboard.
  5. Auth + multi-tenancy — only once 1–4 are solid.

======================================================================
2. BEFORE WRITING ANY CODE — TELL ME
======================================================================

Before writing any code, state:
  1. What you will implement (feature summary)
  2. Which files you will create or modify (exact paths)
  3. Why this approach is correct given `docs/REQUIREMENTS.md` and the
     active phase in `docs/TASKS.md`

This applies every time, without exception.

======================================================================
3. WHAT AI MUST ALWAYS DO
======================================================================

LANGUAGE:
- Backend code — variables, functions, classes, docstrings, comments, log
  and exception messages, DB table/column names — English, always.
- UI-facing text — page copy, labels, button text, validation/error
  messages a user sees — DE/EN via the `t()` mechanism in `app/i18n/`,
  never hardcoded prose in a template.

ARCHITECTURE:
- Routers grouped by domain (`app/routers/clients.py`, `runs.py`, ...), not
  by HTTP verb.
- A router never calls a provider SDK directly — always through
  `app/adapters/<provider>.py`, implementing the shared
  `run(prompt_text, model_name) -> RawResponsePayload` interface.
- New provider = new adapter file + new `providers`/`ai_models` rows —
  never a hardcoded provider list in code.
- All API errors return the shared `{error_code, message, detail}` shape
  via `app.errors.AppError` — never a raw `HTTPException(detail="...")`.

DATABASE:
- `schema_phase1.sql` (repo root) is authoritative. Never add a table or
  column that isn't in it, or in a later phase's equivalent schema file,
  without flagging it first.
- Never overwrite historical/evidence rows (`raw_responses`, `citations`,
  prompt versions) — new data is always a new row. A `Run`'s own lifecycle
  status (`pending` → `success`/`error`) is the one exception: that's a
  normal state transition on the run's own control row, not evidence.
- Every migration goes through Alembic; never a manual `ALTER TABLE`
  against a running database.

DOCUMENTATION:
- Every route function gets a docstring FastAPI can surface in `/docs`:
  one-line summary, plus a short paragraph if behavior isn't obvious.
- Every non-obvious `Form(...)`/Pydantic field gets `description=...`.
- Update `docs/REQUIREMENTS.md` / `TASKS.md` / `PROMPTS.md` when a
  decision changes what they say — show the diff, don't silently drift.

FRONTEND:
- Jinja2 + HTMX + Tailwind for CRUD screens; Vue3 islands only for
  client-side-state screens (dashboard, live filtering) — don't build a
  full SPA shell.
- Every screen must be usable on mobile, tablet, and desktop — test at
  ~640px / ~1024px breakpoints, not just desktop width.
- Reuse the macros in `app/templates/partials/macros.html` for form
  fields; extend them before writing new markup from scratch.

======================================================================
4. WHAT AI MUST NEVER DO
======================================================================

NEVER:
- Create new top-level documentation files without explicit instruction —
  fold operational notes into the existing `docs/*.md` files instead.
- Implement anything from a phase later than the one currently active in
  `docs/TASKS.md` — flag it instead of building it silently.
- Invent a table, column, or provider list beyond `schema_phase1.sql`
  without flagging it first.
- Commit `.env`, API keys, or any other secret. `.env.example` documents
  the required keys; real values live only in the gitignored `.env`.
- Run `git push` or merge to `main`/`master` without explicit instruction.
- Run a destructive git operation (`reset --hard`, `checkout --`,
  `clean -f`, force-push) without explicit instruction.
- Let a provider adapter be called directly from a router, bypassing the
  shared adapter interface.
- Return a raw, unstructured error from an API route.
- Mix backend-English and UI-bilingual text — pick the right rule for the
  layer you're editing (see §3).

======================================================================
5. CODING STANDARDS
======================================================================

Naming:
  Routers:      app/routers/{domain}.py            clients.py, runs.py
  Models:       app/models/{aggregate}.py           client.py, run.py
  Adapters:     app/adapters/{provider}.py           google.py, anthropic.py
  Templates:    app/templates/{domain}/{action}.html clients/detail.html

Layered call chain:
  HTTP → Router (thin: parse Form, call ORM/adapter, render/redirect)
       → SQLAlchemy models / adapter
       → PostgreSQL / provider API

Error handling:
  raise AppError("client_not_found", t("errors.client_not_found"), status_code=404)
  → caught by the shared handler in app/errors.py → {error_code, message, detail} JSON

======================================================================
6. TASK COMPLETION PROTOCOL
======================================================================

After completing every task:
  1. Implementation summary — exact file paths, what was added/changed.
  2. Validation checklist — what the user should manually verify (and, for
     UI work, confirm it was actually exercised in a browser at mobile/
     tablet/desktop widths — not just "should work").
  3. Docs update — update docs/REQUIREMENTS.md / TASKS.md / PROMPTS.md if
     anything they describe changed, only after the user confirms the
     feature works.
  4. Commit message — compose it and show it to the user; never commit
     without being asked to.

======================================================================
7. COMMIT CONVENTIONS
======================================================================

Format: type(scope): short description
Types:  feat | fix | docs | chore | refactor | test
Scope:  clients | prompts | runs | adapters | i18n | infra | docs

Examples:
  feat(runs): add Gemini run trigger with grounding-based citations
  fix(clients): keep slug immutable on edit
  chore(infra): move Postgres password into .env
  docs(requirements): record Tailwind-now decision for phase 1

Rules:
- Subject in English, imperative mood, max ~72 characters.
- One logical change per commit — don't mix feat + docs.
- Never `git push` without explicit instruction.

======================================================================
END OF AI INSTRUCTIONS
======================================================================
