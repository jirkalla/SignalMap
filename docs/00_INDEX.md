# SignalMap — Documentation Index

Where every feature-branch task/prompt document stands: done or not, which
PR, when it merged, which release it's in. Named `00_INDEX.md` so it sorts
first in `docs/` — read this before opening any `TASKS_*.md`/`PROMPTS_*.md`
pair to know whether it's current or historical.

Not the phase-1 walkthrough itself — that stays in `docs/TASKS.md`, since
601 code/doc references already point at it (docs/TASKS_VERSIONING.md
decision 15). This index only tracks status; it never replaces or moves a
document.

**Update this file at PR-merge time**, as part of the same task-completion
step that adds the `## Status:` heading to that PR's own `TASKS_*.md`/
`PROMPTS_*.md` (see `AI_INSTRUCTIONS.md` §7) — not in a batch pass
reconstructed from git history later. PR numbers and merge dates come from
`git log --merges` / `gh pr list --state merged`, never from memory.

---

## In progress / not started

Nothing in progress right now.

## Merged, release pending

| Document | Prefix | Branch | PR | Merged | Notes |
|---|---|---|---|---|---|
| TASKS_OPENAI_IMPORT_RACE | OIR | feature/signalmap-openai-import-race | #21 | 2026-09-25 | Production deploy and verification (T3) not done yet |

## Released — newest first

One row per `docs/TASKS_*.md` / `docs/PROMPTS_*.md` pair. Not the phase-1
tasks (Task 1–6), which live in `docs/TASKS.md` itself and predate this
per-branch pattern.

| Document | Prefix | Branch | PR | Merged | Released in |
|---|---|---|---|---|---|
| TASKS_NEW_PROVIDERS | NP | feature/signalmap-new-providers | #20 | 2026-09-23 | v1.1.0 |
| TASKS_VERSIONING | VER | feature/signalmap-versioning-footer | #19 | 2026-09-23 | v1.1.0 |
| TASKS_SCHEDULER | SCH | feature/signalmap-scheduler | #18 | 2026-09-22 | v1.0.0 |
| TASKS_PRE_SCHEDULER | PRE | feature/signalmap-prescheduler-data-hygiene | #17 | 2026-09-19 | v1.0.0 |
| TASKS_MAINTENANCE_PAGE | MP | feature/signalmap-maintenance-page | #16 | 2026-09-18 | v1.0.0 |
| TASKS_GEMINI_CITATIONS | GC | feature/signalmap-gemini-citation-extraction | #15 | 2026-09-16 | v1.0.0 |
| TASKS_COST_COMPONENTS | CC | feature/signalmap-cost-components | #14 | 2026-09-16 | v1.0.0 |
| TASKS_OPS_DASHBOARD | — | feature/signalmap-ops-dashboard | #13 | 2026-09-16 | v1.0.0 |
| TASKS_BULK_IMPORT_MULTI_MODEL | BIM | feature/signalmap-bulk-import-multi-model | #12 | 2026-09-15 | v1.0.0 |
| TASKS_LOCAL_TIME | LT | feature/signalmap-local-time-display | #11 | 2026-09-14 | v1.0.0 |
| TASKS_CHATGPT_PERSONA_PRICING | CPH | feature/signalmap-chatgpt-persona-pricehistory | #10 | 2026-09-13 | v1.0.0 |
| TASKS_PHASE6 | P6 | feature/signalmap-phase6-auth | #8 | 2026-09-12 | v1.0.0 |
| TASKS_PHASE5 | P5 | feature/signalmap-phase5-competitive-visibility | #7 | 2026-09-11 | v1.0.0 |
| TASKS_PHASE4 | P4 | feature/signalmap-phase4-dashboard-v0 | #6 | 2026-09-11 | v1.0.0 |
| TASKS_PHASE3 | P3 | feature/signalmap-phase3-mention-detection | #5 | 2026-09-10 | v1.0.0 |
| TASKS_SEARCH_QUERIES | SQ | feature/signalmap-search-queries | #4 | 2026-09-10 | v1.0.0 |
| TASKS_EXPORT | EX | feature/signalmap-runs-export | #3 | 2026-09-10 | v1.0.0 |
| TASKS_PHASE2 | P2 | feature/signalmap-phase2-anthropic-admin | #2 | 2026-09-09 | v1.0.0 |
| TASKS_HARDENING | HD | feature/signalmap-phase1-hardening | #1 | 2026-09-09 | v1.0.0 |

PR #9 (`feature/signalmap-deploy-compose`, merged 2026-09-17) isn't listed:
it has no `TASKS_*.md`/`PROMPTS_*.md` pair of its own, it's covered by
`docs/DEPLOYMENT.md` directly.
