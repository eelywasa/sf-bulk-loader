# Spec: Observability Baseline for the Salesforce Bulk Loader

**Jira Epic: SFBL-12**

## Overview

The application already exposes several useful operational signals: backend logs, a public `/api/health` endpoint, Docker health checks, and typed WebSocket run/step/job lifecycle events for the UI. However, these signals are not yet organized into a coherent observability model. Logging is not standardized as structured telemetry, correlation identifiers are not propagated consistently, metrics and traces are absent, health semantics are coarse, and the existing WebSocket event stream is UI-oriented rather than explicitly defined as part of a broader canonical operational event model.

This spec defines a **backend/platform-focused observability baseline** for the current **self-hosted** deployment profile. It is intentionally designed to be **forward-compatible** with a future architecture in which a central orchestrator coordinates work executed by distributed, queue-driven executors.

This spec does **not** attempt to design that future execution architecture. Instead, it establishes the invariants that future architectures must preserve:

- transport-independent canonical event names
- stable correlation identifiers
- structured outcome taxonomy
- explicit progress semantics
- low-cardinality metrics conventions
- observability requirements in Definition of Done for future enhancements

The baseline priorities for this spec are:

1. **Logs first**
2. **Metrics second**
3. **Traces third**

The implementation should remain **vendor-neutral**. The application must expose observability data in widely portable formats such as structured JSON logs, Prometheus-compatible metrics, and OpenTelemetry-compatible spans.

---

## Phase 0 Decisions

The following decisions are fixed unless explicitly revised later.

| Topic | Decision |
|---|---|
| Primary scope | **Backend/platform focused** |
| Delivery target | **Baseline now with forward-compatibility rules** |
| Deployment profile in scope | **Self-hosted only** |
| Signal priority | **Logs first, metrics second, traces third** |
| Stack posture | **Vendor-neutral** |
| Correlation identifiers | **IDs only** — no usernames as first-class observability dimensions |
| Sensitive data policy | **No raw CSV row data, no secrets, no tokens in telemetry** |
| Health semantics | Split into **liveness**, **readiness**, and dependency-aware health views |
| Event model | Define a **canonical internal event taxonomy** |
| WebSocket relationship | WebSocket events are a **projection of canonical events**, not the source of truth |
| Log format | **Plain text in local development**, **JSON in deployed self-hosted environments** |
| Log sinks | Application writes to **stdout/stderr only** |
| Metrics surface | Expose a **Prometheus-style `/metrics` endpoint** |
| Business throughput metrics | **In scope**, with low-cardinality labels only |
| Tracing scope | Include **custom workflow spans** around run/step/partition execution |
| Error monitoring | **Optional**, config-driven integration point only |
| Outcome taxonomy | Define a **formal operational outcome/error taxonomy** |
| Progress semantics | Define a **formal operational progress model** |
| Delivery posture | Implement as a **dedicated observability pass** |
| Future work rule | Future enhancements must include required observability changes in **Definition of Done** |
| Future architecture assumption | **Orchestrator plus distributed executors**, likely queue-based |

---

## Current State

### Existing signals

The repo already includes several observability-adjacent building blocks:

- backend logging via Python `logging`
- run, step, and job lifecycle logs in orchestration and Salesforce integration code
- a public `/api/health` endpoint that checks database connectivity and returns basic runtime info
- Docker Compose health checks wired to `/api/health`
- WebSocket run-status streaming with typed run/step/job lifecycle event publishers
- a `log_level` setting in application configuration

The current architecture is a browser → nginx → FastAPI backend model with SQLite by default and PostgreSQL optionally supported. The backend owns API routing, orchestration, Salesforce integration, and WebSocket status updates.

### Gaps

The current implementation does **not** yet provide:

- centralized structured logging configuration
- request-scoped correlation IDs
- systematic propagation of run/step/job IDs into telemetry context
- Prometheus-style metrics
- tracing instrumentation
- formal outcome taxonomy
- formal progress/stuck-run semantics
- transport-independent canonical event naming
- split liveness/readiness/dependency health endpoints
- engineering guardrails that require observability updates when new workflow features are added

