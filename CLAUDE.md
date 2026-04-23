# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is
A containerized application for orchestrating large-scale data loads into Salesforce
using the Bulk API 2.0. Python backend (FastAPI), SQLite database, React frontend,
Docker deployment.

## Spec
The full specification is in `salesforce-bulk-loader-spec.md`. Always refer to it for architectural decisions, data model, API design, and build order.
For the UI extra guidance can be found in `frontend-claude-runbook.md`. Treat it as an authority — if other docs conflict, follow the runbook.

Active spec files live in `docs/specs/`. Ticket status is tracked in Jira — do not mark tickets as done in the spec files themselves.

## Parallel Agent Orchestration

When running multiple Claude Code subagents in parallel across worktrees, follow
`PARALLEL_AGENTS.md` at repo root. It encodes hard-won rules about feature-branch
discipline, worktree path pinning, scope fencing, the post-batch check sequence
(`pytest` + `npm run typecheck` + `npm run test:run`), and model selection
(Sonnet by default, Haiku for trivial, Opus only when escalated). Read it
before launching any multi-agent wave.

## Jira Workflow

Tickets are tracked in Jira project **SFBL** at `matthew-jenkin.atlassian.net`. Use the Jira MCP tools to manage ticket state as you work.

**When starting a ticket:**
1. If a plan has been produced, post it as a comment on the Jira issue using `jira_add_comment` before writing any code
2. Transition the Jira issue to **In Progress** using `jira_transition_issue`
3. Begin implementation

**When completing a ticket:**
1. Run backend tests (`cd backend && pytest`) and frontend tests (`cd frontend && npm run test:run`) and note results
2. Transition the Jira issue to **Done** using `jira_transition_issue`
3. Add a comment to the Jira issue using `jira_add_comment` summarising:
   - What was implemented
   - Key files changed
   - Test results (pass/fail counts)
   - Any notable decisions or deviations from the spec

## Epic Delivery: One PR Per Epic

An epic ships as **one shippable PR**, not a stack of per-ticket PRs. Each
child ticket is still tracked individually in Jira (plan comment → In Progress
→ Done with completion comment), but the code for every ticket in the epic
lands on a single long-lived epic branch (`feat/sfbl-<epic-id>-<slug>`) and is
opened as one PR at the end once the feature is complete end-to-end.

Rationale: an epic is the unit that delivers user value; merging half of an
epic leaves the product in an inconsistent state (e.g. a backend channel with
no UI to configure it). Reviewers also get the full picture in one pass.

Per-ticket commits on the epic branch should use conventional commit prefixes
so they're greppable (`SFBL-179: …`, `SFBL-180: …`). Push regularly so CI
stays green, but do not open the PR until the final ticket of the epic is
implemented. The PR description lists all child tickets with a brief note per
ticket.

### Epic Definition of Done — documentation

Before opening the epic PR, audit `docs/` and `README.md` for staleness
introduced by the epic's changes. Treat documentation updates as part of the
epic's DoD, not a follow-up task. Specifically:

- **User-facing behaviour changes** (new settings, env vars, UI flows, CLI
  commands, profiles/permissions, file formats) must have corresponding
  updates to the relevant page under `docs/` (usage, deployment guides,
  admin-recovery, etc.) and to `.env.example` where applicable.
- **Spec changes** — if the shipped reality diverges from the spec file under
  `docs/specs/`, update the spec in the same PR. Specs that have been fully
  implemented should be moved to `docs/specs/implemented/` or have a banner
  added at the top noting what superseded them.
- **UI conventions** — any new reusable component, modal pattern, form style,
  or navigation change needs a matching note in `docs/ui-conventions.md`.
- **README** — confirm the quick-start, feature list, and screenshots still
  reflect the shipped product. Keep it a signpost only — add detail to
  `docs/`, not the README.
- **Stale references** — grep for references to any env var, route, CLI flag,
  or terminology the epic removed or renamed. Missed references in deployment
  guides are the most common source of post-release confusion.

If any of the above is skipped, the epic PR is not ready to open. A "docs
refresh" follow-up story is an anti-pattern — it tends to be deprioritised
and leaves the product in a state where the docs actively mislead users.

## Documentation Policy

The docs are organised into three **pillars** plus a spec layer. The index at
[`docs/README.md`](docs/README.md) is the authoritative map.

### Pillars

- **Architecture & design** (`docs/architecture.md` + `docs/architecture/*.md`) —
  how the system is built. Read these before making architectural changes.
- **Operations & developer** (`docs/deployment/*.md`, `docs/development.md`,
  `docs/observability.md`, `docs/ci.md`, `docs/email.md`) — how to run,
  develop, and operate the app.
