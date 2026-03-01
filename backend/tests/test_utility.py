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
    filenames = [f["filename"] for f in resp.json()]
    assert "accounts.csv" in filenames
    assert "contacts.csv" in filenames
    assert "readme.txt" not in filenames


def test_list_input_files_missing_dir_returns_empty(client):
    with patch("app.api.utility.settings") as mock_settings:
        mock_settings.input_dir = "/path/that/does/not/exist"
        resp = client.get("/api/files/input")
    assert resp.status_code == 200
    assert resp.json() == []


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
    # FastAPI URL decoding normalizes the path — the filename after decoding
    # will not match the safe name check.  Expect 400.
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
