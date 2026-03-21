# Architecture Decision Log

Captures *why* specific implementation choices were made. Consult this before
changing anything that might seem like an obvious simplification — there's
usually a reason.

---

## 001 — SQLite + WAL mode (not Postgres)

**Decision:** Use SQLite as the primary database, accessed via SQLAlchemy so it
can be swapped later.

**Why:** Zero config, single-file DB, no extra container. This is a
single-user/small-team tool; SQLite's write serialization is not a bottleneck.
WAL mode (`PRAGMA journal_mode=WAL`) improves concurrent read performance during
active load runs (polling goroutines reading while the orchestrator writes).

**How WAL is set:** Via a SQLAlchemy `@event.listens_for(engine.sync_engine, "connect")`
listener in `app/database.py`. This fires on every new connection so it applies
even if the DB file is recreated. The same listener also sets
`PRAGMA foreign_keys=ON` (SQLite disables FK enforcement by default).

**Trade-off:** SQLite serialises writes, which means heavy concurrent writes from
the orchestrator could queue. Acceptable for the expected load volume. If this
becomes an issue, swapping to Postgres is a SQLAlchemy config change.

---

## 002 — UUIDs stored as String(36)

**Decision:** Primary keys are UUID v4, stored as `VARCHAR(36)` strings in SQLite.

**Why:** SQLite has no native UUID type. SQLAlchemy's `Uuid` type uses BLOB in
SQLite, which makes raw SQL inspection painful and complicates JSON serialisation.
String(36) keeps IDs human-readable in the DB and in API responses with no extra
conversion step.

**How generated:** `default=lambda: str(uuid.uuid4())` on the column definition,
so the Python layer always generates the ID — never the DB. This makes IDs
available immediately after object construction, before a flush.

---

## 003 — Async throughout (aiosqlite + AsyncSession)

**Decision:** Use `create_async_engine` with the `aiosqlite` driver and
`AsyncSession` everywhere. No sync SQLAlchemy usage in application code.

**Why:** The orchestrator's polling loop (Salesforce Bulk API status checks)
is I/O-bound and runs concurrently with DB writes. Using async DB access means
the event loop is never blocked waiting for disk I/O, keeping poll latency low.
FastAPI is also async-native, so mixing sync SQLAlchemy would require
`run_in_executor` workarounds.

**Trade-off:** Alembic's migration runner is sync-only. `alembic/env.py` bridges
this with `asyncio.run(run_async_migrations())` and a `NullPool` engine so
connections aren't shared across the sync/async boundary.

---

## 004 — Alembic with render_as_batch=True

**Decision:** Always run Alembic in batch mode (`render_as_batch=True` in
`env.py`).

**Why:** SQLite does not support `ALTER TABLE ... DROP COLUMN`, `ADD CONSTRAINT`,
or most other DDL mutations. Alembic's batch mode works around this by
reconstructing the table: create temp table → copy data → drop original →
rename temp. Without this, any future migration that changes a column will fail
against SQLite.

**Note:** This is a no-op overhead for Postgres if the DB is ever swapped.

---

## 005 — Enums as Python str+enum.Enum, stored as VARCHAR in SQLite

**Decision:** `Operation`, `RunStatus`, and `JobStatus` are `str, enum.Enum`
subclasses, declared with `sa.Enum(MyEnum, name="...")`.

**Why `str` mixin:** Makes enum values JSON-serialisable and comparable to plain
strings without `.value` unwrapping. FastAPI/Pydantic handle them transparently.

**Why not CHECK constraints manually:** `sa.Enum` generates the appropriate CHECK
constraint for SQLite automatically, and the named enum type for Postgres if
ever migrated.

**SQLite behaviour:** SQLite stores enums as plain VARCHAR — there is no native
ENUM type. The CHECK constraint is enforced at the DB level. Alembic's migration
emits the correct DDL for whichever dialect is in use.

---

## 006 — Foreign key cascade strategy

