"""Tests for the OutputStorage abstraction (SFBL-160).

Covers:
- LocalOutputStorage.write_bytes: creates directory tree, writes bytes, returns relative path.
- S3OutputStorage.write_bytes: correct key, s3:// URI, root_prefix handling, ClientError wrapping.
- Factory: None → local; valid S3 connection (direction=out) → S3; missing ID → error; direction=in → error.
"""

from __future__ import annotations

import io
import pathlib
from unittest.mock import patch, MagicMock

import pytest
from botocore.exceptions import ClientError

from app.models.input_connection import InputConnection
from app.services.output_storage import (
    LocalOutputStorage,
    OutputConnectionNotFoundError,
    OutputStorageError,
    S3OutputStorage,
    UnsupportedOutputProviderError,
    get_output_storage,
)
from app.utils.encryption import encrypt_secret


# ── Fake S3 client ────────────────────────────────────────────────────────────


class _FakeS3Client:
    """Minimal boto3 S3 client stub that records upload_fileobj calls."""

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}  # key → bytes written

    def upload_fileobj(self, fileobj: io.IOBase, bucket: str, key: str, **_kwargs) -> None:
        self.uploads[key] = fileobj.read()

    def create_bucket(self, **_kwargs) -> None:
        pass

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.uploads:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "HeadObject"
            )
        return {"ContentLength": len(self.uploads[Key])}


class _ErroringS3Client(_FakeS3Client):
    """S3 client stub that raises ClientError on upload_fileobj."""

    def upload_fileobj(self, fileobj: io.IOBase, bucket: str, key: str, **_kwargs) -> None:
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
            "PutObject",
        )


# ── LocalOutputStorage ────────────────────────────────────────────────────────


def test_local_write_bytes_creates_directory_tree(tmp_path):
    storage = LocalOutputStorage(str(tmp_path))
    rel = "run-abc/step-1/partition_0_results.csv"
    storage.write_bytes(rel, b"col1,col2\nval1,val2\n")
    assert (tmp_path / rel).exists()


def test_local_write_bytes_writes_correct_bytes(tmp_path):
    storage = LocalOutputStorage(str(tmp_path))
    data = b"header\nrow1\nrow2\n"
    rel = "run-xyz/step-2/partition_1_errors.csv"
    storage.write_bytes(rel, data)
    assert (tmp_path / rel).read_bytes() == data


def test_local_write_bytes_returns_relative_path(tmp_path):
    storage = LocalOutputStorage(str(tmp_path))
    rel = "run-1/step-1/partition_0_success.csv"
    result = storage.write_bytes(rel, b"a,b\n1,2\n")
    assert result == rel


def test_local_write_bytes_nested_dirs_created(tmp_path):
    storage = LocalOutputStorage(str(tmp_path))
    rel = "a/b/c/partition_0_results.csv"
    storage.write_bytes(rel, b"x")
    assert (tmp_path / rel).exists()


# ── S3OutputStorage ───────────────────────────────────────────────────────────


def _make_s3_storage(fake_client, *, bucket="test-bucket", root_prefix=None):
    """Helper: create an S3OutputStorage backed by *fake_client*."""
    with patch("app.services.output_storage.boto3.client", return_value=fake_client):
        storage = S3OutputStorage(
            bucket=bucket,
            root_prefix=root_prefix,
            region="us-east-1",
            access_key_id="AKID",
            secret_access_key="SAK",
        )
    return storage


def test_s3_write_bytes_object_exists_at_expected_key():
    client = _FakeS3Client()
    storage = _make_s3_storage(client, bucket="my-bucket", root_prefix=None)
    rel = "run-1/step-1/partition_0_results.csv"
    storage.write_bytes(rel, b"header\nrow\n")
    assert rel in client.uploads


def test_s3_write_bytes_returns_s3_uri():
    client = _FakeS3Client()
    storage = _make_s3_storage(client, bucket="my-bucket", root_prefix=None)
    rel = "run-1/step-1/partition_0_results.csv"
    result = storage.write_bytes(rel, b"data")
    assert result == f"s3://my-bucket/{rel}"


def test_s3_write_bytes_with_root_prefix_key_is_prefixed():
    client = _FakeS3Client()
    storage = _make_s3_storage(client, bucket="my-bucket", root_prefix="results")
    rel = "run-2/step-3/partition_0_results.csv"
    result = storage.write_bytes(rel, b"data")
    expected_key = f"results/{rel}"
    assert expected_key in client.uploads
    assert result == f"s3://my-bucket/{expected_key}"


