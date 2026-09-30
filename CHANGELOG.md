# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows the rules in `docs/DEPLOYMENT.md` §0, tailored to a
deployed internal app rather than a published library: a release is a
`master` merge plus one deploy, and version numbers describe what that
deploy means for the person deploying it and for the data already stored,
not a public API.

## [Unreleased]

## [1.2.0] - 2026-09-30

### Added

- `tools/local/refresh_dev_db.py` loads a production dump into the local
  development database: it backs up the dev data first, turns off every
  schedule in the copy, and enforces dry-run, so the copy can never call a
  provider.
- `tools/local/compare_peec.py` compares SignalMap's own runs against a
  client's Peec export for the models both tools have, and reports whether
  the two tools' brand and source-domain findings differ more than either
  tool's own day-to-day/repeat noise.
- Citation verification: every cited source is fetched and its text
  snapshotted, with an archive.org fallback when the live page has
  changed or disappeared. Anthropic and Perplexity citations are checked
  against their captured source deterministically (literal-quote
  matching); OpenAI and Gemini citations can additionally be judged by an
  LLM for paraphrased claims, gated behind a per-client toggle with a
  cost estimate shown before any bulk run. Reviewers can agree or
  disagree with an LLM verdict, or blind-label a citation without seeing
  it first. The run detail page, the client dashboard, and `/ops` all
  show the resulting verified/partial/unsupported/unverifiable rates.
- `python -m app.cli.backfill_sources` captures sources for a client's
  existing citation history (a one-time backfill, not part of the
  regular run flow).

### Fixed

- The first runs on OpenAI, Perplexity, DeepSeek, Grok, or Anthropic
  after a restart could fail with "deadlock detected by _ModuleLock"
  when triggered concurrently.
- OpenAI citations showed the raw link marker
  (`([domain](url?utm_source=openai))`) as the "Cited claim" on the run
  detail page and in exports, instead of the actual claim text from the
  rendered answer.

## [1.1.0] - 2026-09-23

### Added

- Perplexity provider (Agent API), with citations and search queries from
  its `search_results` output item.
- DeepSeek provider for ungrounded baseline answers — never returns
  citations, by design (no web search surface exists in its API).
- xAI Grok provider, with `web_search` citations and real geographic
  targeting.
- Deployed version, build SHA, and environment shown in the page footer
  for logged-in users (`docs/TASKS_VERSIONING.md`).

### Changed

- The AI models admin list (`/ai-models`) is now grouped by provider
  instead of one flat table, for readability now that six providers are
  seeded.
- The system-instruction hint text on `/settings` is shorter and
  provider-specific, instead of one paragraph naming Anthropic on every
  provider's card regardless of whether it applied.

## [1.0.0] - 2026-09-22

The first tagged release. SignalMap has been live on Hetzner and in use
against a real client since 2026-09-17; this baseline gathers everything
built up to that point into one release rather than reconstructing 18
historical PRs as separate versions that never actually existed.

### Added

- Phase 1 vertical slice: client, prompt set and prompt management, and
  manually-triggered runs against Google Gemini with stored raw answers
  and citations.
- Market CRUD, prompt versioning, per-run market override, visible
  request payloads, a per-provider system-instruction template
  (`/settings`), an in-app bilingual help guide, and an English-only
  findings log — all built on top of the phase-1 slice.
- Production-readiness hardening: structured logging, a non-root
  container, pinned dependencies, a consistent delete policy, and the
  first pytest suite (#1).
- Anthropic provider and a providers/AI-models admin UI (#2).
- Run export to CSV, XLSX and JSON (#3).
- Search queries as a distinct concept from prompts (#4).
- First analysis skill: mention/visibility detection (#5).
- Dashboard v0: domain league table and time series (#6).
- Competitive visibility: second-generation analysis skills (#7).
- Authentication and user management, with role-based access (#8).
- Docker Compose deployment and server tooling, and the first production
  deploy to Hetzner (#9).
- ChatGPT adapter, persona placeholder, and AI-model price history (#10).
- Local-timezone display for run timestamps (#11).
- Bulk prompt import and multi-model runs (#12).
- Ops dashboard (#13).
- Cost components, replacing flat per-1k pricing with per-unit price
  history (#14).
- Maintenance page shown during deploys (#16).
- Pre-scheduler data hygiene: test-client flagging and related cleanup
  (#17).
- Scheduler: recurring run schedules, queueing, retries, budget and
  quota notifications (#18).

### Fixed

- Gemini citation extraction resolving the domain from the redirect URL
  instead of the grounding chunk title, and the related claim /
  source-passage split (#15).

Detailed history before this baseline — every commit and design
decision — lives in `git log` and in `docs/TASKS_*.md` / `docs/PROMPTS_*.md`,
one pair per feature listed above.
