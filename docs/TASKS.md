# SignalMap — Tasks (Phase 1: Thin Vertical Slice)

**Status (2026-09-09): Tasks 1–6 all done and verified.** The full loop —
client → prompt set → prompt → run against Gemini → rendered answer +
citations — works end to end with real data, no mocks. See "Beyond phase 1
scope" below for what got built on top of the original 6 tasks along the
way.

For the status of every feature branch since phase 1 — done or not, PR,
merge date, released version — see `docs/00_INDEX.md`, not this file.

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

## Phase 6: Auth + user management ✅ Done

Branch `feature/signalmap-phase6-auth`, merged 2026-09-12
([PR #8](https://github.com/jirkalla/SignalMap/pull/8)). Adds fastapi-users cookie-session authentication with
three roles (admin/editor/viewer), enforced at the route level via `require_role()` — a UI
element hidden by role elsewhere is convenience only, never a substitute for that check:

- **Login/logout** (P6-T1–T3) — cookie + JWT session, no API tokens in v1. `current_user`
  injected into every rendered template.
- **Admin-only user management** (P6-T4) — create/edit/deactivate-reactivate users, reset
  passwords, from a UI (`/users`). No self-service registration or emailed password reset —
  admin sets the initial/reset password directly, communicated out of band. A bootstrap script
  (`scripts/create_admin.py`) creates the first admin account before any UI exists to do it.
- **Forced password change** (P6-T5) — a new or admin-reset account must set its own password on
  first login, enforced by ASGI middleware regardless of role or requested path.
- **Role-based route guards across the whole app** (P6-T6) — every mutating route requires
  editor/admin; `/providers`/`/users` stay admin-only; `/ai-models`/`/settings`/`/help`/`/findings`
  were opened to editor (a deviation from the original plan, made explicitly mid-branch — see
  `docs/ROADMAP.md` §1 "Dodatečná úprava"). Viewer is read-only everywhere: no export, no domain
  classification, and (added later) no raw provider response on the run detail page.
- **Audit** (P6-T7) — `Run.triggered_by_user_id` records who triggered each run.
- **Self-service account page** (`/account`, added mid-branch, not in the original plan) — every
  role can set its own display name (falls back to initials from the full name when unset) and
  change its own password (now requiring the current password first — see code review below).
  Also shows a plain-language table of what each role can/can't do, since viewer especially has
  no other way to learn why buttons are missing.

Includes a **two-round code-review pass** (security/DRY focus) before merge. First round, over
the whole branch, found 10 issues: an admin could strip their own admin role with no guard
(total lockout risk, mirroring a self-deactivation guard that already existed for the sibling
action); `/change-password` accepted a new password from any active session with no proof of the
old one (a stolen session cookie alone could take over the account); an invalid `role` reached the
DB's CHECK constraint unvalidated (raw 500 instead of a structured error, and mislabeled as a
duplicate-email conflict); the CLI password-reset script reactivated a deactivated account but the
admin-UI route didn't (undocumented divergence); a stale README/docs reference to a renamed dev
fixture email; 25 duplicated copies of the same role-check template logic; 4 duplicated copies of
building a `User` row; and 3 redundant DB round-trips to resolve "who is this" on every rendered
page. A second, targeted round over the fix itself caught two more real bugs the fix introduced:
wrong guard ordering (self-role-change check running before role validation, producing a
misleading error) and the DB-lookup consolidation silently breaking `/health`'s documented
"no database access" guarantee for any request carrying a session cookie. All fixed and
re-verified (124/124 tests passing).

Full task breakdown, design decisions, and rationale: `docs/TASKS_PHASE6.md`,
`docs/PROMPTS_PHASE6.md`, and `docs/ROADMAP.md` §1.

## ChatGPT adapter + persona placeholder + AI-model price history

Branch `feature/signalmap-chatgpt-persona-pricehistory`, merged 2026-09-13
([PR #10](https://github.com/jirkalla/SignalMap/pull/10)). Bundles three
independent additions plus one small follow-up fix, per
`docs/TASKS_CHATGPT_PERSONA_PRICING.md` — none of them depend on running live in production,
same reasoning as `docs/TASKS_PHASE5.md` for why one branch, not four:

- **Third AI provider — OpenAI ChatGPT** — same adapter pattern as Gemini/Anthropic (fázi 1/2),
  Responses API with the `web_search` tool and real `user_location` geo-targeting (parity with
  Anthropic, not just Gemini's text-only hint).
- **Persona placeholder in the system-instruction template** — the previously hardcoded "The
  person asking..." (`app/routers/settings.py`) becomes a data-driven `{persona}` placeholder
  with full CRUD (`/personas`), selectable per run (same pattern as the existing market
  override on `Run`).
- **AI-model price history** — new `ai_model_price_history` table records every price change
  instead of silently overwriting it, with a viewable "valid from–until" history on
  `/ai-models/{id}/edit`.
- **Duplicate-run guard** — `trigger_run` rejects a second submission on the same prompt while
  one is still `status='pending'`, plus an immediate client-side button-disable (the run-trigger
  form gives no other feedback while blocking synchronously on a slow provider call).

A focused security review (SQL injection/XSS/authorization/format-string-injection) found no
qualifying new vulnerabilities in the branch. A DRY pass found and fixed one duplication: a
shared `_record_price_history()` helper (`app/routers/ai_models.py`) replacing two copies of the
same `AIModelPriceHistory` insert. Also hardened `scripts/create_admin.py --from-env` to refuse
running when `ENVIRONMENT=production`, matching `scripts/seed_dev_users.py`'s existing guard.

Full task breakdown, design decisions, and rationale: `docs/TASKS_CHATGPT_PERSONA_PRICING.md`
and `docs/PROMPTS_CHATGPT_PERSONA_PRICING.md`.

## Local timezone display fix

Branch `feature/signalmap-local-time-display`, merged 2026-09-14
([PR #11](https://github.com/jirkalla/SignalMap/pull/11)). Fixes a bug found during manual
production verification: every displayed timestamp (`.strftime()` on the stored UTC value,
14 occurrences across 7 templates) was rendered as-is with no timezone conversion — a run
showed 2 hours off from the real local time (UTC vs. CEST). Not one of the five roadmap
phases; no new database/backend logic — conversion is purely client-side (viewer's own
browser timezone via `Intl.DateTimeFormat`, not a fixed server zone), with a server-rendered
UTC `<time>` fallback for when JS fails.

A DRY-focused code review found the new `local_time()` macro hardcoded the "UTC" suffix in
English instead of routing it through `t()` — fixed by adding a `common.utc_suffix` i18n key
and passing it in from every call site.

Full task breakdown and design decisions: `docs/TASKS_LOCAL_TIME.md` and
`docs/PROMPTS_LOCAL_TIME.md`.

## Bulk prompt import + multi-model runs

Branch `feature/signalmap-bulk-import-multi-model`, merged 2026-09-15
([PR #12](https://github.com/jirkalla/SignalMap/pull/12)). Two independent features bundled in
one branch, neither dependent on the other: (1) **multi-model run triggering** — checkboxes
instead of a single `<select>` on the run-trigger form, runs every checked model in parallel
against the existing `POST /prompts/{id}/runs` endpoint, no new route; (2) **bulk CSV/XLSX/JSON
prompt import** into an existing prompt set, with a preview step (duplicate detection, per-row
market override) before anything is saved. Neither adds a database table or column — both reuse
the existing `Prompt`/`Run`/`AIModel` models. "Run every prompt at once" (the roadmap's "Study"
concept) is explicitly out of scope, deferred until the Scheduler exists.

Two rounds of code-review remediation followed (19 findings, 18 fixed, 1 deliberately deferred):
a DB-level partial unique index closing a TOCTOU race on run creation, CSV/XLSX row-shape
validation hardening (including a bug found while testing — `openpyxl` pads a sheet's header row
to its widest row, silently hiding a ragged-row check keyed off the raw header length), i18n
hardening for row-level import errors, a locale-switch 405 fix affecting ~14 POST-rendered pages
app-wide (not just this branch's own pages), and several correctness/DRY fixes in the bulk-import
confirm flow (an overly broad `except IntegrityError`, a silently-dropped intentional-duplicate
override, an unbounded `market_id` causing an unhandled 500, and blocking DB calls running on the
event loop instead of a threadpool).

Full task breakdown, design decisions, and the complete code-review remediation log:
`docs/TASKS_BULK_IMPORT_MULTI_MODEL.md` and `docs/PROMPTS_BULK_IMPORT_MULTI_MODEL.md`.

## Ops dashboard

Branch `feature/signalmap-ops-dashboard`, merged 2026-09-16
([PR #13](https://github.com/jirkalla/SignalMap/pull/13)). Internal `/ops` dashboard — cost,
latency, and error visibility across every client, admin/editor only and never client-facing,
entirely separate from the client-facing `/dashboard` (docs/ROADMAP.md #4; a prerequisite for the
Scheduler, #5). Adds a `7d` range option to the existing client dashboard, a shared date-range
resolver (`app/services/date_ranges.py`), run cost estimation (`app/services/cost.py`, reading
whichever provider-specific token-usage key shape a given run's `token_usage` actually has), a
9-endpoint SQL aggregation API (`app/services/ops_dashboard.py`/`app/routers/ops_dashboard.py`),
and a Vue3 island page (`app/templates/ops/index.html`) with client/prompt-set/prompt drill-down
plus an independent cross-client user axis — including a "Scheduler" pseudo-user
(`trigger_type='scheduled'`) and a separate "unknown attribution" bucket for runs that predate
phase 6's user-attribution tracking, found while implementing this rather than assumed upfront.
Bundles one small unrelated fix found along the way: bulk-import's default market now comes from
the prompt set's own existing prompts (or the browser's last choice), not always the
alphabetically-first market.

`/code-review high` on the full branch diff found and fixed 10 issues before merge: a
prompt-detail "view all runs" link that undercounted against its own lineage-wide totals (closed
via an additive `/prompts/{id}?scope=lineage` param, default behavior unchanged for every other
caller), hardcoded English status text bypassing i18n, a daily-chart date off-by-one for
negative-UTC-offset viewers, unhandled failed API responses rendered as if they were valid data, a
stale-response race on rapid drill-down clicks, plus reuse/efficiency cleanup (shared
day/week/month bucketing, a deduplicated Scheduler/unknown-attribution classifier, `ops_summary`
cut from 3 DB round-trips to 1, `prompt_ops_rows` from 2 to 1, keyword-only scoping filters).

Full task breakdown and design decisions: `docs/TASKS_OPS_DASHBOARD.md` and
`docs/PROMPTS_OPS_DASHBOARD.md`.

## Gemini citation extraction fix + claim/source-passage split

Branch `feature/signalmap-gemini-citation-extraction` (2026-09-16), roadmap
item #11 — not one of the five roadmap phases in the signalmap-conventions
skill; it corrects extraction of data `raw_payload` already contained (FR-10)
and adds the fields roadmap #12 will need.

Two independent defects, both measured against production data rather than
estimated. First, `app/adapters/google.py::_map_citations` walked the
grounding chunks and kept only the first support referencing each one, so
Gemini's many-to-many relation between answer segments and sources was
flattened: 551 stored rows for 1354 real claim-source pairs, 59% of the links
dropped, in 40 of 52 answers. It now walks the supports and emits one row per
(segment, source) pair, ordered by position in the answer. Second,
`citations.cited_answer_span` meant different things per provider — the answer
span for Gemini and OpenAI, but the passage quoted from the source page for
Anthropic, contradicting FR-12 (the stored value was findable in the answer
for 551/551 Gemini and 71/71 OpenAI rows, but only 17/158 Anthropic ones).
`citations` gained `source_passage`, `answer_span_start` and `answer_span_end`
(migration `0027`), and the run detail page now labels the two separately.

All three mappers moved from SDK objects onto the serialized payload dict, so
migration `0028` could replay the exact same functions over stored
`raw_payload` rows and recompute the whole archive (Gemini 583 → 1386,
Anthropic 167 with the text moved, OpenAI 71 with offsets filled) instead of
growing a second copy of the extraction logic — the one deliberate,
user-confirmed exception to never rewriting evidence rows, legitimate only
because `citations` is derived from a `raw_payload` this migration never
touches. Dashboard code was deliberately left alone: `count(citations)` now
uniformly means "claim-source links", which is what it always meant for the
other two providers.

Closes the test gap that let this survive three phases — `_map_citations` had
no test for any provider, including a named regression test for the original
`break` bug (243 tests, up from 221).

Full task breakdown, measured figures and design decisions:
`docs/TASKS_GEMINI_CITATIONS.md` and `docs/PROMPTS_GEMINI_CITATIONS.md`.

## Scheduler

Branch `feature/signalmap-scheduler` (**merged 2026-09-22 — [PR #18](https://github.com/jirkalla/SignalMap/pull/18)**),
roadmap item #5. Adds recurring, unattended runs (FR-9) on top of the
manual-trigger loop everything before this branch was built around: a
recurrence rule (`run_schedules`), a persistent queue with its own history
(`run_queue`), a worker process (`app/worker.py`) that plans and executes
independently of the web app, and the safeguards that make unattended spend
survive contact with reality — a hard per-client daily run cap, a soft
monthly budget warning, a queue-depth ceiling, two independent idempotence
guarantees, a `SCHEDULER_DRY_RUN` kill switch defaulting on, and a schedule
pausing (never silently resuming) when its owning user is deactivated. Also
ships an in-app notification outbox, a `/schedules` monitoring page
(schedules/queue/history), dead-letter retry, and admin UI for the new
per-client scheduler settings.

Thirty design decisions and fourteen code prompts (SCH-0 through SCH-11);
full breakdown in `docs/TASKS_SCHEDULER.md` and `docs/PROMPTS_SCHEDULER.md`.

## New providers — Perplexity, DeepSeek, xAI Grok

Branch `feature/signalmap-new-providers` (not yet merged). Adds three more providers on top of
the established pattern (new adapter + `providers`/`ai_models` rows, no code-level provider
list) — motivated by a comparative benchmark against peec.ai for client Knauf, not one of the
five roadmap phases.

- **Perplexity (Agent API)** — built against the Agent API, not the retiring Sonar Chat
  Completions surface. A real probe call (NP-T1) corrected two documentation-sourced
  assumptions before any adapter code was written: the real endpoint is `POST
  {base_url}/responses` with `base_url` including `/v1` (not the documented `/v1/agent`), and the
  `model` field takes a `{vendor}/{model}` router string (`perplexity/sonar`), not a "preset".
  Citations arrive as a dedicated `search_results` output item, not `url_citation` annotations.
- **DeepSeek** — OpenAI-compatible Chat Completions, deliberately confirmed with the user before
  building (design decision 3): DeepSeek has no web search/grounding surface at all, so its runs
  never carry citations, permanently, by construction — not a temporary gap. Threaded through
  generically via `AIModel.supports_web_search` (never a hardcoded provider check): a dedicated
  DE/EN explanation on the run detail page, and exclusion from `own_domain_rate`'s denominator
  (`app/services/dashboard.py`), which would otherwise be silently deflated by a provider that
  can never be cited.
- **xAI Grok** — a NP-T1 probe corrected this project's own planning doc: citations are not in a
  flat `response.citations` array (that field doesn't exist) — Grok uses the same
  `url_citation`-annotation shape as OpenAI, reusing `OPENAI_SHAPE` in `app/services/cost.py`
  rather than a redundant new constant, once the real payload turned out to match it exactly.

All three verified against real API calls before any adapter code was written (`docs/
TASKS_NEW_PROVIDERS.md` NP-T1's "Ověřené tvary odpovědí"), not against provider documentation —
which in Perplexity's and Grok's cases turned out to disagree with the real API in ways that
would have shipped wrong code otherwise. Full task breakdown, all corrected design decisions,
and the verified response shapes: `docs/TASKS_NEW_PROVIDERS.md`.

## After phase 1 (not started yet — flag if a request touches these early)
- Source/signal map, intervention hypotheses (dashboard v0 itself is done — see Phase 4 above).
- Multi-tenant scoping by client_id (authentication itself is done — see Phase 6 above).

See `docs/ROADMAP.md` for the full ordered plan beyond phase 6 — deploy
hardening, going online, a cost/ops dashboard, a scheduler, brand-attribute
tagging, sentiment, gap/opportunity score, and a new ExpressYourself.AI
interface (a screenshot and an idea so far, not an agreed design system —
and aimed at a UI that works on a phone and tablet, not a repaint of the
desktop screens).