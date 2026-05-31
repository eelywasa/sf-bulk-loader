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

**Spike reference:** `docs/specs/implemented/worker-execution-spike.md` (SFBL-120) documents
the full evaluation including Celery reconsideration and the SQLite-as-broker
analysis. Follow-up implementation Epic to be created; this entry is
provisional until that Epic sequences the rollout (Postgres migration → worker
mode with `in_process` default → image/CI → operator docs → flip aws-hosted
default).

---

## 019 — Email SMTP credentials: env > file > error, no auto-generation

**Context:** `ENCRYPTION_KEY` and `JWT_SECRET_KEY` follow an env → file →
auto-generate resolution chain. When neither the env var nor the key file is
present, the application generates a random secret, persists it to the key
file, and continues booting. This is appropriate because those secrets are
owned entirely by the application.

SMTP passwords are fundamentally different: they are credentials issued by an
external provider (AWS SES SMTP, Gmail app passwords, Mailgun, Postfix, etc.).
An auto-generated password is meaningless to the provider and would cause every
send attempt to fail with an authentication error.

**Decision:** `EMAIL_SMTP_PASSWORD` resolves as:

1. `EMAIL_SMTP_PASSWORD` env var — used as-is if non-empty.
2. `EMAIL_SMTP_PASSWORD_FILE` — file contents (stripped) if the file exists.
3. Neither present with `EMAIL_BACKEND=smtp` → hard `ValueError` at boot; app
   does not start.
4. Neither present with any other backend (`noop`, `ses`) → silently accepted;
   SMTP password is irrelevant.

SMTP passwords are **never auto-generated**. Boot-time failure is the correct
behaviour: it surfaces the misconfiguration immediately rather than allowing the
application to start and then fail on the first send attempt.

**Consequences:**

- `desktop` and `self_hosted` profiles default to `EMAIL_BACKEND=noop`, so
  operators who have not configured email do not see a boot error.
- `EMAIL_BACKEND=smtp` is an explicit opt-in that requires a credential.
  Forgetting the credential is caught at startup, not at first send.
- Rotating the password is a `.env` change + container restart — no migration
  or DB change required.

**References:** SFBL-137, `docs/specs/implemented/email-service-spec.md` §Configuration,
`docs/email.md` §"SMTP credential resolution".

---

## 020 — SES backend: use v2 SendEmail with configuration sets, not v1

**Context:** AWS SES has two distinct API generations:

- **v1** — `ses` service client: `SendEmail`, `GetSendQuota`, etc. Still
  functional but maintenance-mode.  Corresponds to `boto3.client("ses")`.
- **v2** — `ses-v2` (Amazon SES API v2) service client: `SendEmail` (different
  request shape with `Content.Simple/Raw/Template`), `GetAccount`, native
  configuration-set tagging.  Corresponds to `boto3.client("sesv2")`.

**Decision:** Use `ses-v2` `SendEmail` exclusively.  When
`EMAIL_SES_CONFIGURATION_SET` is set, pass it as `ConfigurationSetName` on
every send.  Omit the key entirely when the setting is `None` or empty — SES
treats a missing key differently from an empty string.

For the `healthcheck()` probe, attempt `sesv2.get_account()` first.  If the
botocore model for this `aioboto3` version does not expose that method, fall
back to `ses.get_send_quota()`.  Either call proves connectivity and credential
validity.  The result is cached for 60 seconds to avoid unnecessary API calls
from the `/dependencies` probe on every request.

**Rationale:**

1. **v2 is the current API.** AWS documents v1 as maintenance-mode; new
   features (templates, virtual deliverability manager, etc.) land only in v2.
2. **Configuration sets are a prerequisite for bounce/complaint webhooks.**
   SES configuration sets carry SNS event destinations that deliver bounce and
   complaint notifications.  The deferred inbound-webhook ticket (SFBL-142+)
   needs this infrastructure already in place; tagging every send with a
   configuration set from day one means no back-fill.
3. **Richer Content shape.** The v2 `Content` envelope (`Simple` / `Raw` /
   `Template`) gives a clear extension point for raw multipart sends and
   server-side template rendering.  The v1 `Message` shape has no equivalent.
4. **Credentials via boto3 default chain.** In `aws_hosted` deployments the
   IAM instance / ECS task role is picked up automatically.  In `self_hosted`
   and dev, env vars or `~/.aws/credentials` work.  No SES keys are accepted
   via `config.py` to avoid credential sprawl.

