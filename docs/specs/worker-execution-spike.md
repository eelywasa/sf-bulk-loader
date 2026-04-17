# Spec: Worker Execution Architecture Spike

**Jira:** SFBL-120 (spike) — follow-up implementation Epic TBD

## Summary

Move `execute_run` off the FastAPI process onto a separate worker tier on hosted
profiles, using **arq** (async-native, Redis-backed) as the task queue. Desktop
profile keeps today's in-process `asyncio` executor unchanged. Worker deployment
ships as a **separate image** sharing the backend codebase. On hosted profiles,
an in-process executor remains available as an explicit fallback
(`EXECUTOR_MODE=in_process`) for small deployments or broker-outage degraded
operation.

This supersedes DECISIONS.md #008 ("No Celery; asyncio for background tasks")
for hosted profiles only — asyncio remains the desktop execution model. Celery
was re-evaluated as part of this spike and remains out of scope (reasoning
below).

---

## Problem

`execute_run` currently runs as an `asyncio.create_task` in the FastAPI process
(see `backend/app/services/run_coordinator.py:62`). Consequences:

- API restarts (deploys, crashes, container rescheduling) abort in-flight runs.
  The SFBL-112 backstop marks them failed on the next API boot but the work is
  lost mid-partition.
- All runs share the single process's CPU and event loop. Heavy concurrent runs
  can starve the web thread and degrade API latency.
- No horizontal scale path: additional capacity can only come from vertical
  scaling the single API process.

The primary driver (per spike Q&A) is **horizontal scale** — the aws-hosted
profile needs a path to run multiple partition workers independently of the web
tier.

---

## Decision drivers

Captured during the spike Q&A (2026-04-17):

| Driver | Answer | Consequence for design |
|---|---|---|
| Primary pain | Horizontal scale | Threadpool-in-process (Option B) is insufficient; a real queue is required |
| Per-profile execution | Desktop in-process; hosted can diverge | Execution backend is profile-aware; not one-model-for-all |
| Durability guarantee | Graceful restart only | No lease/heartbeat/crash-reclaim state machine needed; SIGTERM drain is enough |
| Broker infra on self-hosted | Redis-only, SQLite-as-broker dropped | Single backend keeps the queue abstraction tight |
| App DB for worker-enabled profiles | Postgres required | Self-hosted with workers must upgrade to Postgres; aws-hosted already requires Postgres |
| Redis durability posture | Document both; operator chooses | Operator-facing doc covers ephemeral vs AOF/RDB trade-offs |
| In-process fallback on hosted | Yes, as degraded mode | `EXECUTOR_MODE` env var; hosted can run in-process when broker is unavailable |
| Deliverable | Doc + recommendation only, no prototype | Implementation epic follows; this spike does not land code |

---

## Options considered

### Option A — Worker processes + Redis broker **(recommended for hosted)**

Dedicated worker processes consume partition jobs from a Redis-backed queue.
API enqueues; workers execute. Preserves the existing async `partition_executor`
code; only the dispatch boundary changes.

- **Deployment impact:** self-hosted adds a `redis` + `worker` service to the
  compose file; aws-hosted adds a worker task/service. Desktop unaffected.