---

## Goals

This spec must deliver the following.

### 1. Coherent baseline observability

The application must emit logs, metrics, traces, and health signals through a consistent backend/platform model rather than through ad hoc per-feature instrumentation.

### 2. Workflow-aware observability

Observability must reflect the domain model of the product:

- load runs
- load steps
- job records / partitions
- Salesforce jobs
- file/storage interactions
- retries, thresholds, aborts, and terminal outcomes

### 3. Stable correlation across layers

Operators must be able to connect:

- inbound API request
- run creation or control action
- step execution
- partition/job submission
- Salesforce job activity
- terminal outcome

### 4. Forward compatibility with distributed execution

The observability model must survive future decomposition into orchestrator + queue + distributed executors without renaming core events or rethinking basic semantics.

### 5. Portable standards

The implementation must be usable with multiple toolchains and vendors without changing application semantics.

---

## Non-goals

The following are out of scope for this spec.

- designing the future queue or distributed execution architecture itself
- building a full in-app operator dashboard UX
- adding per-user or per-tenant analytics
- guaranteeing parity across `desktop` or `aws_hosted` deployment profiles
- selecting or mandating a specific observability vendor
- using observability telemetry for billing or product analytics

This spec is allowed to introduce additive API or WebSocket changes where required for cleaner observability contracts.

---

## Architecture

## Guiding principles

### Canonical first, transport second

The application must define a **canonical observability model** first. Logs, metrics, traces, health endpoints, WebSocket messages, and any future queue/event transports are all projections of that model.

### Domain over framework noise

Framework-level telemetry is useful but not sufficient. The system must emit observability signals at the domain boundaries that matter operationally:

- run lifecycle
- step lifecycle
- partition/job lifecycle
- outbound Salesforce interaction
- storage access
- retries and backoff
- threshold-driven aborts
- unexpected exceptions

### IDs in context, not high-cardinality metrics

Stable entity IDs must be present in logs and traces, but **must not** become metrics labels. Metrics must remain low-cardinality and aggregatable.

### Stdout/stderr at app level

The application layer must emit logs to stdout/stderr only. Log storage, rotation, and aggregation are infrastructure concerns outside this spec.

### Self-hosted first

This spec optimizes for the current self-hosted model, where the app is containerized and reverse-proxied, and where operational owners may choose different downstream observability stacks.

---

## Canonical Observability Model

## Canonical entities

The following identifiers are first-class observability dimensions:

- `request_id`
- `run_id`
- `step_id`
- `job_record_id`
- `sf_job_id`
- `load_plan_id`
- `input_connection_id`

These identifiers must be usable across logs, traces, and canonical events. They are not all required in every signal, but when the application is operating in a scope where one is known, it should be attached.

### Identity exclusion rule

Human-readable identity such as `username` may appear in audit-oriented domain data where required by business logic, but it is **not** a first-class observability correlation dimension for this spec.

---

## Canonical event taxonomy

The system must standardize on canonical, transport-independent event names. Dot-separated naming should be used.

### Run events

- `run.created`
- `run.started`
- `run.completed`
- `run.failed`
- `run.aborted`
- `run.progress.updated`

### Step events

- `step.started`
- `step.completed`
- `step.failed`
- `step.threshold_exceeded`

### Job / partition events

- `job.created`
- `job.status_changed`
- `job.completed`
- `job.failed`
- `job.aborted`

### Salesforce integration events

- `salesforce.auth.requested`
- `salesforce.auth.failed`
- `salesforce.bulk_job.created`
- `salesforce.bulk_job.uploaded`
- `salesforce.bulk_job.closed`
- `salesforce.bulk_job.polled`
- `salesforce.bulk_job.completed`
- `salesforce.bulk_job.failed`
- `salesforce.request.retried`
- `salesforce.rate_limited`

### Storage events

- `storage.input.listed`
- `storage.input.previewed`
- `storage.input.failed`
- `storage.output.persisted`

### System events

