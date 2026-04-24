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
- RunEvent        — top-level load run lifecycle
- StepEvent       — per-step execution events
- JobEvent        — per-partition / Bulk API job events
- SalesforceEvent — Salesforce integration layer events
- BulkQueryEvent  — Bulk API 2.0 query job lifecycle events (SFBL-171)
- StorageEvent    — file storage (input/output) events
- SystemEvent     — infrastructure and connectivity events
- AuthEvent       — authentication and account management events
- EmailEvent      — outbound email delivery lifecycle events

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


class BulkQueryEvent:
    """Bulk API 2.0 query job lifecycle events (SFBL-171).

    Distinct from :class:`SalesforceEvent` (which covers the DML path) so that
    dashboards can filter query-specific signals without parsing ``object_name``
    or ``operation`` labels.
    """

    #: Query job successfully created on Salesforce.
    JOB_CREATED = "bulk_query.job.created"

    #: Single poll cycle against the job status endpoint.  Emitted at DEBUG.
    JOB_POLLED = "bulk_query.job.polled"

    #: One results page successfully downloaded and streamed to output storage.
    #: Carries ``page_index`` (0-based) and ``row_count`` in ``extra``.
    JOB_PAGE_DOWNLOADED = "bulk_query.job.page_downloaded"

    #: Query job reached ``JobComplete`` terminal state and all pages streamed.
    JOB_COMPLETED = "bulk_query.job.completed"

    #: Query job reached ``Failed`` or ``Aborted`` terminal state.
    JOB_FAILED = "bulk_query.job.failed"

    #: Transient HTTP error retried (5xx or network) on the query path.
    REQUEST_RETRIED = "bulk_query.request.retried"

    #: Rate-limited (429) on the query path.
    RATE_LIMITED = "bulk_query.rate_limited"


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
    profile/email-change (SFBL-148), token rejection (SFBL-145),
    login attempt lifecycle (SFBL-190), and break-glass CLI recovery (SFBL-193).
    """

    # SFBL-190: login attempt lifecycle
    LOGIN_SUCCEEDED = "auth.login.succeeded"
    LOGIN_FAILED = "auth.login.failed"
    LOGIN_RATE_LIMITED = "auth.login.rate_limited"

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

    # SFBL-193: break-glass CLI admin password recovery
    ADMIN_RECOVERED = "auth.admin.recovered"

    # SFBL-191: progressive lockout + admin unlock
    ACCOUNT_LOCKED = "auth.account.locked"
    ACCOUNT_UNLOCKED = "auth.account.unlocked"

    # SFBL-195: permission enforcement
    PERMISSION_DENIED = "auth.permission_denied"

    # SFBL-200: admin user-management lifecycle
    USER_INVITED = "auth.user.invited"
    USER_PROFILE_CHANGED = "auth.user.profile_changed"
    USER_DEACTIVATED = "auth.user.deactivated"
    USER_REACTIVATED = "auth.user.reactivated"
    USER_DELETED = "auth.user.deleted"
    TEMP_PASSWORD_ISSUED = "auth.user.temp_password_issued"
    INVITE_RESENT = "auth.user.invite_resent"

    # SFBL-202: invitation accept lifecycle
    INVITATION_EMAIL_SENT = "auth.invitation.email_sent"
    INVITATION_ACCEPTED = "auth.invitation.accepted"

    # SFBL-244 / SFBL-248: 2FA login lifecycle (phase-1 MFA challenge, forced
    # enrolment redirect). The rest of the MFA event surface lives on the
    # dedicated :class:`MfaEvent` namespace below.
    LOGIN_MFA_CHALLENGE_ISSUED = "auth.login.mfa_challenge_issued"
    LOGIN_MFA_ENROLL_STARTED = "auth.login.mfa_enroll_started"


class MfaEvent:
    """2FA lifecycle events (SFBL-244).

    Covers self-service enrolment, backup-code consumption, admin reset,
    and the tenant ``require_2fa`` toggle. The phase-1 login-flow events
    (``auth.login.mfa_challenge_issued`` etc.) stay on :class:`AuthEvent`
    so existing dashboards filtering on ``auth.*`` pick them up.
    """

    #: User successfully confirmed their first TOTP code and a user_totp row
    #: plus backup codes were persisted.
    ENROLL_SUCCESS = "mfa.enroll.success"

    #: Verification failed during the confirm step (wrong code / bad secret).
    ENROLL_FAILED = "mfa.enroll.failed"

    #: The self-service ``/enroll/start`` endpoint generated a fresh secret.
    ENROLL_STARTED = "mfa.enroll.started"

    #: Backup codes rotated via self-service regenerate.
    BACKUP_CODES_REGENERATED = "mfa.backup_codes.regenerated"

    #: User consumed their last backup code.
    BACKUP_CODES_EXHAUSTED = "mfa.backup_codes.exhausted"

    #: User disabled their own factor (allowed only when ``require_2fa`` is off).
    FACTOR_DISABLED = "mfa.factor.disabled"

    #: Tenant-wide ``require_2fa`` setting toggled.
    TENANT_TOGGLE_CHANGED = "mfa.tenant_toggle.changed"

    #: Admin reset of another user's factor.
    ADMIN_RESET = "mfa.admin_reset"


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


class NotificationEvent:
    """Run-complete notification dispatch lifecycle events (SFBL-117 / SFBL-180)."""

    DISPATCH_REQUESTED = "notification.dispatch.requested"
    DISPATCH_SUCCEEDED = "notification.dispatch.succeeded"
    DISPATCH_FAILED = "notification.dispatch.failed"
    WEBHOOK_RETRIED = "notification.webhook.retried"
    NO_MATCHING_SUBSCRIPTIONS = "notification.no_matching_subscriptions"


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
    query_sf_job_failed        — Bulk query job reached Failed/Aborted terminal state (SFBL-171)
    query_soql_syntax_rejected — Salesforce explain endpoint returned 400 (invalid SOQL)

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

    Login attempt outcome codes (SFBL-190)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    wrong_password      — credentials supplied but password did not match
    unknown_user        — submitted username did not match any account
    user_locked         — account status is 'locked' or tier-1 lockout is active
    must_reset_password — credentials valid but must_reset_password flag is set
    ip_limit            — per-IP rate limit (20/5 min) exceeded

    Progressive lockout codes (SFBL-191)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    tier1_auto          — account received a tier-1 auto-lock (locked_until set, status=active)
    tier2_hard          — account transitioned to status='locked' (hard lock, admin unlock needed)
    tier1_auto_expired  — tier-1 lock had already expired at login time (auto-cleared)
    admin_manual        — admin explicitly unlocked an account via the unlock endpoint
    admin_unlock        — login_attempt audit row written when admin performs an unlock
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

    # Bulk query codes (SFBL-171)
    # More specific than SALESFORCE_API_ERROR so dashboards can filter query failures.
    QUERY_SF_JOB_FAILED = "query_sf_job_failed"
    # Emitted when the Salesforce query explain endpoint returns 400 (invalid SOQL).
    QUERY_SOQL_SYNTAX_REJECTED = "query_soql_syntax_rejected"

    # Storage output (SFBL-163)
    # Separate from STORAGE_ERROR so dashboards can distinguish input-read from output-write failures.
    OUTPUT_UPLOAD_ERROR = "output_upload_error"

    # Email
    EMAIL_SMTP_ERROR = "email_smtp_error"
    EMAIL_SES_ERROR = "email_ses_error"
    EMAIL_RENDER_ERROR = "email_render_error"
    EMAIL_CONFIG_ERROR = "email_config_error"
    EMAIL_TEMPLATE_LOAD_FAILED = "email_template_load_failed"

    # Notifications (SFBL-180)
    NOTIFICATION_WEBHOOK_ERROR = "notification_webhook_error"

    # Login attempt outcomes (SFBL-190)
    WRONG_PASSWORD = "wrong_password"
    UNKNOWN_USER = "unknown_user"
    USER_LOCKED = "user_locked"
    MUST_RESET_PASSWORD = "must_reset_password"
    IP_LIMIT = "ip_limit"

    # Break-glass CLI (SFBL-193)
    CLI_RECOVERY = "cli_recovery"

    # Permission enforcement (SFBL-195)
    PERMISSION_DENIED = "permission_denied"

    # Progressive lockout (SFBL-191)
    # tier1_auto          — account received a tier-1 auto-lock (locked_until set)
    # tier2_hard          — account transitioned to status='locked' (hard lock)
    # tier1_auto_expired  — tier-1 lock had already expired at login time (auto-cleared)
    # admin_manual        — admin explicitly unlocked the account via the API
    # admin_unlock        — login_attempt row written when admin performs unlock
    TIER1_AUTO = "tier1_auto"
    TIER2_HARD = "tier2_hard"
    TIER1_AUTO_EXPIRED = "tier1_auto_expired"
    ADMIN_MANUAL = "admin_manual"
    ADMIN_UNLOCK = "admin_unlock"

    # Admin user-management outcome codes (SFBL-200)
    INVITATION_ISSUED = "invitation_issued"
    LAST_ADMIN_GUARD = "last_admin_guard"

    # Invitation accept outcome codes (SFBL-202)
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_EMAIL_SENT = "invitation_email_sent"
    INVITATION_EMAIL_SKIPPED = "invitation_email_skipped"

    # 2FA outcome codes (SFBL-244)
    ALREADY_ENROLLED = "already_enrolled"
    INVALID_CODE = "invalid_code"
    INVALID_SECRET = "invalid_secret"
    TENANT_ENFORCED = "tenant_enforced"
    MFA_CHALLENGE_ISSUED = "mfa_challenge_issued"
    WRONG_MFA = "wrong_mfa"
    BACKUP_CODE_USED = "backup_code_used"
    ADMIN_RESET_2FA = "admin_reset_2fa"
