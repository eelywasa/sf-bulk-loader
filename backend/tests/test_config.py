"""Tests for the distribution profile config model."""

import os

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

# Ensure a valid encryption key is present before importing Settings
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.config import Settings  # noqa: E402


SQLITE_URL = "sqlite+aiosqlite:////data/db/test.db"
PG_URL = "postgresql+asyncpg://user:pass@localhost/testdb"


def make(**kwargs) -> Settings:
    """Construct Settings with test-safe required fields plus overrides."""
    base = {
        "encryption_key": Fernet.generate_key().decode(),
        "jwt_secret_key": "test-secret",
        "database_url": SQLITE_URL,
    }
    base.update(kwargs)
    return Settings(**base)


class TestDefaultProfile:
    def test_defaults_to_self_hosted(self):
        s = make()
        assert s.app_distribution == "self_hosted"

    def test_self_hosted_derives_auth_local(self):
        s = make()
        assert s.auth_mode == "local"

    def test_self_hosted_derives_transport_http(self):
        s = make()
        assert s.transport_mode == "http"

    def test_self_hosted_derives_storage_local(self):
        s = make()
        assert s.input_storage_mode == "local"


class TestDesktopProfile:
    def test_desktop_with_sqlite_is_valid(self):
        s = make(app_distribution="desktop")
        assert s.app_distribution == "desktop"
        assert s.auth_mode == "none"
        assert s.transport_mode == "local"
        assert s.input_storage_mode == "local"

    def test_desktop_rejects_postgresql(self):
        with pytest.raises(ValidationError, match="desktop profile requires a SQLite DATABASE_URL"):
            make(app_distribution="desktop", database_url=PG_URL)

    def test_desktop_rejects_auth_local(self):
        with pytest.raises(ValidationError, match="desktop profile does not support auth_mode=local"):
            make(app_distribution="desktop", auth_mode="local")

    def test_desktop_rejects_non_local_transport(self):
        with pytest.raises(ValidationError, match="desktop profile requires transport_mode=local"):
            make(app_distribution="desktop", transport_mode="http")


# S3 bucket coordinates the aws_hosted profile now requires (SFBL-386). Injected
# in production by the IaC; tests supply them explicitly to construct a valid
# aws_hosted profile.
AWS_S3 = {
    "s3_input_bucket": "bulk-loader-staging-input",
    "s3_output_bucket": "bulk-loader-staging-output",
    "s3_bucket_region": "eu-west-1",
}


class TestAwsHostedProfile:
    def test_aws_hosted_with_postgresql_is_valid(self):
        s = make(app_distribution="aws_hosted", database_url=PG_URL, **AWS_S3)
        assert s.app_distribution == "aws_hosted"
        assert s.auth_mode == "local"
        assert s.transport_mode == "https"
        assert s.input_storage_mode == "s3"

    def test_aws_hosted_rejects_sqlite(self):
        with pytest.raises(ValidationError, match="aws_hosted requires a PostgreSQL DATABASE_URL"):
            make(app_distribution="aws_hosted", database_url=SQLITE_URL, **AWS_S3)

    def test_aws_hosted_rejects_http_transport(self):
        with pytest.raises(ValidationError, match="aws_hosted requires transport_mode=https"):
            make(app_distribution="aws_hosted", database_url=PG_URL, transport_mode="http", **AWS_S3)

    def test_aws_hosted_rejects_local_storage(self):
        with pytest.raises(ValidationError, match="aws_hosted requires input_storage_mode=s3"):
            make(app_distribution="aws_hosted", database_url=PG_URL, input_storage_mode="local")


