# SignalMap — Prompts for Claude Code (Phase 1)

Copy these into Claude Code one at a time, in order, matching tasks.md.
Wait for each task to be confirmed working before moving to the next.
Attach schema_phase1.sql when running Task 1's prompt.

---

## Task 1 — Database models & migration

Set up SQLAlchemy models under app/models/ and an Alembic migration based on
schema_phase1.sql (attached). Include the seed data (markets, google_gemini
provider, the two ai_model rows — gemini-3.5-flash and gemini-3.1-flash-lite)
as a data migration or seed script — tell me which approach you recommend
and why before implementing it.

Follow the project conventions: English code/comments throughout, models
organized one file per aggregate under app/models/, table/column naming
exactly as in schema_phase1.sql.

Run the migration against the running Postgres container and confirm the
tables exist and seed data is present.

---

## Task 2 — Client CRUD

Build CRUD screens for Client (list, create, edit) using Jinja2 + HTMX.
Fields: name, industry, notes. No styling polish yet — plain HTML is fine,
Tailwind comes later.

Write docstrings on the route functions so they read well in the
auto-generated Swagger docs at /docs — one-line summary plus a short
paragraph if the behavior isn't obvious. Add Field(..., description="...")
on any non-obvious Pydantic model field.

Errors should use a structured response shape (error_code, message, detail)
rather than raw HTTPException strings — introduce the shared error model now
if it doesn't exist yet, since every later endpoint will reuse it.

---

## Task 3 — Prompt Set & Prompt CRUD

Build CRUD for PromptSet and Prompt, nested under a client. A prompt set has
a name; a prompt has text, a market (dropdown populated from the markets
table), an optional topic, and is_active. List and create views only for now
— edit can come later.

Reuse whatever form-field / table components you already built for the
Client screens rather than writing new markup from scratch — this is a
DRY requirement for the project, not just a style preference.

---

## Task 4 — Google Gemini adapter

Implement app/adapters/google.py following the shared adapter interface:
run(prompt_text: str, model_name: str) -> RawResponsePayload.

Use the Gemini API (google-genai SDK) with Google Search grounding enabled.
Support both seeded models (gemini-3.5-flash and gemini-3.1-flash-lite) —
the model name is passed in, not hardcoded.

Map the Gemini response's grounding metadata (groundingChunks /
groundingSupports) into our canonical citations list (source_url,
source_title, source_domain, citation_position, cited_answer_span where
available). Keep the complete, untouched raw response for raw_payload —
don't transform it before storing.

If the response has no grounding metadata at all, record has_citations as
false explicitly rather than leaving it ambiguous.

---

## Task 5 — Run trigger endpoint

Add a POST endpoint that takes a prompt_id and model_id, calls the Google
adapter, creates a Run row (status, started_at/finished_at, latency_ms), and
stores the resulting RawResponse + Citations.

Handle provider failures (timeout, API error, rate limit) by setting the
Run status to 'error' with error_message populated, and return the shared
structured error response (error_code in English, user-facing message going
through the DE/EN localization layer once that exists — for now, English
text in the message field is fine since German content isn't populated
yet, but keep the field name/shape ready for i18n).

Add a simple trigger button in the Prompt detail view that calls this
endpoint.

---

## Task 6 — Run list & detail view

Add a view showing the list of runs for a prompt — each row should show the
model used, provider, started_at, status, and latency_ms, so it's clear at
a glance which model answered which prompt and when.

Add a run detail page showing: rendered_text, an expandable/collapsible raw
JSON block (raw_payload), and the list of citations (source_url,
source_title, source_domain). This is the end-to-end proof point — after
this, "create client → create prompt → run against Gemini → see the raw
answer" should work fully in the browser.