**Decision:**
- `load_step` → `load_plan`: `CASCADE` (deleting a plan removes its steps)
- `job_record` → `load_run`: `CASCADE` (deleting a run removes its jobs)
- `load_plan` → `connection`: `RESTRICT` (can't delete a connection that has plans)
- `load_run` → `load_plan`: `RESTRICT` (can't delete a plan that has run history)
- `job_record` → `load_step`: `RESTRICT` (step referenced by job history is protected)

**Why:** Steps are config that belongs to a plan — deleting the plan should clean
them up. Job records are audit history that belongs to a run — deleting the run
should clean them up. Connections and plans with history are protected to prevent
accidental data loss of audit trails.

---

## 007 — Relative file paths in job_record

**Decision:** `success_file_path`, `error_file_path`, and `unprocessed_file_path`
in `job_record` are stored as paths relative to `OUTPUT_DIR`, not absolute paths.

**Why:** Absolute paths bake the container's mount point into the DB. If the
container is recreated with a different mount or the DB is inspected from the
host, paths break. Relative paths keep the DB portable. The application resolves
them to absolute paths at runtime using `settings.output_dir`.

---

## 008 — No Celery; asyncio for background tasks

**Decision:** The orchestrator runs as an `asyncio` background task (FastAPI's
`BackgroundTasks` or a direct `asyncio.create_task`). No Celery, Redis, or
separate worker process.

**Why:** The workload is I/O-bound (Salesforce API polling), not CPU-bound.
`asyncio` handles hundreds of concurrent poll coroutines efficiently with a
single process. Celery adds ops complexity (broker, worker containers) that
isn't justified for a single-user tool. If horizontal scaling is ever needed,
the orchestrator interface is isolated enough to swap the execution backend.

---

## 009 — JWT Bearer auth only (no OAuth web flow in MVP)

**Decision:** Only the OAuth 2.0 JWT Bearer (server-to-server) flow is
implemented in the MVP. Username/password and web OAuth flows are explicitly
deferred.

**Why:** JWT Bearer requires no interactive login, making it suitable for
automated/scheduled runs. It's the recommended approach for server integrations
by Salesforce. The Connected App setup is a one-time operation.

**How credentials are protected:** The RSA private key and access token are
encrypted at rest with Fernet symmetric encryption. The encryption key comes
from the `ENCRYPTION_KEY` environment variable and is never stored in the DB
or committed to source control.

---

## 011 — Business rules extracted from routers into domain services (Phase 1.3)

**Decision:** Domain logic that is not HTTP-specific (plan duplication, run creation,
abort, summary aggregation, logs ZIP assembly, retry step orchestration, step
reordering) lives in `app/services/load_plan_service.py`,
`app/services/load_run_service.py`, and `app/services/load_step_service.py`.
Routers handle only HTTP concerns: parameter parsing, status codes, background
task enqueueing, and streaming responses.

**Why:** The router files had grown to contain DB orchestration and domain rules
that were untestable without an HTTP layer. Extracting them makes the logic
directly unit-testable and keeps each layer's responsibility clear.

**HTTPException in services:** Services raise `HTTPException` directly rather than
introducing a new domain-exception layer. This matches the existing pattern in
`app/services/auth.py` and avoids over-engineering for the current scope.

**BackgroundTasks stays in routers:** `BackgroundTasks` is a FastAPI dependency
that can only be resolved inside route handlers. Services return all data needed
(e.g. the new `LoadRun` and computed partitions); routers enqueue the task.

**Trade-off:** Services importing FastAPI's `HTTPException` creates a soft coupling
to the HTTP layer. Acceptable for now — if a non-HTTP consumer (e.g. a CLI or test
fixture) needs the same logic, the raise can be replaced with a domain exception at
that point.

---

## 010 — External IDs for object relationships (no runtime ID mapping)

**Decision:** Child records reference parent records via Salesforce external ID
fields (e.g. `Account.ExternalId__c`) in the CSV, not via Salesforce record IDs
resolved at runtime.

**Why:** Runtime ID mapping requires reading parent success CSVs after each step,
joining them to child CSVs, and injecting IDs before upload. This is complex,
error-prone, and tightly couples step execution. External ID resolution happens
server-side in Salesforce during upsert, eliminating all of that. It is the
Salesforce-recommended approach for data migrations.

**Trade-off:** Requires the customer's source data to include external IDs on
both parent and child records. Insert-only workflows without external IDs cannot
use this approach. Runtime ID mapping is listed as a future consideration in the
spec (§13).

---

## 012 — PlanEditor decomposed into feature components and hooks (Phase 2.4)

**Decision:** `frontend/src/pages/PlanEditor.tsx` was split from a 1103-line monolith into:

- `frontend/src/pages/planEditorUtils.ts` — shared types (`PlanFormData`, `StepFormData`, `PreviewEntry`), constants (`OPERATIONS`, `INPUT_CLASS`, `LABEL_CLASS`), and helpers (`extractErrors`, `operationVariant`)
- `frontend/src/hooks/usePlanEditorState.ts` — all form state, step modal state, file picker state, queries (plan, connections, sfObjects, patternPreview), mutations, derived state, and action handlers
- `frontend/src/hooks/useStepPreview.ts` — per-step preview state and preflight logic
- `frontend/src/components/FilePicker.tsx` — file browser component (was inline in PlanEditor)
- `frontend/src/components/PlanForm.tsx` — plan details card (pure rendering)
- `frontend/src/components/StepList.tsx` — step table with inline preview results
- `frontend/src/components/StepEditorModal.tsx` — add/edit step dialog
- `frontend/src/components/PreflightPreviewModal.tsx` — preflight results grid

`PlanEditor.tsx` is now ~190 lines: route wiring, header, loading/error guards, and component assembly.

**Why:** The single-file approach made each new feature expensive — any change required navigating 1100 lines and risked touching unrelated state. Decomposing by concern (form rendering, step list rendering, modal rendering, state/query management, preview management) makes each unit independently readable and testable.

**Hook boundary rationale:** `usePlanEditorState` owns everything that involves mutations or cross-cutting state (plan form ↔ connection id ↔ sfObjects query, step form ↔ file picker ↔ patternPreview query). `useStepPreview` is kept separate because it has no dependency on mutation state and only needs the plan id.

**No behaviour changes:** This is a pure structural refactor. Existing `PlanEditor.test.tsx` tests continue to pass without modification.

---

## 013 — Connections page: two separate sections, not tabs

**Decision:** The Connections page shows Salesforce connections and S3 input connections as two
distinct sections on a single scrollable page, each with its own heading, table, modals, and test
result panel. No tabs, no unified polymorphic form.

**Why — separate sections over tabs:** The spec explicitly requires "separate, not unified". A
single scrollable page with two headed sections avoids tab-state management and keeps both
connection lists visible at a glance. There are only two types, so the added complexity of tabs
would be pure overhead.

**Why — separate forms over a generic polymorphic form:** Salesforce and S3 have entirely
different fields (JWT private key vs. AWS access keys, login URL vs. bucket/prefix/region). A
shared form would require either heavy conditional rendering or a dynamic field schema, both of
which obscure intent and make validation harder. Two explicit forms are simpler and more
maintainable.

**Why — credential fields blank = keep existing:** AWS credentials (access_key_id,
secret_access_key, session_token) are never echoed back to the frontend. On edit, blank fields
mean "leave unchanged", matching the pattern already established for `private_key` on Salesforce
connections. Only non-empty values are included in the PATCH payload.

**Why — `provider` hardcoded to `'s3'`:** Only one input provider is implemented. Adding a
dropdown for a single option is YAGNI and creates a false impression of extensibility. When a
second provider is added, the form can be extended then.
