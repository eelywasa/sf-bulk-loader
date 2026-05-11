#!/usr/bin/env bash
# disable_duplicate_rules.sh — Deactivate the scratch org's active DuplicateRule
# records so Bulk API 2.0 inserts don't blanket-fail with
# `DUPLICATES_DETECTED:Duplicate Alert`.
#
# WHY this exists:
#   Spring '26 scratch orgs ship with three active "Standard *_Duplicate_Rule"
#   records (Account, Contact, Lead).  These rules trigger on fuzzy Name
#   matches and reject inserts via the Bulk API once a few similar names
#   are already in the org.  The default action is `Block`, not `Allow`,
#   so every row in a 50-row Account insert fails with
#     DUPLICATES_DETECTED:Duplicate Alert:--
#   even when the External_Id__c is unique (the rule doesn't consult it).
#
#   Bulk API 2.0 has no header to bypass duplicate detection — the only
#   ways are (a) disable the rules or (b) grant a profile/permset that
#   carries the "ManageDuplicateRules" permission and then... no, even
#   that doesn't bypass.  Disabling the rules in the scratch is the only
#   reliable path.
#
# IDEMPOTENT: looks up only IsActive=true rules and flips them off via the
#   Tooling API.  Already-inactive rules are skipped.  Safe to run on
#   every reuse-mode invocation.
#
# Usage:   ./disable_duplicate_rules.sh
# Env:     E2E_SCRATCH_ORG — target scratch org alias.
# Exits 0 on success, 1 on any failure.

set -euo pipefail

: "${E2E_SCRATCH_ORG:?E2E_SCRATCH_ORG must be set (scratch org alias)}"

echo "[disable_duplicate_rules] target org: ${E2E_SCRATCH_ORG}" >&2

# Tooling API query — DuplicateRule lives there, not in the data API.
# Capture stderr so we see the underlying sf failure if the call returns
# non-zero (without this the GH step just shows "exit code 1" with no clue).
set +e
ACTIVE_RULES_JSON="$(sf data query \
  --query "SELECT Id, DeveloperName FROM DuplicateRule WHERE IsActive = true" \
  --target-org "${E2E_SCRATCH_ORG}" \
  --use-tooling-api \
  --json 2>&1)"
SF_EXIT=$?
set -e
if [[ $SF_EXIT -ne 0 ]]; then
  echo "[disable_duplicate_rules] sf data query failed with exit ${SF_EXIT}" >&2
  echo "[disable_duplicate_rules] sf output below:" >&2
  echo "$ACTIVE_RULES_JSON" >&2
  exit 1
fi

# Use Python to parse + iterate (avoids jq dependency in the runner).
python3 - "${E2E_SCRATCH_ORG}" "${ACTIVE_RULES_JSON}" <<'PYEOF'
import json
import subprocess
import sys

target_org = sys.argv[1]
raw = sys.argv[2]

try:
    data = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"ERROR: could not parse DuplicateRule query output: {e}", file=sys.stderr)
    sys.exit(1)

rules = data.get("result", {}).get("records", [])
if not rules:
    print("[disable_duplicate_rules] no active rules found — nothing to do.", file=sys.stderr)
    sys.exit(0)

print(f"[disable_duplicate_rules] deactivating {len(rules)} active rule(s) ...", file=sys.stderr)
for rule in rules:
    rule_id = rule["Id"]
    dev_name = rule.get("DeveloperName", "<unknown>")
    print(f"[disable_duplicate_rules]   {dev_name} ({rule_id})", file=sys.stderr)
    subprocess.run(
        [
            "sf", "data", "update", "record",
            "--sobject", "DuplicateRule",
            "--record-id", rule_id,
            "--values", "IsActive=false",
            "--use-tooling-api",
            "--target-org", target_org,
        ],
        check=True,
    )

print("[disable_duplicate_rules] done.", file=sys.stderr)
PYEOF
