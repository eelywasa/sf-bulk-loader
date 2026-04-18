"""Canonical event names and outcome codes for the Salesforce Bulk Loader.

This module is the single authoritative source for all observability event names
and outcome codes used in logs, metrics, and traces. All categories use
dot-separated string constants, suitable for use as ``event_name`` and
``outcome_code`` fields in structured log records.

Usage:
    from app.observability.events import JobEvent, OutcomeCode, RunEvent

    logger.info(
        "Run %s completed",
        run_id,
        extra={
            "event_name": RunEvent.COMPLETED,
            "outcome_code": OutcomeCode.OK,
            "run_id": run_id,
        },
    )

Event categories
----------------
- RunEvent     — top-level load run lifecycle
- StepEvent    — per-step execution events
- JobEvent     — per-partition / Bulk API job events
- SalesforceEvent — Salesforce integration layer events
- StorageEvent — file storage (input/output) events
- SystemEvent  — infrastructure and connectivity events
- EmailEvent   — outbound email delivery lifecycle events

Outcome taxonomy
----------------
- OutcomeCode  — machine-readable terminal and error outcome codes

All names are stable and transport-independent. WebSocket event payloads, log
records, and future metrics/trace labels should reference these constants rather
than inlining raw strings.
"""

from __future__ import annotations


class RunEvent:
    """Top-level load run lifecycle events."""

    CREATED = "run.created"
    STARTED = "run.started"
    COMPLETED = "run.completed"
    FAILED = "run.failed"
    ABORTED = "run.aborted"
    PROGRESS_UPDATED = "run.progress.updated"
    PREFLIGHT_STARTED = "run.preflight.started"
    PREFLIGHT_COMPLETED = "run.preflight.completed"
    PREFLIGHT_FAILED = "run.preflight.failed"


class StepEvent:
    """Per-step execution events."""

    STARTED = "step.started"
    COMPLETED = "step.completed"
    FAILED = "step.failed"
    THRESHOLD_EXCEEDED = "step.threshold_exceeded"


class JobEvent:
    """Per-partition / Bulk API job events."""

    CREATED = "job.created"
    STATUS_CHANGED = "job.status_changed"
    COMPLETED = "job.completed"
    FAILED = "job.failed"
    ABORTED = "job.aborted"


class SalesforceEvent:
    """Salesforce integration layer events."""

    AUTH_REQUESTED = "salesforce.auth.requested"
    AUTH_FAILED = "salesforce.auth.failed"
    BULK_JOB_CREATED = "salesforce.bulk_job.created"
    BULK_JOB_UPLOADED = "salesforce.bulk_job.uploaded"
    BULK_JOB_CLOSED = "salesforce.bulk_job.closed"
    BULK_JOB_POLLED = "salesforce.bulk_job.polled"
    BULK_JOB_COMPLETED = "salesforce.bulk_job.completed"
    BULK_JOB_FAILED = "salesforce.bulk_job.failed"
    BULK_JOB_POLL_TIMEOUT = "salesforce.bulk_job.poll_timeout"
    REQUEST_RETRIED = "salesforce.request.retried"
    RATE_LIMITED = "salesforce.rate_limited"


class StorageEvent:
    """File storage (input/output) events."""

    INPUT_LISTED = "storage.input.listed"
    INPUT_PREVIEWED = "storage.input.previewed"
    INPUT_FAILED = "storage.input.failed"
    OUTPUT_PERSISTED = "storage.output.persisted"


class SystemEvent:
    """Infrastructure and connectivity events."""

    HEALTH_CHECKED = "health.checked"
    WEBSOCKET_CONNECTED = "websocket.connected"
    WEBSOCKET_DISCONNECTED = "websocket.disconnected"
    WEBSOCKET_ERROR = "websocket.error"
    EXCEPTION_UNHANDLED = "exception.unhandled"


class EmailEvent:
    """Outbound email delivery lifecycle events."""

    SEND_REQUESTED = "email.send.requested"
    SEND_SUCCEEDED = "email.send.succeeded"
    SEND_FAILED = "email.send.failed"
    SEND_RETRIED = "email.send.retried"
    SEND_SKIPPED = "email.send.skipped"
    SEND_CLAIM_LOST = "email.send.claim_lost"
    TEMPLATE_LOAD_FAILED = "email.template.load_failed"
    BOOT_SWEEP_COMPLETED = "email.boot_sweep.completed"
    SERVICE_INITIALISED = "email.service.initialised"


class AuthEvent:
    """Auth user-management events (profile, email change, password reset)."""

    PROFILE_UPDATED = "auth.profile.updated"
    EMAIL_CHANGE_REQUESTED = "auth.email.change.requested"
    EMAIL_CHANGE_CONFIRMED = "auth.email.change.confirmed"


class OutcomeCode:
    """Machine-readable outcome codes for logs, events, and traces.

    Baseline codes
    ~~~~~~~~~~~~~~
    ok                   — terminal success
    degraded             — completed with partial errors
    failed               — terminal failure
    aborted              — explicitly cancelled
    unexpected_exception — unhandled exception / programming error

    Workflow / dependency codes
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    auth_error           — Salesforce JWT/OAuth authentication failure
    storage_error        — input storage access failure (S3, SFTP, etc.)
    database_error       — SQLite/database operation failure
    salesforce_api_error — Salesforce Bulk API or REST API error
    rate_limited         — HTTP 429 or explicit rate-limit response
    network_error        — TCP/TLS connectivity failure
    timeout              — operation exceeded configured deadline
    validation_error     — request or data validation failure
    step_threshold_exceeded — error rate exceeded configured threshold
    dependency_unavailable  — external service not reachable
    configuration_error  — missing or invalid application configuration
    job_poll_timeout     — Bulk API job exceeded ``sf_job_max_poll_seconds`` cap

    Email codes
    ~~~~~~~~~~~
    email_smtp_error          — SMTP backend delivery failure
    email_ses_error           — SES backend delivery failure
    email_render_error        — Jinja2 template render / subject-safety failure
    email_config_error        — Email backend misconfiguration (missing host, auth, etc.)
    email_template_load_failed — Template failed to load at startup
    """

    # Baseline
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    ABORTED = "aborted"
    UNEXPECTED_EXCEPTION = "unexpected_exception"

    # Workflow / dependency
    AUTH_ERROR = "auth_error"
    STORAGE_ERROR = "storage_error"
    DATABASE_ERROR = "database_error"
    SALESFORCE_API_ERROR = "salesforce_api_error"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    VALIDATION_ERROR = "validation_error"
    STEP_THRESHOLD_EXCEEDED = "step_threshold_exceeded"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CONFIGURATION_ERROR = "configuration_error"
    JOB_POLL_TIMEOUT = "job_poll_timeout"

    # Email
    EMAIL_SMTP_ERROR = "email_smtp_error"
    EMAIL_SES_ERROR = "email_ses_error"
    EMAIL_RENDER_ERROR = "email_render_error"
    EMAIL_CONFIG_ERROR = "email_config_error"
    EMAIL_TEMPLATE_LOAD_FAILED = "email_template_load_failed"

    # Auth / profile / email-change codes
    SENT = "sent"
    EMAIL_UNCHANGED = "unchanged"
    EMAIL_IN_USE = "in_use"
    INVALID_TOKEN = "invalid_token"
    EXPIRED_TOKEN = "expired_token"
    USED_TOKEN = "used_token"
    IN_USE_AT_CONFIRM = "in_use_at_confirm"
