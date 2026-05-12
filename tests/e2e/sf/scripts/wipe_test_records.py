#!/usr/bin/env python3
"""
wipe_test_records.py — delete Tier 2 test records from a scratch org.

WHY this exists:
  Playwright Tier 2 specs create Salesforce records during their runs.  Each
  spec receives a per-run prefix (D3 hyphen-separated convention) so records
  are namespaced.  This script deletes exactly those records after a test run
  using a LIKE '${prefix}%' query — nothing broader.

SAFETY INVARIANTS (fail-fast — these must never be bypassed):
  1. The prefix must be non-empty and non-whitespace-only.
  2. The prefix must match /^[A-Za-z0-9-]+$/ — only letters, digits, and
     hyphens.  Any other character (underscore, percent, quote, semicolon, …)
     is rejected with a clear error and zero SOQL is issued.
  3. The query never uses the broad LIKE 'E2E-%' pattern.  It always uses the
     exact per-run prefix.

Invocation:
  python3 wipe_test_records.py --prefix E2E-RUN-1-0-test- \\
      [--org <alias>] [--targets-file <path>] [--dry-run]

Environment variables:
  E2E_SCRATCH_ORG — default scratch org alias (overridden by --org)

Exit codes:
  0  — all matching records deleted (or --dry-run completed)
  1  — validation error (bad prefix) or runtime error (SOQL/delete failure)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ── Prefix validator ──────────────────────────────────────────────────────────
# Only allow letters, digits, and hyphens.  This covers all valid D3 prefixes
# (e.g. "E2E-RUN-123-456-") and rejects SQL wildcards, quotes, semicolons, etc.
_VALID_PREFIX_RE = re.compile(r"^[A-Za-z0-9-]+$")


def validate_prefix(prefix: str) -> None:
    """
    Validate the prefix before any SOQL is issued.

    Raises SystemExit(1) with a clear message for any invalid prefix:
      - empty or whitespace-only
      - contains characters outside [A-Za-z0-9-]
      - contains _ or % (common SOQL wildcard / naming-convention violations)

    This is the primary safety mechanism — a misconfigured caller will fail
    loudly rather than silently widening the delete scope.
    """
    if not prefix or not prefix.strip():
        print(
            "ERROR: --prefix must not be empty or whitespace-only.\n"
            "  Provide the per-run prefix, e.g. 'E2E-RUN-123-456-'.",
            file=sys.stderr,
        )
        sys.exit(1)

    if "_" in prefix:
        print(
            f"ERROR: prefix '{prefix}' contains an underscore.\n"
            "  D3 prefixes use hyphens, not underscores.  "
            "No SOQL has been issued.",
            file=sys.stderr,
        )
        sys.exit(1)

    if "%" in prefix:
        print(
            f"ERROR: prefix '{prefix}' contains a SOQL wildcard character '%'.\n"
            "  No SOQL has been issued.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _VALID_PREFIX_RE.match(prefix):
        invalid_chars = sorted(set(c for c in prefix if not re.match(r"[A-Za-z0-9-]", c)))
        print(
            f"ERROR: prefix '{prefix}' contains invalid characters: "
            f"{invalid_chars!r}\n"
            "  Only letters (A-Z, a-z), digits (0-9), and hyphens (-) are allowed.\n"
            "  No SOQL has been issued.",
            file=sys.stderr,
        )
        sys.exit(1)


# ── SOQL helpers ──────────────────────────────────────────────────────────────

def _sf(*args: str) -> dict:
    """Run a `sf` CLI command with --json and return the parsed result dict."""
    cmd = ["sf", *args, "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print(
            "ERROR: 'sf' CLI not found.  Install @salesforce/cli:\n"
            "  npm install -g @salesforce/cli@latest",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(
            f"ERROR: sf CLI returned non-JSON output.\n"
            f"  command: {' '.join(cmd)}\n"
            f"  stdout:  {proc.stdout[:500]}\n"
            f"  stderr:  {proc.stderr[:500]}",
            file=sys.stderr,
        )
        sys.exit(1)

    # sf CLI uses status 0 for success and non-zero for errors, but also
    # embeds a status field inside the JSON.  Check both.
    if proc.returncode != 0 or data.get("status", 0) != 0:
        print(
            f"ERROR: sf CLI command failed.\n"
            f"  command : {' '.join(cmd)}\n"
            f"  message : {data.get('message', '(no message)')}\n"
            f"  name    : {data.get('name', '(no name)')}",
            file=sys.stderr,
        )
        sys.exit(1)

    return data


def query_records(sobject: str, prefix: str, org: str) -> list[str]:
    """Return the Ids of records in `sobject` whose Name starts with `prefix`."""
    # SOQL: WHERE Name LIKE 'E2E-RUN-...-' is exact-prefix; never 'E2E-%'.
    soql = f"SELECT Id FROM {sobject} WHERE Name LIKE '{prefix}%'"
    data = _sf("data", "query", "--query", soql, "--target-org", org)
    records = data.get("result", {}).get("records", [])
    return [r["Id"] for r in records]


def delete_record(sobject: str, record_id: str, org: str) -> None:
    """Delete a single record by ID."""
    _sf("data", "delete", "record", "--sobject", sobject, "--record-id", record_id, "--target-org", org)


# ── Target-list loader ────────────────────────────────────────────────────────

def load_targets(targets_file: Path) -> list[str]:
    """
    Load the SObject list from a YAML file.
    Uses the stdlib only (no PyYAML dependency) — the file is simple enough.
    """
    sobjects: list[str] = []
    in_list = False
    for line in targets_file.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped == "sobjects:":
            in_list = True
            continue
        if in_list:
            if stripped.startswith("- "):
                sobjects.append(stripped[2:].strip())
            else:
                # End of the list block
                in_list = False
    if not sobjects:
        print(
            f"ERROR: no SObjects found in {targets_file}.\n"
            "  Add at least one entry under the 'sobjects:' key.",
            file=sys.stderr,
        )
        sys.exit(1)
    return sobjects


# ── Entry point ───────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> None:
    import os

    parser = argparse.ArgumentParser(
        description="Delete Tier 2 test records from a Salesforce scratch org.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--prefix",
        required=True,
        help=(
            "Per-run record prefix (D3 convention, hyphens only).  "
            "Example: 'E2E-RUN-1-0-test-'"
        ),
    )
    parser.add_argument(
        "--org",
        default=os.environ.get("E2E_SCRATCH_ORG", ""),
        help="Scratch org alias (defaults to $E2E_SCRATCH_ORG).",
    )
    parser.add_argument(
        "--targets-file",
        type=Path,
        default=Path(__file__).parent / "wipe-targets.yml",
        help="Path to the YAML file listing SObjects to wipe (default: wipe-targets.yml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without issuing any delete calls.",
    )
    args = parser.parse_args(argv)

    # ── Validate prefix (FAIL FAST — no SOQL if invalid) ─────────────────────
    validate_prefix(args.prefix)

    if not args.org:
        print(
            "ERROR: --org is required (or set $E2E_SCRATCH_ORG).",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Load SObject targets ──────────────────────────────────────────────────
    targets = load_targets(args.targets_file)
    print(
        f"[wipe_test_records] prefix='{args.prefix}'  org='{args.org}'  "
        f"sobjects={targets}"
        + ("  (dry-run)" if args.dry_run else ""),
        file=sys.stderr,
    )

    # ── Wipe each SObject ─────────────────────────────────────────────────────
    total_deleted = 0
    for sobject in targets:
        ids = query_records(sobject, args.prefix, args.org)
        if not ids:
            print(f"[wipe_test_records] {sobject}: 0 matching records.", file=sys.stderr)
            continue

        print(f"[wipe_test_records] {sobject}: {len(ids)} record(s) to delete.", file=sys.stderr)
        for record_id in ids:
            if args.dry_run:
                print(f"  [dry-run] would delete {sobject}/{record_id}", file=sys.stderr)
            else:
                delete_record(sobject, record_id, args.org)
                print(f"  deleted {sobject}/{record_id}", file=sys.stderr)
                total_deleted += 1

    if args.dry_run:
        print("[wipe_test_records] dry-run complete — no records deleted.", file=sys.stderr)
    else:
        print(f"[wipe_test_records] done — {total_deleted} record(s) deleted.", file=sys.stderr)


if __name__ == "__main__":
    main()