- `health.checked`
- `websocket.connected`
- `websocket.disconnected`
- `websocket.error`
- `exception.unhandled`

The application does not need to emit every event externally in V1, but the code structure and naming should align with these canonical names.

---

## Outcome taxonomy

The system must standardize on outcome codes suitable for logs, events, and traces.

### Baseline outcome codes

- `ok`
- `degraded`
- `failed`
- `aborted`
- `unexpected_exception`

### Workflow / dependency codes

- `auth_error`
- `storage_error`
- `database_error`
- `salesforce_api_error`
- `rate_limited`
- `network_error`
- `timeout`
- `validation_error`
- `step_threshold_exceeded`
- `dependency_unavailable`
- `configuration_error`

Outcome codes must be:

- stable
- machine-readable
- transport-independent
- documented in one shared module or constants file

Free-form exception text may still be logged, but dashboards and alerting should rely on codes rather than string matching.

---

## Progress semantics

Because runs and steps are long-lived and may later execute across queues and distributed workers, “progress” must be defined explicitly.

### Run-level progress

A run may expose the following progress dimensions where known:

- `total_records_expected`
- `records_processed`
- `records_succeeded`
- `records_failed`
- `steps_total`
- `steps_completed`
- `jobs_total`
- `jobs_terminal`
- `active_jobs`

### Step-level progress

A step may expose:

- `records_processed`
- `records_succeeded`
- `records_failed`
- `jobs_total`
- `jobs_terminal`
- `active_jobs`

### Progress update rules

- Progress must be monotonic where applicable.
- Preflight estimates such as record counts must be distinguishable from actual execution counts.
- Terminal outcomes must include final totals where available.
- Lack of recent progress must be detectable for operational alerting.

### Stuck-run semantics

The spec must support future stuck-run detection, even if alerting rules are implemented later. The baseline design therefore requires enough telemetry to determine:

- last observed progress timestamp
- last state transition timestamp
- whether the run still has active jobs
- whether the run is awaiting external dependency completion

---

## Logging

## Logging design

A centralized logging setup module must be introduced and initialized from the application startup path.

### Local development

- human-readable plain text logs
- log level configurable via existing config
- optimized for developer readability

### Deployed self-hosted environments

- structured JSON logs
- one JSON object per line
- suitable for stdout/stderr collection

### Required common log fields

At minimum, deployed structured logs must include:

- `timestamp`
- `level`
- `message`
- `service`
- `env`
- `event_name` when applicable
- `outcome_code` when applicable

### Contextual fields when known

- `request_id`
- `run_id`
- `step_id`
- `job_record_id`
- `sf_job_id`
- `load_plan_id`
- `input_connection_id`
- `route`
- `method`
- `duration_ms`

### Logging rules

- Logs must be event-oriented and machine-joinable.
- Logs must not include raw CSV row content.
- Logs must not include secrets, tokens, or authorization headers.
- Exception logs must carry outcome codes and correlation IDs whenever possible.
- Retries and backoff must log attempt number, retry reason, and wait duration.
- Terminal workflow logs must include summary counts where available.

The current orchestration and Salesforce layers already log meaningful run/job transitions and retry behavior; those call sites should be migrated to the new standardized structure rather than replaced with generic middleware-only logging.

---

## Metrics

## Metrics surface

The application must expose a Prometheus-compatible `/metrics` endpoint.

This endpoint is intended for infrastructure scraping and should remain separate from user-facing application APIs.

## Metrics conventions

- low-cardinality labels only
- no entity IDs as labels
- counters for totals
- histograms for duration
- gauges for point-in-time concurrency or health state

### Baseline HTTP metrics

- request count
- request duration
- response status class/count

Suggested labels:
- `route`
- `method`
- `status_class`

### Baseline workflow metrics

- runs started total
- runs completed total
- runs failed total
- runs aborted total
- step completed total
- records processed total
- records succeeded total
- records failed total
- run duration histogram
- step duration histogram

Suggested labels:
- `object_name`
- `operation`
- `final_status`

### Baseline integration metrics

