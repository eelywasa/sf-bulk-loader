#!/usr/bin/env python3
"""
capture_describes.py — Capture Salesforce SObject describe fixtures.

Writes trimmed describe JSON to tests/e2e/sf/fixtures/describe/ for use
by the SFBL E2E suite (SF_DESCRIBE_FIXTURES_DIR fixture mode, SFBL-320).

Usage:
    python capture_describes.py --target-org cdo
    python capture_describes.py --target-org cdo --sobject Account
    python capture_describes.py  # reads $E2E_SCRATCH_ORG env var

REST endpoints used:
    GET /services/data/v62.0/sobjects/              — object list
    GET /services/data/v62.0/sobjects/{name}/describe — per-SObject describe

These are Salesforce REST API calls (not SOQL). sf data query cannot issue them.

Determinism note:
    All JSON output is sorted by key; fields[] are sorted by api_name;
    child_relationships[] are sorted by (child_sobject, field). The only
    non-deterministic field is `fetched_at` (UTC ISO timestamp). Re-running
    against an org with no schema changes produces zero diff besides fetched_at.

Authentication strategy (in order):
    1. Preferred: `sf api request rest <path> --target-org <alias>` — delegates
       auth entirely to the Salesforce CLI. No token extraction needed.
    2. Fallback: `sf org display --json --target-org <alias>` to extract
       accessToken + instanceUrl, then httpx to the REST endpoint directly.
       Used when `sf api request rest` is not available in the pinned CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML not installed. Run: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parents[3]  # tests/e2e/sf/scripts/ → repo root
FIXTURES_DIR = SCRIPT_DIR.parent / "fixtures" / "describe"
TARGETS_FILE = SCRIPT_DIR / "describe-targets.yml"

SF_API_VERSION = "v62.0"


# ---------------------------------------------------------------------------
# Trim helpers (mirrors field-mapping-spec.md D2 field-flag inventory)
# ---------------------------------------------------------------------------


def _trim_field(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Trim a raw Salesforce field descriptor to the shape mandated by D2.

    Included flags:
        api_name, label, type,
        createable, updateable, nillable, defaulted_on_create,
        external_id, id_lookup,
        reference_to ([] unless type == "reference"),
        relationship_name (null unless reference field)
    """
    return {
        "api_name": raw.get("name", ""),
        "createable": bool(raw.get("createable", False)),
        "defaulted_on_create": bool(raw.get("defaultedOnCreate", False)),
        "external_id": bool(raw.get("externalId", False)),
        "id_lookup": bool(raw.get("idLookup", False)),
        "label": raw.get("label", ""),
        "nillable": bool(raw.get("nillable", False)),
        "reference_to": sorted(raw.get("referenceTo", []) or []),
        "relationship_name": raw.get("relationshipName"),
        "type": raw.get("type", ""),
        "updateable": bool(raw.get("updateable", False)),
    }


def _trim_child_relationship(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Trim a raw child relationship to {field, child_sobject}.
    Only include relationships that have both a child SObject and a field name.
    """
    return {
        "child_sobject": raw.get("childSObject", ""),
        "field": raw.get("field", ""),
    }


def _trim_describe(raw: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    """
    Trim a raw SObject describe payload to the D2 fixture shape.

    Output shape:
    {
        "name": "...",
        "fields": [ {D2 field shape, sorted by api_name} ],
        "child_relationships": [ {field, child_sobject}, ... ],
        "fetched_at": "..."
    }
    """
    fields = sorted(
        (_trim_field(f) for f in raw.get("fields", [])),
        key=lambda f: f["api_name"].lower(),
    )

    # Only include child relationships that have both field + childSObject
    child_rels_raw = raw.get("childRelationships", []) or []
    child_rels = sorted(
        (
            _trim_child_relationship(cr)
            for cr in child_rels_raw
            if cr.get("childSObject") and cr.get("field")
        ),
        key=lambda cr: (cr["child_sobject"].lower(), cr["field"].lower()),
    )

    return {
        "child_relationships": child_rels,
        "fetched_at": fetched_at,
        "fields": fields,
        "name": raw.get("name", ""),
    }


def _trim_object_list(raw_sobjects: list[dict[str, Any]]) -> list[str]:
    """
    Mirror the trim logic in backend/app/api/connections.py:list_connection_objects.

    Keep API names of objects where createable OR updateable OR deletable is True.
    Return sorted list of strings.
    """
    return sorted(
        obj["name"]
        for obj in raw_sobjects
        if obj.get("createable") or obj.get("updateable") or obj.get("deletable")
    )


# ---------------------------------------------------------------------------
# Salesforce REST authentication + request
# ---------------------------------------------------------------------------


def _sf_rest_via_cli(path: str, target_org: str) -> dict[str, Any]:
    """
    Preferred path: delegate auth entirely to `sf api request rest`.

    Raises subprocess.CalledProcessError or json.JSONDecodeError on failure.
    """
    result = subprocess.run(
        ["sf", "api", "request", "rest", path, "--target-org", target_org],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _sf_rest_via_httpx(path: str, target_org: str) -> dict[str, Any]:
    """
    Fallback path: extract token + instanceUrl via `sf org display`, then
    call the REST endpoint directly with httpx.

    Requires httpx to be importable (it's in backend/requirements.txt and
    tests/e2e/sf/scripts/requirements.txt).
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "httpx not installed. Run: pip install httpx"
        ) from exc

    display_result = subprocess.run(
        ["sf", "org", "display", "--json", "--target-org", target_org],
        capture_output=True,
        text=True,
        check=True,
    )
    org_info = json.loads(display_result.stdout)["result"]
    access_token = org_info["accessToken"]
    instance_url = org_info["instanceUrl"].rstrip("/")

    url = f"{instance_url}{path}"
    resp = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def sf_rest(path: str, target_org: str) -> dict[str, Any]:
    """
    Call the Salesforce REST API at `path` authenticating as `target_org`.

    Tries `sf api request rest` first; falls back to httpx on failure
    (e.g. older CLI versions that don't support the subcommand).
    """
    try:
        return _sf_rest_via_cli(path, target_org)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(
            f"  [warn] sf api request rest failed ({exc}); falling back to httpx...",
            file=sys.stderr,
        )
        return _sf_rest_via_httpx(path, target_org)


