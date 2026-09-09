---
name: signalmap-conventions
description: Project conventions for SignalMap, an AI corporate-perception intelligence tool (FastAPI backend, PostgreSQL, hybrid Jinja2/HTMX + Vue3 frontend, German/English UI localization). Use this skill whenever working on the SignalMap codebase — writing FastAPI routes, SQLAlchemy models, Jinja2 templates, Vue components, translations, or Docker/deployment config for this project. Also use it when the user mentions SignalMap, the AI corporate-perception dashboard, the evidence-dossier UI style, or asks to add a new AI provider adapter, a new analysis skill, or a new localized page — even if they don't explicitly say "SignalMap".
---

# SignalMap Project Conventions

SignalMap is an internal AI corporate-perception intelligence tool: it runs configured prompts against multiple AI providers (Google Gemini, Anthropic Claude, later Perplexity/OpenAI), stores raw answers and citations, runs structured analysis skills over them, and surfaces the results — including a source/signal map and intervention hypotheses — to an internal analyst.

Stack: FastAPI + SQLAlchemy + PostgreSQL backend, hybrid Jinja2/HTMX + Vue3 frontend, Docker Compose for local dev and deployment.

Read this skill before writing or reviewing any code in this project, and follow the rules below even if a specific request doesn't repeat them.

## Language rule — this is the one that gets missed most often

**Backend code is always English. UI text is always bilingual (German + English).** These are two separate rules for two separate layers — don't mix them up:

- Python code: variable names, function names, class names, docstrings, code comments, commit messages, log messages, exception messages, database column/table names — **all English**, no exceptions, even though the client-facing product and the person building it are German/Czech-context.
- Anything a human user reads in the browser (page text, button labels, table headers, dashboard copy, validation messages shown to the user) — **must exist in both German and English**, selectable/switchable, never hardcoded in only one language inside a template.
- Internal analyst tool, not public product — but still build the localization layer from the start. Retrofitting i18n after templates are full of hardcoded strings is expensive; doing it from the first template is nearly free.

## Localization approach

**Single source of truth for translations**, shared between Jinja2 and Vue so strings aren't maintained twice:

```
app/
└── i18n/
    ├── de.json
    └── en.json
```

Each file is a flat or nested key→string map, e.g.:
```json
{ "nav.dashboard": "Übersicht", "client.create_button": "Klient anlegen" }
```

- **Jinja2 side**: load the active locale's JSON into the template context as a `t` dict (or a small `t(key)` helper function registered as a Jinja global), so templates call `{{ t('client.create_button') }}` rather than writing prose inline.
- **Vue side**: feed the same JSON files into `vue-i18n`. Don't hand-write a second copy of the strings in a `.vue` file — import the JSON.
- **Locale selection**: prefer a URL path prefix (`/de/clients`, `/en/clients`) over a cookie-only approach — it's shareable, bookmarkable, and makes it obvious in logs/screenshots which locale produced a given page. Fall back to `Accept-Language` only to pick the *default* redirect target, not to silently switch content on an existing URL.
- Keep `de.json` and `en.json` key sets in sync — when you add a key to one, add it to the other in the same change. A missing key should fail loudly in dev (raise/log), not silently render the key name to the analyst.

## Backend conventions (FastAPI / SQLAlchemy)

- Routers grouped by domain, not by HTTP verb: `app/routers/clients.py`, `app/routers/runs.py`, `app/routers/analysis.py` — not `app/routers/get.py` / `post.py`.
- SQLAlchemy models live in `app/models/`, one file per aggregate (`client.py`, `prompt.py`, `run.py`, `analysis.py`), mirroring the schema already agreed for the project: `Client`, `StrategyConfig`, `PromptSet`/`Prompt`, `Market`, `Provider`, `AIModel`, `Run`, `RawResponse`, `Citation`, `AnalysisSkill`, `AnalysisResult` (plus later `SourceLayer`, `SourcePassage`, `InterventionHypothesis` when that layer gets built).
- Every table name, column name, enum value: English, snake_case, matching the schema already established — don't invent parallel naming when extending it.
- Provider integrations follow one shared adapter interface (`run(prompt_text: str, model_name: str) -> RawResponsePayload`), one file per provider under `app/adapters/` (`google.py`, `anthropic.py`, ...). Never let a router call a provider SDK directly — always through the adapter, so a provider can be swapped or mocked without touching the rest of the app.
- Never overwrite historical rows (raw responses, analysis results, prompt versions) — new version = new row, per the project's evidence-retention requirement. This is a hard constraint, not a style preference.
- `.env` holds config (`DATABASE_URL`, API keys); nothing secret ever hardcoded or committed.

## Frontend conventions (hybrid Jinja2/HTMX + Vue3)

Use the right tool for each part of the app — don't default to one everywhere:

