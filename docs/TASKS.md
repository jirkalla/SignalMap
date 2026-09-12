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

## Production-readiness hardening (separate from phase-1 scope above)

Branch `feature/signalmap-phase1-hardening` (2026-09-09) covers technical
debt found in a post-phase-1 code review — no new user-facing feature:
structured logging + no more leaked exception details to clients,
non-root container, pinned dependency versions, a consistent delete
policy with the previously-missing Client/PromptSet/Prompt CRUD, audit
columns (`created_at`/`updated_at`) on `markets`/`prompt_sets`, and a
first pytest suite (health/clients/markets/runs). Full task list, design
decisions, and per-task commit hashes: `docs/TASKS_HARDENING.md` and
`docs/PROMPTS_HARDENING.md`.

## Phase 2: Anthropic provider + admin UI

Branch `feature/signalmap-phase2-anthropic-admin` (2026-09-09) adds the
second AI provider (Anthropic Claude, with real `web_search` `user_location`
geo-targeting instead of Gemini's text-hint workaround) and admin UI for
providers/models (`/providers`, `/ai-models` — activation, pricing, context
window parameters). Includes a code-review pass (security/DRY focus) with
its findings fixed before merge. Full task list, design decisions, and
per-task detail: `docs/TASKS_PHASE2.md` and `docs/PROMPTS_PHASE2.md`.

## Runs export (CSV / XLSX / JSON)

Branch `feature/signalmap-runs-export`, merged 2026-09-10
([PR #3](https://github.com/jirkalla/SignalMap/pull/3)). Extends Task 6
(run list & detail) with downloadable exports at three scopes — one run,
all runs for a prompt (current version by default, `?versions=all` for the
full edit history), all runs for a client — each as CSV (zip of
`runs.csv` + `citations.csv`), XLSX (workbook), or JSON, with a
`?content=answer|raw|full` switch for how much of the raw provider payload
to include. Not one of the five roadmap phases in the signalmap-conventions
skill; doesn't touch auth/analysis/dashboard.

Includes a code-review pass (security/stability/DRY) before merge: fixed
CSV formula-injection and an XLSX crash on control characters in provider
text, deduplicated the export routes/templates/delete-lineage logic. Also
fixed an unrelated pre-existing bug found along the way — deleting a
run-less multi-version prompt (or a prompt set containing one) 500'd on a
self-referential FK violation (`Prompt.root_prompt_id`).

Full task breakdown, design decisions, and the code-review findings:
`docs/TASKS_EXPORT.md` and `docs/PROMPTS_EXPORT.md`. Visual design + UI
mockups: [Export Design artifact](https://claude.ai/code/artifact/c6e05821-fbb5-4ad7-8440-613db84ba022).

## Phase 3: First analysis skill — mention/visibility detection ✅ Done

Branch `feature/signalmap-phase3-mention-detection`, merged 2026-09-10
([PR #5](https://github.com/jirkalla/SignalMap/pull/5)). Adds the first entry in
`analysis_skills`/`analysis_results` (excluded from `schema_phase1.sql` by design — see
its header comment) — a deterministic, non-LLM mention/visibility check: does the
client's name (or a known alias) appear in a run's `rendered_text`, and is the client's
own domain among its `citations`. Deliberately the simplest defensible metric to start
the analysis layer with — no sentiment, no brand-attribute extraction, no LLM
classification call. Those carry materially higher risk of arbitrary/unreliable output
and are explicitly deferred to a later skill.

Also adds `clients.domain` and a new `client_aliases` table (name variants to match
against, e.g. "Acme" / "Acme Corp" / "Acme GmbH"), and a general `execution_type`
(`rule_based` | `llm_prompt`) on `analysis_skills` so a future LLM-based skill (e.g.
sentiment) doesn't require another schema rework — this skill is the first `rule_based`
entry, not the only kind the framework supports. Matched mention terms are also
highlighted (`<mark>`) directly in the rendered answer on the run detail page, based on
the `match_spans` stored at computation time.

Full task breakdown, design decisions, and rationale: `docs/TASKS_PHASE3.md` and
`docs/PROMPTS_PHASE3.md`.

## Phase 4: Dashboard v0 — domain league table + time series ✅ Done

Branch `feature/signalmap-phase4-dashboard-v0`, merged 2026-09-11
([PR #6](https://github.com/jirkalla/SignalMap/pull/6)). Adds the first dashboard
screen: for one selected client, a league table of the domains
AI providers cite when talking about them, and a weekly time series of citation/run
volume — built entirely from `citations`/`runs`/`raw_responses` that already exist, no
new analysis skill. The "own-domain citation rate" figure is the one exception — it
reads the existing `mention_visibility` result from phase 3 rather than recomputing
domain matching.

First real use of the Vue3-island pattern described in the signalmap-conventions skill:
the rest of the app stays Jinja2 + HTMX, but `/dashboard` mounts a single Vue3
component (loaded from a CDN, no build step) so changing a filter re-fetches and
re-renders the table/chart without a full page reload — deliberately not a move to a
full Vue frontend (see design decision 1 in `docs/TASKS_PHASE4.md` for why, and what
would actually trigger that move later).

Includes a code-review pass (security/DRY focus, `/code-review high`) before merge —
10 findings (deterministic league-table ranking, UTC-pinned week bucketing, `tojson`
XSS hardening, a shared `is_own_domain()` helper replacing two duplicated copies,
merged/indexed queries, aggregation logic extracted into `app/services/dashboard.py`,
a cross-client data-isolation regression test) all fixed and re-verified before the PR
was opened.

Full task breakdown, design decisions, and rationale: `docs/TASKS_PHASE4.md` and
`docs/PROMPTS_PHASE4.md`.

## Phase 5: Competitive visibility — generation 2 analysis skills ✅ Done

Branch `feature/signalmap-phase5-competitive-visibility`, merged 2026-09-11
([PR #7](https://github.com/jirkalla/SignalMap/pull/7)). Bundles three pieces of work into
one branch — solo development, no second reviewer, so the fine-grained one-branch-per-task
split earlier phases used wasn't needed here (see `docs/TASKS_PHASE5.md` intro for the
reasoning):

- **Trend deltas and manual domain type classification** on the dashboard (P5-T1/P5-T2) —
  independent of the rest, no new analysis skill.
- **Competitive visibility** (P5-T3–T7) — a second analysis skill, `competitive_visibility`,
  extending phase 3's single-entity mention/citation detection to a configurable list of
  tracked competitors per client. Produces share-of-voice and position figures alongside the
  existing `mention_visibility` output, computed automatically after every run and surfaced on
  both the run detail page and a new "Competitive visibility" dashboard section. Shares its
  word-boundary matching logic with `mention_visibility` via a new `app/analysis/matching.py`
  module, moved out rather than duplicated.
- **Dashboard-to-client link** (P5-T9) and a **`/help` section documenting every current
  dashboard metric** (P5-T10), including how each one is computed and where it's read from.

New schema: `tracked_entities`/`tracked_entity_aliases` (competitors tracked per client,
mirrors `client_aliases`' shape — the client itself never gets a row, so its identity stays
sourced from `Client.name`/`ClientAlias` alone) and `domain_classifications` (manual editorial
typing of cited domains — Institutional/Editorial/Corporate/Reference/UGC/Other, deliberately
without a "competitor" category, since that's derivable from `tracked_entities.domain`). Both
schema designs were explicitly confirmed before implementation, per AI_INSTRUCTIONS.md §4.

Includes a code-review pass (security/DRY focus) before merge — 10 findings (wrong
`run_coverage_pct` denominator and a duplicated join in `entity_league_rows`, an
Analysis-section guard/loop mismatch, `is_own_client`-fragile own-entity lookup,
`DOMAIN_TYPES`/migration sync documentation, a trend-delta boundary double-count, an
ambiguous shared `position_label` i18n key, duplicated tracked-entity conflict-response
closures, a duplicated client-detail card pattern, and a reimplemented citation-matching
helper) all fixed and re-verified before the PR was opened.

Full task breakdown, design decisions, and rationale: `docs/TASKS_PHASE5.md` and
`docs/PROMPTS_PHASE5.md`.

## After phase 1 (not started yet — flag if a request touches these early)
- Source/signal map, intervention hypotheses (dashboard v0 itself is done — see Phase 4 above).
- Authentication (fastapi-users) and multi-tenant scoping by client_id.

See `docs/ROADMAP.md` for the full ordered plan beyond phase 5 — auth, deploy
hardening, going online, a cost/ops dashboard, a scheduler, brand-attribute
tagging, sentiment, gap/opportunity score, and the ExpressYourself.AI frontend
rebrand (design tokens and prototype already agreed, implementation pending).