# ---------------------------------------------------------------------------
# Capture routines
# ---------------------------------------------------------------------------


def capture_object_list(target_org: str) -> list[str]:
    """Fetch and trim the SObject list from the org."""
    path = f"/services/data/{SF_API_VERSION}/sobjects/"
    raw = sf_rest(path, target_org)
    return _trim_object_list(raw.get("sobjects", []))


def capture_describe(sobject: str, target_org: str, fetched_at: str) -> dict[str, Any]:
    """Fetch and trim the describe for a single SObject."""
    path = f"/services/data/{SF_API_VERSION}/sobjects/{sobject}/describe"
    raw = sf_rest(path, target_org)
    return _trim_describe(raw, fetched_at)


def write_json(data: Any, output_path: Path) -> None:
    """Write data as diff-friendly JSON (2-space indent, sorted keys, trailing newline)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def load_targets() -> list[str]:
    """Load the configurable SObject list from describe-targets.yml."""
    if not TARGETS_FILE.exists():
        raise FileNotFoundError(f"Targets file not found: {TARGETS_FILE}")
    with TARGETS_FILE.open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    return config.get("sobjects", [])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capture_describes",
        description=(
            "Capture Salesforce SObject describe fixtures for the SFBL E2E suite. "
            "Writes trimmed JSON to tests/e2e/sf/fixtures/describe/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python capture_describes.py --target-org cdo
  python capture_describes.py --target-org cdo --sobject Account
  E2E_SCRATCH_ORG=my-alias python capture_describes.py

Output:
  tests/e2e/sf/fixtures/describe/_object_list.json   (sorted API names)
  tests/e2e/sf/fixtures/describe/{SObjectName}.json  (trimmed describe)
""",
    )
    parser.add_argument(
        "--target-org",
        default=os.environ.get("E2E_SCRATCH_ORG"),
        metavar="ALIAS",
        help=(
            "Salesforce CLI org alias or username. "
            "Defaults to $E2E_SCRATCH_ORG. "
            "Required if $E2E_SCRATCH_ORG is not set."
        ),
    )
    parser.add_argument(
        "--sobject",
        metavar="SOBJECT",
        help=(
            "Capture a single SObject describe and skip the rest. "
            "The object-list (_object_list.json) is always refreshed."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.target_org:
        parser.error(
            "No target org specified. Use --target-org <alias> "
            "or set $E2E_SCRATCH_ORG."
        )

    target_org: str = args.target_org
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Target org  : {target_org}")
    print(f"Fixtures dir: {FIXTURES_DIR}")
    print(f"Fetched at  : {fetched_at}")
    print()

    # Always refresh the object list
    print("Fetching object list...")
    object_list = capture_object_list(target_org)
    obj_list_path = FIXTURES_DIR / "_object_list.json"
    write_json(object_list, obj_list_path)
    print(f"  -> {obj_list_path} ({len(object_list)} objects)")

    # Determine which SObjects to describe
    if args.sobject:
        targets = [args.sobject]
    else:
        targets = load_targets()

    print(f"\nDescribing {len(targets)} SObject(s)...")
    for sobject in targets:
        print(f"  {sobject}...")
        data = capture_describe(sobject, target_org, fetched_at)
        out_path = FIXTURES_DIR / f"{sobject}.json"
        write_json(data, out_path)
        print(f"    -> {out_path} ({len(data['fields'])} fields, {len(data['child_relationships'])} child rels)")

    print("\nDone.")


if __name__ == "__main__":
    main()
