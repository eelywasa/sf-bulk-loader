# Observability Developer Guide

This document is the practical developer reference for the observability baseline
established in **SFBL-12**. For the full design rationale and architecture decisions
see [`docs/specs/observability-baseline-spec.md`](specs/observability-baseline-spec.md).

---

## What the baseline provides

| Signal | Location | Always on? |
|---|---|---|
| Structured logging (plain / JSON) | `app/observability/logging_config.py` | Yes |
| Request ID middleware + access logs | `app/observability/middleware.py` | Yes |
| Workflow context propagation (ContextVars) | `app/observability/context.py` | Yes |
| Canonical event names + outcome codes | `app/observability/events.py` | Yes |
| Prometheus-compatible `/metrics` endpoint | `app/observability/metrics.py` + `metrics_middleware.py` | Yes |
| Split health endpoints (`/live`, `/ready`, `/dependencies`) | `app/api/utility.py` | Yes |
| OpenTelemetry workflow spans | `app/observability/tracing.py` | Optional (`TRACING_ENABLED=true`) |
| Sentry error monitoring | `app/observability/error_monitoring.py` | Optional (`ERROR_MONITORING_ENABLED=true`) |
| Telemetry sanitization helpers | `app/observability/sanitization.py` | Always (used by all above) |

---

## Observability module map

```
backend/app/observability/
├── logging_config.py      # configure_logging(settings) — plain or JSON, filters for context IDs
├── middleware.py           # RequestIDMiddleware — stamps every request with X-Request-ID
├── context.py             # ContextVars: request_id, run_id, step_id, job_record_id, sf_job_id,
│                          #              load_plan_id, input_connection_id
├── events.py              # RunEvent, StepEvent, JobEvent, SalesforceEvent, StorageEvent,
│                          # SystemEvent, OutcomeCode — canonical constants
├── metrics.py             # Prometheus counters/histograms/gauges + /metrics endpoint
├── metrics_middleware.py  # ASGI middleware that instruments every HTTP request
├── tracing.py             # configure_tracing, run_span, step_span, partition_span
├── error_monitoring.py    # configure_error_monitoring, capture_exception
└── sanitization.py        # SCRUBBED_KEYS, scrub_dict, scrub_headers,
                           # safe_exc_message, safe_record_exception
```

---

## Canonical event taxonomy (quick reference)

Import from `app.observability.events`. Always use these constants — never inline strings.

```python
from app.observability.events import RunEvent, StepEvent, JobEvent, SalesforceEvent, OutcomeCode

logger.info(
    "Run %s completed",
    run_id,
    extra={
        "event_name": RunEvent.COMPLETED,
        "outcome_code": OutcomeCode.OK,
    },
)
```

### Run events
`run.created` · `run.started` · `run.completed` · `run.failed` · `run.aborted` · `run.progress.updated`
`run.preflight.started` · `run.preflight.completed` · `run.preflight.failed`

Preflight events cover the pre-count phase that runs before the main step loop.
A `run.preflight.failed` log record is emitted with a matching `outcome_code`
(`storage_error` for `InputStorageError`, `unexpected_exception` otherwise) for
each step that cannot be counted. Preflight failures are **non-fatal** — the
run proceeds with an approximate `total_records`, and warnings are surfaced on
`LoadRun.error_summary.preflight_warnings` for the UI to render. The counter
`sfbl_run_preflight_failures_total{reason}` increments once per failing step.

### Step events
`step.started` · `step.completed` · `step.failed` · `step.threshold_exceeded`

### Job / partition events
`job.created` · `job.status_changed` · `job.completed` · `job.failed` · `job.aborted`

### Salesforce integration events
`salesforce.auth.requested` · `salesforce.auth.failed` · `salesforce.bulk_job.created`
`salesforce.bulk_job.uploaded` · `salesforce.bulk_job.closed` · `salesforce.bulk_job.polled`
`salesforce.bulk_job.completed` · `salesforce.bulk_job.failed` · `salesforce.bulk_job.poll_timeout`
`salesforce.request.retried` · `salesforce.rate_limited`

`salesforce.bulk_job.poll_timeout` fires when a Bulk API job exceeds
`SF_JOB_MAX_POLL_SECONDS` (default 3600s; set to 0 to opt out). The client
marks the JobRecord failed, attempts a best-effort `abort_job` on Salesforce,
and increments `sfbl_bulk_job_poll_timeout_total`. See SFBL-111.

### Storage events
`storage.input.listed` · `storage.input.previewed` · `storage.input.failed` · `storage.output.persisted`

### System events
`health.checked` · `websocket.connected` · `websocket.disconnected` · `websocket.error`
`exception.unhandled`

### Email events

Emitted by `app.services.email.service`, `app.services.email.templates`, and
`app.main` (boot sweep). Import from `app.observability.events.EmailEvent`.

