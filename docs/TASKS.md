# SignalMap — Tasks (Phase 1: Thin Vertical Slice)

Scope: create a client, create a prompt (with market/language), run it
against Google Gemini, store and view the raw response. No auth, no
multi-tenancy, no strategy/analysis layer, no scheduling — those are later
phases (see signalmap-conventions skill, "Build sequencing").

Work through these in order. Don't start a task before the previous one is
confirmed working — each one builds on the last, and skipping ahead (e.g.
building the run endpoint before the models exist) creates rework.

---

## Task 1 — Database models & migration
Turn `schema_phase1.sql` into SQLAlchemy models and a working Alembic
migration, plus seed data (markets, Google provider, the two Gemini models).
**Done when:** tables exist in Postgres and seed rows are queryable.

## Task 2 — Client CRUD
List, create, and edit screens for `Client` (name, industry, notes).
**Done when:** you can create and edit a client through the browser.

## Task 3 — Prompt Set & Prompt CRUD
Under a client: create a prompt set, add prompts to it (text, market,
optional topic). List + create only.
**Done when:** you can add a real prompt with a market attached, under a
client, through the browser.

## Task 4 — Google Gemini adapter
`app/adapters/google.py` implementing the shared adapter interface, with
Google Search grounding enabled, mapping Gemini's response into the
canonical raw_payload / rendered_text / citations shape.
**Done when:** calling the adapter directly (e.g. from a quick script or
test) returns a populated RawResponsePayload for a real prompt.

## Task 5 — Run trigger endpoint
Endpoint + UI button: pick a prompt + model, run it, store Run +
RawResponse + Citations, handle provider errors with the shared structured
error response.
**Done when:** clicking the button in the browser creates a real row in
`runs` and `raw_responses`.

## Task 6 — Run list & detail view
List of runs per prompt (model, provider, timestamp, status, latency).
Detail page: rendered text, expandable raw JSON, citations list.
**Done when:** the full loop — create client → create prompt → run against
Gemini → see the raw answer — works end to end in the browser, with nothing
left to fake or mock.

---

## After phase 1 (not started yet — flag if a request touches these early)
- Second provider (Anthropic) — proves the adapter pattern generalizes.
- First analysis skill.
- Dashboard polish, Tailwind styling, Vue islands for interactivity.
- DE/EN translation content (the `t()` mechanism should exist from phase 1,
  but German strings don't need to be filled in yet).
- Authentication (fastapi-users) and multi-tenant scoping by client_id.