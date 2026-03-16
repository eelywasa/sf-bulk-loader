from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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

    # Paths
    input_dir: str = "/data/input"
    output_dir: str = "/data/output"


settings = Settings()
