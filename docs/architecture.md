# System Architecture

## What this covers / who should read this

This document explains how the Salesforce Bulk Loader's backend, frontend, database, and orchestration layers fit together. Read it to understand the moving parts before diving into code or designing features.

---

## System at a glance

The app is a containerised web application (with desktop and cloud variants) for orchestrating multi-step data loads into Salesforce via the Bulk API 2.0. A user defines a **LoadPlan** (what to load, in what order), triggers a **LoadRun** (an execution instance), and watches job progress in real time via WebSocket updates. The backend orchestrator partitions input CSVs, creates Salesforce Bulk API jobs concurrently, polls to completion, and writes result CSVs to local or S3 storage. Three profiles (`desktop`, `self_hosted`, `aws_hosted`) tune auth, transport, and storage per deployment model.

```
┌─────────────────────────────────────────────────────────┐
│ Browser (React SPA)                                     │
│ - Pages: Dashboard, Connections, Plans, Runs, Files     │
│ - React Query for server state (30s stale time)         │
│ - Context API for theme + auth                          │
└─────────────────┬───────────────────────────────────────┘
                  │ HTTP / HTTPS
                  ▼
         ┌─────────────────┐
         │  nginx proxy    │ (port 80 / 443, hosted profiles)
         └────┬────────┬───┘
         ┌────▼──┐   ┌─▼──────────────┐
         │ /api/*│   │ /ws/* + static │
         └────┬──┘   └─┬──────────────┘
         ┌────▼──────────▼──────────────────────┐
         │  FastAPI backend                     │
         │  - REST routers (connections,        │
         │    load_plans, load_runs, jobs, …)   │
         │  - WebSocket: /ws/runs/{run_id}      │
         │  - Background orchestrator tasks     │
         └────┬─────────────────────────────────┘
              │
              ▼
         ┌─────────────────────┐
         │  SQLite (WAL mode)  │
         │  or PostgreSQL      │
         │  (async SQLAlchemy) │
         └─────────────────────┘
```

---

## Backend

The backend is a **FastAPI application** structured into layers. See [`backend/app/main.py`](../backend/app/main.py) for app initialisation and middleware wiring.

- **Routers** ([`backend/app/api/`](../backend/app/api)) — one module per resource (connections, load_plans, load_steps, load_runs, jobs, files, auth, users, invitations, …). Dependency injection provides the `AsyncSession` and the authenticated user.
- **Schemas** ([`backend/app/schemas/`](../backend/app/schemas)) — Pydantic models for request/response contracts; mirrors the ORM.
- **Services** ([`backend/app/services/`](../backend/app/services)) — business logic (orchestrator, Salesforce client, CSV processor, auth, storage, notifications, email).
- **Utils** ([`backend/app/utils/`](../backend/app/utils)) — WebSocket manager, encryption helpers, CLI entry-points.

**Database.** Async SQLAlchemy 2.0 with `aiosqlite` (SQLite) or `asyncpg` (PostgreSQL). See [`database.py`](../backend/app/database.py) — SQLite uses WAL mode with foreign keys enforced; PostgreSQL uses a pooled connection. Migrations run automatically on container start via Alembic ([`backend/alembic/versions/`](../backend/alembic/versions)).

**Config.** `Settings` is a Pydantic model in [`config.py`](../backend/app/config.py) driven by `.env`. Notable env vars:

- `APP_DISTRIBUTION` — `desktop` | `self_hosted` | `aws_hosted` (sets `auth_mode`, transport, storage defaults)
- `ENCRYPTION_KEY` — Fernet key for storing Salesforce private keys at rest
- `SF_API_VERSION` — Salesforce API version (default `v62.0`)
- `JWT_SECRET_KEY` — HS256 key for session tokens
- `ADMIN_EMAIL`, `ADMIN_PASSWORD` — bootstrap admin credentials (first-boot seed)
- `INVITATION_TTL_HOURS` — invitation-link lifetime (default 24 h)

---

## Frontend

React 18 SPA built with Vite + TypeScript + Tailwind. See [`frontend/src/`](../frontend/src).

