"""Tests for the LocalInputStorage service and detect_encoding utility."""

import csv
import io
import pathlib
from unittest.mock import patch

import pytest

from app.models.input_connection import InputConnection
from app.services.input_storage import (
    InputStorageError,
    LocalInputStorage,
    S3InputStorage,
    detect_encoding,
    get_storage,
)
from app.utils.encryption import encrypt_secret


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_csv(path: str, rows: int = 3) -> None:
    """Write a minimal CSV with a header and *rows* data rows."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Value"])
        for i in range(rows):
            writer.writerow([f"item_{i}", str(i)])


# ── detect_encoding ───────────────────────────────────────────────────────────


def test_detect_encoding_utf8(tmp_path):
    f = tmp_path / "utf8.csv"
    f.write_bytes("Name,Value\nAlpha,1\n".encode("utf-8"))
    assert detect_encoding(f) == "utf-8-sig"


def test_detect_encoding_utf8_bom(tmp_path):
    f = tmp_path / "bom.csv"
    f.write_bytes("Name,Value\nAlpha,1\n".encode("utf-8-sig"))
    assert detect_encoding(f) == "utf-8-sig"


def test_detect_encoding_cp1252(tmp_path):
    f = tmp_path / "cp1252.csv"
    # Byte 0x80 is the Euro sign in cp1252; it is invalid in utf-8.
    f.write_bytes(b"Name\nCaf\x80\n")
    assert detect_encoding(f) == "cp1252"


def test_detect_encoding_latin1(tmp_path):
    f = tmp_path / "latin1.csv"
    # Byte 0x81 is undefined in cp1252 (raises UnicodeDecodeError) but valid in
    # latin-1 (which maps all 256 byte values).  Forces the latin-1 fallback.
    f.write_bytes(b"Name\n\x81\n")
    assert detect_encoding(f) == "latin-1"


# ── Path safety ───────────────────────────────────────────────────────────────


def test_safe_path_rejects_double_dot(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage._safe_path("../outside") is None


def test_safe_path_rejects_embedded_double_dot(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage._safe_path("sub/../../outside") is None


def test_safe_path_accepts_valid_relative(tmp_path):
    (tmp_path / "sub").mkdir()
    storage = LocalInputStorage(str(tmp_path))
    result = storage._safe_path("sub")
    assert result is not None
    assert result.is_dir()


# ── list_entries ──────────────────────────────────────────────────────────────


def test_list_entries_empty_dir(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage.list_entries() == []


def test_list_entries_returns_csvs_only(tmp_path):
    _write_csv(str(tmp_path / "accounts.csv"))
    (tmp_path / "readme.txt").write_text("ignore me")
    storage = LocalInputStorage(str(tmp_path))
    entries = storage.list_entries()
    names = [e.name for e in entries]
    assert "accounts.csv" in names
    assert "readme.txt" not in names


def test_list_entries_directories_first(tmp_path):
    (tmp_path / "zfolder").mkdir()
    _write_csv(str(tmp_path / "accounts.csv"))
    storage = LocalInputStorage(str(tmp_path))
    entries = storage.list_entries()
    assert entries[0].kind == "directory"
    assert entries[1].kind == "file"


def test_list_entries_dotfiles_excluded(tmp_path):
    (tmp_path / ".hidden").mkdir()
    _write_csv(str(tmp_path / "visible.csv"))
    storage = LocalInputStorage(str(tmp_path))
    names = [e.name for e in storage.list_entries()]
    assert ".hidden" not in names
    assert "visible.csv" in names


def test_list_entries_has_size_and_row_count(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=5)
    storage = LocalInputStorage(str(tmp_path))
    entry = storage.list_entries()[0]
    assert entry.size_bytes is not None and entry.size_bytes > 0
    assert entry.row_count == 5


def test_list_entries_subdirectory(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _write_csv(str(sub / "deep.csv"), rows=2)
    storage = LocalInputStorage(str(tmp_path))
    entries = storage.list_entries("sub")
    assert len(entries) == 1
    assert entries[0].name == "deep.csv"
    assert entries[0].path == "sub/deep.csv"
    assert entries[0].row_count == 2


def test_list_entries_traversal_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.list_entries("../outside")


def test_list_entries_nonexistent_path_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.list_entries("nonexistent")


def test_list_entries_missing_base_returns_empty(tmp_path):
    storage = LocalInputStorage(str(tmp_path / "does_not_exist"))
    assert storage.list_entries() == []


# ── preview_file ──────────────────────────────────────────────────────────────


def test_preview_file_returns_header_and_rows(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=20)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=5)
    assert preview.filename == "data.csv"
    assert preview.header == ["Name", "Value"]
    assert len(preview.rows) == 5
    assert preview.row_count == 5
    assert preview.offset == 0
    assert preview.limit == 5
    assert preview.has_next is True
    assert preview.total_rows is None
    assert preview.filtered_rows is None


def test_preview_file_respects_row_limit(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=3)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=100)
    assert len(preview.rows) == 3  # only 3 data rows exist
    assert preview.has_next is False


def test_preview_file_traversal_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.preview_file("../secret.csv", limit=10)


def test_preview_file_not_found_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        storage.preview_file("nonexistent.csv", limit=10)


def test_preview_file_cp1252_encoding(tmp_path):
    """Files encoded as cp1252 should preview without errors."""
    csv_path = tmp_path / "cp1252.csv"
    # Write header + 1 row with a cp1252-specific byte
    csv_path.write_bytes(b"Name\nCaf\x80\n")
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("cp1252.csv", limit=10)
    assert preview.header == ["Name"]
    assert len(preview.rows) == 1


def test_preview_file_latin1_encoding(tmp_path):
    """Files with unmapped cp1252 bytes should fall back to latin-1."""
    csv_path = tmp_path / "latin1.csv"
    csv_path.write_bytes(b"Name\n\x81\n")
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("latin1.csv", limit=10)
    assert preview.header == ["Name"]
    assert len(preview.rows) == 1


# ── preview_file pagination ────────────────────────────────────────────────────


def test_preview_unfiltered_has_next_true(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=10)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=5, offset=0)
    assert len(preview.rows) == 5
    assert preview.has_next is True
    assert preview.offset == 0
    assert preview.limit == 5


def test_preview_unfiltered_last_page_has_next_false(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=10)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=5, offset=5)
    assert len(preview.rows) == 5
    assert preview.has_next is False


def test_preview_unfiltered_middle_page(tmp_path):
    """Rows at offset 5 of a 15-row file should be rows 6–10."""
    path = str(tmp_path / "data.csv")
    # Write CSV with predictable Name values: row-0, row-1, …
    with open(path, "w", newline="") as fh:
        import csv as _csv
        w = _csv.writer(fh)
        w.writerow(["Name", "Value"])
        for i in range(15):
            w.writerow([f"row-{i}", str(i)])
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=5, offset=5)
    assert len(preview.rows) == 5
    assert preview.rows[0]["Name"] == "row-5"
    assert preview.rows[4]["Name"] == "row-9"
    assert preview.has_next is True


def test_preview_unfiltered_offset_beyond_file(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=3)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=10, offset=100)
    assert preview.rows == []
    assert preview.has_next is False


def test_preview_unfiltered_exact_limit_fit(tmp_path):
    """When file row count equals limit exactly, has_next must be False."""
    _write_csv(str(tmp_path / "data.csv"), rows=5)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=5, offset=0)
    assert len(preview.rows) == 5
    assert preview.has_next is False


# ── preview_file filtering ─────────────────────────────────────────────────────


def _write_named_csv(path: str, names: list[str]) -> None:
    """Write a CSV with Name and Value columns where Name is taken from *names*."""
    import csv as _csv
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["Name", "Value"])
        for i, name in enumerate(names):
            w.writerow([name, str(i)])


def test_preview_filtered_matches_subset(tmp_path):
    _write_named_csv(str(tmp_path / "data.csv"), ["Acme", "Beta", "Acme Corp", "Gamma"])
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=10, filters=[{"column": "Name", "value": "Acme"}])
    assert preview.filtered_rows == 2
    assert len(preview.rows) == 2
    assert all("Acme" in r["Name"] for r in preview.rows)
    assert preview.has_next is False


def test_preview_filtered_no_matches(tmp_path):
    _write_named_csv(str(tmp_path / "data.csv"), ["Alpha", "Beta", "Gamma"])
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=10, filters=[{"column": "Name", "value": "Zzz"}])
    assert preview.filtered_rows == 0
    assert preview.rows == []
    assert preview.has_next is False


def test_preview_filtered_case_insensitive(tmp_path):
    _write_named_csv(str(tmp_path / "data.csv"), ["acme", "BETA", "Acme Corp"])
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=10, filters=[{"column": "Name", "value": "ACME"}])
    assert preview.filtered_rows == 2
    assert len(preview.rows) == 2


def test_preview_filtered_multi_column_and(tmp_path):
    """Only rows matching ALL filters should be returned."""
    import csv as _csv
    path = str(tmp_path / "data.csv")
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["Name", "Status"])
        w.writerow(["Acme", "open"])    # matches both filters
        w.writerow(["Acme", "closed"])  # Name matches but Status does not
        w.writerow(["Beta", "open"])    # Status matches but Name does not
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file(
        "data.csv",
        limit=10,
        filters=[{"column": "Name", "value": "Acme"}, {"column": "Status", "value": "open"}],
    )
    assert preview.filtered_rows == 1
    assert preview.rows[0]["Name"] == "Acme"
    assert preview.rows[0]["Status"] == "open"


def test_preview_filtered_total_rows_populated(tmp_path):
    """total_rows should reflect the full file row count from the filtered scan."""
    _write_named_csv(str(tmp_path / "data.csv"), ["Acme", "Beta", "Gamma", "Acme2"])
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", limit=10, filters=[{"column": "Name", "value": "Acme"}])
    assert preview.total_rows == 4  # full file has 4 data rows


def test_preview_filtered_pagination_window(tmp_path):
    """offset and limit should slice the matched rows correctly."""
    names = [f"Acme-{i}" for i in range(10)] + ["Beta"] * 5
    _write_named_csv(str(tmp_path / "data.csv"), names)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file(
        "data.csv", limit=3, offset=3, filters=[{"column": "Name", "value": "Acme"}]
    )
    assert preview.filtered_rows == 10
    assert len(preview.rows) == 3
    assert preview.rows[0]["Name"] == "Acme-3"
    assert preview.rows[2]["Name"] == "Acme-5"
    assert preview.has_next is True


def test_preview_filtered_has_next_false_at_last_page(tmp_path):
    names = [f"Acme-{i}" for i in range(5)]
    _write_named_csv(str(tmp_path / "data.csv"), names)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file(
        "data.csv", limit=3, offset=3, filters=[{"column": "Name", "value": "Acme"}]
    )
    assert len(preview.rows) == 2
    assert preview.has_next is False


def test_preview_filter_unknown_column_raises(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=3)
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError, match="not present in the file header"):
        storage.preview_file("data.csv", limit=10, filters=[{"column": "NoSuchCol", "value": "x"}])


def test_preview_filter_blank_column_raises(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=3)
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError, match="must not be blank"):
        storage.preview_file("data.csv", limit=10, filters=[{"column": "", "value": "x"}])


def test_preview_filter_duplicate_column_raises(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=3)
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError, match="Duplicate filter column"):
        storage.preview_file(
            "data.csv",
            limit=10,
            filters=[{"column": "Name", "value": "a"}, {"column": "Name", "value": "b"}],
        )


# ── discover_files ────────────────────────────────────────────────────────────


def test_discover_files_matches_pattern(tmp_path):
    for name in ("accounts_1.csv", "accounts_2.csv", "contacts.csv"):
        _write_csv(str(tmp_path / name))
    storage = LocalInputStorage(str(tmp_path))
    found = storage.discover_files("accounts_*.csv")
    assert len(found) == 2
    assert all(path.startswith("accounts_") for path in found)


def test_discover_files_sorted(tmp_path):
    for name in ("c.csv", "a.csv", "b.csv"):
        _write_csv(str(tmp_path / name))
    storage = LocalInputStorage(str(tmp_path))
    found = storage.discover_files("*.csv")
    assert found == ["a.csv", "b.csv", "c.csv"]


def test_discover_files_regular_files_only(tmp_path):
    _write_csv(str(tmp_path / "real.csv"))
    (tmp_path / "subdir.csv").mkdir()  # directory masquerading as CSV name
    storage = LocalInputStorage(str(tmp_path))
    found = storage.discover_files("*.csv")
    assert found == ["real.csv"]


def test_discover_files_traversal_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.discover_files("../outside/*.csv")


def test_discover_files_no_match_returns_empty(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage.discover_files("nonexistent_*.csv") == []


# ── S3InputStorage ────────────────────────────────────────────────────────────


class _FakeS3Body:
    def __init__(self, data: bytes) -> None:
        self._io = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._io.read() if n == -1 else self._io.read(n)


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_kwargs):
        return list(self._pages)


class _FakeS3Client:
    def __init__(self, *, list_response=None, object_map=None, pages=None) -> None:
        self._list_response = list_response or {}
        self._object_map = object_map or {}
        self._pages = pages or []

    def list_objects_v2(self, **_kwargs):
        return self._list_response

    def get_object(self, *, Bucket, Key):
        if Key not in self._object_map:
            error = {"Error": {"Code": "NoSuchKey", "Message": f"{Key} missing"}}
            from botocore.exceptions import ClientError

            raise ClientError(error, "GetObject")
        return {"Body": _FakeS3Body(self._object_map[Key])}

    def get_paginator(self, _name):
        return _FakePaginator(self._pages)


class _RaisingPaginator:
    def __init__(self, exc) -> None:
        self._exc = exc

    def paginate(self, **_kwargs):
        raise self._exc


class _ErroringS3Client(_FakeS3Client):
    def __init__(self, *, list_error=None, get_error=None, paginator_error=None) -> None:
        super().__init__()
        self._list_error = list_error
        self._get_error = get_error
        self._paginator_error = paginator_error

    def list_objects_v2(self, **_kwargs):
        if self._list_error is not None:
            raise self._list_error
        return super().list_objects_v2(**_kwargs)

    def get_object(self, *, Bucket, Key):
        if self._get_error is not None:
            raise self._get_error
        return super().get_object(Bucket=Bucket, Key=Key)

    def get_paginator(self, _name):
        if self._paginator_error is not None:
            return _RaisingPaginator(self._paginator_error)
        return super().get_paginator(_name)


def test_s3_list_entries_returns_directories_then_csvs():
    client = _FakeS3Client(
        list_response={
            "CommonPrefixes": [{"Prefix": "data/subdir/"}, {"Prefix": "data/archive/"}],
            "Contents": [
                {"Key": "data/accounts.csv", "Size": 123},
                {"Key": "data/readme.txt", "Size": 5},
                {"Key": "data/", "Size": 0},
            ],
        }
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        entries = storage.list_entries()

    assert [(e.kind, e.path) for e in entries] == [
        ("directory", "archive"),
        ("directory", "subdir"),
        ("file", "accounts.csv"),
    ]
    assert entries[-1].size_bytes == 123
    assert entries[-1].row_count is None


def test_s3_list_entries_subdirectory_path():
    client = _FakeS3Client(
        list_response={
            "CommonPrefixes": [{"Prefix": "data/subdir/deeper/"}],
            "Contents": [
                {"Key": "data/subdir/accounts.csv", "Size": 42},
                {"Key": "data/subdir/notes.txt", "Size": 7},
            ],
        }
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        entries = storage.list_entries("subdir")

    assert [(e.kind, e.path) for e in entries] == [
        ("directory", "subdir/deeper"),
        ("file", "subdir/accounts.csv"),
    ]


def test_s3_list_entries_client_error_raises_input_storage_error():
    from botocore.exceptions import ClientError

    client = _ErroringS3Client(
        list_error=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "ListObjectsV2",
        )
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        with pytest.raises(InputStorageError, match="Could not list S3 path"):
            storage.list_entries()


def test_s3_preview_file_returns_rows():
    client = _FakeS3Client(
        object_map={"data/accounts.csv": b"Name,Value\nAcme,1\nBeta,2\n"}
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        preview = storage.preview_file("accounts.csv", limit=1)

    assert preview.filename == "accounts.csv"
    assert preview.header == ["Name", "Value"]
    assert preview.row_count == 1
    assert preview.rows[0]["Name"] == "Acme"
    assert preview.has_next is True
    assert preview.offset == 0
    assert preview.limit == 1
    assert preview.total_rows is None
    assert preview.filtered_rows is None


def test_s3_preview_file_missing_object_raises_file_not_found():
    from botocore.exceptions import ClientError

    client = _ErroringS3Client(
        get_error=ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
            "GetObject",
        )
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        with pytest.raises(FileNotFoundError, match="File not found"):
            storage.preview_file("accounts.csv", limit=5)


def test_s3_discover_files_matches_glob():
    client = _FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "data/accounts/a.csv"},
                    {"Key": "data/accounts/b.csv"},
                    {"Key": "data/contacts/c.csv"},
                    {"Key": "data/root.csv"},
                ]
            }
        ]
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        found = storage.discover_files("accounts/*.csv")
        recursive_found = storage.discover_files("**/*.csv")

    assert found == ["accounts/a.csv", "accounts/b.csv"]
    assert recursive_found == [
        "accounts/a.csv",
        "accounts/b.csv",
        "contacts/c.csv",
        "root.csv",
    ]


def test_s3_discover_files_client_error_raises_input_storage_error():
    from botocore.exceptions import ClientError

    client = _ErroringS3Client(
        paginator_error=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "ListObjectsV2",
        )
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        with pytest.raises(InputStorageError, match="Could not discover S3 files"):
            storage.discover_files("**/*.csv")


def test_s3_open_text_uses_detected_encoding():
    client = _FakeS3Client(object_map={"data/cafe.csv": b"Name\nCaf\x80\n"})
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        with storage.open_text("cafe.csv") as fh:
            content = fh.read()

    assert "Caf" in content


@pytest.mark.asyncio
async def test_get_storage_returns_local_for_local_source():
    storage = await get_storage("local", db=None)
    assert isinstance(storage, LocalInputStorage)


@pytest.mark.asyncio
async def test_get_storage_returns_s3_for_input_connection():
    ic = InputConnection(
        id="ic-1",
        name="S3",
        provider="s3",
        bucket="bucket",
        root_prefix="data",
        region="us-east-1",
        access_key_id=encrypt_secret("ak"),
        secret_access_key=encrypt_secret("sk"),
        session_token=encrypt_secret("st"),
    )

    class _FakeDB:
        async def get(self, model, key):
            assert model is InputConnection
            assert key == "ic-1"
            return ic

    with patch("app.services.input_storage.boto3.client", return_value=_FakeS3Client()):
        storage = await get_storage("ic-1", db=_FakeDB())

    assert isinstance(storage, S3InputStorage)


# ── S3InputStorage — streaming open_text ─────────────────────────────────────


def test_s3_open_text_does_not_load_entire_object_before_returning():
    """open_text must not call body.read() without a size limit."""

    class _GuardedBody:
        def __init__(self, data: bytes) -> None:
            self._io = io.BytesIO(data)

        def read(self, n: int = -1) -> bytes:
            if n == -1:
                raise AssertionError(
                    "open_text called body.read() without a size limit"
                )
            return self._io.read(n)

    class _GuardedClient(_FakeS3Client):
        def get_object(self, *, Bucket, Key):
            return {"Body": _GuardedBody(b"Name\nAlice\n")}

    with patch("app.services.input_storage.boto3.client", return_value=_GuardedClient()):
        storage = S3InputStorage(
            bucket="b",
            root_prefix=None,
            region=None,
            access_key_id="ak",
            secret_access_key="sk",
        )
        with storage.open_text("file.csv") as fh:
            content = fh.read()

    assert "Alice" in content


def test_s3_open_text_streaming_full_content():
    """open_text returns the complete object content when read to end."""
    data = b"Name,Val\nAlice,1\nBob,2\n"
    client = _FakeS3Client(object_map={"file.csv": data})
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="b",
            root_prefix=None,
            region=None,
            access_key_id="ak",
            secret_access_key="sk",
        )
        with storage.open_text("file.csv") as fh:
            content = fh.read()

    assert "Alice" in content
    assert "Bob" in content


@pytest.mark.asyncio
async def test_get_storage_missing_source_raises():
    class _FakeDB:
        async def get(self, _model, _key):
            return None

    with pytest.raises(InputStorageError, match="Input connection not found"):
        await get_storage("missing", db=_FakeDB())


@pytest.mark.asyncio
async def test_get_storage_unsupported_provider_raises():
    ic = InputConnection(
        id="ic-1",
        name="Other",
        provider="gcs",
        bucket="bucket",
        root_prefix=None,
        region=None,
        access_key_id=encrypt_secret("ak"),
        secret_access_key=encrypt_secret("sk"),
        session_token=None,
    )

    class _FakeDB:
        async def get(self, _model, _key):
            return ic

    with pytest.raises(InputStorageError, match="Unsupported input connection provider"):
        await get_storage("ic-1", db=_FakeDB())


# ── S3 preview_file pagination and filtering ──────────────────────────────────


def _make_s3_storage(object_map: dict):
    """Return a patched S3InputStorage backed by *object_map* bytes."""
    client = _FakeS3Client(object_map=object_map)
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
    # Re-enter patch context for method calls
    patcher = patch("app.services.input_storage.boto3.client", return_value=client)
    patcher.start()
    return storage, patcher


def test_s3_preview_unfiltered_has_next_true():
    client = _FakeS3Client(
        object_map={"data/file.csv": b"Name,Value\nAlpha,1\nBeta,2\nGamma,3\n"}
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        preview = storage.preview_file("file.csv", limit=1, offset=0)

    assert len(preview.rows) == 1
    assert preview.rows[0]["Name"] == "Alpha"
    assert preview.has_next is True
    assert preview.total_rows is None
    assert preview.filtered_rows is None


def test_s3_preview_unfiltered_pagination_offset():
    client = _FakeS3Client(
        object_map={"data/file.csv": b"Name,Value\nAlpha,1\nBeta,2\nGamma,3\n"}
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        preview = storage.preview_file("file.csv", limit=1, offset=1)

    assert len(preview.rows) == 1
    assert preview.rows[0]["Name"] == "Beta"
    assert preview.has_next is True


def test_s3_preview_unfiltered_last_page_has_next_false():
    client = _FakeS3Client(
        object_map={"data/file.csv": b"Name,Value\nAlpha,1\nBeta,2\nGamma,3\n"}
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        preview = storage.preview_file("file.csv", limit=2, offset=1)

    assert len(preview.rows) == 2
    assert preview.rows[0]["Name"] == "Beta"
    assert preview.has_next is False


def test_s3_preview_filtered_matches():
    client = _FakeS3Client(
        object_map={"data/file.csv": b"Name,Value\nAcme,1\nBeta,2\nAcme Corp,3\n"}
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        preview = storage.preview_file(
            "file.csv", limit=10, filters=[{"column": "Name", "value": "Acme"}]
        )

    assert preview.filtered_rows == 2
    assert len(preview.rows) == 2
    assert all("Acme" in r["Name"] for r in preview.rows)
    assert preview.has_next is False
    assert preview.total_rows == 3


def test_s3_preview_filtered_no_matches():
    client = _FakeS3Client(
        object_map={"data/file.csv": b"Name,Value\nAlpha,1\nBeta,2\n"}
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        preview = storage.preview_file(
            "file.csv", limit=10, filters=[{"column": "Name", "value": "Zzz"}]
        )

    assert preview.filtered_rows == 0
    assert preview.rows == []
    assert preview.has_next is False


def test_s3_preview_filter_unknown_column_raises():
    client = _FakeS3Client(
        object_map={"data/file.csv": b"Name,Value\nAlpha,1\n"}
    )
    with patch("app.services.input_storage.boto3.client", return_value=client):
        storage = S3InputStorage(
            bucket="bucket",
            root_prefix="data/",
            region="us-east-1",
            access_key_id="ak",
            secret_access_key="sk",
        )
        with pytest.raises(InputStorageError, match="not present in the file header"):
            storage.preview_file(
                "file.csv", limit=10, filters=[{"column": "NoSuchCol", "value": "x"}]
            )