**Consequences:**

- Requires the `ses-v2` client to be present in the `aioboto3`/botocore
  model bundle (available since `aioboto3>=12.0`, which is now pinned in
  `requirements.txt`).
- The IAM role / user must have `ses:SendEmail` permission on the `ses-v2`
  service (ARN prefix differs from v1).
- `GetAccount` permission (`ses:GetAccount`) is required for the v2 healthcheck
  path; the v1 fallback needs `ses:GetSendQuota`.
- `ConfigurationSetName` is optional — deployments without a configuration set
  simply omit the field and SES uses the account default (or none).
  Using a configuration set is recommended but not enforced.

**References:** SFBL-140, `docs/specs/implemented/email-service-spec.md` §"Retry
classification" and §"Backend Protocol", `backend/app/services/email/backends/ses.py`.

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

---

## 019 — AWS-hosted CDK stack split: ECR in DataStack (SFBL-276)

The first iteration of `aws_hosted` (Ticket 9) put both the ECR repository
and the ECS service in `BackendStack`. A fresh `cdk deploy --all` would
then start a Fargate task whose ECR image tag did not yet exist —
deterministic first-deploy failure.

ECR is now in `DataStack`, alongside RDS / S3 / Secrets Manager. Deploy
order is **Network → Data → push image → Backend → Frontend**, with the
operator pushing the initial image between the Data and Backend deploys.
`BackendStack` consumes the ECR repo by reference (`Repository.fromRepositoryAttributes`).

**Trade-off:** the logical resource path changed from
`BulkLoader-{env}-Backend/BackendRepository` to
`BulkLoader-{env}-Data/BackendRepository`. For an existing environment
this would be a delete-and-recreate. The original CDK was scaffolding only
(no real environments existed), so the change is benign — but the SFBL-280
`self_hosted → aws_hosted` migration guide must call this out.

---

## 020 — SSM parameters injected as ECS Secrets, not Environment (SFBL-276)

Originally `CORS_ORIGINS`, `LOG_LEVEL`, and `ADMIN_USERNAME` were passed
to the ECS task via `parameter.stringValue` under the `environment:` block.
CDK resolves `.stringValue` at synth time, so the literal value gets baked
into the CloudFormation template — editing the SSM parameter has no effect
without a `cdk deploy`.

All SSM-sourced env vars now go via `ecs.Secret.fromSsmParameter(...)`
under the `secrets:` block. ECS resolves the live parameter value when
the task starts, so a parameter edit + service rolling restart picks up
the new value without touching CDK. Same pattern as the existing Secrets
Manager values.

**Why this matters:** operational levers (CORS allowed origins, log
verbosity, SES sender, etc.) need to be tunable without a CDK redeploy.
The previous approach silently broke that expectation.

---

## 021 — Health checks: ALB readiness vs container liveness (SFBL-276)

Both checks originally hit `/api/health` (`utility.py:445`), which returns
HTTP 200 even when the database is unreachable. Effect: a degraded task
stayed in service rotation and continued failing real requests; ECS never
restarted the container because nothing observed liveness as broken.

Now:
- **Container Docker health check** → `/api/health/live` (`utility.py:322`).
  Fast process-only probe; returns 200 whenever uvicorn is alive. Used by
  ECS to decide whether to restart the container.
- **ALB target group health check** → `/api/health/ready` (`utility.py:333`).
  Dependency-aware probe; returns 503 when the DB ping fails. The ALB
  pulls a degraded task out of rotation; ECS keeps it alive (liveness
  still passes) so it can rejoin once the DB recovers.

Standard liveness-vs-readiness split. The legacy `/api/health` endpoint
is retained for backward compatibility with existing self-hosted Docker
healthchecks.

---

## 022 — IAM split: S3 connections via BYO keys, SES via task role (SFBL-276/279)

Two architectural choices that share a theme: which boto3 calls run as
the ECS task role, and which run with explicit credentials.

**S3 input/output buckets — BYO IAM access keys.** `S3OutputStorage` and
`InputConnection.test` construct boto3 clients with explicit
`access_key_id` / `secret_access_key` decrypted from the encrypted
`input_connection` row. The CDK does **not** grant S3 perms on the input/
output buckets to the task role — those grants would be unused, and
keeping them would mislead readers. Operators create an IAM user, generate
access keys, and paste them into the InputConnection form via the UI.
Documented in `docs/deployment/aws.md` "S3 input/output connections".