def test_s3_write_bytes_with_trailing_slash_prefix():
    client = _FakeS3Client()
    storage = _make_s3_storage(client, bucket="b", root_prefix="output/")
    rel = "run-3/step-1/partition_0_results.csv"
    result = storage.write_bytes(rel, b"x")
    expected_key = f"output/{rel}"
    assert expected_key in client.uploads
    assert result == f"s3://b/{expected_key}"


def test_s3_write_bytes_uploads_correct_data():
    client = _FakeS3Client()
    storage = _make_s3_storage(client, bucket="b")
    data = b"col1,col2\n1,2\n3,4\n"
    rel = "run-1/step-1/partition_0_results.csv"
    storage.write_bytes(rel, data)
    assert client.uploads[rel] == data


def test_s3_write_bytes_wraps_client_error_as_output_storage_error():
    client = _ErroringS3Client()
    storage = _make_s3_storage(client, bucket="b")
    with pytest.raises(OutputStorageError, match="Could not upload"):
        storage.write_bytes("run-1/step-1/partition_0.csv", b"data")


# ── Factory ───────────────────────────────────────────────────────────────────


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_db_with_connection(ic: InputConnection):
    """Return a mock AsyncSession that returns *ic* from db.get()."""
    db = MagicMock()

    async def _get(model, pk):
        if model is InputConnection and pk == ic.id:
            return ic
        return None

    db.get = _get
    return db


def _make_db_missing():
    """Return a mock AsyncSession that always returns None from db.get()."""
    db = MagicMock()

    async def _get(model, pk):
        return None

    db.get = _get
    return db


def test_factory_none_returns_local_storage(settings_output_dir=None):
    """get_output_storage(None, db) → LocalOutputStorage."""
    db = MagicMock()
    result = _run_async(get_output_storage(None, db))
    assert isinstance(result, LocalOutputStorage)


def test_factory_empty_string_returns_local_storage():
    """get_output_storage('', db) → LocalOutputStorage."""
    db = MagicMock()
    result = _run_async(get_output_storage("", db))
    assert isinstance(result, LocalOutputStorage)


def test_factory_valid_s3_connection_out_returns_s3_storage():
    """Valid S3 connection with direction='out' → S3OutputStorage."""
    ic = InputConnection(
        id="ic-001",
        name="S3 Out",
        provider="s3",
        bucket="output-bucket",
        root_prefix="results/",
        region="us-east-1",
        access_key_id=encrypt_secret("AKID"),
        secret_access_key=encrypt_secret("SAK"),
        session_token=None,
        direction="out",
    )
    db = _make_db_with_connection(ic)
    with patch("app.services.output_storage.boto3.client"):
        result = _run_async(get_output_storage("ic-001", db))
    assert isinstance(result, S3OutputStorage)


def test_factory_valid_s3_connection_both_returns_s3_storage():
    """S3 connection with direction='both' is also accepted."""
    ic = InputConnection(
        id="ic-002",
        name="S3 Both",
        provider="s3",
        bucket="shared-bucket",
        root_prefix=None,
        region="eu-west-1",
        access_key_id=encrypt_secret("AKID"),
        secret_access_key=encrypt_secret("SAK"),
        session_token=None,
        direction="both",
    )
    db = _make_db_with_connection(ic)
    with patch("app.services.output_storage.boto3.client"):
        result = _run_async(get_output_storage("ic-002", db))
    assert isinstance(result, S3OutputStorage)


def test_factory_missing_connection_raises_not_found():
    """Unknown connection ID → OutputConnectionNotFoundError."""
    db = _make_db_missing()
    with pytest.raises(OutputConnectionNotFoundError, match="not found"):
        _run_async(get_output_storage("no-such-id", db))


def test_factory_direction_in_raises_output_storage_error():
    """Connection with direction='in' → OutputStorageError."""
    ic = InputConnection(
        id="ic-003",
        name="S3 In Only",
        provider="s3",
        bucket="in-bucket",
        root_prefix=None,
        region="us-east-1",
        access_key_id=encrypt_secret("AKID"),
        secret_access_key=encrypt_secret("SAK"),
        session_token=None,
        direction="in",
    )
    db = _make_db_with_connection(ic)
    with pytest.raises(OutputStorageError, match="direction"):
        _run_async(get_output_storage("ic-003", db))


def test_factory_unsupported_provider_raises():
    """Connection with an unsupported provider → UnsupportedOutputProviderError."""
    ic = InputConnection(
        id="ic-004",
        name="GCS Out",
        provider="gcs",
        bucket="my-gcs-bucket",
        root_prefix=None,
        region=None,
        access_key_id=encrypt_secret("AKID"),
        secret_access_key=encrypt_secret("SAK"),
        session_token=None,
        direction="out",
    )
    db = _make_db_with_connection(ic)
    with pytest.raises(UnsupportedOutputProviderError, match="gcs"):
        _run_async(get_output_storage("ic-004", db))
