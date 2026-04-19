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
- AuthEvent    — authentication and account management events
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

    # S3 output upload lifecycle (SFBL-163)
    OUTPUT_UPLOAD_STARTED = "storage.output.upload.started"
    OUTPUT_UPLOAD_COMPLETED = "storage.output.upload.completed"
    OUTPUT_UPLOAD_FAILED = "storage.output.upload.failed"


class SystemEvent:
    """Infrastructure and connectivity events."""

    HEALTH_CHECKED = "health.checked"
    WEBSOCKET_CONNECTED = "websocket.connected"
    WEBSOCKET_DISCONNECTED = "websocket.disconnected"
    WEBSOCKET_ERROR = "websocket.error"
    EXCEPTION_UNHANDLED = "exception.unhandled"


class AuthEvent:
    """Authentication and account management events.

    Covers password-change (SFBL-146), password-reset flow (SFBL-147),
    profile/email-change (SFBL-148), and token rejection (SFBL-145).
    """

    # SFBL-146: authenticated password change
    PASSWORD_CHANGED = "auth.password.changed"

    # SFBL-147: unauthenticated password reset
    PASSWORD_RESET_REQUESTED = "auth.password.reset.requested"
    PASSWORD_RESET_CONFIRMED = "auth.password.reset.confirmed"

    # SFBL-148: profile + email change
    PROFILE_UPDATED = "auth.profile.updated"
    EMAIL_CHANGE_REQUESTED = "auth.email.change.requested"
    EMAIL_CHANGE_CONFIRMED = "auth.email.change.confirmed"

    # SFBL-145: JWT watermark rejection
    TOKEN_REJECTED = "auth.token_rejected"


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
    output_upload_error  — S3 output upload failure (distinct from input storage_error)

    Email codes
    ~~~~~~~~~~~
    email_smtp_error          — SMTP backend delivery failure
    email_ses_error           — SES backend delivery failure
    email_render_error        — Jinja2 template render / subject-safety failure
    email_config_error        — Email backend misconfiguration (missing host, auth, etc.)
    email_template_load_failed — Template failed to load at startup

    Auth / password reset + email change codes (SFBL-145 – SFBL-148)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    sent                      — reset/change email dispatched
    unknown_email             — no matching user found (non-enumeration — always 202)
    invalid_token             — token not found or associated user inactive
    expired_token             — token TTL elapsed
    used_token                — token already redeemed
    no_local_auth             — SAML-only account; no local password
    policy_violation          — new password fails strength rules
    success                   — operation completed successfully
    wrong_current             — current password verification failed
    same_password             — new password matches current
    email_unchanged           — email change requested but new == current
    email_in_use              — new email already taken by another user
    in_use_at_confirm         — email claimed by a different user between request and confirm
    stale_after_password_change — JWT issued before latest password change watermark
    expired                   — JWT past its exp claim
    invalid_signature         — JWT signature verification failure
    user_inactive             — token holder's account is deactivated
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

    # Auth / password reset + email change (SFBL-145 – SFBL-148)
    SENT = "sent"
    UNKNOWN_EMAIL = "unknown_email"
    INVALID_TOKEN = "invalid_token"
    EXPIRED_TOKEN = "expired_token"
    USED_TOKEN = "used_token"
    NO_LOCAL_AUTH = "no_local_auth"
    POLICY_VIOLATION = "policy_violation"
    SUCCESS = "success"
    WRONG_CURRENT = "wrong_current"
    SAME_PASSWORD = "same_password"
    EMAIL_UNCHANGED = "unchanged"
    EMAIL_IN_USE = "in_use"
    IN_USE_AT_CONFIRM = "in_use_at_confirm"

    # Token rejection outcome codes (SFBL-145)
    STALE_AFTER_PASSWORD_CHANGE = "stale_after_password_change"
    EXPIRED = "expired"
    INVALID_SIGNATURE = "invalid_signature"
    USER_INACTIVE = "user_inactive"

    # Storage output (SFBL-163)
    # Separate from STORAGE_ERROR so dashboards can distinguish input-read from output-write failures.
    OUTPUT_UPLOAD_ERROR = "output_upload_error"

    # Email
    EMAIL_SMTP_ERROR = "email_smtp_error"
    EMAIL_SES_ERROR = "email_ses_error"
    EMAIL_RENDER_ERROR = "email_render_error"
    EMAIL_CONFIG_ERROR = "email_config_error"
    EMAIL_TEMPLATE_LOAD_FAILED = "email_template_load_failed"
