"""Tests for SFBL-320 — SF_DESCRIBE_FIXTURES_DIR startup-only fixture mode.

Test strategy
-------------
The ``FixtureMode`` class is designed to be instantiated directly in tests
rather than monkeypatching the module-level singleton, which avoids cross-test
leakage.  API-level tests inject a custom ``FixtureMode`` instance into the
``fixture_mode`` module during the test and restore the original afterwards.

Fixture directory structure used by these tests is built in a temporary
directory via ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.fixture_mode import OBJECT_LIST_FIXTURE, FixtureMode


# ── Helpers ────────────────────────────────────────────────────────────────────


def _write_json(path: Path, data) -> Path:
    """Write *data* as JSON to *path*, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── FixtureMode unit tests ─────────────────────────────────────────────────────


class TestFixtureModeInit:
    """FixtureMode constructor behaviour."""

    def test_unset_env_is_live_mode(self):
        fm = FixtureMode(None)
        assert fm.mode == "live"
        assert not fm.is_fixture_mode()

    def test_empty_string_env_is_live_mode(self):
        fm = FixtureMode("")
        assert fm.mode == "live"

    def test_whitespace_only_env_is_live_mode(self):
        fm = FixtureMode("   ")
        assert fm.mode == "live"

    def test_valid_dir_is_fixture_mode(self, tmp_path):
        d = tmp_path / "fixtures"
        d.mkdir()
        fm = FixtureMode(str(d))
        assert fm.mode == "fixture"
        assert fm.is_fixture_mode()

    def test_nonexistent_dir_is_skipped_falls_back_to_live(self, tmp_path):
        fm = FixtureMode("/nonexistent-sfbl-320-test-dir")
        assert fm.mode == "live"

    def test_nonexistent_dir_skipped_valid_dir_kept(self, tmp_path):
        d = tmp_path / "real"
        d.mkdir()
        fm = FixtureMode(f"/nonexistent:{d}")
        assert fm.mode == "fixture"

    def test_colon_separated_multiple_dirs(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        fm = FixtureMode(f"{d1}:{d2}")
        assert fm.mode == "fixture"


class TestResolveFixture:
    """PATH-like first-match-wins resolution for per-SObject describe files."""

    def test_returns_none_in_live_mode(self):
        fm = FixtureMode(None)
        assert fm.resolve_fixture("Account.json") is None

    def test_returns_none_when_file_absent(self, tmp_path):
        d = tmp_path / "fixtures"
        d.mkdir()
        fm = FixtureMode(str(d))
        assert fm.resolve_fixture("Account.json") is None

    def test_returns_path_when_file_present(self, tmp_path):
        d = tmp_path / "fixtures"
        _write_json(d / "Account.json", {"name": "Account"})
        fm = FixtureMode(str(d))
        result = fm.resolve_fixture("Account.json")
        assert result is not None
        assert result.exists()
        assert result.name == "Account.json"

    def test_first_match_wins_across_overlays(self, tmp_path):
        app_dir = tmp_path / "app"
        sf_dir = tmp_path / "sf"
        _write_json(app_dir / "Account.json", {"source": "app"})
        _write_json(sf_dir / "Account.json", {"source": "sf"})
        fm = FixtureMode(f"{app_dir}:{sf_dir}")
        result = fm.resolve_fixture("Account.json")
        assert result is not None
        data = json.loads(result.read_text())
        assert data["source"] == "app"

    def test_falls_back_to_second_dir(self, tmp_path):
        app_dir = tmp_path / "app"
        sf_dir = tmp_path / "sf"
        app_dir.mkdir()
        _write_json(sf_dir / "Account.json", {"source": "sf"})
        fm = FixtureMode(f"{app_dir}:{sf_dir}")
        result = fm.resolve_fixture("Account.json")
        assert result is not None
        data = json.loads(result.read_text())
        assert data["source"] == "sf"


class TestListUnionFor:
    """list-union semantics for _object_list.json across overlay dirs."""

    def test_returns_none_in_live_mode(self):
        fm = FixtureMode(None)
        assert fm.list_union_for(OBJECT_LIST_FIXTURE) is None

    def test_returns_none_when_no_fixture_found_in_any_dir(self, tmp_path):
        d = tmp_path / "fixtures"
        d.mkdir()
        fm = FixtureMode(str(d))
        assert fm.list_union_for(OBJECT_LIST_FIXTURE) is None

    def test_single_dir_returns_list_verbatim(self, tmp_path):
        d = tmp_path / "fixtures"
        _write_json(d / OBJECT_LIST_FIXTURE, ["Account", "Contact", "Lead"])
        fm = FixtureMode(str(d))
        result = fm.list_union_for(OBJECT_LIST_FIXTURE)
        assert result == ["Account", "Contact", "Lead"]

    def test_two_dirs_returns_sorted_union(self, tmp_path):
        app_dir = tmp_path / "app"
        sf_dir = tmp_path / "sf"
        _write_json(app_dir / OBJECT_LIST_FIXTURE, ["CustomObj__c", "Account"])
        _write_json(sf_dir / OBJECT_LIST_FIXTURE, ["Account", "Contact", "Lead"])
        fm = FixtureMode(f"{app_dir}:{sf_dir}")
        result = fm.list_union_for(OBJECT_LIST_FIXTURE)
        # Union, deduplicated, sorted
        assert result == ["Account", "Contact", "CustomObj__c", "Lead"]

    def test_empty_fixture_files_return_empty_list(self, tmp_path):
        d = tmp_path / "fixtures"
        _write_json(d / OBJECT_LIST_FIXTURE, [])
        fm = FixtureMode(str(d))
        result = fm.list_union_for(OBJECT_LIST_FIXTURE)
        assert result == []

    def test_overlapping_entries_deduplicated(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        _write_json(d1 / OBJECT_LIST_FIXTURE, ["Account", "Contact"])
        _write_json(d2 / OBJECT_LIST_FIXTURE, ["Account", "Lead"])
        fm = FixtureMode(f"{d1}:{d2}")
        result = fm.list_union_for(OBJECT_LIST_FIXTURE)
        assert result == ["Account", "Contact", "Lead"]


class TestAmbiguousOverlayWarning:
    """Startup warning when two dirs contain the same describe filename."""

    def test_warning_fires_for_overlapping_describe_file(self, tmp_path, caplog):
        app_dir = tmp_path / "app"
        sf_dir = tmp_path / "sf"
        _write_json(app_dir / "Account.json", {"source": "app"})
        _write_json(sf_dir / "Account.json", {"source": "sf"})

        with caplog.at_level(logging.WARNING, logger="app.services.fixture_mode"):
            FixtureMode(f"{app_dir}:{sf_dir}")

        assert any("ambiguous overlay" in r.message and "Account.json" in r.message
                   for r in caplog.records)

    def test_no_warning_for_object_list_overlap(self, tmp_path, caplog):
        """_object_list.json overlap is expected — no ambiguous warning for it."""
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        _write_json(d1 / OBJECT_LIST_FIXTURE, ["Account"])
        _write_json(d2 / OBJECT_LIST_FIXTURE, ["Contact"])

        with caplog.at_level(logging.WARNING, logger="app.services.fixture_mode"):
            FixtureMode(f"{d1}:{d2}")

        assert not any("ambiguous overlay" in r.message for r in caplog.records)

    def test_no_warning_when_single_dir(self, tmp_path, caplog):
        d = tmp_path / "fixtures"
        _write_json(d / "Account.json", {"name": "Account"})

        with caplog.at_level(logging.WARNING, logger="app.services.fixture_mode"):
            FixtureMode(str(d))

        assert not any("ambiguous overlay" in r.message for r in caplog.records)


# ── API-level tests via TestClient ─────────────────────────────────────────────
#
# These tests inject a custom FixtureMode into the live module singleton so that
# the FastAPI route handlers pick it up without restarting the process.


def _make_connection(auth_client) -> str:
    """Create a dummy connection and return its ID."""
    payload = {
        "name": "Fixture Mode Test Org",
        "instance_url": "https://test.my.salesforce.com",
        "login_url": "https://login.salesforce.com",
        "client_id": "test_client_id",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
        "username": "test@example.com",
        "is_sandbox": False,
    }
    resp = auth_client.post("/api/connections/", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestHealthEndpointFixtureMode:
    """GET /api/health includes describe_fixtures_mode."""

    def test_health_reports_live_when_env_unset(self, auth_client):
        """When no SF_DESCRIBE_FIXTURES_DIR is configured, mode is 'live'."""
        import app.services.fixture_mode as fm_module

        original = fm_module.fixture_mode
        fm_module.fixture_mode = FixtureMode(None)
        try:
            resp = auth_client.get("/api/health")
        finally:
            fm_module.fixture_mode = original

        assert resp.status_code == 200
        body = resp.json()
        assert "describe_fixtures_mode" in body
        assert body["describe_fixtures_mode"] == "live"

    def test_health_reports_fixture_when_env_set(self, auth_client, tmp_path):
        """When SF_DESCRIBE_FIXTURES_DIR is configured, mode is 'fixture'."""
        import app.services.fixture_mode as fm_module

        d = tmp_path / "fixtures"
        d.mkdir()
        original = fm_module.fixture_mode
        fm_module.fixture_mode = FixtureMode(str(d))
        try:
            resp = auth_client.get("/api/health")
        finally:
            fm_module.fixture_mode = original

        assert resp.status_code == 200
        body = resp.json()
        assert body["describe_fixtures_mode"] == "fixture"

    def test_health_mode_does_not_change_at_runtime(self, auth_client, tmp_path):
        """Mode is fixed at process startup; changing the env var after the fact
        does NOT flip the mode (the existing singleton is already resolved)."""
        import app.services.fixture_mode as fm_module

        original = fm_module.fixture_mode
        # Start in live mode
        fm_module.fixture_mode = FixtureMode(None)
        try:
            resp1 = auth_client.get("/api/health")
            # Changing os.environ AFTER startup does not affect an already-
            # constructed FixtureMode (the singleton reads env only at __init__).
            d = tmp_path / "late"
            d.mkdir()
            os.environ["SF_DESCRIBE_FIXTURES_DIR"] = str(d)
            try:
                resp2 = auth_client.get("/api/health")
            finally:
                del os.environ["SF_DESCRIBE_FIXTURES_DIR"]
        finally:
            fm_module.fixture_mode = original

        # Both responses must report the SAME mode because the singleton
        # was not reconstructed between the two requests.
        assert resp1.json()["describe_fixtures_mode"] == resp2.json()["describe_fixtures_mode"] == "live"


class TestObjectsEndpointFixtureMode:
    """GET /api/connections/{id}/objects — fixture mode integration."""

    def test_live_mode_does_not_read_fixtures(self, auth_client, tmp_path):
        """In live mode the endpoint should attempt the Salesforce call (we patch
        get_access_token to avoid a real network call and assert no fixture read)."""
        import app.services.fixture_mode as fm_module

        d = tmp_path / "sf"
        _write_json(d / OBJECT_LIST_FIXTURE, ["Should", "Not", "Appear"])

        conn_id = _make_connection(auth_client)
        original = fm_module.fixture_mode
        fm_module.fixture_mode = FixtureMode(None)  # live mode
        try:
            with patch(
                "app.api.connections.get_access_token",
                side_effect=Exception("network call blocked"),
            ):
                resp = auth_client.get(f"/api/connections/{conn_id}/objects")
        finally:
            fm_module.fixture_mode = original

        # In live mode we hit the Salesforce path which raises → 502
        assert resp.status_code == 502

    def test_fixture_mode_single_dir_returns_list(self, auth_client, tmp_path):
        """Fixture mode serves objects from _object_list.json verbatim (sorted)."""
        import app.services.fixture_mode as fm_module

        d = tmp_path / "sf"
        _write_json(d / OBJECT_LIST_FIXTURE, ["Lead", "Account", "Contact"])

        conn_id = _make_connection(auth_client)
        original = fm_module.fixture_mode
        fm_module.fixture_mode = FixtureMode(str(d))
        try:
            resp = auth_client.get(f"/api/connections/{conn_id}/objects")
        finally:
            fm_module.fixture_mode = original

        assert resp.status_code == 200
        assert resp.json() == ["Account", "Contact", "Lead"]

    def test_fixture_mode_two_dirs_list_union_sorted(self, auth_client, tmp_path):
        """When two overlay dirs both have _object_list.json, the response is
        the sorted set-union of their entries."""
        import app.services.fixture_mode as fm_module

        app_dir = tmp_path / "app"
        sf_dir = tmp_path / "sf"
        _write_json(app_dir / OBJECT_LIST_FIXTURE, ["CustomObj__c", "Account"])
        _write_json(sf_dir / OBJECT_LIST_FIXTURE, ["Account", "Contact", "Lead"])

        conn_id = _make_connection(auth_client)
        original = fm_module.fixture_mode
        fm_module.fixture_mode = FixtureMode(f"{app_dir}:{sf_dir}")
        try:
            resp = auth_client.get(f"/api/connections/{conn_id}/objects")
        finally:
            fm_module.fixture_mode = original

        assert resp.status_code == 200
        assert resp.json() == ["Account", "Contact", "CustomObj__c", "Lead"]

    def test_fixture_mode_no_object_list_returns_empty(self, auth_client, tmp_path):
        """When no _object_list.json exists in any fixture dir, return [] not 5xx.

        This is intentionally operator-friendly: an empty list is a clear signal
        that the fixture file is missing rather than a cryptic 500 error.
        """
        import app.services.fixture_mode as fm_module

        d = tmp_path / "empty_fixtures"
        d.mkdir()

        conn_id = _make_connection(auth_client)
        original = fm_module.fixture_mode
        fm_module.fixture_mode = FixtureMode(str(d))
        try:
            resp = auth_client.get(f"/api/connections/{conn_id}/objects")
        finally:
            fm_module.fixture_mode = original

        assert resp.status_code == 200
        assert resp.json() == []

    def test_fixture_mode_does_not_call_salesforce(self, auth_client, tmp_path):
        """Fixture mode must never reach out to Salesforce.

        We assert this by patching get_access_token to raise; if fixture mode
        bypasses it correctly, the endpoint returns 200.
        """
        import app.services.fixture_mode as fm_module

        d = tmp_path / "sf"
        _write_json(d / OBJECT_LIST_FIXTURE, ["Account"])

        conn_id = _make_connection(auth_client)
        original = fm_module.fixture_mode
        fm_module.fixture_mode = FixtureMode(str(d))
        try:
            with patch(
                "app.api.connections.get_access_token",
                side_effect=Exception("should not be called in fixture mode"),
            ):
                resp = auth_client.get(f"/api/connections/{conn_id}/objects")
        finally:
            fm_module.fixture_mode = original

        assert resp.status_code == 200
        assert resp.json() == ["Account"]
