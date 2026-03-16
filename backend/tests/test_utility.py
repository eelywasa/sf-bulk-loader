"""Tests for health check, file listing/preview, and WebSocket endpoints."""

import csv
import os
import tempfile
from unittest.mock import patch

import pytest


# ── Health check ───────────────────────────────────────────────────────────────


def test_health_returns_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "sf_api_version" in body


# ── Input file listing ─────────────────────────────────────────────────────────


def test_list_input_files_empty_dir(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_input_files_returns_csvs(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create two CSVs and a non-CSV file
        for name in ("accounts.csv", "contacts.csv", "readme.txt"):
            open(os.path.join(tmpdir, name), "w").close()

        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input")

    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()]
    assert "accounts.csv" in names
    assert "contacts.csv" in names
    assert "readme.txt" not in names


def test_list_input_files_missing_dir_returns_empty(client):
    with patch("app.api.utility.settings") as mock_settings:
        mock_settings.input_dir = "/path/that/does/not/exist"
        resp = client.get("/api/files/input")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_input_files_returns_directory_entries(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "subdir"))
        open(os.path.join(tmpdir, "root.csv"), "w").close()
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input")
    assert resp.status_code == 200
    entries = resp.json()
    kinds = {e["name"]: e["kind"] for e in entries}
    assert kinds["subdir"] == "directory"
    assert kinds["root.csv"] == "file"


def test_list_input_files_directories_sorted_before_files(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "zfolder"))
        open(os.path.join(tmpdir, "accounts.csv"), "w").close()
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input")
    entries = resp.json()
    assert entries[0]["kind"] == "directory"
    assert entries[1]["kind"] == "file"


def test_list_input_files_with_path_param(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "sub"))
        csv_path = os.path.join(tmpdir, "sub", "deep.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Id", "Name"])
            writer.writerow(["1", "Alpha"])
            writer.writerow(["2", "Beta"])
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input?path=sub")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["name"] == "deep.csv"
    assert entries[0]["path"] == "sub/deep.csv"
    assert entries[0]["kind"] == "file"
    assert entries[0]["row_count"] == 2


def test_list_input_files_includes_row_count(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "accounts.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Industry"])
            for i in range(5):
                writer.writerow([f"Acme {i}", "Tech"])
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input")
    assert resp.status_code == 200
    entry = resp.json()[0]
    assert entry["row_count"] == 5


def test_list_input_files_path_traversal_returns_400(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input?path=../etc")
    assert resp.status_code == 400


def test_list_input_files_nonexistent_path_returns_400(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input?path=nonexistent")
    assert resp.status_code == 400


def test_list_input_files_hidden_dirs_excluded(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".hidden"))
        open(os.path.join(tmpdir, "visible.csv"), "w").close()
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input")
    names = [e["name"] for e in resp.json()]
    assert ".hidden" not in names
    assert "visible.csv" in names


# ── File preview ───────────────────────────────────────────────────────────────


def test_preview_input_file_returns_rows(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "accounts.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Industry"])
            for i in range(20):
                writer.writerow([f"Acme {i}", "Tech"])

        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input/accounts.csv/preview?rows=5")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "accounts.csv"
    assert body["header"] == ["Name", "Industry"]
    assert len(body["rows"]) == 5
    assert body["row_count"] == 5


def test_preview_input_file_not_found_returns_404(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input/nonexistent.csv/preview")
    assert resp.status_code == 404


def test_preview_input_file_path_traversal_returns_400(client):
    resp = client.get("/api/files/input/../secret.csv/preview")
    assert resp.status_code in (400, 404)


def test_preview_input_file_in_subdirectory(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        sub = os.path.join(tmpdir, "sub")
        os.makedirs(sub)
        csv_path = os.path.join(sub, "deep.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Id", "Name"])
            writer.writerow(["1", "Alpha"])

        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input/sub/deep.csv/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "sub/deep.csv"
    assert body["header"] == ["Id", "Name"]
    assert len(body["rows"]) == 1


def test_preview_input_file_subdir_traversal_returns_400(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.utility.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = client.get("/api/files/input/sub/../../etc/passwd/preview")
    assert resp.status_code in (400, 404)


# ── WebSocket ──────────────────────────────────────────────────────────────────


def test_websocket_run_status_connects_and_receives_event(client):
    run_id = "test-run-123"
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        data = ws.receive_json()
        assert data["event"] == "connected"
        assert data["run_id"] == run_id


def test_websocket_responds_to_ping(client):
    run_id = "ping-test-run"
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        # Consume the initial "connected" event
        ws.receive_json()
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
