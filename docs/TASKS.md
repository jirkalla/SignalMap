# SignalMap — Tasks (Phase 1: Thin Vertical Slice)

**Status (2026-09-09): Tasks 1–6 all done and verified.** The full loop —
client → prompt set → prompt → run against Gemini → rendered answer +
citations — works end to end with real data, no mocks. See "Beyond phase 1
scope" below for what got built on top of the original 6 tasks along the
way.

Scope: create a client, create a prompt (with market/language), run it
against Google Gemini, store and view the raw response. No auth, no
multi-tenancy, no strategy/analysis layer, no scheduling — those are later
phases (see signalmap-conventions skill, "Build sequencing").

Work through these in order. Don't start a task before the previous one is
confirmed working — each one builds on the last, and skipping ahead (e.g.
building the run endpoint before the models exist) creates rework.

---

See the repository root [README.md](../README.md) for how to run the app.

---

## Task 1 — Database models & migration ✅ Done
Turn `schema_phase1.sql` into SQLAlchemy models and a working Alembic
migration, plus seed data (markets, Google provider, the two Gemini models).
**Done when:** tables exist in Postgres and seed rows are queryable.
*(6 migrations applied to date — see "Beyond phase 1 scope" for what 0002–0006 added.)*

## Task 2 — Client CRUD ✅ Done
List, create, and edit screens for `Client` (name, industry, notes).
**Done when:** you can create and edit a client through the browser.

## Task 3 — Prompt Set & Prompt CRUD ✅ Done
Under a client: create a prompt set, add prompts to it (text, market,
optional topic). List + create only.
**Done when:** you can add a real prompt with a market attached, under a
client, through the browser.
*(Prompt editing/versioning was later added on top — see below.)*

## Task 4 — Google Gemini adapter ✅ Done
`app/adapters/google.py` implementing the shared adapter interface, with
Google Search grounding enabled, mapping Gemini's response into the
canonical raw_payload / rendered_text / citations shape.
**Done when:** calling the adapter directly (e.g. from a quick script or
test) returns a populated RawResponsePayload for a real prompt.

## Task 5 — Run trigger endpoint ✅ Done
Endpoint + UI button: pick a prompt + model, run it, store Run +
RawResponse + Citations, handle provider errors with the shared structured
error response.
**Done when:** clicking the button in the browser creates a real row in
`runs` and `raw_responses`.

## Task 6 — Run list & detail view ✅ Done
List of runs per prompt (model, provider, timestamp, status, latency).
Detail page: rendered text, expandable raw JSON, citations list.
**Done when:** the full loop — create client → create prompt → run against
Gemini → see the raw answer — works end to end in the browser, with nothing
left to fake or mock.

---

## Beyond phase 1 scope (already built)

Not in the original 6 tasks, added afterward based on real usage and
findings — each verified live, not just written:

- **Market CRUD** (`/markets`) — add/edit/delete, with ISO 639-1/3166-1
  format validation on language/country, delete blocked while a prompt
  still references the market.
- **Prompt editing with versioning** — editing a prompt creates a new
  version (`root_prompt_id`/`is_current_version`) rather than mutating the
  row, so historical runs keep showing the exact text they ran against.
- **Per-run market override** — a run can target a different market than
  its prompt's default (`runs.market_id`), recorded on the run itself.
- **Visible request payload** — `runs.request_payload` records exactly
  what was sent to the provider (model, prompt text, system instruction),
  for both successful and failed runs.
- **`/settings`** — per-provider, editable `system_instruction` template
  (the locale-framing hint sent with Gemini runs), with placeholder
  validation before saving.
- **`/help`** — in-app bilingual usage guide.
- **`/findings`** — English-only technical log of things discovered
  during development (API limitations, cost/compliance notes), separate
  from the user-facing Guide.
- **DE/EN translation content filled in** — the "After phase 1" list
  below originally deferred this; it's done, not just the mechanism.
- **Tailwind visual styling** — also originally deferred; done, per the
  amendment in `docs/REQUIREMENTS.md` §3a.

## After phase 1 (not started yet — flag if a request touches these early)
- Second provider (Anthropic) — proves the adapter pattern generalizes,
  and is the only way to get real geographic targeting (`user_location`)
  instead of Gemini's text-hint workaround (see `/findings`).
- First analysis skill.
- Dashboard, Vue islands for interactivity.
- Authentication (fastapi-users) and multi-tenant scoping by client_id.
- Admin UI for providers/models — deferred alongside the Anthropic adapter.