- **Usage** (`docs/usage/*.md`) — task-oriented operator handbook. Each file
  is a single self-contained topic.

### Usage authoring contract

Every file under `docs/usage/` must ship with YAML frontmatter so the Phase 2
in-app help build (SFBL-209) has a stable interface:

```yaml
---
title: Running a load                # human-readable nav + page title
slug: running-loads                  # URL-stable topic id; NEVER renamed
nav_order: 50                        # integer sort key; gaps of 10
tags: [runs, monitoring]             # optional; feeds future search + RAG
required_permission: runs.view       # optional; must match a key in backend/app/auth/permissions.py
summary: >-                          # 1-sentence teaser for nav / search
  Trigger a load, watch it live, abort, and retry failed steps.
---
```

Conventions:
- Every usage topic opens with a **"What this covers / who should read this"**
  section and closes with a **"Related"** cross-link block.
- Sub-headings are short and stable — they become deep-link anchors.
- Topics are self-contained: a reader landing via a deep link doesn't need to
  read predecessors.
- Keep under ~300 lines per topic.

### Specs

`docs/specs/` is reserved for **live** cross-team contracts (currently just
the RBAC permission matrix). Specs that have been implemented move to
`docs/specs/implemented/` with a banner; those files are **not** authoritative
about current behaviour — the code and the handbook pillars are.

### README scope

`README.md` is a signpost only — short pitch, quick-start, and links into the
pillars. Add detail to `docs/`, not the README. The three-pillar index in
`docs/README.md` is where readers go next.

### UI conventions (frontend)

`docs/ui-conventions.md` documents the design-token system, `formStyles.ts`,
and shared components. **Must be kept in sync with the code** — any change
to tokens, `formStyles.ts`, or shared UI components requires a corresponding
update to this file in the same task.

### Epic DoD

The *Epic Definition of Done — documentation* rules above still apply: docs
refresh ships with the feature, not in a follow-up story. This policy section
defines **where** updates land; the DoD defines **when**.

## Tech Stack
- Backend: Python 3.12+, FastAPI, SQLAlchemy 2.0 async, Alembic, httpx
- Database: SQLite with WAL mode, accessed via aiosqlite
- Frontend: React 18, Vite, TypeScript, Tailwind CSS, React Query
- Containerization: Docker, Docker Compose

## Commands

### Development
```bash
# Backend dev server
cd backend && uvicorn app.main:app --reload

# Frontend dev server
cd frontend && npm run dev

# Docker build + run
docker compose up --build

# DB migrations
cd backend && alembic upgrade head
```

### Tests
```bash
# Backend — run all
cd backend && pytest

# Backend — run single file
cd backend && pytest tests/test_csv_processor.py

# Backend — run tests matching a name pattern
cd backend && pytest -k test_create_plan

# Frontend — watch mode
cd frontend && npm test

# Frontend — single run (CI)
cd frontend && npm run test:run

# Frontend — type check
cd frontend && npm run typecheck
```

## Backend Architecture

```
backend/app/
├── main.py          # FastAPI app init, CORS, router registration
├── config.py        # Pydantic Settings — reads from .env or ../.env
├── database.py      # Async SQLAlchemy engine, session factory, Base
├── models/          # SQLAlchemy 2.0 ORM models
├── schemas/         # Pydantic request/response schemas (mirror models/)
├── api/             # Route handlers (connections, load_plans, load_steps, load_runs, jobs, utility)
├── services/        # Business logic
│   ├── orchestrator.py      # Main execution engine
│   ├── salesforce_auth.py   # JWT Bearer OAuth 2.0, Fernet-encrypted private keys, token caching
│   ├── salesforce_bulk.py   # Bulk API 2.0 client — job lifecycle, polling with backoff
│   └── csv_processor.py     # Glob discovery, streaming CSV partitioning
└── utils/
    └── ws_manager.py        # WebSocket manager for real-time run status broadcasts
```

### Execution Flow (orchestrator.py)
`execute_run(run_id)` runs as a background task:
1. For each `LoadStep` (ordered by `sequence`): discover CSVs via glob → partition → create `JobRecord` per partition → process partitions concurrently (bounded by `asyncio.Semaphore` sized to `max_parallel_jobs`)
2. Per partition: create Bulk API job → upload CSV → close job → poll to terminal state → download results
3. After each step: evaluate error threshold; if exceeded and `abort_on_step_failure` is set, abort run
4. Broadcasts WebSocket status updates throughout