- Salesforce request count
- Salesforce request duration
- Salesforce retry total
- Salesforce rate-limit total
- Salesforce terminal job outcomes

Suggested labels:
- `salesforce_operation`
- `http_status_class`
- `outcome_code`

### Baseline runtime metrics

- WebSocket active connections
- active runs
- active jobs
- health/readiness state gauges

### Business throughput metrics

Business throughput metrics are in scope, but only with labels that remain low-cardinality and stable across many runs.

Allowed examples:
- `object_name`
- `operation`

Disallowed examples:
- `run_id`
- `sf_job_id`
- file path
- exception message

---

## Tracing

## Tracing posture

Tracing is the third priority but is still in scope for the baseline.

The implementation must remain OpenTelemetry-compatible and vendor-neutral.

### Required framework spans

- inbound ASGI/FastAPI request spans
- outbound `httpx` spans
- database spans where supported

### Required custom workflow spans

The system must add custom spans around:

- run execution
- step execution
- partition/job execution
- result persistence
- significant Salesforce polling loops where useful

These custom spans matter because the workflow shape is more important than generic web request timing in this application. The current run/step/partition orchestration structure provides natural boundaries for these spans.

### Trace attributes

When known, spans should include attributes such as:

- `run.id`
- `step.id`
- `job_record.id`
- `salesforce.job.id`
- `load_plan.id`
- `object.name`
- `operation`
- `outcome.code`

### Trace rules

- Trace attributes must respect the same data sensitivity rules as logs.
- IDs may appear as attributes.
- Raw row data, secrets, and tokens must never appear as attributes.
- Repeated polling spans must be designed carefully to avoid trace explosion.

---

## Health model

The current `/api/health` endpoint checks database connectivity and exposes basic runtime configuration. That is a useful starting point, but the semantics are currently too coarse for a mature operational model.

## Required endpoint split

### `/api/health/live`

Purpose:
- answers whether the process is alive enough to serve

Characteristics:
- fast
- no dependency checks
- should fail only when the process is not functioning

### `/api/health/ready`

Purpose:
- answers whether the service is ready to receive traffic

Characteristics:
- includes database readiness
- may include critical startup prerequisites
- should be used by orchestration/load-balancing infrastructure

### `/api/health/dependencies`

Purpose:
- provides a dependency-oriented view for operators

Characteristics:
- includes database and other key dependencies where meaningful
- should clearly distinguish `ok`, `degraded`, and `failed`

### Backward compatibility

The existing `/api/health` endpoint may be preserved temporarily as a compatibility surface, but the long-term model should align callers to the split endpoints above. The Docker Compose health check may continue to use a readiness-aligned endpoint after migration.

---

## WebSocket relationship to canonical events

The repo already centralizes typed WebSocket event publishing in a dedicated run event publisher module. That is good structure and should be preserved.

However, WebSocket messages must be treated as a **projection** of canonical events, not as the canonical model itself.

### Required rule

For any workflow event that is relevant to both operational observability and UI status updates:

- canonical event naming and semantics are defined once
- WebSocket payloads map from that canonical event model
- logging, tracing, and future queue/event transports should use the same underlying vocabulary

### Consequence for future architecture

When execution later moves into distributed executors, the UI-facing WebSocket layer may remain in the API/orchestrator process, but it must consume canonical events rather than invent a separate domain language.

---

## Context propagation and correlation

## Request context

Each inbound HTTP request must receive a `request_id`.

- If a trusted upstream request ID exists, it may be adopted.
- Otherwise, the application should generate one.

This `request_id` must be bound into log context and attached to request spans.

## Workflow context

When a request triggers or interacts with a run, the application must propagate relevant workflow IDs into log and trace context.

### Binding rules

- Run-scope operations bind `run_id`
- Step-scope operations bind `step_id`
- Partition/job-scope operations bind `job_record_id`
- Salesforce job interactions bind `sf_job_id` when known

### Async boundary rule

The design must not assume that work always stays inside the original request or process. Observability context propagation must therefore be explicit enough to survive future queue boundaries.