- **Pages** ([`pages/`](../frontend/src/pages)) — one file per route (Dashboard, Connections, Plans, RunDetail, JobDetail, Files, Admin Users, …).
- **API client** ([`api/client.ts`](../frontend/src/api/client.ts), [`api/endpoints.ts`](../frontend/src/api/endpoints.ts)) — `apiFetch` base helper + typed wrappers per resource.
- **Context** ([`context/`](../frontend/src/context)) — theme (`ThemeContext`) and auth (`AuthContext`).
- **Components** ([`components/ui/`](../frontend/src/components/ui)) — custom Tailwind component library (no external UI framework).
- **Hooks** ([`hooks/`](../frontend/src/hooks)) — React Query wrappers; [`useLiveRun.ts`](../frontend/src/hooks/useLiveRun.ts) polls a run + jobs every 3 s, stops at terminal status.

**State.** React Query for all server state (30 s stale time, 1 retry). WebSocket (`/ws/runs/{run_id}`) for real-time run updates.

**Dev proxy.** `vite.config.ts` proxies `/api/*` and `/ws/*` to the backend (`http://localhost:8000` by default). In Docker, nginx does the same routing.

---

## Data model

Five core tables; see [`backend/app/models/`](../backend/app/models).

```
Connection (Salesforce OAuth creds; Fernet-encrypted private key)
└─ LoadPlan (load config: max_parallel_jobs, error_threshold_pct, abort_on_step_failure,
             optional output_connection_id for S3 output)
   ├─ LoadStep (object_name, operation, csv_file_pattern, partition_size, sequence)
   └─ LoadRun (execution instance: status, totals, timestamps)
      └─ JobRecord (one partition of one step in one run; sf_job_id,
                    result file paths, record counts)
```

Additional tables: `User`, `Profile`, `InvitationToken`, `PasswordResetToken`, `LoginAttempt`, `NotificationSubscription`, `EmailDelivery`, `AppSetting`, `InputConnection` (S3).

- Relationships use Salesforce **external IDs** (no runtime ID mapping).
- Private keys on `Connection` are Fernet-encrypted at rest.
- `LoadStep.operation` is one of `insert`, `update`, `upsert`, `delete`, `query`, `queryAll`.
- `LoadRun.status` transitions: `pending → running → completed | completed_with_errors | failed | aborted`.
- `JobRecord.status` transitions: `pending → uploading → upload_complete → in_progress → job_complete | failed | aborted`.

---

## Distribution profiles

Selected via `APP_DISTRIBUTION` in `.env`. Each profile sets sensible defaults and enforces invariants at config-load time.

| Profile | Auth | Transport | Input storage | Database | Typical use |
|---|---|---|---|---|---|
| `desktop` | None (`auth_mode=none`) | Loopback | Local filesystem | SQLite only | Electron single-user app |
| `self_hosted` | Password (`auth_mode=local`) | HTTP or HTTPS | Local or S3 | SQLite or PostgreSQL | Docker; internal/on-prem |
| `aws_hosted` | Password (`auth_mode=local`) | HTTPS required | S3 only | PostgreSQL required | Cloud with enforced posture |

Validation lives in [`config.py`](../backend/app/config.py) — invalid combinations (e.g. `desktop` + PostgreSQL) raise at import.

See the per-profile deployment guides: [`docs/deployment/docker.md`](deployment/docker.md), [`docs/deployment/desktop.md`](deployment/desktop.md), [`docs/deployment/aws.md`](deployment/aws.md).

---

## Authentication & RBAC (summary)

- **Desktop** (`auth_mode=none`): no login; a virtual user holds all permissions.
- **Hosted** (`auth_mode=local`): email + password login; short-lived JWT sessions; users belong to one of the `admin`, `operator`, or `viewer` **profiles**.
- Permission enforcement is profile-based using **permission keys** (e.g. `connections.manage`, `runs.execute`, `files.view_contents`). Backend routes declare requirements with `Depends(require_permission(<key>))`; the frontend mirrors with `ProtectedRoute` (route-level) and `PermissionGate` / `usePermission` (in-page).