The code-path alternative (`InputConnection.use_task_role` mode that lets
boto3 resolve from the default credential chain) is filed under SFBL-295
in the production-scale epic.

**SES — task role with two scoped policies.** The SES backend uses the
boto3 default chain — i.e. the ECS task role. CDK adds two policies:
- `SesSendScopedToIdentity`: `ses:SendEmail` + `ses:SendRawEmail`,
  restricted to the SES identity ARN. Least-privilege send.
- `SesAccountReadForHealthProbe`: `ses:GetAccount` + `ses:GetSendQuota`,
  `Resource: "*"`. These are account-wide reads that don't accept a
  resource ARN, used by the `/api/health/dependencies` SES probe.

The split makes the security review easy: send actions can only emit
email as the deployment's own identity; read actions only inform the
health probe.

---

## 023 — RDS hardening: server-enforced TLS + explicit storage encryption (SFBL-279)

Two postures left to defaults in the original CDK:

- **TLS at the server**: the application connects with `?ssl=require` but
  the DB would have accepted plaintext if a misconfigured client tried.
  Now: a custom Postgres parameter group with `rds.force_ssl=1` rejects
  any non-TLS connection at the server. The CFN output
  `RdsParameterGroupName` lets operators verify the DB is using the
  hardened group post-deploy.
- **Storage encryption**: `storageEncrypted: true` set explicitly. CDK
  defaults to encryption-on for most engines but making it explicit
  prevents a future CDK change from silently regressing it.

**Trade-off:** custom parameter group means a future Postgres major version
upgrade requires creating a new parameter group for the new engine
version. Acceptable for the 2–3 year cadence Postgres major versions ship.

---

## 024 — SES domain identity without auto-DKIM (SFBL-279)

The CDK provisions an `ses.EmailIdentity` for the configured domain and a
`mail.<domain>` MAIL FROM subdomain. CDK does **not** auto-write the DKIM
or MAIL FROM records to Route53 — instead it surfaces the three DKIM
tokens as CloudFormation outputs (`SesDkimRecord1/2/3`) for the operator
to add manually.

**Why not auto-DKIM:** `ses.Identity.publicHostedZone(zone)` requires a
`HostedZone.fromLookup`, which runs against the deploying account at
synth time and fails in CI / placeholder accounts. Manual DKIM is two
minutes of console clicks for a Route53-managed zone and is the only
way the synth step works without account credentials.

An automated path that writes the records via a separate stack (or a
context flag that gates the lookup) is a follow-up under SFBL-295.

---

## 025 — Bronze / Silver / Gold tier presets (SFBL-279)

Sizing values (RDS instance class, multi-AZ, allocated storage, backup
retention, ECS task shape, replica count, log retention,
ContainerInsights, S3 lifecycle, optional Gold-only worker / Redis / WAF
flags) are now driven by named tier presets defined under `cdk.json`
`context.tiers`. Each environment selects one via the `tier` field.

Default mapping: `staging → bronze`, `production → silver`. The Gold
tier is fully defined in the preset but its production-scale resources
(arq/Redis worker tier, WAF, autoscaling, Fargate Spot) are not yet
provisioned by SFBL-275 — SFBL-295 reads the same flags and adds them.

**Why not per-env hardcoded values:** the previous design conflated
"which environment" with "how big". Bronze production and Gold staging
are both legitimate configurations, and the tier abstraction lets us
articulate that. Cost matrix and per-tier trade-offs live in
`docs/deployment/aws.md` "Sizing and cost".


---

## 026 — Migration on deploy: one-shot Fargate task + advisory lock (SFBL-277)

The Dockerfile CMD originally ran `alembic upgrade head && uvicorn ...`,
which is the right default for self-hosted Docker compose (single
container, no concurrency) but unsafe for `aws_hosted` ECS rolling
deploys: two service tasks can start concurrently and race on the
schema. Even when Postgres' implicit lock on `alembic_version`
serialises the two upgrades, the mixed-version window where new code
runs against partially-migrated schema is hard to reason about.

**Choice — one-shot migration TaskDefinition + RUN_MIGRATIONS gate**:

- Service `TaskDefinition` runs with `RUN_MIGRATIONS=false`. The
  Dockerfile CMD honours the gate and skips `alembic upgrade head` —
  service tasks start uvicorn directly.
