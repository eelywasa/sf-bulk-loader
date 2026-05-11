#!/usr/bin/env bash
# scratch_create.sh — Spin up a Salesforce scratch org for E2E Tier 2 tests.
#
# Required env vars:
#   E2E_SCRATCH_ORG       Alias to assign to the scratch org.
#
# Optional env vars:
#   E2E_ORG_SHAPE         15- or 18-char Salesforce org ID of the source org
#                         to use as a shape template.
#                         "default" (or unset/empty) → project-scratch-def.json
#                         Any other value            → project-scratch-def.shaped.json
#                           (with sourceOrg injected after 15-char normalisation).
#
# Spec refs: D3 (scratch-org strategy), D6 (JWT auth), L1/L2/M1 (shape logic).
#
# Scratch-def files are resolved relative to the sfdx/ directory alongside
# the scripts/ directory (tests/e2e/sf/sfdx/config/).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# SFDX_DIR_OVERRIDE is for unit-test injection only; do not set in production.
SFDX_DIR="${SFDX_DIR_OVERRIDE:-${SCRIPT_DIR}/../sfdx}"

# ---------------------------------------------------------------------------
# Validate required vars
# ---------------------------------------------------------------------------
if [[ -z "${E2E_SCRATCH_ORG:-}" ]]; then
  echo "ERROR: E2E_SCRATCH_ORG must be set to a non-empty alias for the scratch org." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Determine org-shape mode
# ---------------------------------------------------------------------------
E2E_ORG_SHAPE="${E2E_ORG_SHAPE:-default}"

if [[ "$E2E_ORG_SHAPE" == "default" ]] || [[ -z "$E2E_ORG_SHAPE" ]]; then
  # Plain scratch-def — no shape required.
  SCRATCH_DEF="${SFDX_DIR}/config/project-scratch-def.json"
  USE_SHAPE=false
  echo "INFO: Using default scratch-def (no org shape): ${SCRATCH_DEF}"
else
  # Org-shape mode — normalise the ID and verify an active shape exists.
  USE_SHAPE=true

  # -------------------------------------------------------------------------
  # L2 third-pass: org ID normalisation (15 or 18 chars → 15 chars).
  # The Salesforce CLI's sourceOrg field expects 15-char IDs only.
  # -------------------------------------------------------------------------
  ID_LEN="${#E2E_ORG_SHAPE}"
  if [[ "$ID_LEN" -eq 18 ]]; then
    E2E_ORG_SHAPE_NORMALIZED="${E2E_ORG_SHAPE:0:15}"
    echo "INFO: 18-char E2E_ORG_SHAPE detected; truncated to 15 chars: ${E2E_ORG_SHAPE_NORMALIZED}"
  elif [[ "$ID_LEN" -eq 15 ]]; then
    E2E_ORG_SHAPE_NORMALIZED="${E2E_ORG_SHAPE}"
    echo "INFO: 15-char E2E_ORG_SHAPE: ${E2E_ORG_SHAPE_NORMALIZED}"
  else
    echo "ERROR: expected 15- or 18-char Salesforce org ID, got ${ID_LEN}-char value: '${E2E_ORG_SHAPE}'" >&2
    exit 1
  fi

  # -------------------------------------------------------------------------
  # L1 + M1 fourth-pass: verify an active org shape exists before attempting
  # scratch creation.
  #
  # Command: sf org list shape --json
  # IMPORTANT: --target-org is NOT a valid flag on this command (M1 fix).
  # The JSON response structure is:
  #   { "status": 0, "result": [ { "sourceOrg": "<15orgs>", "status": "Active" }, ... ] }
  # -------------------------------------------------------------------------
  echo "INFO: Checking for an active org shape for source org: ${E2E_ORG_SHAPE_NORMALIZED}"

  # Allow sf to fail without killing the script so we can surface a clear error.
  SF_SHAPE_JSON="$(sf org list shape --json 2>&1)" || {
    echo "ERROR: 'sf org list shape --json' failed." >&2
    echo "  Ensure the Salesforce CLI is installed and you are authenticated to a Dev Hub" >&2
    echo "  that has Org Shape enabled." >&2
    echo "  CLI output: ${SF_SHAPE_JSON}" >&2
    exit 1
  }

  # Parse with Python (available in the CI runner; avoids a jq dependency).
  # Exits 0 with stdout "FOUND" or "NOT_FOUND".
  SHAPE_MATCH="$(python3 - "${E2E_ORG_SHAPE_NORMALIZED}" "${SF_SHAPE_JSON}" <<'PYEOF'