See [`docs/architecture/auth-and-rbac.md`](architecture/auth-and-rbac.md) for the full flow and [`docs/specs/rbac-permission-matrix.md`](specs/rbac-permission-matrix.md) for the authoritative matrix (drift-tested by [`backend/tests/test_permission_matrix.py`](../backend/tests/test_permission_matrix.py)).

---

## Run execution (summary)

The orchestrator ([`backend/app/services/orchestrator.py`](../backend/app/services/orchestrator.py)) runs as an asyncio background task when a run is started:

1. Iterate `LoadStep`s in `sequence` order.
2. For each step: discover CSVs via glob, partition them, create one `JobRecord` per partition, then process all partitions concurrently (bounded by `LoadPlan.max_parallel_jobs`).
3. For each partition: authenticate to Salesforce via JWT Bearer (token cached with a 300 s refresh buffer), create a Bulk API 2.0 job, upload CSV, close the job, poll to completion with exponential backoff (5 s → 30 s), download success/error/unprocessed CSVs.
4. After each step: evaluate `error_threshold_pct`; if exceeded and `abort_on_step_failure=True`, abort the run.
5. Broadcast progress via WebSocket throughout.

See [`docs/architecture/run-execution.md`](architecture/run-execution.md) for the full lifecycle, polling timeouts, HTTP retries, and concurrency invariants.

---

## Storage (summary)

- **Input** — CSVs under `data/input/` (or `INPUT_DIR`), matched by `LoadStep.csv_file_pattern` (glob). Path-traversal safe. For hosted profiles an S3 `InputConnection` is an alternative source.
- **Output** — per-job success/error/unprocessed CSVs + per-run `logs.zip`. Local filesystem (`data/output/`) by default; S3 when a `LoadPlan` has an `output_connection_id`.
- **Encryption at rest** — Salesforce private keys on `Connection` are Fernet-encrypted using `ENCRYPTION_KEY`.

See [`docs/architecture/storage.md`](architecture/storage.md) for partitioning, S3 wiring, and encryption details.

---

## Observability

Structured logging with canonical event names, outcome codes, correlation IDs (ContextVar-propagated), Prometheus metrics, optional OpenTelemetry spans, and optional Sentry error reporting. The baseline reference (event taxonomy, metric names, span boundaries, DoD checklist) lives in [`docs/observability.md`](observability.md) — do not duplicate it elsewhere.

Core modules in [`backend/app/observability/`](../backend/app/observability):

- `logging_config.py` — format and level
- `middleware.py` — request-ID stamping and access logs
- `events.py` — `RunEvent`, `StepEvent`, `JobEvent`, `OutcomeCode` constants
- `metrics.py` — Prometheus counters / histograms exposed at `/metrics`

---

## Key design decisions

- **JWT Bearer auth only** for Salesforce (no OAuth web flow). Short-lived user JWTs for the app itself (no refresh tokens).
- **External IDs** for Salesforce object references (no runtime ID mapping).
- **SQLite + WAL mode** by default; PostgreSQL for hosted profiles. Async SQLAlchemy makes the DB swappable.
- **asyncio for background tasks** — no Celery. One orchestrator task per run, supervised by FastAPI's lifespan.
- **CSV streaming** via Python's `csv` module (no pandas). At most one partition's worth of rows is in memory.
- **Three distribution profiles** with profile-aware config validation and router gating.
- **Fresh `AsyncSession` per concurrent partition task** — never share sessions across coroutines.
- **WebSocket for real-time run updates**; terminal run statuses stop broadcasts.
- **Polling with exponential backoff** for Bulk API job completion; HTTP retries (1 s / 2 s / 4 s) on transient errors.
- **Fernet encryption** for Salesforce private keys at rest (symmetric, single key from env).
- **Jira is the planning surface** for new feature work; `docs/specs/` root is reserved for *live* contracts (currently just the RBAC permission matrix). Historical specs live under [`docs/specs/implemented/`](specs/implemented).
