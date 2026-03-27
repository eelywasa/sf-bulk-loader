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

    @model_validator(mode="after")
    def _apply_distribution_profile(self) -> "Settings":
        profile = self.app_distribution
        is_sqlite = self.database_url.startswith("sqlite")

        # Fill derived defaults when not explicitly set
        defaults: dict[str, dict] = {
            "desktop":     {"auth_mode": "none",  "transport_mode": "local", "input_storage_mode": "local"},
            "self_hosted": {"auth_mode": "local", "transport_mode": "http",  "input_storage_mode": "local"},
            "aws_hosted":  {"auth_mode": "local", "transport_mode": "https", "input_storage_mode": "s3"},
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
