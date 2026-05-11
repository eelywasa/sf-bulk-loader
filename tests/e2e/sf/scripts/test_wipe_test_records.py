"""
Unit tests for wipe_test_records.py — prefix validator and SOQL-safety invariants.

These tests run WITHOUT a live Salesforce org.  The sf CLI is mocked so that:
  - validate_prefix() failures are confirmed to exit before any subprocess call
  - valid-prefix paths never reach actual SOQL (mock confirms zero calls)

Run:
  python -m pytest tests/e2e/sf/scripts/test_wipe_test_records.py -v
  # or from the scripts directory:
  python -m pytest test_wipe_test_records.py -v
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the scripts directory is importable regardless of working directory.
sys.path.insert(0, str(Path(__file__).parent))

from wipe_test_records import main, validate_prefix  # noqa: E402

# ── validate_prefix unit tests ────────────────────────────────────────────────


class TestValidatePrefix:
    """validate_prefix should accept only [A-Za-z0-9-]+ prefixes."""

    # ── Accepted prefixes ─────────────────────────────────────────────────────

    def test_hyphen_prefix_accepted(self):
        """A well-formed D3 run prefix with trailing hyphen is accepted."""
        validate_prefix("E2E-RUN-1-0-test-")  # must not raise

    def test_letters_and_digits_accepted(self):
        validate_prefix("E2E-abc-123")  # must not raise

    def test_short_prefix_accepted(self):
        validate_prefix("A")  # single character — valid

    def test_all_lowercase_accepted(self):
        validate_prefix("e2e-run-abc-")  # must not raise

    # ── Rejected prefixes ─────────────────────────────────────────────────────

    def test_underscore_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E_RUN_1")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "underscore" in captured.err

    def test_underscore_in_middle_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E-run_test-")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "underscore" in captured.err

    def test_percent_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E-%")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "%" in captured.err

    def test_empty_string_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower()

    def test_whitespace_only_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("   ")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower() or "whitespace" in captured.err.lower()

    def test_single_quote_sql_injection_rejected(self, capsys):
        """Classic SQL injection attempt — must be rejected."""
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E-test-' OR 1=1--")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "invalid" in captured.err.lower() or "characters" in captured.err.lower()

    def test_double_quote_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix('E2E-"test"-')
        assert exc_info.value.code == 1

    def test_semicolon_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E-test;DROP TABLE")
        assert exc_info.value.code == 1

    def test_space_in_prefix_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E test")
        assert exc_info.value.code == 1

    def test_slash_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E/test/")
        assert exc_info.value.code == 1

    def test_star_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E-*-test")
        assert exc_info.value.code == 1


# ── No-SOQL guarantee for invalid prefixes ────────────────────────────────────


class TestNoSoqlOnInvalidPrefix:
    """
    When the prefix is invalid the wiper must exit BEFORE issuing any SOQL.
    We confirm this by mocking subprocess.run and asserting it was never called.
    """

    def _run_main_with_mocked_sf(self, prefix: str, expected_exit: int) -> MagicMock:
        """
        Run main() with the given prefix; assert it exits with expected_exit
        and that subprocess.run was never invoked.
        """
        mock_run = MagicMock(spec=subprocess.run)
        with patch("wipe_test_records.subprocess.run", mock_run):
            with pytest.raises(SystemExit) as exc_info:
                main(["--prefix", prefix, "--org", "fake-org"])
        assert exc_info.value.code == expected_exit
        return mock_run

    def test_underscore_prefix_no_soql(self):
        mock_run = self._run_main_with_mocked_sf("E2E_bad_prefix", 1)
        mock_run.assert_not_called()

    def test_percent_prefix_no_soql(self):
        mock_run = self._run_main_with_mocked_sf("E2E-%", 1)
        mock_run.assert_not_called()

    def test_empty_prefix_no_soql(self):
        mock_run = self._run_main_with_mocked_sf("", 1)
        mock_run.assert_not_called()

    def test_whitespace_prefix_no_soql(self):
        mock_run = self._run_main_with_mocked_sf("   ", 1)
        mock_run.assert_not_called()

    def test_sql_injection_no_soql(self):
        mock_run = self._run_main_with_mocked_sf("E2E-' OR 1=1--", 1)
        mock_run.assert_not_called()


# ── Valid prefix: mock sf CLI so we can test the happy-path logic ─────────────


class TestValidPrefixWithMockedCli:
    """Smoke-test the happy path — valid prefix, mock sf CLI returns zero records."""

    def _make_sf_response(self, records: list[dict] | None = None) -> MagicMock:
        """Build a mock subprocess.CompletedProcess that looks like `sf --json` output."""
        records = records or []
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json_dumps({"status": 0, "result": {"records": records}})
        mock_proc.stderr = ""
        return mock_proc

    def test_valid_prefix_zero_records(self, capsys):
        """Valid prefix + zero records → exits 0, no deletes."""
        mock_proc = self._make_sf_response(records=[])
        with patch("wipe_test_records.subprocess.run", return_value=mock_proc):
            # Should not raise
            main(["--prefix", "E2E-RUN-1-0-test-", "--org", "fake-org"])
        out = capsys.readouterr()
        assert "done" in out.err.lower() or "0 record" in out.err

    def test_dry_run_valid_prefix(self, capsys):
        """Dry-run with a valid prefix should print 'dry-run' and not call delete."""
        mock_query = self._make_sf_response(records=[{"Id": "001abc"}])
        call_count = {"n": 0}

        def side_effect(cmd, **kwargs):
            # query → return a record; delete → should never be called
            if "delete" in cmd:
                raise AssertionError("delete called during dry-run")
            call_count["n"] += 1
            return mock_query

        with patch("wipe_test_records.subprocess.run", side_effect=side_effect):
            main(["--prefix", "E2E-RUN-1-0-test-", "--org", "fake-org", "--dry-run"])
        out = capsys.readouterr()
        assert "dry-run" in out.err.lower()


def json_dumps(obj: dict) -> str:
    import json
    return json.dumps(obj)
