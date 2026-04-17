import email.utils
import logging
import os
import secrets
from pathlib import Path
from typing import List, Literal

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # Distribution profile
    app_distribution: Literal["desktop", "self_hosted", "aws_hosted"] = "self_hosted"
    auth_mode: Literal["none", "local"] | None = None
    transport_mode: Literal["local", "http", "https"] | None = None
    input_storage_mode: Literal["local", "s3"] | None = None

    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: Literal["plain", "json"] = "plain"
    service_name: str = "sf-bulk-loader-backend"
    request_id_header_name: str = "X-Request-ID"

    # Health checks
    health_enable_dependency_checks: bool = True

    # Tracing (SFBL-56)
    tracing_enabled: bool = False
    trace_sample_ratio: float = 1.0
    otlp_endpoint: str | None = None

    # Error monitoring (SFBL-58)
    error_monitoring_enabled: bool = False
    error_monitoring_dsn: str | None = None
    encryption_key: str = ""
    encryption_key_file: str = "/data/db/encryption.key"

    # Database
    database_url: str = "sqlite+aiosqlite:////data/db/bulk_loader.db"
    db_pool_size: int = 20
    db_pool_max_overflow: int = 10

    # Salesforce defaults
    sf_api_version: str = "v62.0"
    sf_poll_interval_initial: int = 5
    sf_poll_interval_max: int = 30
    sf_job_timeout_minutes: int = 30
    # Absolute wall-clock cap on a single Bulk API job's polling loop. When
    # exceeded, the job is marked failed (best-effort abort on Salesforce) and
    # the run continues with remaining partitions. Set to 0 to opt out and
    # preserve the previous unbounded behaviour. See SFBL-111.
    sf_job_max_poll_seconds: int = 3600

    # Partitioning defaults
    default_partition_size: int = 10_000
    max_partition_size: int = 100_000_000

    # Authentication
    jwt_secret_key: str = ""
    jwt_secret_key_file: str = "/data/db/jwt_secret.key"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    admin_username: str | None = None
    admin_password: str | None = None

    # CORS
    cors_origins: List[str] = ["http://localhost:3000", "https://localhost:3000"]

    # Paths
    input_dir: str = "/data/input"
    output_dir: str = "/data/output"

    # Email — general
    email_backend: Literal["smtp", "ses", "noop"] | None = None
    email_from_address: str | None = None
    email_from_name: str | None = None
    email_reply_to: str | None = None
    email_max_retries: int = 3
    email_retry_backoff_seconds: float = 2.0
    email_retry_backoff_max_seconds: float = 120.0
    email_timeout_seconds: float = 15.0
    email_claim_lease_seconds: int = 60
    email_pending_stale_minutes: int = 15
    email_log_recipients: bool = False   # Opt-in plaintext recipient storage

    # Email — SMTP
    email_smtp_host: str | None = None
    email_smtp_port: int = 587
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_smtp_password_file: str | None = None
    email_smtp_starttls: bool = True
    email_smtp_use_tls: bool = False     # implicit TLS (port 465)

    # Email — SES
    email_ses_region: str | None = None
    email_ses_configuration_set: str | None = None
    # SES credentials resolved via boto3 default chain — no explicit keys here

    @model_validator(mode="after")
    def _apply_distribution_profile(self) -> "Settings":
        profile = self.app_distribution
        is_sqlite = self.database_url.startswith("sqlite")

        # Fill derived defaults when not explicitly set
        defaults: dict[str, dict] = {
            "desktop":     {"auth_mode": "none",  "transport_mode": "local", "input_storage_mode": "local", "email_backend": "noop"},
            "self_hosted": {"auth_mode": "local", "transport_mode": "http",  "input_storage_mode": "local", "email_backend": "noop"},
            "aws_hosted":  {"auth_mode": "local", "transport_mode": "https", "input_storage_mode": "s3",   "email_backend": "ses"},
        }
        for field, default in defaults[profile].items():
            if getattr(self, field) is None:
                setattr(self, field, default)

        # Validate combinations
        if profile == "aws_hosted":
            if is_sqlite:
                raise ValueError("aws_hosted requires a PostgreSQL DATABASE_URL, not SQLite")
            if self.transport_mode != "https":
                raise ValueError("aws_hosted requires transport_mode=https")
            if self.input_storage_mode != "s3":
                raise ValueError("aws_hosted requires input_storage_mode=s3")

        if profile == "desktop":
            if not is_sqlite:
                raise ValueError("desktop profile requires a SQLite DATABASE_URL")
            if self.auth_mode != "none":
                raise ValueError("desktop profile does not support auth_mode=local")
            if self.transport_mode != "local":
                raise ValueError("desktop profile requires transport_mode=local")

        return self

    @model_validator(mode="after")
    def _resolve_auto_keys(self) -> "Settings":
        """Load or auto-generate ENCRYPTION_KEY and JWT_SECRET_KEY.

        Resolution order for each key:
          1. Env var / explicit value — used as-is.
          2. Key file (ENCRYPTION_KEY_FILE / JWT_SECRET_KEY_FILE) — read and used.
          3. Neither present — generate, persist to the key file, and log a warning.

        A ValueError is raised if auto-generation is required but the key file
        directory is not writable (e.g. local dev without data/db/).
        """

        def _load_or_generate(current: str, file_path: str, generator, label: str) -> str:
            if current:
                return current
            path = Path(file_path)
            if path.exists():
                return path.read_text().strip()
            key = generator()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(key)
                os.chmod(path, 0o600)
                _log.warning(
                    "%s not set — generated and saved to %s. "
                    "Back this file up to avoid losing access to stored data.",
                    label,
                    path,
                )
            except OSError as exc:
                raise ValueError(
                    f"{label} not set and could not write to {file_path}: {exc}. "
                    f"Set {label} in .env or ensure the directory is writable."
                ) from exc
            return key

        self.encryption_key = _load_or_generate(
            self.encryption_key,
            self.encryption_key_file,
            lambda: Fernet.generate_key().decode(),
            "ENCRYPTION_KEY",
        )
        self.jwt_secret_key = _load_or_generate(
            self.jwt_secret_key,
            self.jwt_secret_key_file,
            lambda: secrets.token_hex(32),
            "JWT_SECRET_KEY",
        )
        return self

    @model_validator(mode="after")
    def _resolve_email_smtp_password(self) -> "Settings":
        """Resolve SMTP password from env var or file.

        Resolution order:
          1. EMAIL_SMTP_PASSWORD env var / explicit value — used as-is if non-empty.
          2. EMAIL_SMTP_PASSWORD_FILE — file contents (stripped) if file exists.
          3. Neither present:
             - EMAIL_BACKEND=smtp → hard boot error (ValueError).
             - Other backends     → leave as empty string (password irrelevant).

        Unlike ENCRYPTION_KEY/JWT_SECRET_KEY, SMTP passwords are never auto-generated
        because an auto-generated secret has no meaning to an external SMTP provider.
        See DECISIONS.md #019.
        """
        if self.email_smtp_password:
            # Env var / explicit value present — use as-is
            return self

        if self.email_smtp_password_file:
            path = Path(self.email_smtp_password_file)
            if path.exists():
                self.email_smtp_password = path.read_text().strip()
                return self

        # Neither env nor file resolved a password
        if self.email_backend == "smtp":
            raise ValueError(
                "EMAIL_BACKEND=smtp requires a password. "
                "Set EMAIL_SMTP_PASSWORD (env var) or EMAIL_SMTP_PASSWORD_FILE (path to file). "
                "SMTP passwords are never auto-generated — they must be issued by your SMTP provider."
            )

        # Backend is noop/ses — SMTP password is irrelevant; leave as None
        return self

    @model_validator(mode="after")
    def _validate_email_invariants(self) -> "Settings":
        """Enforce email configuration invariants."""
        # email_from_address: must be a valid RFC-5321 address when set
        if self.email_from_address is not None:
            _, addr_spec = email.utils.parseaddr(self.email_from_address)
            at_count = addr_spec.count("@")
            domain = addr_spec.split("@", 1)[1] if at_count == 1 else ""
            if at_count != 1 or not domain:
                raise ValueError(
                    f"email_from_address {self.email_from_address!r} is not a valid RFC-5321 address. "
                    "Expected format: user@domain or 'Display Name <user@domain>'."
                )

        if self.email_max_retries < 0:
            raise ValueError(
                f"email_max_retries must be >= 0, got {self.email_max_retries}."
            )

        if self.email_retry_backoff_max_seconds < self.email_retry_backoff_seconds:
            raise ValueError(
                f"email_retry_backoff_max_seconds ({self.email_retry_backoff_max_seconds}) "
                f"must be >= email_retry_backoff_seconds ({self.email_retry_backoff_seconds})."
            )

        if self.email_claim_lease_seconds <= self.email_timeout_seconds:
            raise ValueError(
                f"email_claim_lease_seconds ({self.email_claim_lease_seconds}) "
                f"must be strictly greater than email_timeout_seconds ({self.email_timeout_seconds}) "
                "so a slow send cannot outlive its lease."
            )

        return self


settings = Settings()