- A second `MigrationTaskDefinition` (same image, same secrets, same
  task role) runs with `RUN_MIGRATIONS=true` and a `command: ['sh',
  '-c', 'alembic upgrade head']` override. CI invokes it via
  `aws ecs run-task` between `docker push` and the service
  `update-service`.
- A Postgres advisory lock (`pg_advisory_lock(<key>)` in
  `alembic/env.py`) wraps the upgrade. Belt-and-braces: serialises
  any caller — including manual `alembic` runs from a bastion — who
  bypasses the runbook flow.

**Options considered and rejected:**

| Option | Why rejected |
|---|---|
| CDK Custom Resource → Lambda | Lambda needs VPC + RDS access; 15-min timeout; complex DB connectivity setup |
| Init container with Postgres advisory lock | Fargate's init-container support is awkward; harder to reason about |
| `minHealthyPercent: 100`, deploy one task at a time | Doesn't fully eliminate the race; still has the mixed-version window |

**Trade-off:** the deploy now requires three steps in sequence (build →
migrate → roll). CI scripts the orchestration; the operator who has to
do it by hand also follows the same sequence per the runbook. The
advisory lock means even doing the steps out of order is recoverable.

## 027 — First-deploy MigrationTaskDef chicken-and-egg: workaround now, relocate later (SFBL-278 / SFBL-298)

> **Superseded by SFBL-298 (2026-05-31).** The deferred "cleaner
> architectural fix" below has shipped: `MigrationTaskDefinition` now lives
> in `DataStack` (with its own bare Fargate `MigrationCluster`, log group,
> and task role), exposed via the `MigrationTaskDefinitionArn`,
> `MigrationClusterName`, and `BackendServiceSecurityGroupId` outputs. The
> first-deploy runbook (`docs/deployment/aws.md` step 8) no longer does the
> background-deploy + poll + `force-new-deployment` dance — it runs the
> migration task on the Data-stack cluster, then deploys BackendStack against
> a populated schema. The historical workaround is retained below for context.

Decision 026 split migrations out into a `MigrationTaskDefinition`
created by `BackendStack`, alongside the service `TaskDefinition`. That
works on every deploy **after** the first one, but the first deploy
against a clean account hits a chicken-and-egg: `BackendStack` creates
the migration task and the service task in the same stack, and the
service task starts immediately against an empty schema. `lifespan()` in
`app/main.py` queries `profile_permissions` and calls `seed_admin` on
boot — which crashes against a DB with no tables. Service tasks
crashloop, the service never reaches steady state, and `BackendStack`
hangs in `CREATE_IN_PROGRESS` until rollback.

**Choice now — operator runbook workaround:** the first-deploy runbook
(`docs/deployment/aws.md` step 8) starts `cdk deploy BulkLoader-${ENV}-Backend`
in the background, polls until the `MigrationTaskDefinitionArn` output
appears, runs the migration task manually with `aws ecs run-task`, then
issues `update-service --force-new-deployment` so service tasks come up
against a populated schema. CDK then reaches `CREATE_COMPLETE`.

This is intentionally a runbook step rather than a code change in PR 2:
the workaround is mechanical, the validation against
`bulkloader.forcetide.net` proved it works, and shipping it lets PR 2
focus on the actual aws-validation surface.

**Options considered and rejected for the immediate fix:**

| Option | Why rejected for now |
|---|---|
| `dependsOn` between service and migration task in the same stack | ECS service `dependsOn` only works for sibling containers in a single task definition, not across task definitions in the same service |
| CloudFormation custom resource to run the migration during stack creation | Lambda needs VPC + RDS access; 15-minute timeout; reintroduces the connectivity complexity 026 explicitly rejected |
| Make `lifespan()` tolerant of an empty schema | Defers crashes from boot to first request, makes the failure mode harder to diagnose, and only papers over the underlying ordering problem |