| Constant | Value | Description |
|---|---|---|
| `EmailEvent.SEND_REQUESTED` | `email.send.requested` | A send was initiated (before first backend call) |
| `EmailEvent.SEND_SUCCEEDED` | `email.send.succeeded` | Backend accepted the message; status→`sent` |
| `EmailEvent.SEND_FAILED` | `email.send.failed` | Terminal failure — permanent error or retries exhausted |
| `EmailEvent.SEND_RETRIED` | `email.send.retried` | Transient failure; retry task scheduled |
| `EmailEvent.SEND_SKIPPED` | `email.send.skipped` | Noop backend; no network send performed |
| `EmailEvent.SEND_CLAIM_LOST` | `email.send.claim_lost` | Retry task lost CAS race to another worker |
| `EmailEvent.TEMPLATE_LOAD_FAILED` | `email.template.load_failed` | Non-auth template failed to load at startup |
| `EmailEvent.BOOT_SWEEP_COMPLETED` | `email.boot_sweep.completed` | Boot-sweep reaped stale pending rows |
| `EmailEvent.SERVICE_INITIALISED` | `email.service.initialised` | Email service singleton initialised |

Email metrics have a combined cardinality ceiling of **324 series** across all
four counters/histograms: 3 backends × 3 categories × 4 statuses × 9 error
reasons. See `backend/tests/test_email_cardinality.py` for the enforcement test.

The `email.send` span (in `app.observability.tracing.email_send_span`) records
`email.backend`, `email.category`, `email.template`, `email.to_domain`, and
`email.attempt` as attributes. On failure it additionally records `email.reason`
(the `EmailErrorReason` enum value) and `email.provider_error_code` (the raw
provider code, e.g. `"SES:Throttling"` — **span-only**, never a metric label).

The `/api/health/dependencies` endpoint includes an `email` entry:
- `noop` backend → always `healthy` (no probe).
- `smtp` / `ses` backends → TCP connect (SMTP) or cached `GetSendQuota` (SES).
  Failure → `degraded` (not `unhealthy`), because email is not strictly required
  for app functionality.

---

## Outcome codes (quick reference)

```python
from app.observability.events import OutcomeCode

# Baseline
OutcomeCode.OK                    # terminal success
OutcomeCode.DEGRADED              # completed with partial errors
OutcomeCode.FAILED                # terminal failure
OutcomeCode.ABORTED               # explicitly cancelled
OutcomeCode.UNEXPECTED_EXCEPTION  # unhandled exception / programming error

# Workflow / dependency
OutcomeCode.AUTH_ERROR
OutcomeCode.STORAGE_ERROR
OutcomeCode.DATABASE_ERROR
OutcomeCode.SALESFORCE_API_ERROR
OutcomeCode.RATE_LIMITED
OutcomeCode.NETWORK_ERROR
OutcomeCode.TIMEOUT
OutcomeCode.VALIDATION_ERROR
OutcomeCode.STEP_THRESHOLD_EXCEEDED
OutcomeCode.DEPENDENCY_UNAVAILABLE
OutcomeCode.CONFIGURATION_ERROR
OutcomeCode.JOB_POLL_TIMEOUT      # Bulk API job exceeded sf_job_max_poll_seconds

# Email
OutcomeCode.EMAIL_SMTP_ERROR          # SMTP backend delivery failure
OutcomeCode.EMAIL_SES_ERROR           # SES backend delivery failure
OutcomeCode.EMAIL_RENDER_ERROR        # Template render / subject-safety failure
OutcomeCode.EMAIL_CONFIG_ERROR        # Email backend misconfiguration
OutcomeCode.EMAIL_TEMPLATE_LOAD_FAILED  # Template failed to load at startup
```

### Unhandled-exception funnel (SFBL-112)

Any exception raised from `step_executor.execute_step` that is not
`InputStorageError` or `asyncio.CancelledError` is caught by the broad handler
in `run_coordinator._execute_run_body`, logged with `event_name=run.failed` +
`outcome_code=unexpected_exception`, reported to error monitoring, and funneled
through `_mark_run_failed_fresh` so the run transitions to `failed` (merging
an `unexpected_exception` key into `LoadRun.error_summary`).

`asyncio.CancelledError` takes the `aborted` branch instead: the run is marked
aborted via `_mark_run_aborted_fresh`, `run.aborted` is published, and the
exception is re-raised so task-group shutdown semantics still hold.

As a final safety net, `_execute_run` wraps the body in `try/finally`. The
finally helper (`_backstop_mark_failed_if_running`) opens a fresh session,
re-fetches the run, and — if still `running` — marks it `failed` with an
`unknown_exit` marker. Runs therefore never stay stuck in `running`.

---

## Correlation identifiers

These IDs are the first-class observability dimensions. Attach them wherever the scope is known.
Never use them as metric labels — only in logs and span attributes.

| ID | Set by | Scope |
|---|---|---|
| `request_id` | `RequestIDMiddleware` | Every inbound HTTP request |
| `run_id` | `run_coordinator` | Run execution |
| `step_id` | `step_executor` | Step execution |
| `job_record_id` | `partition_executor` | Partition/job execution |
| `sf_job_id` | `partition_executor` | Once Salesforce job is created |
| `load_plan_id` | `run_coordinator` | Run execution |
| `input_connection_id` | `run_coordinator` | Run execution |

