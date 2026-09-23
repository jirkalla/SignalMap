# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows the rules in `docs/DEPLOYMENT.md` §0, tailored to a
deployed internal app rather than a published library: a release is a
`master` merge plus one deploy, and version numbers describe what that
deploy means for the person deploying it and for the data already stored,
not a public API.

## [Unreleased]

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