- **Failure behaviour on restart:** API restart does not abort in-flight runs.
  Worker SIGTERM drains in-flight partitions (arq's `max_shutdown_delay`). Hard
  worker crash mid-partition → partition marked retryable on next worker boot
  (graceful-restart tier only; no crash-proof reclaim).
- **Code churn:** medium. Partition executor becomes an arq task function;
  run_coordinator enqueues instead of creating a task; observability and session
  management are already isolated well enough to port cleanly.
- **Observability implications:** correlation IDs and event context must be
  serialised across the enqueue boundary. arq supports task-level `ctx` which
  we extend with our ContextVars payload.
- **Added infra surface:** Redis. Minor for docker-compose users; pre-existing
  for aws-hosted (ElastiCache is a standard option).

### Option B — Threadpool inside FastAPI

Run each partition in a worker thread within the existing backend.

- **Deployment impact:** none.
- **Failure behaviour on restart:** unchanged — still aborts on API restart.
- **Code churn:** low.
- **Scale:** single-node only. **Fails the primary driver**, so rejected.

### Option C — Stay as-is + graceful-shutdown hardening

Document current behaviour; add SIGTERM handling so restarts mark running runs
as retryable rather than silently aborting.

- **Deployment impact:** none.
- **Failure behaviour on restart:** runs end cleanly on deploy; still lose work.
- **Scale:** single-node only. **Fails the primary driver**, so rejected as the
  target end-state, though a subset of this work (graceful SIGTERM marking)
  should ship as part of Option A anyway for the in-process fallback path.

### Option D — Celery (re-evaluated)

CLAUDE.md (and DECISIONS.md #008) rule Celery out. Re-examined on 2026-04-17
with the current constraints in mind:

- **Sync-first architecture.** Celery tasks are sync; async tasks require
  `asyncio.run()` wrapping per task. Our partition executor is deeply async
  (aiohttp to Salesforce, `AsyncSession` for DB writes, multi-coroutine polling).
  Wrapping adds a per-task event-loop startup cost and an awkward bridging
  layer.
- **Single-task-type workload.** Celery's value is in managing *many* task
  types with heterogeneous routing, retries, result backends, and scheduling.
  We have one task type: "execute partition". Most of Celery's ecosystem
  (Flower, Canvas, Beat, result backends, priorities) is unused weight.
- **No SQLite/lightweight story.** Celery's SQLAlchemy broker is deprecated and
  not production-advised. Celery effectively *requires* Redis or RabbitMQ, so
  it does not simplify the infra footprint vs. a narrower Redis-native library.
- **Operator familiarity.** The one genuine argument for Celery — "every Python
  operator knows it" — is weaker in our case because the operator-facing surface
  is Docker Compose services and env vars, not Celery CLI invocations.

**Verdict:** re-evaluated, still ruled out. arq (below) gives us the Redis
integration without the sync-first tax or the ecosystem surface we don't use.

---

## Library landscape

| Library | Async-native | SQLite backend | Redis backend | Scope match | Verdict |
|---|---|---|---|---|---|
| **arq** | Yes | No | Yes | Narrow, single-task — fits | **Selected** |
| Celery | No (sync-first) | No (deprecated) | Yes | Heavy; ecosystem mostly unused | Rejected (see Option D) |
| Dramatiq | No (sync) | No | Yes | Similar tax to Celery | Rejected |
| Taskiq | Yes | Community SQLite (immature) | Yes | Broker-agnostic design wasted once SQLite is dropped | Rejected |
| Huey | Partial | Yes | Yes | Older, sync-leaning async | Rejected |
| Procrastinate | Yes | No (Postgres only) | No | DB-wrong | Rejected |
| Roll-our-own `BaseJobQueue` | N/A | N/A | N/A | Most code, no external dep, no upside vs. arq given single backend | Rejected |

### Why arq

- **Async-native.** Task functions are `async def`; integrates with our existing
  `aiohttp`/`httpx`/`AsyncSession` code with no sync bridge.
- **Redis-only.** With SQLite-as-broker dropped, multi-backend abstraction is
  wasted optionality. One backend, one code path.
- **Small dependency surface.** ~4kLOC, focused; no plugin ecosystem to pin.
- **Graceful shutdown built-in.** `max_shutdown_delay` + `max_jobs` plus SIGTERM
  handling gives us the drain semantics we need without custom signal plumbing.
- **Observability hooks.** arq exposes `on_startup`, `on_shutdown`, `on_job_start`,
  `on_job_end`, `on_job_prerun`, `on_job_postrun`. These map cleanly onto our
  existing `app/observability/events.py` event surface — one place to inject
  correlation-ID restoration and canonical log emission.

---

## SQLite-as-broker — evaluated and dropped

The Q&A initially proposed mirroring the SQLite/Postgres abstraction from the
app DB. On further analysis this is rejected:

1. **Workload mismatch.** The DB abstraction works because SQLAlchemy flattens
   DDL/DML across dialects. Queue semantics do *not* flatten: Redis `BLPOP`
   (blocking pop with server-side scheduling) vs. a SQLite polling queue
   (writer-serialised, busy-timeout-tuned, no native blocking dequeue) are
   genuinely different concurrency models. Any abstraction leaks.
2. **Narrow target audience.** Deployments too small to run Redis can stay on
   the in-process fallback path (`EXECUTOR_MODE=in_process`), which already
   covers the "single-binary / single-container" use case. A SQLite queue would
   only serve a narrow middle ground: "I've opted into workers but won't run
   Redis" — a configuration we don't need to optimise for.
3. **Engineering cost.** Production-grade SQLite queues require careful
   transaction design, `PRAGMA busy_timeout` tuning, polling-interval trade-offs,
   and `UPDATE ... RETURNING` workarounds for pre-3.35 compatibility. That work
   is strictly additional to the Redis path we also need.
4. **Operator cost of Redis is low.** `redis:7-alpine` as a compose service is
   a one-line addition; it is not a meaningful barrier for self-hosted users
   who are already running Docker Compose.

---

## Recommended architecture

### Execution modes

Define an execution mode per deployment, resolved from config:

| Mode | When used | Execution |
|---|---|---|
| `in_process` | Desktop (default, enforced); hosted with no broker configured or as explicit fallback | `asyncio.create_task` in the API process, as today |
| `worker` | Hosted with `EXECUTOR_MODE=worker` and a reachable `REDIS_URL` | Enqueue to arq; worker process consumes |

The mode is selected at API startup. Desktop is forced to `in_process`
(matches existing `app_distribution` profile constraints in
`backend/app/config.py:86`). Hosted profiles default to `in_process` for backward
compatibility; opting into workers is explicit via env var.

### Deployment topology

- **Desktop:** unchanged. Single binary / single container. `in_process` only.
- **Self-hosted (worker mode):** compose adds `redis` + `worker` services
  alongside existing `api`/`web`. Worker uses the same codebase but a separate
  image (see below). **Requires Postgres** for the app DB (writer contention
  across processes on SQLite is not acceptable for this path).
- **Self-hosted (in-process mode):** unchanged from today. SQLite continues to
  work. Documented as the default path for small installations.
- **aws-hosted:** worker becomes an additional ECS service / task definition
  (topology TBD during implementation). Already requires Postgres per
  existing config validation, so no DB migration is triggered here.

### Separate worker image

Worker ships as a distinct image (e.g. `sfbl-worker:<version>`) alongside
`sfbl-backend`. Rationale:

- Smaller runtime — no FastAPI, uvicorn, or HTTP middleware needed in the
  worker.
- Clearer resource profiles — worker containers can be scaled / sized
  independently of API containers without carrying HTTP server dependencies.
- Shared source tree — both images are built from `backend/`, differing only in
  Dockerfile entrypoint (`uvicorn app.main:app` vs `arq app.workers.run_worker.WorkerSettings`).

### Shutdown semantics

- **Worker SIGTERM:** arq drains in-flight jobs up to `max_shutdown_delay`
  (default 60s; tunable). Enqueued-but-unstarted jobs remain in Redis and are
  picked up by the next available worker.
- **API SIGTERM:** in-process fallback path runs the existing SFBL-112 backstop
  (`run_coordinator._backstop_mark_failed_if_running`) to mark in-flight runs
  failed with `unknown_exit`. In worker mode the API carries no runs, so SIGTERM
  is trivial.
- **Redis restart (ephemeral):** enqueued jobs are lost; run is marked failed
  on next reconciliation. Operator chooses AOF/RDB to avoid this (see below).

### Redis durability posture

Two modes documented; operator chooses per deployment:

- **Ephemeral Redis (default, simpler):** no persistence. In-flight enqueued
  jobs lost on Redis restart. Acceptable if Redis restarts are rare and users
  accept re-running affected load runs. Good match for self-hosted where
  operator owns the host.
- **AOF/RDB persistence:** Redis configured with `appendonly yes` (AOF) or RDB
  snapshots. Graceful-restart semantics then extend to Redis itself. Required
  for aws-hosted production where Redis is managed (ElastiCache has this on by
  default).

This is a doc/config concern, not a code decision. The spike flags it as an
operator choice with clear guidance per profile.

---

## Observability

The enqueue boundary crosses a process boundary. Correlation must survive.

### Correlation propagation

Current pattern: `app/observability/correlation.py` (ContextVars for `run_id`,
`step_id`, `job_id`) is set at the start of `execute_run` and propagated into
every async scope via `contextvars.copy_context()` when spawning tasks.

In worker mode: at enqueue time, snapshot ContextVars into a serialisable dict
and store as task kwargs. Worker's `on_job_start` restores them into the worker
ContextVars before the task body runs. This preserves the single source of
truth — one correlation ID per run, threaded through API logs and worker logs
alike.

### Canonical events

Add to `app/observability/events.py`:

- `worker.job.enqueued` — emitted at enqueue site in the API.
- `worker.job.started` — emitted in arq `on_job_start`.
- `worker.job.finished` — emitted in arq `on_job_end` with outcome.
- `worker.shutdown.draining` — emitted when worker receives SIGTERM.

No change to existing `run.*`/`step.*`/`job.*` events; those continue to fire
from inside the partition executor regardless of execution mode.

### Metrics

Add to `app/observability/metrics.py`:

- `sfbl_worker_queue_depth` (gauge) — sampled periodically by the API.
- `sfbl_worker_job_duration_seconds` (histogram) — emitted per finished job.
- `sfbl_worker_job_wait_seconds` (histogram) — enqueue → start latency.
- `sfbl_worker_active_jobs` (gauge) — in-flight per worker.

### Spans

New span: `worker.job.execute` wrapping the partition task body. Child of the
existing `run.execute_step` span where possible (context propagated via the
correlation snapshot).

---

## Consequences

### Hard prerequisites for the implementation epic

1. **Postgres for self-hosted + workers.** A separate migration ticket must
   precede worker rollout on self-hosted. SQLite cannot safely back a
   multi-process writer workload at this concurrency. aws-hosted is unaffected
   (already requires Postgres).
2. **Docker Compose example for self-hosted.** A worked `docker-compose.yml`
   example with `api`, `worker`, `redis`, `postgres` services.
3. **Operator documentation:** `docs/deployment/self-hosted.md` gains a
   "Scaling with workers" section; Redis durability trade-offs documented
   explicitly.
4. **Graceful shutdown wiring.** Both API (in-process fallback) and worker
   SIGTERM paths must interact cleanly with the existing SFBL-112 backstop so
   `unknown_exit`/`retryable` marking happens consistently across modes.

### Code changes (scope preview for implementation epic)

- New `backend/app/workers/` package: `worker_settings.py`,
  `tasks.py` (`execute_partition_task` wrapping existing `partition_executor`
  code), `enqueue.py` (API-side enqueue helpers).
- `run_coordinator.execute_run`: branch on `settings.executor_mode` — in-process
  path unchanged, worker path enqueues one task per partition.
- `backend/app/config.py`: add `executor_mode: Literal["in_process", "worker"]`
  with profile-aware defaults and validation (desktop must be `in_process`).
- Correlation serialisation helpers in `app/observability/correlation.py`.
- New Dockerfile target `Dockerfile.worker` (or multi-stage with distinct
  entrypoints).
- New CI job to build and push worker image.
- New metrics/events per the Observability section.

### Non-goals (explicitly deferred)

- **Hard-crash-reclaim semantics.** Lease/heartbeat-based job reclaim after a
  worker SIGKILL is not in scope; graceful-restart only.
- **Multi-tenant worker pools.** One worker pool per deployment; no per-tenant
  isolation.
- **Priority queues.** All partition jobs are equal priority.
- **Scheduled/recurring tasks via arq.** SFBL-XXX scheduler epic uses
  APScheduler; arq is only for partition execution.
- **Bulk Query task type.** SFBL Bulk Query epic can piggyback on this
  infrastructure but the task type is its own concern.

---

## Open questions (for implementation epic)

1. **aws-hosted worker topology.** ECS service with autoscaling vs. ECS task
   launched per run vs. Fargate. Not decided here; implementation epic picks
   based on target cost profile.
2. **Retry semantics for enqueued-but-unstarted jobs.** When a worker picks up
   a stale job (e.g. one enqueued before a Redis restart with persistence), how
   do we detect it and should we re-enqueue vs. fail? Proposal: job kwargs
   include a `run_attempt_id`; worker rejects if DB state shows the run has
   moved on.
3. **Fallback auto-downgrade.** If `EXECUTOR_MODE=worker` but Redis is
   unreachable at API startup, do we refuse to start, or auto-downgrade to
   `in_process` with a warning? Proposal: refuse to start (explicit config =
   explicit expectation).
4. **Worker-image rollout ordering.** The implementation epic should sequence:
   (a) Postgres migration for self-hosted, (b) worker-mode code + tests with
   `in_process` as default (no behavioural change), (c) image/CI, (d)
   operator docs, (e) flip default to `worker` for aws-hosted.

---

## Exit criteria for SFBL-120

- [x] Doc produced (`docs/specs/worker-execution-spike.md`).
- [x] Celery re-evaluated, outcome documented (stays out of scope).
- [x] SQLite-as-broker evaluated, outcome documented (dropped).
- [x] Library selection justified (arq).
- [ ] DECISIONS.md entry added, superseding #008 for hosted profiles.
- [ ] Follow-up implementation Epic created (placeholder reference in DECISIONS
      entry until the Jira issue exists).

No prototype branch is being produced per spike scope; the implementation epic
will validate assumptions against real code.