This does not require queue infrastructure in V1, but it **does** require that context is represented as stable IDs and canonical attributes rather than hidden thread-local assumptions only.

---

## Security and data handling

Observability must not compromise the application’s security posture.

### Prohibited telemetry content

The following must never be emitted into logs, metrics, traces, or optional error-monitoring integrations:

- raw CSV rows
- Salesforce access tokens
- JWTs or JWT secrets
- encryption keys
- passwords
- authorization headers
- secret environment variable values

### Exception handling rule

Exceptions may be logged with:

- exception type
- sanitized message
- stack trace where appropriate
- outcome code
- correlation IDs

But exception capture must not dump unsafe request or payload contents by default.

### Metrics rule

Metrics must never include:
- IDs
- secrets
- payload-derived free text

---

## Forward compatibility for distributed execution

This spec is intentionally written to survive a future architecture in which an orchestrator coordinates queue-backed distributed executors.

The following are the key forward-compatibility rules.

### 1. Canonical events are transport-independent

Canonical events must not be named or structured in a way that assumes:
- WebSocket delivery
- single-process execution
- synchronous request ownership of the whole workflow

### 2. Context must survive handoff

The correlation model must be serializable and transferable across process boundaries. Future queue messages and executor jobs must carry the IDs needed to maintain observability continuity.

### 3. Progress must not depend on in-process memory

Long-running progress and terminal state must be observable from durable workflow state and canonical events, not only from ephemeral in-memory status.

### 4. Metrics must remain aggregate-friendly

Distributed execution will increase event volume and cardinality risk. V1 must therefore avoid per-entity metric labels so later scaling does not force a redesign.

### 5. Executors must emit the same outcome codes

Future executor components must use the same outcome taxonomy and canonical event names as the baseline implementation.

---

## Definition of Done and engineering guardrails

This spec establishes an ongoing engineering rule for the repo.

### Observability Definition of Done addition

Any future enhancement that introduces or materially changes:

- run lifecycle behavior
- step execution behavior
- job/partition lifecycle
- Salesforce interaction flows
- storage flows
- retry behavior
- terminal outcomes
- background execution boundaries

must update observability as part of completion.

At minimum, future work must assess and update where relevant:

- logs
- metrics
- spans
- health/readiness impact
- canonical events
- outcome taxonomy
- progress semantics
- tests

### Review checklist

Every architecturally meaningful feature or refactor should answer:

- What new canonical events exist, if any?
- Which outcome codes can now be emitted?
- Which metrics should increment or time this behavior?
- Which IDs must be propagated?
- Does this affect readiness or dependency health?
- Does this require a DoD/test update for observability?

---

## Configuration additions

The current config already includes `log_level` and environment/profile settings. This spec requires additional observability-related configuration.

### Required additions

```python
log_format: Literal["plain", "json"] = "plain"
metrics_enabled: bool = True
tracing_enabled: bool = False
error_monitoring_enabled: bool = False
request_id_header_name: str = "X-Request-ID"
health_enable_dependency_checks: bool = True
```

### Optional additions

```python
service_name: str = "sf-bulk-loader-backend"
service_namespace: str | None = None
trace_sample_ratio: float = 0.0
error_monitoring_dsn: str | None = None
```

Configuration should allow safe local development defaults while enabling deployed JSON logging and optional tracing/error monitoring in self-hosted environments.

---

## Implementation Tickets

These are intended to be small enough for incremental execution while preserving coherent checkpoints.

### 1. Add Centralized Logging Configuration (SFBL-36)

Goal: establish a single observability-aware logging setup for local and deployed environments.

Scope:
- add a centralized logging configuration module
- initialize it from backend startup
- support plain text local logs and JSON deployed logs
- add shared common fields (`service`, `env`, etc.)
- standardize logger usage patterns across modules
- add tests for config behavior where practical

Dependencies:
- none

Exit criteria:
- app starts with consistent log formatting
- local dev logs remain readable
- deployed mode can emit structured JSON logs

### 2. Add Request ID Middleware and Context Binding (SFBL-40)

Goal: ensure inbound requests receive stable request correlation.

