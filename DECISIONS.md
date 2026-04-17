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

---

## 014 — Preflight warnings surfaced via `LoadRun.error_summary` (no new column)

**Decision:** Non-fatal warnings raised during the pre-count preflight phase (e.g. storage
unavailable for one step, malformed CSV) are stored in the existing `LoadRun.error_summary`
JSON column under a typed `preflight_warnings` list, rather than on a dedicated new column.

**Why:** `error_summary` is already the frontend's single channel for run-level context/problem
state. The UI already conditionally renders it on the run detail page. Adding a new column
would require an Alembic migration, a new API field, and a second rendering surface for
conceptually-the-same category of information ("things the UI should show about this run's
execution context"). The existing column is `Text` holding arbitrary JSON, so extension is
additive.

**How it stays typed:** `RunErrorSummary` (Pydantic, `extra="ignore"`) gains a
`PreflightWarning` sub-model and a `preflight_warnings: Optional[List[PreflightWarning]]`
field. Older runs with `error_summary=None` still parse correctly. The frontend `RunErrorSummary`
TS interface mirrors the Pydantic schema.

**Trade-off:** Conflates "terminal failure context" (`auth_error`) with "non-fatal warnings"
in a single blob. Accepted because the UI already treats them uniformly as "things to render",
and the typed sub-models keep semantics explicit.

---

## 015 — `_mark_run_failed` merges into `error_summary` (does not overwrite)

**Decision:** `_mark_run_failed` shallow-merges the supplied `error_summary` dict into any
existing JSON already stored on the run, rather than overwriting it. A helper
`_merge_run_error_summary(run, updates)` encapsulates the merge.

**Why:** Preflight warnings are written into `error_summary` *before* the main step loop
starts. If a subsequent failure (e.g. auth error, storage error during execution) called
`_mark_run_failed` with a fresh dict, the previous `json.dumps(error_summary)` assignment
would wipe the warnings out, losing non-fatal context that operators need to interpret the
terminal failure. Merge semantics preserve all written keys.

**Behaviour preserved:** Callers of `_mark_run_failed` that used to set a single-key dict
against a previously-`None` column see no change — merge against an empty dict reduces to the
old assignment. The only visible change is *when* prior keys exist, which is exactly the case
the preflight path creates.

**Related:** This helper is the plumbing that SFBL-112 will lean on when funnelling unhandled
step exceptions through `_mark_run_failed`.

---

## 016 — `SF_JOB_MAX_POLL_SECONDS` defaults to 1h; `0` is an explicit opt-out sentinel

**Decision:** The Bulk API poll loop has a hard wall-clock cap of `sf_job_max_poll_seconds`
(default `3600`, one hour). When exceeded, the client raises `BulkJobPollTimeout` (a subclass
of `BulkAPIError`), increments `sfbl_bulk_job_poll_timeout_total`, emits a canonical log
(`event_name=salesforce.bulk_job.poll_timeout`, `outcome_code=job_poll_timeout`), attempts a
best-effort `abort_job` on Salesforce, and lets the partition executor mark the `JobRecord`
failed. Setting the value to `0` preserves the previous unbounded behaviour (opt-out).

**Why — a hard cap, distinct from `sf_job_timeout_minutes`:** `sf_job_timeout_minutes` is a
soft warning (spec §9.1): once crossed the loop logs once and continues polling indefinitely.
That is not the right behaviour when Salesforce leaves a job stuck in `InProgress` forever —
the run would block one slot in the semaphore and operators would see the UI hang with no
terminal status. SFBL-111 adds a distinct *hard* cap so the run can free the slot and
continue with remaining partitions. The two settings coexist: the soft warning still fires
first (so operators see "still running after Xm" before the hard cut-off).

**Why — 1h default:** Most Bulk API 2.0 jobs complete within minutes; an hour is a comfortable
headroom that surfaces genuine stuck-job pathology without false positives. Users with
unusually large ingest volumes can raise it; setting `0` disables the cap entirely.

**Why — subclass `BulkAPIError` not a new top-level exception:** The existing
`except BulkAPIError` branches in `partition_executor` already handle "the job failed; mark
the record failed" cleanly. Making `BulkJobPollTimeout` a subclass means no existing handler
needs to change — they all catch it by inheritance — while call sites that want to
differentiate (for metrics, logs) can `isinstance`-check.

**Enforcement is duplicated on purpose:** Both `salesforce_bulk.poll_job` (used by other
callers / future code) and the hand-rolled loop in `partition_executor._process_partition_body`
enforce the cap. The partition loop reads `time.monotonic()`-equivalent values via
`asyncio.get_event_loop().time()`, tracks `last_state`, and on timeout performs the abort
and raises. Having both ensures the guarantee holds regardless of which polling entry point
is used.

---

## 018 — Hosted execution tier: arq workers on Redis (supersedes #008 for hosted profiles)

**Decision:** On hosted profiles (`self_hosted`, `aws_hosted`) opting into
worker mode, `execute_run` is dispatched to a separate worker process via
[arq](https://arq-docs.helpmanual.io/) backed by Redis. Worker ships as a
distinct image sharing the `backend/` codebase. Desktop profile keeps today's
in-process `asyncio` executor (DECISIONS.md #008 stands for desktop). Hosted
profiles default to `EXECUTOR_MODE=in_process` for backward compatibility;
opting into workers is explicit.

**Why — workers over threadpool or status-quo:** Primary driver is horizontal
scale (aws-hosted). A threadpool inside FastAPI cannot scale past one node and
does not survive API restart. Status-quo with graceful-shutdown hardening
addresses restart resilience but not scale. A broker + worker architecture is
the only option that satisfies both.

**Why — arq over Celery:** Re-evaluated Celery during SFBL-120 spike and ruled
out again. The partition executor is deeply async (httpx to Salesforce,
`AsyncSession` for DB writes, multi-coroutine polling). Celery is sync-first
and would require `asyncio.run()` wrapping per task with an awkward sync/async
bridge. Our task surface is narrow (one task type: execute partition), so
Celery's ecosystem (Flower, Canvas, Beat, result backends, priorities) is
mostly unused weight. arq is async-native, Redis-only, ~4kLOC, and its
`on_job_start` / `on_job_end` hooks map cleanly onto our existing
`app/observability/events.py` surface.

**Why — Redis only (no SQLite broker):** Queue semantics do not abstract
cleanly across SQLite and Redis the way SQLAlchemy flattens DDL/DML. The
narrow "workers without Redis" configuration is already covered by the
`in_process` fallback; a SQLite broker would be strictly additional work
serving no audience the fallback doesn't already cover. `redis:7-alpine` as a
compose service is a one-line operator change.

**Why — separate worker image:** Smaller runtime (no FastAPI/uvicorn/middleware
in the worker), clearer independent scaling, shared source tree. Both images
build from `backend/` with different Dockerfile entrypoints.

**Durability posture:** Graceful restart only. Worker SIGTERM drains in-flight
jobs (arq `max_shutdown_delay`); enqueued-but-unstarted jobs remain in Redis.
Hard worker crash (SIGKILL mid-partition) marks the partition retryable on
next worker boot. No lease/heartbeat reclaim state machine. Redis durability
(ephemeral vs AOF/RDB) is an operator choice documented per profile.

**Hard prerequisite — Postgres for self-hosted + workers:** Multi-process
SQLite writers have real limits (writer serialisation under WAL, file locking).
Self-hosted opting into workers must migrate to Postgres as part of the same
rollout. `aws_hosted` already requires Postgres (see `config.py:95`), so only
`self_hosted` is affected.

**Trade-off:** Two execution paths in the codebase (in-process + worker). The
worker path is the production target for hosted deployments; the in-process
path remains as the desktop executor and as a degraded-mode fallback for
hosted. Both paths share the `partition_executor` body — only the dispatch
boundary differs — which limits the duplication cost.

**Spike reference:** `docs/specs/worker-execution-spike.md` (SFBL-120) documents
the full evaluation including Celery reconsideration and the SQLite-as-broker
analysis. Follow-up implementation Epic to be created; this entry is
provisional until that Epic sequences the rollout (Postgres migration → worker
mode with `in_process` default → image/CI → operator docs → flip aws-hosted
default).

---

## 017 — Run lifecycle: broad exception handler + try/finally backstop

**Decision:** `run_coordinator._execute_run_body` wraps the `execute_step` call in a three-way
exception chain:

1. `except InputStorageError` — existing, marks run failed with `storage_error` key.
2. `except asyncio.CancelledError` — marks run aborted via a fresh session
   (`_mark_run_aborted_fresh`), publishes `run.aborted`, then **re-raises** so task-group
   shutdown semantics are preserved.
3. `except Exception` — broad backstop for anything else (programming errors, unexpected
   SDK failures). Logs with `event_name=run.failed` + `outcome_code=unexpected_exception`,
   calls `capture_exception` for Sentry, and funnels through `_mark_run_failed_fresh` with an
   `unexpected_exception` key in `error_summary`.

As a final safety net, `_execute_run` wraps the body in `try/finally`. The `finally`
helper `_backstop_mark_failed_if_running` opens a **fresh** session, re-fetches the run,
and — if still `running` — marks it `failed` with an `unknown_exit` marker.

**Why — fresh sessions for exception paths:** The primary `db` session may be mid-transaction
when an exception fires. Attempting to reuse it can raise `InvalidRequestError` or silently
roll back the status update. Opening a fresh session via `db_factory` sidesteps that. The
fresh helpers are defensively wrapped in their own `try/except` (best-effort) because they
are the last line of defence — if even they fail, there is nothing else to do except log.

**Why — re-raise `CancelledError`:** asyncio relies on `CancelledError` propagation to
unwind task groups and close connections cleanly. Swallowing it would break structured
concurrency. The coordinator takes its persistence action (mark aborted) and then lets the
exception continue.

**Why — `unknown_exit` as a distinct marker:** Separating "something raised and we caught it
but don't know why" (`unexpected_exception`) from "no exception fired but the body returned
without finalising" (`unknown_exit`) lets operators triage. `unknown_exit` only appears if
there is a bug in the coordinator itself; `unexpected_exception` appears if a downstream
helper raised.

**Trade-off:** The main body of `_execute_run` is now a nested function
(`_execute_run_body`). That indirection is the cost of wrapping ~200 lines of code in
`try/finally` without breaking the existing early-return paths.
