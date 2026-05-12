"""
test_scratch_create.py — Unit tests for scratch_create.sh logic.

Covers:
- Org ID normalisation (15-char, 18-char, invalid lengths, empty).
- Shape availability lookup (active 15-char match, active 18-char match,
  inactive match, empty list).

These tests run the shell script in a controlled environment using a mock
`sf` CLI binary injected via PATH. The mock records whether it was called
and returns fixture JSON for the shape-list command.

Run with:
    cd tests/e2e/sf/scripts && python -m pytest test_scratch_create.py -v

Requirements: Python 3.12+, no third-party packages needed (uses only stdlib).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).parent
FIXTURES_DIR = SCRIPTS_DIR / "test-fixtures"
SCRATCH_CREATE = SCRIPTS_DIR / "scratch_create.sh"
SFDX_DIR = SCRIPTS_DIR.parent / "sfdx"
CONFIG_DIR = SFDX_DIR / "config"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_sf(tmp_path: Path, shape_json_fixture: Path | None = None) -> Path:
    """
    Write a mock `sf` executable to tmp_path/bin/ and return the bin dir.

    If shape_json_fixture is provided, `sf org list shape --json` prints
    that fixture's content and exits 0.  If None, exits 1 with an error
    (simulates CLI not found / failure).

    `sf org create scratch` always exits 0 and prints a one-liner.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sf_bin = bin_dir / "sf"

    if shape_json_fixture is None:
        shape_response = '{"status":1,"result":[],"warnings":[]}'
    else:
        shape_response = shape_json_fixture.read_text()
    # Escape single quotes inside the JSON for embedding in a heredoc-free sh.
    # We write the JSON to a side file and cat it to avoid quoting nightmares.
    json_file = tmp_path / "shape_response.json"
    json_file.write_text(shape_response)

    sf_bin.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Mock sf CLI for unit tests.
        if [[ "$1 $2 $3" == "org list shape" ]]; then
            cat '{json_file}'
            exit 0
        elif [[ "$1 $2 $3" == "org create scratch" ]]; then
            echo "Mock: scratch org created."
            exit 0
        else
            echo "Mock sf: unhandled command: $*" >&2
            exit 1
        fi
    """))
    sf_bin.chmod(sf_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _make_scratch_defs(tmp_dir: Path) -> Path:
    """
    Write minimal project-scratch-def.json and project-scratch-def.shaped.json
    under tmp_dir/sfdx/config/ so the script can resolve them.
    Returns the sfdx dir path.
    """
    config_dir = tmp_dir / "sfdx" / "config"
    config_dir.mkdir(parents=True)
    base_def = {"edition": "Developer", "orgName": "E2E Test Org"}
    (config_dir / "project-scratch-def.json").write_text(json.dumps(base_def))
    shaped_def = {**base_def}  # sourceOrg will be injected by the script
    (config_dir / "project-scratch-def.shaped.json").write_text(json.dumps(shaped_def))
    return tmp_dir / "sfdx"


def _run_script(
    tmp_path: Path,
    env_overrides: dict[str, str],
    mock_sf_bin_dir: Path,
    sfdx_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    """
    Run scratch_create.sh and return the CompletedProcess.

    If sfdx_dir is provided, it is injected via SFDX_DIR_OVERRIDE so the
    script resolves scratch-def files from the test's tmp_path rather than
    the real tests/e2e/sf/sfdx/ directory.
    """
    env = {
        "PATH": f"{mock_sf_bin_dir}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        **env_overrides,
    }
    if sfdx_dir is not None:
        env["SFDX_DIR_OVERRIDE"] = str(sfdx_dir)
    return subprocess.run(
        ["bash", str(SCRATCH_CREATE)],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Org ID normalisation tests
# ---------------------------------------------------------------------------

class TestOrgIdNormalisation:
    """Tests that exercise the 15/18-char normalisation in scratch_create.sh."""

    def test_15_char_id_accepted(self, tmp_path):
        """A 15-char E2E_ORG_SHAPE is accepted and passes through unchanged."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path, FIXTURES_DIR / "shape_list_active_15.json")
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D000000000001",  # 15 chars
            },
            mock_sf_bin_dir=mock_bin,
            sfdx_dir=sfdx,
        )
        assert result.returncode == 0, result.stderr
        assert "15-char E2E_ORG_SHAPE" in result.stdout
        assert "Active org shape confirmed" in result.stdout

    def test_18_char_id_truncated_to_15(self, tmp_path):
        """An 18-char E2E_ORG_SHAPE is truncated to 15 and proceeds."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path, FIXTURES_DIR / "shape_list_active_18.json")
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D000000000001AAA",  # 18 chars
            },
            mock_sf_bin_dir=mock_bin,
            sfdx_dir=sfdx,
        )
        assert result.returncode == 0, result.stderr
        assert "truncated to 15 chars: 00D000000000001" in result.stdout

    def test_20_char_id_rejected(self, tmp_path):
        """A 20-char E2E_ORG_SHAPE is rejected with the documented error."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path)
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D000000000001AAAAA",  # 20 chars
            },
            mock_sf_bin_dir=mock_bin,
        )
        assert result.returncode != 0
        assert "expected 15- or 18-char Salesforce org ID, got 20-char value" in result.stderr

    def test_14_char_id_rejected(self, tmp_path):
        """A 14-char E2E_ORG_SHAPE is rejected with the documented error."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path)
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D00000000000",  # 14 chars
            },
            mock_sf_bin_dir=mock_bin,
        )
        assert result.returncode != 0
        assert "expected 15- or 18-char Salesforce org ID, got 14-char value" in result.stderr

    def test_default_shape_skips_normalisation(self, tmp_path):
        """E2E_ORG_SHAPE=default bypasses shape lookup entirely."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path)  # No fixture — shape cmd should not be called
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "default",
            },
            mock_sf_bin_dir=mock_bin,
        )
        assert result.returncode == 0, result.stderr
        assert "Using default scratch-def" in result.stdout

    def test_empty_shape_treated_as_default(self, tmp_path):
        """Empty E2E_ORG_SHAPE is treated as default (no shape path)."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path)
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "",
            },
            mock_sf_bin_dir=mock_bin,
        )
        assert result.returncode == 0, result.stderr
        assert "Using default scratch-def" in result.stdout


# ---------------------------------------------------------------------------
# Shape availability lookup tests
# ---------------------------------------------------------------------------

class TestShapeAvailabilityLookup:
    """Tests that exercise the sf org list shape --json filtering logic."""

    def test_active_15_char_shape_succeeds(self, tmp_path):
        """A list with one matching 15-char shape → succeeds."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path, FIXTURES_DIR / "shape_list_active_15.json")
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D000000000001",  # 15-char, matches fixture
            },
            mock_sf_bin_dir=mock_bin,
            sfdx_dir=sfdx,
        )
        assert result.returncode == 0, result.stderr
        assert "Active org shape confirmed" in result.stdout

    def test_active_18_char_shape_normalised_match_succeeds(self, tmp_path):
        """A list with one 18-char sourceOrg that normalises to a match → succeeds."""
        sfdx = _make_scratch_defs(tmp_path)
        # Fixture has sourceOrg="00D000000000001AAA" (18 chars).
        # We pass 15-char ID — the script normalises the fixture's 18-char sourceOrg
        # to 15 chars for comparison.
        mock_bin = _make_mock_sf(tmp_path, FIXTURES_DIR / "shape_list_active_18.json")
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D000000000001",  # 15-char; fixture has 18-char form
            },
            mock_sf_bin_dir=mock_bin,
            sfdx_dir=sfdx,
        )
        assert result.returncode == 0, result.stderr
        assert "Active org shape confirmed" in result.stdout

    def test_inactive_shape_fails_fast(self, tmp_path):
        """A matching shape with status != Active → fails fast."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path, FIXTURES_DIR / "shape_list_inactive.json")
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D000000000001",
            },
            mock_sf_bin_dir=mock_bin,
            sfdx_dir=sfdx,
        )
        assert result.returncode != 0
        assert "No active org shape found" in result.stderr
        assert "sf org create shape --target-org" in result.stderr

    def test_empty_shape_list_fails_fast(self, tmp_path):
        """An empty shape list → fails fast with the operator-pointer error."""
        sfdx = _make_scratch_defs(tmp_path)
        mock_bin = _make_mock_sf(tmp_path, FIXTURES_DIR / "shape_list_empty.json")
        result = _run_script(
            tmp_path,
            env_overrides={
                "E2E_SCRATCH_ORG": "e2e-test-org",
                "E2E_ORG_SHAPE": "00D000000000001",
            },
            mock_sf_bin_dir=mock_bin,
            sfdx_dir=sfdx,
        )
        assert result.returncode != 0
        assert "No active org shape found" in result.stderr
        # Operator pointer must mention the manual creation command
        assert "sf org create shape --target-org" in result.stderr


# ---------------------------------------------------------------------------
# Missing env var guard
# ---------------------------------------------------------------------------

class TestEnvVarGuards:
    def test_missing_scratch_org_alias_exits_1(self, tmp_path):
        """E2E_SCRATCH_ORG unset → exits 1 with a clear error."""
        mock_bin = _make_mock_sf(tmp_path)
        result = _run_script(
            tmp_path,
            env_overrides={},  # No E2E_SCRATCH_ORG
            mock_sf_bin_dir=mock_bin,
        )
        assert result.returncode != 0
        assert "E2E_SCRATCH_ORG must be set" in result.stderr
