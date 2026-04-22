import logging
import os
import secrets
from pathlib import Path
from typing import List, Literal

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


# Test suites set SFBL_DISABLE_ENV_FILE=1 before importing app.config to
# prevent the developer's repo-root `.env` (used for `docker compose up`) from
# bleeding into `Settings` and flipping profile-default assertions / health
# checks. Production always loads the usual `.env` chain.
_ENV_FILES: tuple[str, ...] = (
    () if os.getenv("SFBL_DISABLE_ENV_FILE") == "1" else ("../.env", ".env")
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, env_file_encoding="utf-8", extra="ignore")

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

    # Salesforce defaults — migrated to DB-backed settings (SFBL-156).
    # These fields are retained for backward compatibility only (fallback when
    # SettingsService is not yet initialised, e.g. during tests). The live
    # values are sourced from the settings API / UI after first boot.
    # → now managed via /settings/salesforce UI
    sf_api_version: str = "v62.0"
    sf_poll_interval_initial: int = 5
    sf_poll_interval_max: int = 30
    sf_job_timeout_minutes: int = 30
    # Absolute wall-clock cap on a single Bulk API job's polling loop. When
    # exceeded, the job is marked failed (best-effort abort on Salesforce) and
    # the run continues with remaining partitions. Set to 0 to opt out and
    # preserve the previous unbounded behaviour. See SFBL-111.
    sf_job_max_poll_seconds: int = 3600

    # Partitioning defaults — migrated to DB-backed settings (SFBL-156).
    # → now managed via /settings/partitioning UI
    default_partition_size: int = 10_000
    max_partition_size: int = 100_000_000

    # Authentication — jwt_secret_key and jwt_algorithm are bootstrap-only secrets.
    # jwt_expiry_minutes is migrated to DB-backed settings (SFBL-156) but retained
    # here as a fallback for create_access_token() when SettingsService is unavailable.
    jwt_secret_key: str = ""
    jwt_secret_key_file: str = "/data/db/jwt_secret.key"
    jwt_algorithm: str = "HS256"
    # → now managed via /settings/security UI
    jwt_expiry_minutes: int = 60
    # SFBL-198: ADMIN_EMAIL is the new required bootstrap var for first-boot seeding.
    # ADMIN_USERNAME is retained only for use as display_name on the seeded admin account.
    admin_email: str | None = None
    admin_username: str | None = None
    admin_password: str | None = None

    # Auth: login rate limit — migrated to DB-backed settings (SFBL-156).
    # → now managed via /settings/security UI
    # Per-IP sliding-window limit across all usernames.  Per-process — each
    # worker maintains its own counter.  See services/rate_limit.py for details.
    login_rate_limit_attempts: int = 20
    login_rate_limit_window_seconds: int = 300

    # Auth: progressive lockout — migrated to DB-backed settings (SFBL-156).
    # → now managed via /settings/security UI
    # Tier 1 — temporary auto-lock: if ``login_tier1_threshold`` failed attempts
    # accumulate within ``login_tier1_window_minutes``, set locked_until for
    # ``login_tier1_lock_minutes``. Status stays 'active'; lock expires silently.
    login_tier1_threshold: int = 5
    login_tier1_window_minutes: int = 15
    login_tier1_lock_minutes: int = 15
    # Tier 2 — hard lock: transitions status to 'locked' (requires admin unlock).
    # Triggered by either:
    #   A) ``login_tier2_threshold`` cumulative failed logins since last success.
    #   B) ``login_tier2_tier1_count`` tier-1 locks within ``login_tier2_window_hours``.
    login_tier2_threshold: int = 15
    login_tier2_tier1_count: int = 3
    login_tier2_window_hours: int = 24

    # Auth: password reset & email change — migrated to DB-backed settings (SFBL-156).
    # → now managed via /settings/security UI
    pw_reset_rate_limit_per_ip_hour: int = 5
    pw_reset_rate_limit_per_email_hour: int = 3
    email_change_rate_limit_per_user_hour: int = 3
    password_reset_ttl_minutes: int = 15
    email_change_ttl_minutes: int = 30

    # Invitations — TTL for invitation tokens issued to new users.
    # The absolute expires_at is computed in the application by adding this
    # value to the current time.  SFBL-199.
    invitation_ttl_hours: int = 24

    # CORS
    cors_origins: List[str] = ["http://localhost:3000", "https://localhost:3000"]

    # Paths
    input_dir: str = "/data/input"
    output_dir: str = "/data/output"

    # Email — note: all email configuration is now managed via DB-backed settings
    # (SFBL-155). The fields below are retained only for the distribution profile
    # email_backend default in _apply_distribution_profile; they are seeded into
    # the DB at first boot via SettingsService.seed_from_env().
    email_backend: Literal["smtp", "ses", "noop"] | None = None

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

settings = Settings()