**Cleaner architectural fix — deferred to [SFBL-298](https://matthew-jenkin.atlassian.net/browse/SFBL-298):**
relocate `MigrationTaskDefinition` from `BackendStack` to `DataStack`.
The migration task definition then exists as soon as the data layer
(RDS + secrets + ECR repo) is up, before the service stack is even
synthesised. The CI / runbook flow becomes:

1. `cdk deploy Network Data` → DataStack now publishes
   `MigrationTaskDefinitionArn` as a stack output.
2. Push image to ECR.
3. `aws ecs run-task` against the migration task — schema is at head.
4. `cdk deploy Backend` — service tasks come up against a populated
   schema, no race, no manual `force-new-deployment`.

The migration task only depends on the image + secrets + RDS, all of
which DataStack already owns; moving it is a stack-membership change
without touching the underlying construct properties. PR-2's runbook
explicitly references SFBL-298 so the workaround has a clear sunset.

**Trade-off accepted:** until SFBL-298 ships, every operator who runs a
truly clean first-deploy hits this manual step. The runbook is explicit
and the workaround is reliable; subsequent deploys use the standard
three-step flow from decision 026 unchanged.

## 028 — persistOnDestroy: snapshot the DB and retain secrets/buckets across teardown (SFBL-297)

Bronze `cdk destroy` wipes the RDS instance, regenerates secrets, and
deletes the input/output buckets — correct for disposable validation
environments. But an operator who wants to tear an environment down to
save money and bring it back later with the **same data** needs three
pieces of state to survive together: the RDS data, the `EncryptionKey`
secret (without which the Fernet-encrypted Salesforce-credential columns
are unrecoverable ciphertext), and the DB master password.

**Decision:** add a tier-level `persistOnDestroy` flag (bronze `false`;
silver/gold `true`). When set:

- **RDS** uses `RemovalPolicy.SNAPSHOT` — a final snapshot is taken on
  destroy and the instance is deleted. Rebuild with
  `cdk deploy -c restoreFromSnapshot=<id>`, which switches the construct to
  `rds.DatabaseInstanceFromSnapshot`.
- **The five app secrets** (`encryption-key`, `jwt-secret-key`,
  `database-url`, `admin-email`, `admin-password`) use
  `RemovalPolicy.RETAIN`. On restore they are **imported by name**
  (`Secret.fromSecretNameV2`) rather than created, because a retained
  secret name cannot be recreated by CloudFormation.
- **Input/output buckets** use `RemovalPolicy.RETAIN`.

**Deletion-protection precedence (deliberate posture change for silver/gold).**
A `SNAPSHOT` removal policy cannot run on destroy while RDS-level deletion
protection is on — `DeleteDBInstance` is blocked — so `persistOnDestroy`
forces `deletionProtection: false` (`rdsDeletionProtection && !persist`).
Silver/gold previously ran with `rdsDeletionProtection: true` →
`RemovalPolicy.RETAIN` (instance orphaned, never auto-deleted). They now
**snapshot-and-delete** on destroy instead. The durability guarantee moves
from "the instance is undeletable" to "a final snapshot + the retained
secrets make the environment fully restorable" — which is also far cheaper
to park (snapshot storage only, no running instance). An operator who wants
the old undeletable-instance posture for a true-production env can set that
tier back to `persistOnDestroy: false` + `rdsDeletionProtection: true`
(the two are mutually exclusive by design).

**Two caveats, documented in the runbook:**

1. **Snapshot rooting.** Once an instance is restored with a
   `DBSnapshotIdentifier`, that identifier must keep being supplied on every
   subsequent deploy. If it's dropped, CloudFormation creates a fresh empty
   instance and deletes the restored one (per the AWS RDS docs).
2. **DatabaseUrl endpoint.** `DATABASE_URL` embeds the RDS endpoint, which
   changes on restore, and the master password is reset to a new generated
   secret by `SnapshotCredentials.fromGeneratedSecret`. Even though the
   `database-url` secret is retained, the operator must rewrite it to the new
   endpoint + password after a restore.

**Options considered and rejected:**

| Option | Why rejected |
|---|---|
| Keep `RETAIN` (orphan the instance) for silver/gold | Leaves a full running instance accruing cost while "parked"; doesn't give a clean restore story |
| Cross-account / cross-region snapshot copy | DR scope, out of this ticket |
| S3 versioning / PITR on the buckets | Out of scope — buckets simply `RETAIN` |
| Custom resource to re-encrypt columns under a new key | Defeats the purpose; retaining the `EncryptionKey` secret is simpler and correct |

**Amendments (2026-05-31, during PR #97 review):**

1. **Decoupled persistence from the tier.** The original design put
   `persistOnDestroy` on the *tier* preset, which conflated two orthogonal
   concerns: a tier is about **sizing/cost**, while persistence is about an
   **environment's lifecycle**. A small (bronze) environment can still be a
   real, persistent one (e.g. a low-utilisation `staging` used occasionally by
   a few admins). `persistOnDestroy` is now resolved per-environment in
   `bin/app.ts` as `envConfig.persistOnDestroy ?? tier.persistOnDestroy ??
   false` — the tier value is just the default. Bronze's `rdsBackupRetentionDays`
   was also raised 1 → 7, since a persistent low-util env warrants a real
   backup window.

2. **Buckets now actually reattach on restore (Codex P2 on PR #97).** Retaining
   the input/output buckets with CDK-*generated* names meant a snapshot restore
   created *new* buckets and orphaned the retained objects — "retained" without
   recoverability. Fix: when an environment persists, the input/output/access-logs
   buckets get **deterministic names** (`bulk-loader-{env}-{input,output,access-logs}`)
   and the restore path **imports them by name** (`s3.Bucket.fromBucketName`),
   mirroring the secret-import path, so the same objects reattach. Disposable
   environments keep generated names for clean, collision-free redeploy cycles.

## 029 — CDK hardening quick-wins (SFBL-355)

An AWS IaC MCP (cfn-guard) scan + manual review of the SFBL-275 stacks
during SFBL-299 planning surfaced four small, non-breaking, CDK-only
refinements - too minor for the production-scale SFBL-295 effort, shipped
alongside the SFBL-299 mop-up children:

- **ECS deployment circuit breaker** (`backend-stack.ts`):
  `circuitBreaker: { rollback: true }` on the Fargate service. A failed
  image rollout (a task that crashloops on boot) now auto-rolls-back to the
  last good task set instead of hanging IN_PROGRESS until timeout.
- **S3 server access logging** (`data-stack.ts`): a dedicated access-logs
  bucket receives input/output object-access logs under `input/` and
  `output/` prefixes - an audit trail the data buckets lacked.
- **RDS gp3 + auto minor version upgrade** (`data-stack.ts`): `gp3` storage
  (cheaper than the CDK-default gp2, higher baseline IOPS) and
  `autoMinorVersionUpgrade: true` so the instance receives Postgres 16.x
  patches in the maintenance window.
- **VPC flow logs** (`network-stack.ts`, resolving the long-standing TODO):
  flow logs to CloudWatch, **gated on `tier.containerInsightsEnabled`** so
  disposable bronze envs don't pay the ingestion cost; silver/gold get them.

Production-grade items the same scan surfaced (RDS Multi-AZ, enhanced
monitoring, CloudWatch alarms, AWS WAF, ALB access logs) are owned by
**SFBL-295** and deliberately not in this change. cfn-guard false positives
(S3 object-lock / replication / public-RW-ACL, the SG egress-port-range
sentinel rule, the RDS master-user secure-parameter rules) were triaged out.

## 030 — Homebrew distribution via a dedicated third-party tap (SFBL-333)

macOS desktop distribution ships through a Homebrew **third-party tap**, not
the official `homebrew-cask`. This is a constraint, not a preference: the
official cask has notability/maturity bars (broad usage, stability) the app
does not yet clear, so self-publishing via a tap is the only viable path
today. Migrating to the official cask is a *future* option once the app is
mature/popular enough — the tap can then remain as a fallback.

A Homebrew tap **is** a separate Git repository by convention:
`brew tap eelywasa/sf-bulk-loader` resolves to a repo literally named
`eelywasa/homebrew-sf-bulk-loader`. There is no way to host a proper tap
inside this monorepo, so a dedicated public repo under the `eelywasa`
account is required.

Consequences:
- **Artifact format:** the macOS release artifact changes from `.zip` to
  `.dmg` (the format Cask expects). Changed in both `release.yml`
  (`--mac dmg`, `*.dmg` glob) and `electron-builder.config.js`
  (`target: "dmg"`). Signing + notarization were already wired and active,
  so no signing work was needed.
- **Cross-repo automation needs an owner-provisioned token:** the tap's
  auto-bump workflow must push `version`/`sha256` updates to the *separate*
  tap repo, which the default `GITHUB_TOKEN` cannot do. A least-privilege
  fine-grained PAT (Contents: read & write on the tap repo only), stored as
  `HOMEBREW_TAP_TOKEN` in this repo, is required — and only for the
  auto-update step, not the initial manual cask.
- **Scope split:** the app-repo changes (artifact format, docs, this entry)
  ship in the SFBL-333 app PR; the tap repo, its cask, and its bump workflow
  live in the separate repo and are created after the first `.dmg` release
  exists to populate the cask `sha256`.

