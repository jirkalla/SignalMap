# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows the rules in `docs/DEPLOYMENT.md` §0, tailored to a
deployed internal app rather than a published library: a release is a
`master` merge plus one deploy, and version numbers describe what that
deploy means for the person deploying it and for the data already stored,
not a public API.

## [Unreleased]

## [1.4.0] - 2026-10-03

### Added

- The worker panel on `/schedules` shows, for every worker, its name,
  state, last signal and what it is processing right now (client,
  model, run), with a "Long call" flag for work that runs unusually
  long. It refreshes every 10 seconds.
- The client page shows the daily run quota — runs used out of the
  limit in the last 24 hours — in amber from 80 % and in red once the
  limit is reached.
- `/schedules` and the client page show a banner with the runs skipped
  in the last 24 hours, broken down by reason (dry runs are not
  counted).
- One notification when a provider's credit runs out (at most once per
  provider every 6 hours), instead of a failure notification for every
  run.
- `/ops` shows the number of LLM verdicts for the selected range and
  client, next to the verification queue.

### Changed

- Scheduled runs are processed several times faster: the worker no
  longer pauses between queue items, and more than one worker can run
  at once (`WORKER_REPLICAS`).
- Workers are named `worker-1` … `worker-N`, matching the containers in
  `docker compose ps`, instead of a random container ID. Entries of
  workers that disappeared without a clean shutdown are removed after
  an hour.
- When a provider's credit is exhausted, the affected runs wait and are
  retried every 30 minutes until the end of the grace period, instead
  of failing after three attempts within minutes. They count towards
  the client's queue limit while they wait.
- Authentication errors and invalid requests are no longer retried;
  temporary errors (rate limit, provider outage, timeout) are tried four
  times, after 1, 5 and 25 minutes. Every attempt now records its error
  category in the queue item.
- The reason a run was skipped is shown in plain language in the
  History view.
- The "LLM judge" row of the verification queue on `/ops` is renamed
  "Manual LLM judging (jobs)": it counts only the jobs started by hand,
  not the automatic judging that runs inside capture jobs.

### Fixed

- A provider call longer than 60 seconds no longer triggers a false
  "worker not responding" notification.
- A corrupted or truncated PDF no longer fails a verification job: the
  citation is recorded as "PDF has no extractable text" and the other
  citations of the response are still verified.
- A malformed response from archive.org (an error page instead of data)
  is treated like an outage and retried, instead of failing the job.
- An unusable source address no longer fails verification: a citation whose
  link cannot be parsed (or whose redirect or headers are malformed) is
  recorded as "Blocked (other HTTP error)", and an over-long Content-Type
  header, an unknown character set or a PDF with broken text no longer
  abort the check.
- A source page title longer than 300 characters (or a domain longer than
  200) no longer makes the run fail to save. The value is cut to fit.

## [1.3.0] - 2026-10-01

### Added

- Clients have a Vision field — how the client wants AI assistants to
  describe it (values, positioning, key messages). It is entered on the
  client form above Notes (up to 4000 characters), shown as a card at the
  top of the client page, and as a collapsible strip on the dashboard. It
  is informational only: it does not affect runs, prompts or exports.

### Changed

- "Verify citations" on a run now shows its progress — queued, running,
  retrying, or failed — instead of a bare button, reloads the page once
  the check finishes so the new verdicts appear, and ignores another click
  while a check for that run is already in progress (no duplicate paid
  LLM passes).

### Fixed

- Gemini citations can be verified again. Their links go through a Google
  redirect page whose robots.txt forbids crawling it, so every Gemini
  citation used to fail as "Blocked by robots.txt". The redirect is now
  followed first and robots.txt is checked for the page the link actually
  leads to. This is also stricter for ordinary links: a link that redirects
  to a site disallowing crawlers is now reported as blocked, where before
  only the original address was checked.
- A hanging AI provider call no longer blocks a worker for up to half an
  hour. Every provider call now has a timeout (`PROVIDER_TIMEOUT_SECONDS`,
  default 120 seconds and at least 10, because the Gemini API rejects
  shorter deadlines; `PROVIDER_JUDGE_TIMEOUT_SECONDS`, default 60 seconds,
  for citation-verification verdicts), and the OpenAI-compatible and
  Anthropic clients retry at most once on their own. The app refuses to
  start with a timeout under 10 seconds.
- An archive.org outage no longer stops verification of a run's other
  citations. Citations whose archived copy could not be checked are retried
  together with the job; if archive.org is still unavailable on the last
  attempt they are recorded as "Archive copy unavailable".
- A source document containing NUL bytes no longer fails verification.
- Bulk verification on a client page no longer queues responses whose
  verification (or source download) is already queued or running.

## [1.2.1] - 2026-09-30

### Fixed

- The run detail page returned a 500 error when a citation's source capture
  had failed with reason `http_429` or `http_other` — both were valid
  reason values but had no matching translation key. `/ops`'s "Capture
  success by reason" table had the same gap for the two reasons.

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
