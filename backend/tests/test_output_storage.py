"""Tests for the OutputStorage abstraction (SFBL-160, SFBL-174).

Covers:
- LocalOutputStorage.write_bytes: creates directory tree, writes bytes, returns relative path.
- LocalOutputStorage.open_writer: multi-chunk writes, parent dir creation, .tmp cleanup, atomic rename.
- S3OutputStorage.write_bytes: correct key, s3:// URI, root_prefix handling, ClientError wrapping.
- S3OutputStorage.open_writer: multipart upload completion, abort on exception, empty stream.
- Factory: None → local; valid S3 connection (direction=out) → S3; missing ID → error; direction=in → error.
"""

from __future__ import annotations

import asyncio
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


def _run_async(coro_or_gen):
    """Run a coroutine on a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_or_gen)
    finally:
        loop.close()


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


# ── LocalOutputStorage.open_writer ───────────────────────────────────────────


def test_local_open_writer_writes_correct_bytes(tmp_path):
    """Multiple chunks written via open_writer produce the expected file contents."""

    async def _run():
        storage = LocalOutputStorage(str(tmp_path))
        rel = "run-1/step-1/results.csv"
        async with storage.open_writer(rel) as w:
            await w.write(b"header,col\n")
            await w.write(b"1,2\n")
            await w.write(b"3,4\n")
        return (tmp_path / rel).read_bytes()

    result = _run_async(_run())
    assert result == b"header,col\n1,2\n3,4\n"


def test_local_open_writer_creates_parent_dirs(tmp_path):
    """open_writer creates nested parent directories automatically."""

    async def _run():
        storage = LocalOutputStorage(str(tmp_path))
        rel = "deep/nested/dir/output.csv"
        async with storage.open_writer(rel) as w:
            await w.write(b"x")

    _run_async(_run())
    assert (tmp_path / "deep/nested/dir/output.csv").exists()


def test_local_open_writer_atomic_rename_no_tmp_on_success(tmp_path):
    """On successful close the final file exists and no .tmp remains."""

    async def _run():
        storage = LocalOutputStorage(str(tmp_path))
        rel = "run-2/result.csv"
        async with storage.open_writer(rel) as w:
            await w.write(b"data\n")

    _run_async(_run())
    final = tmp_path / "run-2/result.csv"
    tmp = tmp_path / "run-2/result.csv.tmp"
    assert final.exists()
    assert not tmp.exists()


def test_local_open_writer_tmp_cleaned_up_on_exception(tmp_path):
    """On exception inside the writer block the .tmp file is deleted and error propagates."""

    async def _run():
        storage = LocalOutputStorage(str(tmp_path))
        rel = "run-3/result.csv"
        with pytest.raises(ValueError, match="boom"):
            async with storage.open_writer(rel) as w:
                await w.write(b"partial")
                raise ValueError("boom")

    _run_async(_run())
    final = tmp_path / "run-3/result.csv"
    tmp = tmp_path / "run-3/result.csv.tmp"
    assert not final.exists()
    assert not tmp.exists()


def test_local_open_writer_empty_stream(tmp_path):
    """Writing zero bytes creates an empty file (no error)."""

    async def _run():
        storage = LocalOutputStorage(str(tmp_path))
        rel = "run-4/empty.csv"
        async with storage.open_writer(rel) as w:
            pass  # write nothing

    _run_async(_run())
    assert (tmp_path / "run-4/empty.csv").read_bytes() == b""


# ── S3OutputStorage.open_writer ───────────────────────────────────────────────


class _FakeMultipartS3Client:
    """Minimal S3 client stub that implements the multipart upload API."""

    def __init__(self) -> None:
        self._upload_id_counter = 0
        self._uploads: dict[str, dict] = {}  # upload_id → {"parts": [...], "aborted": bool}
        self.objects: dict[str, bytes] = {}  # key → assembled bytes

    # Existing write_bytes path
    def upload_fileobj(self, fileobj: io.IOBase, bucket: str, key: str, **_kwargs) -> None:
        self.objects[key] = fileobj.read()

    # Multipart
    def create_multipart_upload(self, *, Bucket: str, Key: str, **_kw) -> dict:
        self._upload_id_counter += 1
        uid = f"uid-{self._upload_id_counter}"
        self._uploads[uid] = {"key": Key, "bucket": Bucket, "parts": [], "aborted": False}
        return {"UploadId": uid}

    def upload_part(self, *, Bucket: str, Key: str, UploadId: str, PartNumber: int, Body: bytes, **_kw) -> dict:
        assert UploadId in self._uploads, f"Unknown UploadId: {UploadId}"
        self._uploads[UploadId]["parts"].append((PartNumber, Body))
        return {"ETag": f"etag-{PartNumber}"}

    def complete_multipart_upload(
        self, *, Bucket: str, Key: str, UploadId: str, MultipartUpload: dict, **_kw
    ) -> dict:
        upload = self._uploads[UploadId]
        # Sort parts by PartNumber and assemble
        sorted_parts = sorted(upload["parts"], key=lambda t: t[0])
        self.objects[Key] = b"".join(body for _, body in sorted_parts)
        return {"Location": f"https://{Bucket}.s3.amazonaws.com/{Key}", "ETag": "etag-final"}

    def abort_multipart_upload(self, *, Bucket: str, Key: str, UploadId: str, **_kw) -> None:
        self._uploads[UploadId]["aborted"] = True

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}


def _make_s3_multipart_storage(fake_client, *, bucket="test-bucket", root_prefix=None):
    """Create an S3OutputStorage backed by a fake multipart-capable client."""
    with patch("app.services.output_storage.boto3.client", return_value=fake_client):
        storage = S3OutputStorage(
            bucket=bucket,
            root_prefix=root_prefix,
            region="us-east-1",
            access_key_id="AKID",
            secret_access_key="SAK",
        )
    return storage


def test_s3_open_writer_multi_chunk_upload_completes(tmp_path):
    """Multiple chunks (one of which exceeds 5 MiB) triggers multi-part upload and completes."""
    from app.services.output_storage import _S3_MIN_PART_SIZE

    client = _FakeMultipartS3Client()
    storage = _make_s3_multipart_storage(client, bucket="my-bucket")
    rel = "run-1/step-1/results.csv"

    # Send two parts: first exactly fills the min part size, second is small
    chunk1 = b"a" * _S3_MIN_PART_SIZE
    chunk2 = b"b" * 100

    async def _run():
        async with storage.open_writer(rel) as w:
            await w.write(chunk1)
            await w.write(chunk2)

    _run_async(_run())

    # The assembled object should have both chunks in order
    assert client.objects[rel] == chunk1 + chunk2
    # There should be 2 parts (one flushed mid-stream, one final)
    uid = "uid-1"
    assert not client._uploads[uid]["aborted"]
    assert len(client._uploads[uid]["parts"]) == 2


def test_s3_open_writer_exception_aborts_multipart():
    """An exception inside the writer block triggers abort_multipart_upload."""
    client = _FakeMultipartS3Client()
    storage = _make_s3_multipart_storage(client, bucket="my-bucket")
    rel = "run-2/step-1/results.csv"

    async def _run():
        with pytest.raises(RuntimeError, match="oops"):
            async with storage.open_writer(rel) as w:
                await w.write(b"some data")
                raise RuntimeError("oops")

    _run_async(_run())

    uid = "uid-1"
    assert client._uploads[uid]["aborted"]
    assert rel not in client.objects


def test_s3_open_writer_empty_stream_produces_valid_object():
    """Writing zero bytes still results in a completed (empty) object."""
    client = _FakeMultipartS3Client()
    storage = _make_s3_multipart_storage(client, bucket="my-bucket")
    rel = "run-3/empty.csv"

    async def _run():
        async with storage.open_writer(rel) as w:
            pass  # write nothing

    _run_async(_run())

    # The key should exist with empty content
    assert rel in client.objects
    assert client.objects[rel] == b""
    uid = "uid-1"
    assert not client._uploads[uid]["aborted"]


def test_s3_open_writer_small_payload_single_part():
    """A payload smaller than 5 MiB is handled as a single final part."""
    client = _FakeMultipartS3Client()
    storage = _make_s3_multipart_storage(client, bucket="my-bucket")
    rel = "run-4/small.csv"
    data = b"col1,col2\n1,2\n"

    async def _run():
        async with storage.open_writer(rel) as w:
            await w.write(data)

    _run_async(_run())

    assert client.objects[rel] == data
    uid = "uid-1"
    # Only one part (the final flush)
    assert len(client._uploads[uid]["parts"]) == 1
    assert not client._uploads[uid]["aborted"]


def test_s3_open_writer_with_root_prefix():
    """root_prefix is prepended to the key in the multipart upload."""
    client = _FakeMultipartS3Client()
    storage = _make_s3_multipart_storage(client, bucket="b", root_prefix="results/")
    rel = "run-5/output.csv"

    async def _run():
        async with storage.open_writer(rel) as w:
            await w.write(b"hello")

    _run_async(_run())

    expected_key = f"results/{rel}"
    assert expected_key in client.objects
    assert client.objects[expected_key] == b"hello"
