from typing import List, Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    encryption_key: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:////data/db/bulk_loader.db"

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
            if self.auth_mode != "none":
                raise ValueError("desktop profile does not support auth_mode=local")
            if self.transport_mode != "local":
                raise ValueError("desktop profile requires transport_mode=local")

        return self


settings = Settings()