class TestS3BucketFailFast:
    """input_storage_mode=s3 fails fast without the first-party bucket coordinates (SFBL-386).

    Falsification: if the resolver silently fell back to the filesystem (or
    deferred the error to the first request), these would not raise at config
    construction and the test would fail.
    """

    def test_aws_hosted_requires_input_bucket(self):
        with pytest.raises(ValidationError, match="S3_INPUT_BUCKET"):
            make(
                app_distribution="aws_hosted",
                database_url=PG_URL,
                s3_output_bucket="out",
                s3_bucket_region="eu-west-1",
            )

    def test_aws_hosted_requires_output_bucket(self):
        with pytest.raises(ValidationError, match="S3_OUTPUT_BUCKET"):
            make(
                app_distribution="aws_hosted",
                database_url=PG_URL,
                s3_input_bucket="in",
                s3_bucket_region="eu-west-1",
            )

    def test_aws_hosted_requires_bucket_region(self):
        with pytest.raises(ValidationError, match="S3_BUCKET_REGION"):
            make(
                app_distribution="aws_hosted",
                database_url=PG_URL,
                s3_input_bucket="in",
                s3_output_bucket="out",
            )

    def test_missing_all_lists_all_three(self):
        with pytest.raises(
            ValidationError,
            match="S3_INPUT_BUCKET, S3_OUTPUT_BUCKET, S3_BUCKET_REGION",
        ):
            make(app_distribution="aws_hosted", database_url=PG_URL)

    def test_self_hosted_with_s3_mode_also_fails_fast(self):
        """The contract is scoped to the storage mode, not the profile."""
        with pytest.raises(ValidationError, match="S3_INPUT_BUCKET"):
            make(app_distribution="self_hosted", database_url=PG_URL, input_storage_mode="s3")


class TestSelfHostedProfile:
    def test_self_hosted_accepts_sqlite(self):
        s = make(app_distribution="self_hosted", database_url=SQLITE_URL)
        assert s.app_distribution == "self_hosted"

    def test_self_hosted_accepts_postgresql(self):
        s = make(app_distribution="self_hosted", database_url=PG_URL)
        assert s.app_distribution == "self_hosted"

    def test_self_hosted_accepts_https_transport(self):
        s = make(app_distribution="self_hosted", transport_mode="https")
        assert s.transport_mode == "https"


class TestAutoKeyGeneration:
    def test_encryption_key_auto_generated(self, tmp_path):
        key_file = tmp_path / "encryption.key"
        s = Settings(
            encryption_key="",  # override env — test the auto-generate path
            encryption_key_file=str(key_file),
            jwt_secret_key="test-secret",
            database_url=SQLITE_URL,
        )
        assert s.encryption_key
        assert key_file.exists()
        assert key_file.read_text().strip() == s.encryption_key

    def test_encryption_key_loaded_from_file(self, tmp_path):
        key = Fernet.generate_key().decode()
        key_file = tmp_path / "encryption.key"
        key_file.write_text(key)
        s = Settings(
            encryption_key="",  # override env — test the file-load path
            encryption_key_file=str(key_file),
            jwt_secret_key="test-secret",
            database_url=SQLITE_URL,
        )
        assert s.encryption_key == key

    def test_encryption_key_env_takes_precedence(self, tmp_path):
        explicit_key = Fernet.generate_key().decode()
        key_file = tmp_path / "encryption.key"
        s = Settings(
            encryption_key=explicit_key,
            encryption_key_file=str(key_file),
            jwt_secret_key="test-secret",
            database_url=SQLITE_URL,
        )
        assert s.encryption_key == explicit_key
        assert not key_file.exists()

    def test_jwt_secret_key_auto_generated(self, tmp_path):
        key_file = tmp_path / "jwt.key"
        s = Settings(
            encryption_key=Fernet.generate_key().decode(),
            jwt_secret_key="",  # override env — test the auto-generate path
            jwt_secret_key_file=str(key_file),
            database_url=SQLITE_URL,
        )
        assert s.jwt_secret_key
        assert key_file.exists()
        assert key_file.read_text().strip() == s.jwt_secret_key

    def test_key_file_unwritable_raises_clear_error(self, tmp_path):
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o555)
        key_file = readonly_dir / "encryption.key"
        try:
            with pytest.raises((ValidationError, ValueError), match="ENCRYPTION_KEY"):
                Settings(
                    encryption_key="",  # override env — force the write attempt
                    encryption_key_file=str(key_file),
                    jwt_secret_key="test-secret",
                    database_url=SQLITE_URL,
                )
        finally:
            readonly_dir.chmod(0o755)  # restore so tmp_path cleanup works