- **Jinja2 + HTMX + Tailwind CSS**: CRUD screens (client setup, strategy config, prompt management, run history list). These are form- and table-heavy with no need for client-side state. Tailwind was chosen over Bootstrap specifically because Bootstrap's default component look fights against the dossier visual language below — Tailwind gives utility-class-level control with no defaults to override. Build reusable Jinja2 macros / Vue components for recurring patterns (text field, select, data table) early, so the extra utility-class verbosity doesn't get repeated across every screen.
- **Vue3 "islands" embedded in a Jinja2 page**: the analyst dashboard — source/signal map, live filtering, charts, anything needing client-side state or re-rendering without a full page reload. Don't build a full SPA shell; mount Vue components into specific `<div>`s inside otherwise server-rendered pages.
- Visual language follows the existing "evidence dossier" prototypes: IBM Plex Sans/Mono typography, muted/restrained color palette, deliberately *not* a generic SaaS card-and-shadow look. When building any new screen, match this tone rather than defaulting to a generic component-library appearance.
- **Responsive by default**: every screen must work on mobile, tablet, and desktop — this is a stated product requirement, not a nice-to-have. Test breakpoints roughly at ~640px (mobile), ~1024px (tablet), and above (desktop). Tables that don't fit on mobile should collapse to a card/stacked layout, not force horizontal scrolling as the only option.
- **Every delete action gets a client-side `confirm()` guard** on the form (`onsubmit="return confirm(t('<entity>.delete_confirm'))"`) — never a bare delete button. Reuse the pattern from `markets/list.html`.

## Open decisions (flag, don't silently pick)

A few things aren't finalized — when a task touches these, surface the choice rather than assuming:

- **Which localization library** for FastAPI/Jinja2 (hand-rolled `t()` dict helper vs. a package like `fastapi-babel`) — start with the hand-rolled JSON approach above unless a concrete need for pluralization/gettext-style features comes up.

## Code quality baseline (DRY, documentation, error handling)

This project is also meant to be a showcase — treat these as non-negotiable, not aspirational:

- **DRY, project-wide, not per-feature.** Before writing a new form field, table, or validation rule, check whether an equivalent already exists elsewhere in the project and reuse/extend it rather than duplicating. This applies across the whole codebase, not just within one screen — a reusable Jinja2 macro or Vue component built for the client form should be built so the prompt form or run form can reuse it too, not re-implemented per feature.
- **Docstrings double as API documentation — write them for that audience.** FastAPI auto-generates interactive docs (Swagger UI at `/docs`, ReDoc at `/redoc`) directly from route type hints, Pydantic models, and docstrings — no separate documentation effort needed if these are written well. Every endpoint function gets a docstring: one-line summary, then (if non-trivial) a short paragraph on behavior and side effects. Every request/response Pydantic model gets a `Field(..., description="...")` on non-obvious fields — that description shows up in Swagger too.
- **Structured, consistent error responses.** Don't let raw exceptions or ad-hoc `HTTPException(status_code=400, detail="bad thing")` strings proliferate. Define one error response shape (e.g. `{ "error_code": "...", "message": "...", "detail": "..." }`) via a shared Pydantic model, and one place (a FastAPI exception handler) that maps internal exceptions to it. `error_code` and internal exception classes: English (backend rule). `message` shown to the user: goes through the same DE/EN localization layer as everything else UI-facing — an error the analyst sees is UI text, not backend text, so it follows the i18n rule above, not the "backend is English" rule.

## Build sequencing — don't front-load infrastructure before the core loop works

This project intentionally layers complexity. When picking up new work, default to this order rather than building auth, multi-tenancy, or extra polish ahead of a working vertical slice:

1. One client, one prompt, one provider (Google), running end-to-end and landing in the database — no auth at all yet.
2. Second provider (Anthropic) — proves the adapter pattern actually generalizes.
3. First analysis skill running against stored raw responses.
4. Dashboard surfacing what's already in the database.
5. **Only then**: authentication (`fastapi-users`) and authorization/multi-tenancy (internal team sees everything; a client user is scoped to their own `client_id` via a dependency that filters queries — this is custom code on top of `fastapi-users`, not something the library gives you out of the box).

If a request touches step 5 while steps 1–4 aren't solid yet, flag that explicitly rather than building it silently — the data model is still likely to shift, and auth built on a shifting model gets rewritten.

## When adding a new AI provider or analysis skill

- New provider: new file in `app/adapters/`, implementing the shared interface, plus a new row in the `providers`/`ai_models` tables (don't hardcode provider lists in code — they're data).
- New analysis skill: follows the pattern already defined in the schema — input schema, output schema, prompt template, version number, stored in `analysis_skills`. A skill starts as a well-designed prompt returning structured JSON; it doesn't need custom code per skill.