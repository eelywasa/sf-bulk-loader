"""Tests for health check, file listing/preview, and WebSocket endpoints."""

import csv
import os
import tempfile
from unittest.mock import patch

import pytest
import app.models  # noqa: F401
from app.services.input_storage import (
    UnsupportedInputProviderError,
)

_IC = {
    "name": "My S3 Bucket",
    "provider": "s3",
    "bucket": "my-bucket",
    "root_prefix": "data/",
    "region": "us-east-1",
    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}


class _FakeS3Body:
    def __init__(self, data: bytes) -> None:
        import io
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


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


class _ErroringS3Client(_FakeS3Client):
    def __init__(self, *, list_error=None, get_error=None) -> None:
        super().__init__()
        self._list_error = list_error
        self._get_error = get_error

    def list_objects_v2(self, **_kwargs):
        if self._list_error is not None:
            raise self._list_error
        return super().list_objects_v2(**_kwargs)

    def get_object(self, *, Bucket, Key):
        if self._get_error is not None:
            raise self._get_error
        return super().get_object(Bucket=Bucket, Key=Key)


def _create_input_connection(auth_client) -> str:
    resp = auth_client.post("/api/input-connections/", json=_IC)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class _PreviewOSErrorStorage:
    provider = "local"

    def preview_file(self, path: str, limit: int = 50, offset: int = 0, filters=None):
        raise OSError("disk error")

    def list_entries(self, path: str = ""):
        return []


# ── Runtime config ─────────────────────────────────────────────────────────────


def test_runtime_config_is_public(client):
    """/api/runtime requires no authentication."""
    resp = client.get("/api/runtime")
    assert resp.status_code == 200


def test_runtime_config_returns_expected_fields(client):
    """/api/runtime includes all required distribution profile fields."""
    resp = client.get("/api/runtime")
    body = resp.json()
    assert "auth_mode" in body
    assert "app_distribution" in body
    assert "transport_mode" in body
    assert "input_storage_mode" in body


def test_runtime_config_reflects_self_hosted_defaults(client):
    """Default test profile is self_hosted with auth_mode=local."""
    resp = client.get("/api/runtime")
    body = resp.json()
    assert body["app_distribution"] == "self_hosted"
    assert body["auth_mode"] == "local"


def test_runtime_config_reflects_desktop_profile(client):
    """/api/runtime returns correct values when distribution is desktop."""
    from unittest.mock import patch

    with patch("app.api.utility.settings") as mock_settings:
        mock_settings.auth_mode = "none"
        mock_settings.app_distribution = "desktop"
        mock_settings.transport_mode = "local"
        mock_settings.input_storage_mode = "local"
        resp = client.get("/api/runtime")

    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_mode"] == "none"
    assert body["app_distribution"] == "desktop"


# ── Health check ───────────────────────────────────────────────────────────────


def test_health_returns_ok(auth_client):
    resp = auth_client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "sf_api_version" in body


# ── Input file listing ─────────────────────────────────────────────────────────


def test_list_input_files_empty_dir(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_input_files_returns_csvs(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create two CSVs and a non-CSV file
        for name in ("accounts.csv", "contacts.csv", "readme.txt"):
            open(os.path.join(tmpdir, name), "w").close()

        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input")

    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()]
    assert "accounts.csv" in names
    assert "contacts.csv" in names
    assert "readme.txt" not in names
    assert all(f["source"] == "local" for f in resp.json())
    assert all(f["provider"] == "local" for f in resp.json())


def test_list_input_files_missing_dir_returns_empty(auth_client):
    with patch("app.services.input_storage.settings.input_dir", "/path/that/does/not/exist"):
        resp = auth_client.get("/api/files/input")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_input_files_returns_directory_entries(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "subdir"))
        open(os.path.join(tmpdir, "root.csv"), "w").close()
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input")
    assert resp.status_code == 200
    entries = resp.json()
    kinds = {e["name"]: e["kind"] for e in entries}
    assert kinds["subdir"] == "directory"
    assert kinds["root.csv"] == "file"


def test_list_input_files_directories_sorted_before_files(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "zfolder"))
        open(os.path.join(tmpdir, "accounts.csv"), "w").close()
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input")
    entries = resp.json()
    assert entries[0]["kind"] == "directory"
    assert entries[1]["kind"] == "file"


