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

## Documentation Structure
User-facing documentation lives in `docs/`:
- `docs/deployment/` — deployment guides per distribution (docker, desktop, aws)
- `docs/usage.md` — using the app (Salesforce setup, CSV format, load plans)
- `docs/development.md` — local dev, tests, migrations
- `docs/observability.md` — observability baseline reference: event taxonomy, metrics, spans, DoD checklist. **Read this before implementing any ticket that touches workflow behaviour.**
- `docs/ui-conventions.md` — frontend design token system, component usage, form styling rules. **Must be kept in sync with the code** — any change to tokens, `formStyles.ts`, or shared UI components requires a corresponding update to this file in the same task.
- `docs/specs/` — architecture and feature specs (not user-facing)

When implementing tickets that require documentation changes, add to or update the
appropriate file in `docs/` rather than expanding `README.md`. The README is a
project overview and signpost only.

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