Scope:
- add request ID middleware
- generate or accept trusted upstream request IDs
- bind `request_id` into per-request log context
- attach `request_id` to request-scoped tracing context if tracing is enabled
- log request timing and status in structured form

Dependencies:
- Ticket 1

Exit criteria:
- every request has a request ID
- request logs contain consistent request-scoped context

### 3. Standardize Workflow Context Propagation (SFBL-43)

Goal: ensure run/step/job/Salesforce IDs are consistently available to logs and traces.

Scope:
- introduce shared helpers or context-binding utilities
- bind `run_id`, `step_id`, `job_record_id`, `sf_job_id`, `load_plan_id`, `input_connection_id` where known
- update orchestration and Salesforce layers to use the standardized context approach
- add tests around representative paths

Dependencies:
- Ticket 2

Exit criteria:
- workflow logs consistently include relevant IDs
- context propagation works across async workflow boundaries in the current monolith

### 4. Define Canonical Event and Outcome Taxonomy (SFBL-46)

Goal: create a single authoritative definition of canonical event names and outcome codes.

Scope:
- add shared constants/types for event names and outcome codes
- map current workflow/WebSocket concepts to canonical naming
- document event categories in code comments or a dedicated module docstring
- update key existing log sites to use canonical event names and outcome codes

Dependencies:
- Ticket 1

Exit criteria:
- canonical event names and outcome codes exist in one shared place
- major workflow paths use them consistently

### 5. Refactor WebSocket Publisher to Project Canonical Events (SFBL-48)

Goal: align the existing WebSocket run-event publisher with the canonical event model.

Scope:
- update run/step/job WebSocket publishing helpers to map from canonical events
- allow additive payload adjustments where needed for consistency
- preserve UI functionality while clarifying projection behavior
- add/update tests for WebSocket event payloads

Dependencies:
- Ticket 4

Exit criteria:
- WebSocket messages clearly reflect canonical events rather than defining independent semantics

### 6. Add Prometheus-Compatible Metrics Endpoint (SFBL-51)

Goal: expose a vendor-neutral metrics surface for self-hosted monitoring.

Scope:
- add `/metrics`
- instrument HTTP request counts and latency
- instrument workflow counters and duration histograms
- instrument WebSocket active connection count
- instrument Salesforce retries, rate limits, request counts, and latency
- instrument business throughput metrics with low-cardinality labels only

Dependencies:
- Ticket 4

Exit criteria:
- metrics endpoint is scrapeable
- core runtime, workflow, and integration metrics are exposed

### 7. Split Health Endpoints into Liveness, Readiness, and Dependencies (SFBL-53)

Goal: provide clearer operational health semantics.

Scope:
- add `/api/health/live`
- add `/api/health/ready`
- add `/api/health/dependencies`
- decide temporary compatibility strategy for existing `/api/health`
- update Docker health check documentation and/or compose config as needed
- add tests for endpoint semantics

Dependencies:
- none

Exit criteria:
- health semantics are clearly separated
- readiness reflects real deployability concerns better than the current single endpoint

### 8. Add Custom Workflow Tracing (SFBL-56)

Goal: provide lightweight, workflow-aware tracing over the monolith baseline.

Scope:
- add optional tracing instrumentation hooks
- instrument FastAPI/ASGI, outbound HTTP, and DB where supported
- add custom spans for run, step, partition/job, and result persistence boundaries
- attach canonical attributes and IDs to spans
- keep sampling/config optional and environment-driven

Dependencies:
- Ticket 3

Exit criteria:
- traces show workflow structure, not just framework plumbing
- tracing remains optional and vendor-neutral

### 9. Add Optional Error Monitoring Integration Point (SFBL-58)

Goal: support optional exception aggregation without making it a hard dependency.

Scope:
- add config-driven integration point
- ensure sanitization rules are respected
- capture unhandled exceptions with outcome codes and correlation context
- do not leak secrets or payload data
- document opt-in behavior

Dependencies:
- Ticket 1

Exit criteria:
- optional error monitoring can be enabled safely
- exception telemetry uses the same correlation and taxonomy model