def test_list_input_files_with_path_param(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "sub"))
        csv_path = os.path.join(tmpdir, "sub", "deep.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Id", "Name"])
            writer.writerow(["1", "Alpha"])
            writer.writerow(["2", "Beta"])
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input?path=sub")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["name"] == "deep.csv"
    assert entries[0]["path"] == "sub/deep.csv"
    assert entries[0]["kind"] == "file"
    assert entries[0]["row_count"] == 2
    assert entries[0]["source"] == "local"
    assert entries[0]["provider"] == "local"


def test_list_input_files_includes_row_count(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "accounts.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Industry"])
            for i in range(5):
                writer.writerow([f"Acme {i}", "Tech"])
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input")
    assert resp.status_code == 200
    entry = resp.json()[0]
    assert entry["row_count"] == 5
    assert entry["source"] == "local"
    assert entry["provider"] == "local"


def test_list_input_files_source_local_matches_default(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "accounts.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Industry"])
            writer.writerow(["Acme", "Tech"])
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            default_resp = auth_client.get("/api/files/input")
            explicit_resp = auth_client.get("/api/files/input?source=local")

    assert default_resp.status_code == 200
    assert explicit_resp.status_code == 200
    assert default_resp.json() == explicit_resp.json()


def test_list_input_files_remote_source_returns_source_and_provider(auth_client):
    ic_id = _create_input_connection(auth_client)
    client = _FakeS3Client(
        list_response={
            "CommonPrefixes": [{"Prefix": "data/subdir/"}],
            "Contents": [{"Key": "data/accounts.csv", "Size": 123}],
        }
    )

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.get(f"/api/files/input?source={ic_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {
            "name": "subdir",
            "kind": "directory",
            "path": "subdir",
            "size_bytes": None,
            "row_count": None,
            "source": ic_id,
            "provider": "s3",
        },
        {
            "name": "accounts.csv",
            "kind": "file",
            "path": "accounts.csv",
            "size_bytes": 123,
            "row_count": None,
            "source": ic_id,
            "provider": "s3",
        },
    ]


def test_list_input_files_unknown_source_returns_404(auth_client):
    resp = auth_client.get("/api/files/input?source=missing-input-connection")
    assert resp.status_code == 404


def test_list_input_files_invalid_remote_path_returns_400(auth_client):
    ic_id = _create_input_connection(auth_client)
    client = _FakeS3Client()

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.get(f"/api/files/input?source={ic_id}&path=../etc")

    assert resp.status_code == 400


def test_list_input_files_remote_storage_error_returns_400(auth_client):
    from botocore.exceptions import ClientError

    ic_id = _create_input_connection(auth_client)
    client = _ErroringS3Client(
        list_error=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "ListObjectsV2",
        )
    )

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.get(f"/api/files/input?source={ic_id}")

    assert resp.status_code == 400
    assert "Could not list S3 path" in resp.json()["detail"]


def test_list_input_files_unsupported_provider_returns_400(auth_client):
    with patch(
        "app.api.utility.get_storage",
        side_effect=UnsupportedInputProviderError("Unsupported input connection provider: gcs"),
    ):
        resp = auth_client.get("/api/files/input?source=ic-unsupported")

    assert resp.status_code == 400
    assert "Unsupported input connection provider" in resp.json()["detail"]


def test_list_input_files_path_traversal_returns_400(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input?path=../etc")
    assert resp.status_code == 400


def test_list_input_files_nonexistent_path_returns_400(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input?path=nonexistent")
    assert resp.status_code == 400


def test_list_input_files_hidden_dirs_excluded(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".hidden"))
        open(os.path.join(tmpdir, "visible.csv"), "w").close()
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input")
    names = [e["name"] for e in resp.json()]
    assert ".hidden" not in names
    assert "visible.csv" in names


# ── File preview ───────────────────────────────────────────────────────────────


def test_preview_input_file_returns_rows(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "accounts.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Industry"])
            for i in range(20):
                writer.writerow([f"Acme {i}", "Tech"])

        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input/accounts.csv/preview?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "accounts.csv"
    assert body["header"] == ["Name", "Industry"]
    assert len(body["rows"]) == 5
    assert body["has_next"] is True   # file has 20 rows, limit=5
    assert body["offset"] == 0
    assert body["limit"] == 5
    assert body["total_rows"] is None
    assert body["filtered_rows"] is None
    assert body["source"] == "local"
    assert body["provider"] == "local"