import sys, json

normalized_id = sys.argv[1]
raw_json      = sys.argv[2]

try:
    data = json.loads(raw_json)
except json.JSONDecodeError as e:
    print(f"ERROR: Could not parse 'sf org list shape --json' output: {e}", file=sys.stderr)
    sys.exit(2)

shapes = data.get("result", [])
for shape in shapes:
    source_org = shape.get("sourceOrg", "")
    # Normalise the field value the same way (18→15 by truncation).
    if len(source_org) == 18:
        source_org = source_org[:15]
    if source_org == normalized_id and shape.get("status") == "Active":
        print("FOUND")
        sys.exit(0)

print("NOT_FOUND")
sys.exit(0)
PYEOF
  )" || {
    echo "ERROR: Org shape lookup script failed unexpectedly." >&2
    exit 1
  }

  if [[ "$SHAPE_MATCH" != "FOUND" ]]; then
    echo "ERROR: No active org shape found for source org ID '${E2E_ORG_SHAPE_NORMALIZED}'." >&2
    echo "" >&2
    echo "  Org Shape must be created manually (async, typically ~30 min) before running Tier 2." >&2
    echo "  Run the following against your source org ALIAS (not its ID):" >&2
    echo "" >&2
    echo "    sf org create shape --target-org <source-org-alias>" >&2
    echo "" >&2
    echo "  Then re-run once 'sf org list shape --json' shows status=Active for that org." >&2
    exit 1
  fi

  echo "INFO: Active org shape confirmed for ${E2E_ORG_SHAPE_NORMALIZED}."
  SCRATCH_DEF="${SFDX_DIR}/config/project-scratch-def.shaped.json"
  echo "INFO: Using shaped scratch-def: ${SCRATCH_DEF}"
fi

# ---------------------------------------------------------------------------
# For shaped scratch creation, inject the normalised 15-char sourceOrg into
# the scratch-def JSON before invoking the CLI.
# ---------------------------------------------------------------------------
if [[ "$USE_SHAPE" == "true" ]]; then
  TMP_SCRATCH_DEF="$(mktemp /tmp/scratch-def-XXXXXX.json)"
  trap 'rm -f "$TMP_SCRATCH_DEF"' EXIT

  python3 - "${SCRATCH_DEF}" "${E2E_ORG_SHAPE_NORMALIZED}" "${TMP_SCRATCH_DEF}" <<'PYEOF'
import sys, json

src_file   = sys.argv[1]
source_org = sys.argv[2]
dst_file   = sys.argv[3]

with open(src_file) as f:
    spec = json.load(f)

spec["sourceOrg"] = source_org

with open(dst_file, "w") as f:
    json.dump(spec, f, indent=2)

print(f"INFO: Wrote scratch-def with sourceOrg={source_org} to {dst_file}")
PYEOF

  SCRATCH_DEF="$TMP_SCRATCH_DEF"
fi

# ---------------------------------------------------------------------------
# Create the scratch org
# ---------------------------------------------------------------------------
echo "INFO: Creating scratch org alias='${E2E_SCRATCH_ORG}' from def='${SCRATCH_DEF}'"

sf org create scratch \
  -f "${SCRATCH_DEF}" \
  --alias "${E2E_SCRATCH_ORG}" \
  --set-default \
  --duration-days 1

echo "INFO: Scratch org '${E2E_SCRATCH_ORG}' created successfully."