### 10. Formalize Progress Semantics and Stuck-Run Instrumentation Hooks (SFBL-59)

Goal: make long-running workflow progress operationally observable.

Scope:
- define and emit progress-relevant fields where known
- ensure terminal run/step events include final totals
- capture last-progress/last-transition timestamps sufficiently for future stuck-run rules
- add/update tests on representative long-running paths

Dependencies:
- Ticket 4

Exit criteria:
- progress is machine-readable and consistent enough for future stuck-run alerting

### 11. Harden Sensitive Telemetry Handling (SFBL-60)

Goal: prevent accidental leakage of secrets or data through observability channels.

Scope:
- document prohibited telemetry content
- add sanitization helpers where necessary
- review representative error, request, and integration paths
- ensure logs/traces/error monitoring integrations do not emit unsafe content

Dependencies:
- Tickets 1, 8, and 9 as applicable

Exit criteria:
- telemetry hygiene rules are documented and enforced in key paths

### 12. Add Observability Guardrails to Engineering Workflow (SFBL-62)

Goal: ensure future changes extend the baseline instead of bypassing it.

Scope:
- add observability guidance to development docs and/or spec references
- update contribution or architecture guidance with DoD expectations
- add a lightweight checklist section for future feature work
- ensure future implementation specs can reference this baseline

Dependencies:
- Tickets 1 through 11

Exit criteria:
- repo guidance explicitly requires observability updates for future workflow changes

---

## Files Likely Affected

### Backend

| File | Change |
|---|---|
| `backend/app/main.py` | Initialize logging, middleware, metrics, health routing, optional tracing hooks |
| `backend/app/config.py` | Add observability configuration |
| `backend/app/api/utility.py` | Split health endpoints, possibly compatibility handling |
| `backend/app/services/run_coordinator.py` | Standardize workflow events, outcome codes, progress instrumentation, tracing |
| `backend/app/services/orchestrator.py` | Context propagation alignment |
| `backend/app/services/salesforce_bulk.py` | Structured retry/request logging, metrics, tracing |
| `backend/app/services/run_event_publisher.py` | WebSocket projection from canonical events |
| `backend/app/utils/` or new `backend/app/observability/` package | New logging, metrics, tracing, event taxonomy, outcome taxonomy, request ID/context modules |

### Deployment / docs

| File | Change |
|---|---|
| `docker-compose.yml` | Health endpoint alignment if updated |
| `docs/development.md` | Observability-related dev guidance |
| `docs/specs/...` | This spec, and future specs referencing it |

### Tests

| File | Change |
|---|---|
| backend tests across API and services | Add coverage for request IDs, health semantics, metrics, event naming, tracing hooks, sanitized logging |

---

## Acceptance criteria

This spec is satisfied when:

1. The backend emits consistent plain text logs in local development and structured JSON logs in deployed self-hosted environments.
2. Every inbound request has a request ID.
3. Workflow logs consistently carry the relevant correlation IDs when known.
4. Canonical event names and outcome codes are defined and used by major workflow paths.
5. WebSocket run/step/job updates are implemented as a projection of the canonical event model.
6. A Prometheus-compatible `/metrics` endpoint exists and exposes core runtime, workflow, and Salesforce integration metrics.
7. Health is split into liveness, readiness, and dependency-oriented views.
8. Optional tracing can expose workflow-aware spans for run/step/job execution.
9. Optional error monitoring can be enabled without leaking sensitive data.
10. Progress semantics are machine-readable enough to support future stuck-run detection.
11. Repo guidance requires future feature work to extend observability as part of Definition of Done.

---

## Open questions for later specs

These are intentionally deferred, not unresolved blockers.

- How future queue messages should carry observability context across orchestrator/executor boundaries
- Whether canonical events should later be durably persisted or streamed through an event backbone
- How operator-facing dashboards inside the app should consume canonical progress and outcome data
- Whether `aws_hosted` should later standardize a stronger reference observability deployment pattern
- Whether specific alert rules should be codified in a separate operational runbook spec