Key implementation details:
- Each concurrent partition gets its own `AsyncSession` (never share sessions across tasks)
- Polling uses exponential backoff: starts at `SF_POLL_INTERVAL_INITIAL` (5s), doubles to `SF_POLL_INTERVAL_MAX` (30s)
- HTTP retries: 3 attempts with 1s/2s/4s backoff on 5xx and 429
- JWT lifetime: 180s (Salesforce enforced); token cached with 300s refresh buffer before expiry

## Frontend Architecture

```
frontend/src/
├── api/
│   ├── client.ts     # Base apiFetch, apiGet/Post/Put/Delete helpers
│   ├── endpoints.ts  # High-level API wrapper functions
│   └── types.ts      # TypeScript types for all API models
├── context/
│   └── ThemeContext.tsx   # Dark/light/system theme (localStorage)
├── hooks/
│   └── useLiveRun.ts      # React Query hook — polls run+jobs every 3s; stops at terminal status
├── layout/
│   └── AppShell.tsx       # Sidebar nav shell
├── pages/                 # One file per route
└── components/ui/         # Custom Tailwind component library (no external UI lib)
```

Routes: `/` Dashboard, `/connections`, `/plans`, `/plans/:id`, `/runs`, `/runs/:id`, `/runs/:runId/jobs/:jobId`, `/files`

State: React Query for all server state (30s stale time, 1 retry). Context API only for theme.

## Data Model (key relationships)
```
Connection → LoadPlan → LoadStep → JobRecord
                     ↘ LoadRun  → JobRecord
```
- `LoadPlan` owns the run config (`max_parallel_jobs`, `error_threshold_pct`, `abort_on_step_failure`)
- `LoadStep` defines what to load (`object_name`, `operation`, `csv_file_pattern`, `partition_size`)
- `LoadRun` is an execution instance; `JobRecord` is one partition of one step
- Private keys on `Connection` are Fernet-encrypted at rest

## Key Design Decisions
- JWT Bearer auth only (no OAuth web flow).
- External IDs for object relationships (no runtime ID mapping).
- SQLite + WAL mode for simplicity and concurrent I/O; SQLAlchemy makes it swappable.
- asyncio for background tasks (no Celery).
- CSV streaming with Python's `csv` module (no pandas).
- Authentication is required for hosted profiles (`self_hosted`, `aws_hosted`). Desktop profile (`auth_mode=none`) bypasses login — controlled via `APP_DISTRIBUTION` in `.env`.

## Observability Definition of Done

Any ticket that introduces or materially changes run/step/job lifecycle behaviour,
Salesforce interaction flows, storage flows, retry behaviour, or terminal outcomes
**must** include observability updates as part of the same ticket. This is not optional.

Before implementing such a ticket, work through the checklist in `docs/observability.md`:
- Are new canonical event names needed? Add them to `app/observability/events.py`.
- Are new outcome codes needed? Add them to `OutcomeCode` in the same file.
- Do new log sites use `event_name` and `outcome_code` in `extra={}`?
- Are correlation IDs propagated into new async scopes via ContextVars?
- Which metrics should increment? Update `app/observability/metrics.py`.
- Does the new path introduce an execution boundary needing a custom span?
- Do any new error paths or exception handlers comply with `sanitization.py` rules?

## Pull Request Review Comments

When addressing review comments on a PR (automated or human), always reply directly
to each comment thread after the fix is in place. Use the GitHub API via `gh`:

```bash
gh api repos/{owner}/{repo}/pulls/{pr}/comments \
  -X POST \
  -f body="Your reply text" \
  -F in_reply_to={comment_id}
```

The reply should briefly describe what was changed and why, so reviewers (and future
readers of the thread) can confirm the fix without digging through the diff. Do this
as the final step of remediating each comment — not as a batch at the end.

## Code Standards
- Python: async/await throughout, type hints on all function signatures, Pydantic schemas, SQLAlchemy 2.0 `mapped_column` style.
- Frontend: functional components with hooks, Tailwind for styling (no separate CSS files).

## Important
- `ENCRYPTION_KEY` env var is required — Fernet key for stored Salesforce private keys.
- Salesforce API version is configurable via `SF_API_VERSION` (default `v62.0`).
- Frontend dev server proxies `/api/*` and `/ws/*` to the backend (see `vite.config.ts`).
- In Docker, nginx handles the same proxying (see `frontend/nginx.conf`).
- Alembic migrations run automatically on container start (before uvicorn).

## In-app Help Content Alignment

Any edit to `docs/usage/*.md` that adds, removes, or renames a heading, changes a `required_permission` field, or modifies an internal cross-link must be verified with:

```bash
node frontend/scripts/check-help-links.mjs
```

The CI `docs-drift` job enforces this automatically, but run it locally before pushing. The in-app `/help` route is built from these files at Vite build time — stale links or invalid permissions cause a broken help experience.