def test_preview_input_file_not_found_returns_404(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input/nonexistent.csv/preview")
    assert resp.status_code == 404


def test_preview_input_file_path_traversal_returns_400(auth_client):
    resp = auth_client.get("/api/files/input/../secret.csv/preview")
    assert resp.status_code in (400, 404)


def test_preview_input_file_in_subdirectory(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        sub = os.path.join(tmpdir, "sub")
        os.makedirs(sub)
        csv_path = os.path.join(sub, "deep.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Id", "Name"])
            writer.writerow(["1", "Alpha"])

        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input/sub/deep.csv/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "sub/deep.csv"
    assert body["header"] == ["Id", "Name"]
    assert len(body["rows"]) == 1
    assert body["has_next"] is False   # only 1 data row
    assert body["offset"] == 0
    assert body["limit"] == 50  # default
    assert body["source"] == "local"
    assert body["provider"] == "local"


def test_preview_input_file_subdir_traversal_returns_400(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input/sub/../../etc/passwd/preview")
    assert resp.status_code in (400, 404)


def test_preview_input_file_source_local_matches_default(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "accounts.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Industry"])
            writer.writerow(["Acme", "Tech"])
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            default_resp = auth_client.get("/api/files/input/accounts.csv/preview?limit=5")
            explicit_resp = auth_client.get("/api/files/input/accounts.csv/preview?limit=5&source=local")

    assert default_resp.status_code == 200
    assert explicit_resp.status_code == 200
    assert default_resp.json() == explicit_resp.json()


def test_preview_input_file_remote_source_returns_source_and_provider(auth_client):
    ic_id = _create_input_connection(auth_client)
    client = _FakeS3Client(
        object_map={"data/accounts.csv": b"Name,Industry\nAcme,Tech\nBeta,Finance\n"}
    )

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.get(f"/api/files/input/accounts.csv/preview?limit=1&source={ic_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "accounts.csv"
    assert body["has_next"] is True   # 2 data rows, limit=1
    assert body["offset"] == 0
    assert body["limit"] == 1
    assert body["total_rows"] is None
    assert body["filtered_rows"] is None
    assert body["source"] == ic_id
    assert body["provider"] == "s3"


def test_preview_input_file_unknown_source_returns_404(auth_client):
    resp = auth_client.get("/api/files/input/accounts.csv/preview?source=missing-input-connection")
    assert resp.status_code == 404


def test_preview_input_file_remote_missing_object_returns_404(auth_client):
    ic_id = _create_input_connection(auth_client)
    client = _FakeS3Client()

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.get(f"/api/files/input/accounts.csv/preview?source={ic_id}")

    assert resp.status_code == 404


def test_preview_input_file_remote_storage_error_returns_400(auth_client):
    from botocore.exceptions import ClientError

    ic_id = _create_input_connection(auth_client)
    client = _ErroringS3Client(
        get_error=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "GetObject",
        )
    )

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.get(f"/api/files/input/accounts.csv/preview?source={ic_id}")

    assert resp.status_code == 400
    assert "Could not read S3 object" in resp.json()["detail"]


def test_preview_input_file_remote_invalid_path_returns_400(auth_client):
    ic_id = _create_input_connection(auth_client)
    client = _FakeS3Client()

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.get(f"/api/files/input/../secret.csv/preview?source={ic_id}")

    assert resp.status_code in (400, 404)


def test_preview_input_file_os_error_returns_500(auth_client):
    with patch("app.api.utility.get_storage", return_value=_PreviewOSErrorStorage()):
        resp = auth_client.get("/api/files/input/accounts.csv/preview")

    assert resp.status_code == 500
    assert "Could not read file" in resp.json()["detail"]


def test_preview_input_file_unsupported_provider_returns_400(auth_client):
    with patch(
        "app.api.utility.get_storage",
        side_effect=UnsupportedInputProviderError("Unsupported input connection provider: gcs"),
    ):
        resp = auth_client.get("/api/files/input/accounts.csv/preview?source=ic-unsupported")

    assert resp.status_code == 400
    assert "Unsupported input connection provider" in resp.json()["detail"]


def _write_preview_csv(path, rows=10):
    """Write a CSV with Name/Industry columns and `rows` data rows."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Industry"])
        for i in range(rows):
            writer.writerow([f"Acme {i}", "Tech"])


def test_preview_input_file_limit_param_controls_page_size(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_preview_csv(os.path.join(tmpdir, "data.csv"), rows=10)
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input/data.csv/preview?limit=3")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 3
    assert body["limit"] == 3
    assert body["has_next"] is True


def test_preview_input_file_has_next_false_on_last_page(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_preview_csv(os.path.join(tmpdir, "data.csv"), rows=5)
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input/data.csv/preview?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 5
    assert body["has_next"] is False


def test_preview_input_file_offset_returns_second_page(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "data.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name"])
            for i in range(5):
                writer.writerow([f"Row{i}"])
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get("/api/files/input/data.csv/preview?limit=2&offset=2")

    assert resp.status_code == 200
    body = resp.json()
    assert body["offset"] == 2
    assert body["limit"] == 2
    assert len(body["rows"]) == 2
    assert body["rows"][0]["Name"] == "Row2"
    assert body["rows"][1]["Name"] == "Row3"


def test_preview_input_file_filtered_request_returns_filtered_rows(auth_client):
    import urllib.parse

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "data.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Industry"])
            writer.writerow(["Acme Corp", "Tech"])
            writer.writerow(["Beta Inc", "Finance"])
            writer.writerow(["Acme Labs", "Research"])
        filters_json = urllib.parse.quote('[{"column":"Name","value":"Acme"}]')
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get(f"/api/files/input/data.csv/preview?filters={filters_json}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filtered_rows"] == 2
    assert len(body["rows"]) == 2
    assert all("Acme" in row["Name"] for row in body["rows"])
    assert body["has_next"] is False


def test_preview_input_file_filtered_no_matches_returns_empty(auth_client):
    import urllib.parse

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_preview_csv(os.path.join(tmpdir, "data.csv"), rows=5)
        filters_json = urllib.parse.quote('[{"column":"Name","value":"NoMatch"}]')
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get(f"/api/files/input/data.csv/preview?filters={filters_json}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["filtered_rows"] == 0
    assert body["has_next"] is False


def test_preview_input_file_malformed_filters_json_returns_400(auth_client):
    import urllib.parse

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_preview_csv(os.path.join(tmpdir, "data.csv"))
        bad = urllib.parse.quote("not-json")
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get(f"/api/files/input/data.csv/preview?filters={bad}")

    assert resp.status_code == 400
    assert "Invalid filters JSON" in resp.json()["detail"]


def test_preview_input_file_filters_not_array_returns_400(auth_client):
    import urllib.parse

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_preview_csv(os.path.join(tmpdir, "data.csv"))
        bad = urllib.parse.quote('{"column":"Name","value":"x"}')
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get(f"/api/files/input/data.csv/preview?filters={bad}")

    assert resp.status_code == 400
    assert "filters must be a JSON array" in resp.json()["detail"]


def test_preview_input_file_unknown_filter_column_returns_400(auth_client):
    import urllib.parse

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_preview_csv(os.path.join(tmpdir, "data.csv"))
        bad = urllib.parse.quote('[{"column":"Nonexistent","value":"x"}]')
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get(f"/api/files/input/data.csv/preview?filters={bad}")

    assert resp.status_code == 400


def test_preview_input_file_duplicate_filter_column_returns_400(auth_client):
    import urllib.parse

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_preview_csv(os.path.join(tmpdir, "data.csv"))
        bad = urllib.parse.quote('[{"column":"Name","value":"a"},{"column":"Name","value":"b"}]')
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.get(f"/api/files/input/data.csv/preview?filters={bad}")

    assert resp.status_code == 400


# ── WebSocket ──────────────────────────────────────────────────────────────────


def _ws_token() -> str:
    """Generate a valid JWT for WebSocket authentication in tests."""
    import uuid

    from app.models.user import User
    from app.services.auth import create_access_token

    user = User(id=str(uuid.uuid4()), username="wstest", role="user", status="active")
    return create_access_token(user)


def test_websocket_run_status_connects_and_receives_event(client):
    token = _ws_token()
    run_id = "test-run-123"
    with client.websocket_connect(f"/ws/runs/{run_id}?token={token}") as ws:
        data = ws.receive_json()
        assert data["event"] == "connected"
        assert data["run_id"] == run_id


def test_websocket_responds_to_ping(client):
    token = _ws_token()
    run_id = "ping-test-run"
    with client.websocket_connect(f"/ws/runs/{run_id}?token={token}") as ws:
        # Consume the initial "connected" event
        ws.receive_json()
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"


# ── Health endpoint tests (SFBL-53) ────────────────────────────────────────────


class TestHealthLive:
    def test_returns_200_ok(self, client):
        response = client.get("/api/health/live")
        assert response.status_code == 200

    def test_returns_status_ok(self, client):
        response = client.get("/api/health/live")
        data = response.json()
        assert data["status"] == "ok"

    def test_no_auth_required(self, client):
        """Liveness endpoint must be unauthenticated."""
        response = client.get("/api/health/live")
        assert response.status_code == 200

    def test_does_not_require_database(self, client):
        """Liveness must not perform any DB I/O."""
        with patch("app.api.utility._check_database") as mock_check:
            response = client.get("/api/health/live")
        mock_check.assert_not_called()
        assert response.status_code == 200


class TestHealthReady:
    def test_returns_200_when_db_healthy(self, client):
        response = client.get("/api/health/ready")
        assert response.status_code == 200

    def test_returns_status_ok_when_healthy(self, client):
        response = client.get("/api/health/ready")
        data = response.json()
        assert data["status"] == "ok"

    def test_returns_503_when_db_unavailable(self, client):
        from sqlalchemy.exc import OperationalError

        with patch("app.api.utility._check_database", return_value=("failed", "connection refused")):
            response = client.get("/api/health/ready")
        assert response.status_code == 503

    def test_returns_failed_status_when_db_unavailable(self, client):
        with patch("app.api.utility._check_database", return_value=("failed", "connection refused")):
            response = client.get("/api/health/ready")
        data = response.json()
        assert data["status"] == "failed"

    def test_includes_database_error_detail_on_failure(self, client):
        with patch("app.api.utility._check_database", return_value=("failed", "connection refused")):
            response = client.get("/api/health/ready")
        data = response.json()
        assert "database" in data
        assert "connection refused" in data["database"]


class TestHealthDependencies:
    def test_returns_200_when_all_healthy(self, client):
        response = client.get("/api/health/dependencies")
        assert response.status_code == 200

    def test_returns_ok_overall_when_db_healthy(self, client):
        response = client.get("/api/health/dependencies")
        data = response.json()
        assert data["status"] == "ok"
        assert data["dependencies"]["database"]["status"] == "ok"

    def test_returns_503_when_db_unavailable(self, client):
        with patch("app.api.utility._check_database", return_value=("failed", "SELECT 1 failed")):
            response = client.get("/api/health/dependencies")
        assert response.status_code == 503

    def test_returns_failed_overall_when_db_unavailable(self, client):
        with patch("app.api.utility._check_database", return_value=("failed", "SELECT 1 failed")):
            response = client.get("/api/health/dependencies")
        data = response.json()
        assert data["status"] == "failed"
        assert data["dependencies"]["database"]["status"] == "failed"

    def test_includes_database_dependency(self, client):
        response = client.get("/api/health/dependencies")
        data = response.json()
        assert "dependencies" in data
        assert "database" in data["dependencies"]

    def test_dependency_checks_disabled(self, client):
        from app.config import settings

        original = settings.health_enable_dependency_checks
        try:
            settings.health_enable_dependency_checks = False
            response = client.get("/api/health/dependencies")
            data = response.json()
        finally:
            settings.health_enable_dependency_checks = original
        assert response.status_code == 200
        assert data["dependencies"]["database"]["status"] == "ok"

    # ── Email probe tests (SFBL-142) ───────────────────────────────────────────

    def test_email_entry_present_in_dependencies(self, client):
        """The 'email' key must always appear in the dependencies dict."""
        response = client.get("/api/health/dependencies")
        data = response.json()
        assert "email" in data["dependencies"]

    def test_noop_email_backend_is_healthy(self, client):
        """noop backend must report healthy without a network probe."""
        from app.config import settings

        original = settings.email_backend
        try:
            settings.email_backend = "noop"
            response = client.get("/api/health/dependencies")
            data = response.json()
        finally:
            settings.email_backend = original

        email_dep = data["dependencies"]["email"]
        assert email_dep["status"] == "ok"
        assert "noop" in email_dep.get("detail", "").lower()

    def test_smtp_healthcheck_failure_yields_degraded_not_failed(self, client):
        """When SMTP healthcheck() returns False, status must be 'degraded'."""
        from app.config import settings
        from unittest.mock import AsyncMock, patch

        original = settings.email_backend
        try:
            settings.email_backend = "smtp"
            with patch("app.api.utility._check_email", new=AsyncMock(return_value=("degraded", "smtp healthcheck returned False"))):
                response = client.get("/api/health/dependencies")
                data = response.json()
        finally:
            settings.email_backend = original

        email_dep = data["dependencies"]["email"]
        assert email_dep["status"] == "degraded"
        # Degraded email must NOT cause 503 (email is non-critical)
        assert response.status_code == 200

    def test_email_degraded_does_not_cause_503(self, client):
        """A degraded email probe must keep HTTP 200 — email is non-critical."""
        from unittest.mock import AsyncMock, patch

        with patch("app.api.utility._check_email", new=AsyncMock(return_value=("degraded", "unreachable"))):
            response = client.get("/api/health/dependencies")

        assert response.status_code == 200
        data = response.json()
        # Overall status should be 'degraded', not 'failed'
        assert data["status"] == "degraded"

    def test_email_degraded_and_db_healthy_overall_degraded(self, client):
        """Degraded email + healthy DB → overall 'degraded' (not 'ok', not 'failed')."""
        from unittest.mock import AsyncMock, patch

        with patch("app.api.utility._check_email", new=AsyncMock(return_value=("degraded", "test"))):
            response = client.get("/api/health/dependencies")

        data = response.json()
        assert data["status"] == "degraded"
        assert data["dependencies"]["database"]["status"] == "ok"


# ── Output file listing ────────────────────────────────────────────────────────


def test_list_output_files_requires_auth(client):
    assert client.get("/api/files/output").status_code == 401


def test_list_output_files_empty_dir(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get("/api/files/output")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_output_files_returns_csvs(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ("partition_0_success.csv", "partition_0_errors.csv", "readme.txt"):
            open(os.path.join(tmpdir, name), "w").close()
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get("/api/files/output")
    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()]
    assert "partition_0_success.csv" in names
    assert "partition_0_errors.csv" in names
    assert "readme.txt" not in names
    assert all(f["source"] == "local-output" for f in resp.json())
    assert all(f["provider"] == "local" for f in resp.json())


def test_list_output_files_missing_dir_returns_empty(auth_client):
    with patch("app.api.utility.settings.output_dir", "/path/that/does/not/exist"):
        resp = auth_client.get("/api/files/output")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_output_files_returns_directory_entries(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "plan-abc"))
        open(os.path.join(tmpdir, "root.csv"), "w").close()
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get("/api/files/output")
    assert resp.status_code == 200
    kinds = {e["name"]: e["kind"] for e in resp.json()}
    assert kinds["plan-abc"] == "directory"
    assert kinds["root.csv"] == "file"


def test_list_output_files_with_path_param(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "plan-abc", "run-01"))
        csv_path = os.path.join(tmpdir, "plan-abc", "run-01", "partition_0_success.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sf__Id", "sf__Created"])
            writer.writerow(["001abc", "true"])
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get("/api/files/output?path=plan-abc/run-01")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["name"] == "partition_0_success.csv"
    assert entries[0]["source"] == "local-output"
    assert entries[0]["row_count"] == 1


def test_list_output_files_path_traversal_returns_400(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get("/api/files/output?path=../etc")
    assert resp.status_code == 400


# ── Output file preview ────────────────────────────────────────────────────────


def test_preview_output_file_returns_rows(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "partition_0_success.csv")
        _write_preview_csv(csv_path, rows=5)
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get("/api/files/output/partition_0_success.csv/preview?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "partition_0_success.csv"
    assert body["header"] == ["Name", "Industry"]
    assert len(body["rows"]) == 5
    assert body["source"] == "local-output"
    assert body["provider"] == "local"


def test_preview_output_file_not_found_returns_404(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get("/api/files/output/nonexistent.csv/preview")
    assert resp.status_code == 404


def test_preview_output_file_path_traversal_returns_400(auth_client):
    resp = auth_client.get("/api/files/output/../secret.csv/preview")
    assert resp.status_code in (400, 404)


def test_preview_output_file_in_subdirectory(auth_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "plan-abc", "run-01"))
        csv_path = os.path.join(tmpdir, "plan-abc", "run-01", "partition_0_errors.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sf__Id", "sf__Error"])
            writer.writerow(["", "REQUIRED_FIELD_MISSING"])
        with patch("app.api.utility.settings.output_dir", tmpdir):
            resp = auth_client.get(
                "/api/files/output/plan-abc/run-01/partition_0_errors.csv/preview"
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["header"] == ["sf__Id", "sf__Error"]
    assert len(body["rows"]) == 1