IDs flow through async boundaries via ContextVars (see `app/observability/context.py`).
Use the helpers there to set and read values:

```python
from app.observability.context import run_id_ctx_var

token = run_id_ctx_var.set(str(run.id))
try:
    ...
finally:
    run_id_ctx_var.reset(token)
```

---

## How to extend each layer

### Adding a new canonical event

Add a constant to the appropriate class in `app/observability/events.py`:

```python
class RunEvent:
    MY_NEW_EVENT = "run.my_new_event"   # dot-separated, transport-independent
```

Then emit it in the relevant service:

```python
logger.info("...", extra={"event_name": RunEvent.MY_NEW_EVENT, "outcome_code": OutcomeCode.OK})
```

### Adding a new outcome code

Add a constant to `OutcomeCode` in `app/observability/events.py` and update the docstring:

```python
class OutcomeCode:
    MY_ERROR = "my_error"   # lower-snake, stable, machine-readable
```

### Adding a new metric

In `app/observability/metrics.py`, follow the existing pattern:

```python
my_counter = Counter(
    "sfbl_my_counter_total",
    "Human-readable description",
    ["label_one", "label_two"],   # low-cardinality labels only — never IDs
)
```

Then increment it at the appropriate call site:
```python
from app.observability.metrics import my_counter

my_counter.labels(label_one="value", label_two="value").inc()
```

### Adding a new workflow span

Use the existing context managers in `app/observability/tracing.py` as a reference. For
new execution boundaries, create a context manager following the same pattern:

```python
@contextmanager
def my_boundary_span(entity_id: str) -> Generator[Span, None, None]:
    tracer = _get_tracer()
    with tracer.start_as_current_span("my.boundary") as span:
        span.set_attribute("entity.id", entity_id)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise
```

Attributes must follow the data sensitivity rules in `app/observability/sanitization.py`.
IDs are safe. Tokens, keys, and raw CSV data are not.

---

## Observability Definition of Done

Any future enhancement that introduces or materially changes any of the following
**must** update observability as part of the same ticket — it is not optional:

- run lifecycle behavior
- step execution behavior
- job / partition lifecycle
- Salesforce interaction flows
- storage flows (CSV input, result output)
- retry behavior
- terminal outcome handling
- background execution boundaries

### Checklist for every affected ticket

Work through each item and explicitly note which ones apply:

- [ ] **Canonical events** — are new `event_name` constants needed? Have they been added to `events.py`?
- [ ] **Outcome codes** — can this path emit a new outcome code? Has it been added to `OutcomeCode`?
- [ ] **Structured logging** — do new log sites use `event_name` and `outcome_code` in `extra={}`?
- [ ] **Correlation IDs** — are the relevant IDs (`run_id`, `step_id`, etc.) propagated into context?
- [ ] **Metrics** — which counters, histograms, or gauges should change? Have they been updated?
- [ ] **Spans** — does this introduce a new execution boundary that warrants a custom span?
- [ ] **Span attributes** — do any new span attributes respect the sanitization rules?
- [ ] **Health / readiness** — does this change affect `ready` or `dependencies` endpoint semantics?
- [ ] **Sensitive telemetry** — do any new error paths or exception handlers comply with `sanitization.py`?
- [ ] **Tests** — are there tests covering the new observability paths?

### Review questions for specs and PRs

When reviewing a spec or PR that touches workflow behaviour, ask:

1. What new canonical events exist, if any?
2. Which outcome codes can now be emitted?
3. Which metrics should increment or time this new behaviour?
4. Which correlation IDs must be propagated into the new scope?
5. Does this introduce a new execution boundary needing a custom span?
6. Does this affect liveness, readiness, or dependency health?
7. Do any new error paths respect the prohibited telemetry content rules?

---

## Telemetry hygiene rules

See [`docs/development.md`](development.md#sensitive-telemetry-handling-sfbl-60) and
[`backend/app/observability/sanitization.py`](../backend/app/observability/sanitization.py)
for the full list of prohibited content and the shared scrubbing helpers.

**Summary: never include tokens, keys, passwords, auth headers, or raw CSV data
in any log record, span attribute, metric label, or error monitoring event.**

---

## Forward compatibility

The observability model is designed to survive future decomposition into an
orchestrator + queue + distributed executors architecture. The key rules are:

- Canonical event names must not assume single-process or synchronous execution
- Correlation IDs must be serialisable across process boundaries (they are plain strings)
- Metrics must remain aggregate-friendly (no entity IDs as labels)
- Future executor components must use the same `OutcomeCode` and event name constants
- Progress state must be readable from durable workflow state — not only in-process memory

New architecture components must import from `app.observability.events` and
`app.observability.sanitization` rather than defining their own parallel taxonomy.
