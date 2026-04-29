"""SETTINGS_REGISTRY — central registry of all DB-backed tunable settings (SFBL-153).

Each entry maps a setting key to a SettingMeta dataclass describing its type,
default value, optional env-var override, and whether the value is stored
encrypted at rest.

Wave S1 seeds the registry with the 8 security/lockout integer keys introduced
by Epic A (SFBL-186).  Wave S2 (SFBL-155) adds the ~20 email keys so that
email configuration is managed via the DB-backed settings API rather than
environment variables.  Later waves (SFBL-156/157) will add Salesforce,
partitioning, and auth-tuning keys.

Profile scoping (SFBL-257):
  profiles=None              → visible to all distribution profiles
  profiles=["desktop"]       → desktop only
  profiles=["self_hosted", "aws_hosted"] → hosted profiles only

The settings API filters by the active APP_DISTRIBUTION so hosted profiles
never see desktop-only keys and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class SettingMeta:
    """Metadata for a single DB-backed setting."""

    key: str
    category: str
    type: Literal["str", "int", "bool", "float"]
    default: Any
    is_secret: bool = False
    description: str = ""
    env_var: str | None = None
    restart_required: bool = False
    profiles: list[str] | None = None
    """Distribution profiles this setting applies to.

    None  → all profiles.
    ["desktop"]  → desktop only.
    ["self_hosted", "aws_hosted"]  → hosted profiles only.
    """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SETTINGS_REGISTRY: dict[str, SettingMeta] = {}


def _register(*metas: SettingMeta) -> None:
    for meta in metas:
        if meta.key in SETTINGS_REGISTRY:
            raise ValueError(f"Duplicate setting key in registry: {meta.key!r}")
        SETTINGS_REGISTRY[meta.key] = meta


_HOSTED = ["self_hosted", "aws_hosted"]
_DESKTOP = ["desktop"]

# ---------------------------------------------------------------------------
# Security / login lockout  (category="security")
# ---------------------------------------------------------------------------

_register(
    SettingMeta(
        key="login_rate_limit_attempts",
        category="security",
        type="int",
        default=20,
        description="Maximum login attempts allowed within the rate-limit window before throttling.",
        env_var="LOGIN_RATE_LIMIT_ATTEMPTS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="login_rate_limit_window_seconds",
        category="security",
        type="int",
        default=300,
        description="Window size in seconds for the login rate-limit counter.",
        env_var="LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="login_tier1_threshold",
        category="security",
        type="int",
        default=5,
        description="Number of consecutive failures that trigger a Tier-1 temporary lockout.",
        env_var="LOGIN_TIER1_THRESHOLD",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="login_tier1_window_minutes",
        category="security",
        type="int",
        default=15,
        description="Rolling window in minutes over which Tier-1 failures are counted.",
        env_var="LOGIN_TIER1_WINDOW_MINUTES",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="login_tier1_lock_minutes",
        category="security",
        type="int",
        default=15,
        description="Duration in minutes for a Tier-1 automatic account lock.",
        env_var="LOGIN_TIER1_LOCK_MINUTES",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="login_tier2_threshold",
        category="security",
        type="int",
        default=15,
        description="Total failures within tier2_window_hours that trigger a permanent Tier-2 lock.",
        env_var="LOGIN_TIER2_THRESHOLD",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="login_tier2_tier1_count",
        category="security",
        type="int",
        default=3,
        description="Number of Tier-1 locks within tier2_window_hours that trigger a Tier-2 lock.",
        env_var="LOGIN_TIER2_TIER1_COUNT",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="login_tier2_window_hours",
        category="security",
        type="int",
        default=24,
        description="Rolling window in hours over which Tier-2 lock triggers are counted.",
        env_var="LOGIN_TIER2_WINDOW_HOURS",
        profiles=_HOSTED,
    ),
)

# ---------------------------------------------------------------------------
# Security extras: JWT expiry, password-reset, email-change  (category="security")
# ---------------------------------------------------------------------------

_register(
    SettingMeta(
        key="jwt_expiry_minutes",
        category="security",
        type="int",
        default=60,
        description="JWT access token lifetime in minutes. Applies to new tokens; existing tokens retain their original expiry.",
        env_var="JWT_EXPIRY_MINUTES",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="pw_reset_rate_limit_per_ip_hour",
        category="security",
        type="int",
        default=5,
        description="Maximum password-reset requests per IP address per hour.",
        env_var="PW_RESET_RATE_LIMIT_PER_IP_HOUR",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="pw_reset_rate_limit_per_email_hour",
        category="security",
        type="int",
        default=3,
        description="Maximum password-reset requests per email address per hour.",
        env_var="PW_RESET_RATE_LIMIT_PER_EMAIL_HOUR",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_change_rate_limit_per_user_hour",
        category="security",
        type="int",
        default=3,
        description="Maximum email-change requests per user per hour.",
        env_var="EMAIL_CHANGE_RATE_LIMIT_PER_USER_HOUR",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="password_reset_ttl_minutes",
        category="security",
        type="int",
        default=15,
        description="Password-reset token validity period in minutes.",
        env_var="PASSWORD_RESET_TTL_MINUTES",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_change_ttl_minutes",
        category="security",
        type="int",
        default=30,
        description="Email-change token validity period in minutes.",
        env_var="EMAIL_CHANGE_TTL_MINUTES",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="require_2fa",
        category="security",
        type="bool",
        default=False,
        description=(
            "When true, all users on hosted profiles must have TOTP 2FA "
            "configured. Users without a factor are forced into enrolment at "
            "next login. Desktop profile (auth_mode=none) is exempt. DB value "
            "overrides the REQUIRE_2FA env default once set."
        ),
        env_var="REQUIRE_2FA",
        profiles=_HOSTED,
    ),
)

# ---------------------------------------------------------------------------
# Salesforce  (category="salesforce")
# ---------------------------------------------------------------------------

_register(
    SettingMeta(
        key="sf_api_version",
        category="salesforce",
        type="str",
        default="v62.0",
        description="Salesforce API version used for all Bulk API 2.0 requests, e.g. v62.0.",
        env_var="SF_API_VERSION",
        profiles=None,
    ),
    SettingMeta(
        key="sf_poll_interval_initial",
        category="salesforce",
        type="int",
        default=5,
        description="Initial polling interval in seconds for Bulk API job status checks.",
        env_var="SF_POLL_INTERVAL_INITIAL",
        profiles=None,
    ),
    SettingMeta(
        key="sf_poll_interval_max",
        category="salesforce",
        type="int",
        default=30,
        description="Maximum polling interval in seconds (exponential backoff cap) for Bulk API job status checks.",
        env_var="SF_POLL_INTERVAL_MAX",
        profiles=None,
    ),
    SettingMeta(
        key="sf_job_timeout_minutes",
        category="salesforce",
        type="int",
        default=30,
        description="Soft wall-clock limit in minutes for a Bulk API job. A warning is logged when exceeded; polling continues.",
        env_var="SF_JOB_TIMEOUT_MINUTES",
        profiles=None,
    ),
    SettingMeta(
        key="sf_job_max_poll_seconds",
        category="salesforce",
        type="int",
        default=3600,
        description="Hard cap in seconds on the polling loop for a single Bulk API job. Set to 0 to disable (unbounded). See SFBL-111.",
        env_var="SF_JOB_MAX_POLL_SECONDS",
        profiles=None,
    ),
)

# ---------------------------------------------------------------------------
# Partitioning  (category="partitioning")
# ---------------------------------------------------------------------------

_register(
    SettingMeta(
        key="default_partition_size",
        category="partitioning",
        type="int",
        default=10_000,
        description="Default number of data rows per CSV partition when LoadStep.partition_size is not explicitly set.",
        env_var="DEFAULT_PARTITION_SIZE",
        profiles=None,
    ),
    SettingMeta(
        key="max_partition_size",
        category="partitioning",
        type="int",
        default=100_000_000,
        description="Maximum allowed partition size (rows). Requests exceeding this are rejected.",
        env_var="MAX_PARTITION_SIZE",
        profiles=None,
    ),
)

# ---------------------------------------------------------------------------
# Email  (category="email")
# ---------------------------------------------------------------------------

_register(
    SettingMeta(
        key="email_backend",
        category="email",
        type="str",
        default="noop",
        description=(
            "Email delivery backend. "
            "Allowed values: noop (disabled), smtp, ses."
        ),
        env_var="EMAIL_BACKEND",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_from_address",
        category="email",
        type="str",
        default="",
        description=(
            "Sender address used in the From header. "
            "Must be a valid RFC-5321 address, e.g. noreply@example.com or "
            "'My App <noreply@example.com>'."
        ),
        env_var="EMAIL_FROM_ADDRESS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_from_name",
        category="email",
        type="str",
        default="",
        description=(
            "Display name to use in the From header alongside email_from_address, "
            "e.g. 'Salesforce Bulk Loader'. Leave blank to use the address only."
        ),
        env_var="EMAIL_FROM_NAME",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_reply_to",
        category="email",
        type="str",
        default="",
        description="Reply-To address added to outbound emails. Leave blank to omit the header.",
        env_var="EMAIL_REPLY_TO",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_max_retries",
        category="email",
        type="int",
        default=3,
        description=(
            "Maximum number of retry attempts per failed email delivery "
            "(0 = no retries; each attempt after the first requires a prior failure)."
        ),
        env_var="EMAIL_MAX_RETRIES",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_retry_backoff_seconds",
        category="email",
        type="float",
        default=2.0,
        description=(
            "Base delay in seconds for the exponential retry backoff. "
            "Actual delay = min(base * 2^attempt, max) + uniform(0, base)."
        ),
        env_var="EMAIL_RETRY_BACKOFF_SECONDS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_retry_backoff_max_seconds",
        category="email",
        type="float",
        default=120.0,
        description="Cap in seconds for the exponential retry backoff delay.",
        env_var="EMAIL_RETRY_BACKOFF_MAX_SECONDS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_timeout_seconds",
        category="email",
        type="float",
        default=15.0,
        description=(
            "Per-message send timeout in seconds. "
            "Must be strictly less than email_claim_lease_seconds."
        ),
        env_var="EMAIL_TIMEOUT_SECONDS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_claim_lease_seconds",
        category="email",
        type="int",
        default=60,
        description=(
            "Duration in seconds for which a worker holds an exclusive lease on a "
            "pending email_delivery row. Must be strictly greater than email_timeout_seconds "
            "to guarantee that a slow send cannot outlive its lease."
        ),
        env_var="EMAIL_CLAIM_LEASE_SECONDS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_pending_stale_minutes",
        category="email",
        type="int",
        default=15,
        description=(
            "Rows in 'pending'/'sending' status with a claim that expired more than this "
            "many minutes ago are considered abandoned and reaped to 'failed' at boot."
        ),
        env_var="EMAIL_PENDING_STALE_MINUTES",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_log_recipients",
        category="email",
        type="bool",
        default=False,
        description=(
            "When true, store the plaintext recipient address in email_delivery.to_addr. "
            "Off by default for privacy; enable only when audit requirements demand it."
        ),
        env_var="EMAIL_LOG_RECIPIENTS",
        profiles=_HOSTED,
    ),
    # ── SMTP settings ──────────────────────────────────────────────────────
    SettingMeta(
        key="email_smtp_host",
        category="email",
        type="str",
        default="",
        description="SMTP server hostname, e.g. smtp.sendgrid.net.",
        env_var="EMAIL_SMTP_HOST",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_smtp_port",
        category="email",
        type="int",
        default=587,
        description="SMTP server port. Use 587 for STARTTLS or 465 for implicit TLS.",
        env_var="EMAIL_SMTP_PORT",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_smtp_username",
        category="email",
        type="str",
        default="",
        description="SMTP authentication username.",
        env_var="EMAIL_SMTP_USERNAME",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_smtp_password",
        category="email",
        type="str",
        default="",
        is_secret=True,
        description=(
            "SMTP authentication password. Stored encrypted. "
            "Required when email_backend=smtp."
        ),
        env_var="EMAIL_SMTP_PASSWORD",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_smtp_starttls",
        category="email",
        type="bool",
        default=True,
        description=(
            "Use STARTTLS (connect plain, upgrade to TLS). "
            "Set false when using implicit TLS (email_smtp_use_tls=true) on port 465."
        ),
        env_var="EMAIL_SMTP_STARTTLS",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_smtp_use_tls",
        category="email",
        type="bool",
        default=False,
        description=(
            "Use implicit TLS from the start (port 465). "
            "Mutually exclusive with email_smtp_starttls in practice."
        ),
        env_var="EMAIL_SMTP_USE_TLS",
        profiles=_HOSTED,
    ),
    # ── SES settings ───────────────────────────────────────────────────────
    SettingMeta(
        key="email_ses_region",
        category="email",
        type="str",
        default="",
        description=(
            "AWS region for SES, e.g. us-east-1. "
            "If blank, boto3 resolves region from its default chain."
        ),
        env_var="EMAIL_SES_REGION",
        profiles=_HOSTED,
    ),
    SettingMeta(
        key="email_ses_configuration_set",
        category="email",
        type="str",
        default="",
        description=(
            "Optional SES configuration set name included in every SendEmail call. "
            "When blank the kwarg is omitted entirely."
        ),
        env_var="EMAIL_SES_CONFIGURATION_SET",
        profiles=_HOSTED,
    ),
    # ── Frontend URL (drives email link generation) ────────────────────────
    SettingMeta(
        key="frontend_base_url",
        category="email",
        type="str",
        default="",
        description=(
            "Base URL of the frontend, e.g. https://bulk-loader.example.com. "
            "Used to build password-reset links, email-change confirmation links, "
            "and run-complete notification URLs. Leave blank to fall back to the "
            "inbound request origin."
        ),
        env_var="FRONTEND_BASE_URL",
        profiles=_HOSTED,
    ),
)

# ---------------------------------------------------------------------------
# Storage  (category="storage")
# ---------------------------------------------------------------------------

_register(
    SettingMeta(
        key="desktop_input_dir",
        category="storage",
        type="str",
        default="/data/input",
        description=(
            "Absolute path to the directory where CSV input files are read from. "
            "Seeded from the INPUT_DIR env var on first startup. "
            "Changes take effect immediately without restarting the app."
        ),
        env_var="INPUT_DIR",
        profiles=_DESKTOP,
    ),
    SettingMeta(
        key="desktop_output_dir",
        category="storage",
        type="str",
        default="/data/output",
        description=(
            "Absolute path to the directory where result CSVs are written. "
            "Seeded from the OUTPUT_DIR env var on first startup. "
            "Changes take effect immediately without restarting the app."
        ),
        env_var="OUTPUT_DIR",
        profiles=_DESKTOP,
    ),
)
